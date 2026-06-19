#!/bin/bash

#################################################################################
# Production Ingestion Script for su-docs-search
#
# Purpose: Orchestrate full ingestion pipeline with Docker Compose
#
# Usage:
#   ./ingest-server.sh [OPTION]
#
# Options:
#   --full          Ingest all URLs from sitemap (6911 pages)
#   --50percent     Ingest 50% of URLs (3455 pages) - DEFAULT
#   --100           Ingest 100 pages (quick test)
#   --urls-file <FILE>  Ingest URLs from specific file
#   --no-force      Keep existing index (append mode)
#   --detach        Run in background (don't show logs)
#   --help          Show this message
#
# Examples:
#   ./ingest-server.sh                    # Ingest 50%, show logs
#   ./ingest-server.sh --full --detach    # Ingest all in background
#   ./ingest-server.sh --100              # Quick 100-page test
#   ./ingest-server.sh --urls-file custom_urls.txt
#
# Environment:
#   GEMINI_API_KEY  Required. Set in .env or export before running
#
#################################################################################

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration defaults
MODE="50percent"
URLS_FILE=""
FORCE_FLAG="--force"
DETACH_MODE=""
DOCKER_COMPOSE="docker-compose -f docker-compose-ingest.yml"

# Logging
LOG_DIR="./ingest-logs"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/ingest_${TIMESTAMP}.log"

# Functions
print_banner() {
    echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║     Su-Docs-Search: Production Ingestion Pipeline         ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
}

print_help() {
    grep "^#" "$0" | grep -E "^# " | sed 's/^# //;s/^#!//' | head -30
}

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1" | tee -a "$LOG_FILE"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1" | tee -a "$LOG_FILE"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" | tee -a "$LOG_FILE"
}

check_requirements() {
    log_info "Checking requirements..."

    # Check Docker
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed or not in PATH"
        exit 1
    fi

    # Check Docker Compose
    if ! command -v docker-compose &> /dev/null; then
        log_error "Docker Compose is not installed or not in PATH"
        exit 1
    fi

    # Check .env file
    if [ ! -f ".env" ]; then
        log_error ".env file not found. Please create .env first"
        echo -e "  Copy from .env.example: ${BLUE}cp .env.example .env${NC}"
        echo -e "  Then edit to add your GEMINI_API_KEY"
        exit 1
    fi

    # Check GEMINI_API_KEY (optional)
    if ! grep -q "GEMINI_API_KEY=" .env || grep "GEMINI_API_KEY=" .env | grep -q "placeholder\|your_key"; then
        log_warn "GEMINI_API_KEY not configured"
        log_warn "Running in BM25-only mode (no semantic/vector search)"
        log_warn "To enable vector search: edit .env with a Gemini API key"
    else
        log_info "✓ GEMINI_API_KEY is configured"
    fi

    log_info "✓ All requirements met"
}

prepare_url_list() {
    log_info "Preparing URL list ($MODE mode)..."

    case "$MODE" in
        "100")
            if [ ! -f "/tmp/test_urls_100.txt" ]; then
                python3 << 'PYTHON_EOF'
import httpx
import xml.etree.ElementTree as ET

response = httpx.get("https://docs.searchunify.com/Sitemap.xml", timeout=30)
root = ET.fromstring(response.content)
ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
urls = [loc.text for loc in root.findall('sm:url/sm:loc', ns)][:100]

with open('/tmp/test_urls_100.txt', 'w') as f:
    for url in urls:
        f.write(url + '\n')
print(f"Created /tmp/test_urls_100.txt with {len(urls)} URLs")
PYTHON_EOF
            fi
            URLS_FILE="/tmp/test_urls_100.txt"
            ;;
        "50percent")
            if [ ! -f "/tmp/ingest_50_percent.txt" ]; then
                python3 << 'PYTHON_EOF'
import httpx
import xml.etree.ElementTree as ET

response = httpx.get("https://docs.searchunify.com/Sitemap.xml", timeout=30)
root = ET.fromstring(response.content)
ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
urls = [loc.text for loc in root.findall('sm:url/sm:loc', ns)]
half = len(urls) // 2

with open('/tmp/ingest_50_percent.txt', 'w') as f:
    for url in urls[:half]:
        f.write(url + '\n')
print(f"Created /tmp/ingest_50_percent.txt with {half} URLs (50% of {len(urls)})")
PYTHON_EOF
            fi
            URLS_FILE="/tmp/ingest_50_percent.txt"
            ;;
        "full")
            if [ ! -f "/tmp/all_urls.txt" ]; then
                python3 << 'PYTHON_EOF'
import httpx
import xml.etree.ElementTree as ET

response = httpx.get("https://docs.searchunify.com/Sitemap.xml", timeout=30)
root = ET.fromstring(response.content)
ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
urls = [loc.text for loc in root.findall('sm:url/sm:loc', ns)]

with open('/tmp/all_urls.txt', 'w') as f:
    for url in urls:
        f.write(url + '\n')
