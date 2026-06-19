#!/bin/bash

#################################################################################
# Server Startup Script for Su-Docs-Search
#
# Purpose: Start all services (OpenSearch, Pinecone, Backend, Frontend)
#
# Usage:
#   ./start-server.sh [OPTIONS]
#
# Options:
#   --backend-only      Start only backend API (no frontend)
#   --no-frontend       Start all services except React UI
#   --detach            Run in background
#   --logs              Show live logs after startup
#   --help              Show this message
#
# Examples:
#   ./start-server.sh                    # Start everything, show logs
#   ./start-server.sh --detach           # Start in background
#   ./start-server.sh --backend-only     # Just API (for testing)
#   ./start-server.sh --no-frontend      # API + search engines only
#
#################################################################################

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
BACKEND_ONLY=false
NO_FRONTEND=false
DETACH_MODE=false
SHOW_LOGS=false
DOCKER_COMPOSE="docker-compose"

# Functions
print_banner() {
    echo -e "${BLUE}"
    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║         Su-Docs-Search: Server Startup                        ║"
    echo "║                                                                ║"
    echo "║  Starting services:                                            ║"
    echo "║    • OpenSearch (BM25 search)      → http://localhost:9200    ║"
    echo "║    • Pinecone (Vector search)      → http://localhost:5081    ║"
    echo "║    • FastAPI Backend               → http://localhost:8000    ║"
    echo "║    • React Frontend                → http://localhost:5173    ║"
    echo "║                                                                ║"
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

print_help() {
    grep "^#" "$0" | grep -E "^# " | sed 's/^# //;s/^#!//' | head -30
}

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
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
        log_warn ".env file not found"
        echo -e "  Copy from .env.example: ${BLUE}cp .env.example .env${NC}"
        echo -e "  Then edit to add your GEMINI_API_KEY"
        read -p "Continue anyway? (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi

    log_info "✓ All requirements met"
}

check_ports() {
    log_info "Checking if ports are available..."

    local ports=(9200 5081 8000 5173)
    local in_use=0

    for port in "${ports[@]}"; do
        if lsof -Pi :$port -sTCP:LISTEN -t >/dev/null 2>&1; then
            log_warn "Port $port is already in use"
            in_use=$((in_use + 1))
        fi
    done

    if [ $in_use -gt 0 ]; then
        log_warn "$in_use ports already in use - services might fail"
        read -p "Continue anyway? (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Aborting startup"
            exit 1
        fi
    else
        log_info "✓ All ports available"
    fi
}

start_services() {
    log_info "Starting Docker services..."

    if [ "$BACKEND_ONLY" = true ]; then
        # Only start backend (assumes OpenSearch/Pinecone already running)
        if [ "$DETACH_MODE" = true ]; then
            poetry run uvicorn backend.main:app --host 127.0.0.1 --port 8000 > /tmp/backend_server.log 2>&1 &
            echo $! > /tmp/backend_server.pid
            log_info "✓ Backend started (PID: $(cat /tmp/backend_server.pid))"
        else
            poetry run uvicorn backend.main:app --host 127.0.0.1 --port 8000
        fi
    else
        # Full Docker Compose startup
        if [ "$DETACH_MODE" = true ]; then
            $DOCKER_COMPOSE up -d
            log_info "✓ Services started in background"
        else
            $DOCKER_COMPOSE up
        fi
    fi
}

