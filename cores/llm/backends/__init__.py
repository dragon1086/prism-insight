"""
LLM backend adapters — concrete implementations of cores.llm.ports.LLMBackend.

Each submodule guards its SDK import so collection never fails when the SDK
is absent (e.g. in the lightweight test environment).

Available adapters:
  - mcp_agent_backend.McpAgentBackend  (strangler shim, Phase 1–4)
"""
