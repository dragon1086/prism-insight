#!/bin/bash
set -euo pipefail

PROJECT_ROOT="/app/prism-insight"
CONFIG_DIR="${PROJECT_ROOT}/quickstart-config"
SECRETS_PATH="${CONFIG_DIR}/mcp_agent.secrets.yaml"
CONFIG_PATH="${CONFIG_DIR}/mcp_agent.config.yaml"

echo "========================================"
echo "  PRISM-INSIGHT Quick Start"
echo "========================================"

mkdir -p "${CONFIG_DIR}" \
         "${PROJECT_ROOT}/reports" \
         "${PROJECT_ROOT}/pdf_reports" \
         "${PROJECT_ROOT}/prism-us/reports" \
         "${PROJECT_ROOT}/prism-us/pdf_reports"

if [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "[ERROR] OPENAI_API_KEY is required for quickstart."
    echo "[ERROR] Export OPENAI_API_KEY before running docker compose."
    exit 1
fi

if [ ! -f "${SECRETS_PATH}" ]; then
    echo "[INIT] Creating quickstart secrets config..."
    cat > "${SECRETS_PATH}" <<EOF
\$schema: ../../schema/mcp-agent.config.schema.json

openai:
  api_key: ${OPENAI_API_KEY}

anthropic:
  api_key: ""
EOF
fi

echo "[INIT] Writing quickstart MCP config..."
{
    cat <<EOF
\$schema: ../../schema/mcp-agent.config.schema.json

execution_engine: asyncio
logger:
  type: console
  level: info

mcp:
  servers:
    yahoo_finance:
      command: "uvx"
      args: ["--from", "yahoo-finance-mcp", "yahoo-finance-mcp"]
      read_timeout_seconds: 120
EOF
    if [ -n "${PERPLEXITY_API_KEY:-}" ]; then
        cat <<EOF
    perplexity:
      command: "npx"
      args: ["-y", "@perplexity-ai/mcp-server"]
      env:
        PERPLEXITY_API_KEY: "${PERPLEXITY_API_KEY}"
EOF
    fi
    cat <<EOF
openai:
  default_model: gpt-5.1
  reasoning_effort: high
EOF
} > "${CONFIG_PATH}"

cp "${SECRETS_PATH}" "${PROJECT_ROOT}/mcp_agent.secrets.yaml"
cp "${CONFIG_PATH}" "${PROJECT_ROOT}/mcp_agent.config.yaml"

echo "[INIT] Quickstart config ready"
echo "[INIT] Reports will be written to ./quickstart-output/"
echo "[INIT] Run a demo with:"
echo "       docker exec -it prism-quickstart python3 demo.py NVDA"
echo ""

exec "${PROJECT_ROOT}/docker/entrypoint.sh" "$@"