wait_for_services() {
    log_info "Waiting for services to be ready..."

    local max_attempts=30
    local attempt=0

    # Check backend
    log_info "Checking backend API..."
    while ! curl -s http://localhost:8000/api/health &>/dev/null; do
        attempt=$((attempt + 1))
        if [ $attempt -gt $max_attempts ]; then
            log_error "Backend API failed to start"
            return 1
        fi
        echo -n "."
        sleep 1
    done
    echo ""
    log_info "✓ Backend API is ready"

    # Check OpenSearch
    log_info "Checking OpenSearch..."
    attempt=0
    while ! curl -s http://localhost:9200/_cluster/health &>/dev/null; do
        attempt=$((attempt + 1))
        if [ $attempt -gt $max_attempts ]; then
            log_warn "OpenSearch not responding (might be starting)"
            break
        fi
        echo -n "."
        sleep 1
    done
    echo ""
    log_info "✓ OpenSearch is ready"

    # Check Pinecone
    log_info "Checking Pinecone..."
    attempt=0
    while ! curl -s http://localhost:5081/health &>/dev/null 2>&1; do
        attempt=$((attempt + 1))
        if [ $attempt -gt $max_attempts ]; then
            log_warn "Pinecone not responding (might be starting)"
            break
        fi
        echo -n "."
        sleep 1
    done
    echo ""
    log_info "✓ Pinecone is ready"
}

show_urls() {
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║                    🎉 SERVER IS RUNNING 🎉                      ║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    echo -e "${CYAN}API ENDPOINTS:${NC}"
    echo -e "  ${GREEN}Search API${NC}     http://localhost:8000/api/search?q=jira&top_n=20"
    echo -e "  ${GREEN}Health Check${NC}   http://localhost:8000/api/health"
    echo ""

    echo -e "${CYAN}WEB INTERFACES:${NC}"
    if [ "$NO_FRONTEND" = false ] && [ "$BACKEND_ONLY" = false ]; then
        echo -e "  ${GREEN}Web UI (React)${NC}  http://localhost:5173"
    fi
    echo -e "  ${GREEN}OpenSearch${NC}      http://localhost:9200"
    echo -e "  ${GREEN}Pinecone${NC}        http://localhost:5081"
    echo ""

    echo -e "${CYAN}EXAMPLE COMMANDS:${NC}"
    echo -e "  ${BLUE}# Search API${NC}"
    echo -e "  curl 'http://localhost:8000/api/search?q=jira&top_n=10' | jq ."
    echo ""
    echo -e "  ${BLUE}# With pretty print${NC}"
    echo -e "  curl 'http://localhost:8000/api/search?q=authentication' | jq '.results[:3]'"
    echo ""

    echo -e "${CYAN}USEFUL COMMANDS:${NC}"
    echo -e "  ${BLUE}View logs:${NC}        docker-compose logs -f backend"
    echo -e "  ${BLUE}Stop services:${NC}    docker-compose down"
    echo -e "  ${BLUE}Check status:${NC}     docker-compose ps"
    echo ""
}

show_logs() {
    if [ "$DETACH_MODE" = true ]; then
        echo ""
        log_info "Showing live logs (Ctrl+C to exit)..."
        echo ""

        if [ "$BACKEND_ONLY" = true ]; then
            tail -f /tmp/backend_server.log
        else
            $DOCKER_COMPOSE logs -f
        fi
    fi
}

cleanup_on_exit() {
    log_info "Stopping services..."

    if [ "$BACKEND_ONLY" = false ]; then
        $DOCKER_COMPOSE down
    fi
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --backend-only)
            BACKEND_ONLY=true
            shift
            ;;
        --no-frontend)
            NO_FRONTEND=true
            shift
            ;;
        --detach)
            DETACH_MODE=true
            shift
            ;;
        --logs)
            SHOW_LOGS=true
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

# Main execution
main() {
    print_banner
    check_requirements
    check_ports

    if [ "$BACKEND_ONLY" = false ]; then
        trap cleanup_on_exit EXIT
    fi

    start_services

    if [ "$DETACH_MODE" = true ]; then
        sleep 3
    fi

    wait_for_services
    show_urls

    if [ "$SHOW_LOGS" = true ]; then
        show_logs
    elif [ "$DETACH_MODE" = true ]; then
        log_info "Services running in background"
        if [ "$BACKEND_ONLY" = false ]; then
            log_info "View logs with: docker-compose logs -f"
        else
            log_info "View logs with: tail -f /tmp/backend_server.log"
        fi
    fi
}

# Run main
main "$@"