print(f"Created /tmp/all_urls.txt with {len(urls)} URLs")
PYTHON_EOF
            fi
            URLS_FILE="/tmp/all_urls.txt"
            ;;
    esac

    if [ -f "$URLS_FILE" ]; then
        URL_COUNT=$(wc -l < "$URLS_FILE")
        log_info "✓ URL list ready: $URLS_FILE ($URL_COUNT URLs)"
    else
        log_error "Failed to create URL list"
        exit 1
    fi
}

start_services() {
    log_info "Starting Docker services..."

    # Pull images
    $DOCKER_COMPOSE pull

    # Start services
    if [ -z "$DETACH_MODE" ]; then
        $DOCKER_COMPOSE up --wait
    else
        $DOCKER_COMPOSE up -d --wait
        log_info "✓ Services started in background"
    fi

    # Wait for services to be healthy
    log_info "Waiting for services to be healthy..."
    sleep 5

    # Check OpenSearch
    if curl -s http://localhost:9200/_cluster/health &> /dev/null; then
        log_info "✓ OpenSearch is healthy"
    else
        log_error "OpenSearch not responding"
        exit 1
    fi

    # Check Pinecone
    if curl -s http://localhost:5081/health &> /dev/null; then
        log_info "✓ Pinecone is healthy"
    else
        log_error "Pinecone not responding"
        exit 1
    fi
}

run_ingestion() {
    log_info "Starting ingestion pipeline..."
    log_info "Configuration:"
    log_info "  Mode: $MODE"
    log_info "  URLs: $URLS_FILE"
    log_info "  URL Count: $URL_COUNT"
    log_info "  Force Recreate Index: $([ -n "$FORCE_FLAG" ] && echo 'Yes' || echo 'No')"
    log_info "  Log File: $LOG_FILE"
    echo ""

    # Build ingest command
    INGEST_CMD="python -m backend.ingest --urls-file $URLS_FILE"
    if [ -n "$FORCE_FLAG" ]; then
        INGEST_CMD="$INGEST_CMD $FORCE_FLAG"
    fi

    # Run in container
    if [ -z "$DETACH_MODE" ]; then
        $DOCKER_COMPOSE exec backend $INGEST_CMD 2>&1 | tee -a "$LOG_FILE"
    else
        $DOCKER_COMPOSE exec -T backend $INGEST_CMD >> "$LOG_FILE" 2>&1 &
        INGEST_PID=$!
        log_info "Ingestion running in background (PID: $INGEST_PID)"
        log_info "Monitor with: tail -f $LOG_FILE"
    fi
}

wait_for_completion() {
    if [ -n "$DETACH_MODE" ]; then
        log_info "Waiting for ingestion to complete..."
        log_info "(You can Ctrl+C and monitor with: tail -f $LOG_FILE)"

        # Poll for completion
        while true; do
            if tail -1 "$LOG_FILE" | grep -q "ingest DONE"; then
                log_info "✓ Ingestion completed!"
                break
            fi
            sleep 10
        done
    fi
}

show_summary() {
    log_info ""
    log_info "╔════════════════════════════════════════════════════════════╗"
    log_info "║              INGESTION COMPLETE - NEXT STEPS               ║"
    log_info "╚════════════════════════════════════════════════════════════╝"
    log_info ""
    log_info "✓ Data indexed to:"
    log_info "  - OpenSearch BM25: http://localhost:9200/su-docs"

    # Check if Gemini key is configured
    if grep "GEMINI_API_KEY=" .env | grep -q "placeholder\|your_key"; then
        log_warn "  - Vector Search: DISABLED (no Gemini API key)"
        log_info "    To enable: set GEMINI_API_KEY in .env and re-run ingestion"
    else
        log_info "  - Pinecone Vectors: http://localhost:5081"
    fi

    log_info ""
    log_info "Search API: http://localhost:8000/api/search?q=<query>&top_n=20"
    log_info "Web UI: http://localhost:5173"
    log_info "Health Check: http://localhost:8000/api/health"
    log_info ""
    log_info "Full Log: $LOG_FILE"
    log_info ""
}

cleanup_on_exit() {
    log_info "Cleaning up..."
    # Keep services running, just log the cleanup
}

# Main execution
main() {
    print_banner

    # Create log directory
    mkdir -p "$LOG_DIR"

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --full)
                MODE="full"
                shift
                ;;
            --50percent)
                MODE="50percent"
                shift
                ;;
            --100)
                MODE="100"
                shift
                ;;
            --urls-file)
                MODE="custom"
                URLS_FILE="$2"
                shift 2
                ;;
            --no-force)
                FORCE_FLAG=""
                shift
                ;;
            --detach)
                DETACH_MODE="true"
                shift
                ;;
            --help)
                print_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                print_help
                exit 1
                ;;
        esac
    done

    log_info "Starting ingestion in $MODE mode"
    log_info ""

    trap cleanup_on_exit EXIT

    check_requirements
    prepare_url_list
    start_services
    run_ingestion
    wait_for_completion
    show_summary
}

# Run main
main "$@"
