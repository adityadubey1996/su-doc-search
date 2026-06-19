#!/bin/bash
set -e

echo "================================"
echo "su-docs-search Setup"
echo "================================"
echo ""

# Check if .env already exists
if [ -f .env ]; then
  echo "✓ .env file already exists"
else
  echo "Creating .env from template..."
  cp .env.example .env
  echo "✓ Created .env file"
fi

echo ""
echo "================================"
echo "Configuration"
echo "================================"
echo ""

# Check current GEMINI_API_KEY
CURRENT_KEY=$(grep "GEMINI_API_KEY" .env | cut -d= -f2 | xargs)

if [ "$CURRENT_KEY" = "your_key_here" ] || [ "$CURRENT_KEY" = "placeholder" ]; then
  echo "⚠ GEMINI_API_KEY not configured"
  echo ""
  echo "Without a Gemini API key:"
  echo "  • Search will use BM25 (lexical) only"
  echo "  • Ingestion will be faster (no embedding step)"
  echo "  • Vector/semantic search will be disabled"
  echo ""
  read -p "Do you want to add a Gemini API key now? (y/n) " -n 1 -r
  echo ""
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo "Get a free API key from: https://aistudio.google.com"
    echo ""
    read -p "Enter your Gemini API key: " GEMINI_KEY
    if [ -n "$GEMINI_KEY" ]; then
      sed -i '' "s/GEMINI_API_KEY=.*/GEMINI_API_KEY=$GEMINI_KEY/" .env
      echo "✓ Gemini API key saved to .env"
    fi
  else
    echo "✓ Proceeding with BM25-only search (no semantic search)"
  fi
else
  echo "✓ GEMINI_API_KEY is configured"
fi

echo ""
echo "================================"
echo "Prerequisites Check"
echo "================================"
echo ""

# Check Docker
if ! command -v docker &> /dev/null; then
  echo "✗ Docker not found"
  echo "  Install from: https://www.docker.com/products/docker-desktop"
  exit 1
else
  echo "✓ Docker installed"
fi

# Check Docker Compose
if ! command -v docker-compose &> /dev/null; then
  echo "✗ Docker Compose not found"
  echo "  Install from: https://docs.docker.com/compose/install/"
  exit 1
else
  echo "✓ Docker Compose installed"
fi

# Check if Docker daemon is running
if ! docker info > /dev/null 2>&1; then
  echo "✗ Docker daemon not running"
  echo "  Start Docker Desktop or run: sudo systemctl start docker"
  exit 1
else
  echo "✓ Docker daemon is running"
fi

echo ""
echo "================================"
echo "Setup Complete!"
echo "================================"
echo ""
echo "Next steps:"
echo ""
echo "1. Run ingestion:"
echo "   ./ingest-server.sh --100       # Quick test (1 URL, ~1 min)"
echo "   ./ingest-server.sh --50percent # Medium (3455 URLs, ~9 min)"
echo "   ./ingest-server.sh --full      # Full (6911 URLs, ~45 min)"
echo ""
echo "2. Start the server:"
echo "   ./start-server.sh"
echo ""
echo "3. Access the API:"
echo "   curl http://localhost:8000/api/search?q=jira"
echo ""
echo "4. Open the web UI:"
echo "   http://localhost:5173"
echo ""
echo "For detailed documentation, see:"
echo "  • QUICK_START.md - Quick reference"
echo "  • DEPLOYMENT_GUIDE.md - Complete guide"
echo "  • CODE_WALKTHROUGH.md - Architecture details"
echo ""
