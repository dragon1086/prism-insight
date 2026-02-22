#!/bin/bash
# PRISM-INSIGHT Quick Start Script
#
# Usage:
#   ./quickstart.sh YOUR_OPENAI_API_KEY
#
# This script will:
#   1. Install Python dependencies
#   2. Configure your API key
#   3. Run US stock analysis (no Telegram required)
#
# Reports will be saved to prism-us/pdf_reports/

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo ""
echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     PRISM-INSIGHT Quick Start          ║${NC}"
echo -e "${BLUE}║     AI-Powered Stock Analysis          ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""

# Check for API key
if [ -z "$1" ]; then
    echo -e "${YELLOW}Usage: ./quickstart.sh YOUR_OPENAI_API_KEY${NC}"
    echo ""
    echo "Get your API key from: https://platform.openai.com/api-keys"
    exit 1
fi

OPENAI_API_KEY=$1

# Check Python version
echo -e "${BLUE}[1/5]${NC} Checking Python version..."
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Python 3 is required but not installed.${NC}"
    exit 1
fi
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "       Python $PYTHON_VERSION detected ✓"

# Install dependencies
echo -e "${BLUE}[2/5]${NC} Installing Python dependencies..."
if command -v pip &> /dev/null; then
    pip install -q -r requirements.txt
else
    echo -e "       pip not found, using uv..."
    uv init
    uv add -r requirements.txt
fi
echo -e "       Dependencies installed ✓"

# Install Playwright
echo -e "${BLUE}[3/5]${NC} Installing Playwright for PDF generation..."
python3 -m playwright install chromium > /dev/null 2>&1
echo -e "       Playwright installed ✓"

# Configure API keys
echo -e "${BLUE}[4/5]${NC} Configuring API keys..."
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
cp mcp_agent.config.yaml.example mcp_agent.config.yaml

# Update the secrets file with the API key
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    sed -i '' "s/example key/$OPENAI_API_KEY/" mcp_agent.secrets.yaml
else
    # Linux
    sed -i "s/example key/$OPENAI_API_KEY/" mcp_agent.secrets.yaml
fi
echo -e "       API key configured ✓"

# Run demo analysis (single stock report, not full pipeline)
echo -e "${BLUE}[5/5]${NC} Generating AI analysis report for Apple (AAPL)..."
echo ""
echo -e "${YELLOW}This may take 3-5 minutes. AI agents are analyzing...${NC}"
echo ""

python3 demo.py AAPL

echo ""
echo "Next steps:"
echo "  • Try analyzing other stocks: python3 demo.py MSFT"
echo "  • Run full pipeline: python3 prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram"
echo "  • Set up Telegram for real-time alerts (see docs/SETUP.md)"
echo ""
echo -e "⭐ ${YELLOW}If this helped you, please star us on GitHub!${NC}"
echo "   https://github.com/dragon1086/prism-insight"
echo ""
