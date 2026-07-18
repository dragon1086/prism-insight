import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "report_generator.py"

EXPECTED = {
    "generate_follow_up_response": {
        "args": ["ticker", "ticker_name", "conversation_context", "user_question", "tone"],
        "servers": ("perplexity", "kospi_kosdaq"),
        "max_tokens": 4000,
    },
    "generate_evaluation_response": {
        "args": ["ticker", "ticker_name", "avg_price", "period", "tone", "background", "report_path", "memory_context"],
        "servers": ("perplexity", "kospi_kosdaq", "time"),
        "max_tokens": 8000,
    },
    "generate_us_evaluation_response": {
        "args": ["ticker", "ticker_name", "avg_price", "period", "tone", "background", "memory_context"],
        "servers": ("perplexity", "yahoo_finance", "time"),
        "max_tokens": 8000,
    },
    "generate_us_follow_up_response": {
        "args": ["ticker", "ticker_name", "conversation_context", "user_question", "tone"],
        "servers": ("perplexity", "yahoo_finance"),
        "max_tokens": 4000,
    },
    "generate_journal_conversation_response": {
        "args": ["user_id", "user_message", "memory_context", "ticker", "ticker_name", "conversation_history"],
        "servers": ("perplexity", "kospi_kosdaq"),
        "max_tokens": 4000,
    },
    "generate_firecrawl_search_response": {
        "args": ["search_query", "analysis_prompt", "limit"],
        "servers": (),
        "max_tokens": 4000,
    },
    "generate_firecrawl_followup_response": {
        "args": ["command", "query", "conversation_context", "user_question"],
        "servers": None,
        "max_tokens": 4000,
    },
}


def _keyword_node(call: ast.Call, *names: str):
    for keyword in call.keywords:
        if keyword.arg in names:
            return keyword.value
    raise AssertionError(f"missing keyword {names}")


def _literal_keyword(call: ast.Call, *names: str):
    return ast.literal_eval(_keyword_node(call, *names))


def _contract(function: ast.AsyncFunctionDef):
    agent_call = None
    generation_call = None
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "Agent":
            agent_call = node
        if isinstance(node.func, ast.Name) and node.func.id == "_generate_telegram_text":
            generation_call = node

    assert agent_call is not None
    if agent_call is not None:
        server_node = _keyword_node(agent_call, "server_names")
        servers = None if isinstance(server_node, ast.Name) else tuple(ast.literal_eval(server_node))
    if generation_call is not None:
        max_tokens = _literal_keyword(generation_call, "max_tokens")
    else:
        request_call = next(
            node
            for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "RequestParams"
        )
        max_tokens = _literal_keyword(request_call, "maxTokens")

    return servers, max_tokens


def test_public_async_contracts_are_preserved():
    tree = ast.parse(SOURCE.read_text())
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name in EXPECTED
    }
    assert functions.keys() == EXPECTED.keys()

    for name, expected in EXPECTED.items():
        function = functions[name]
        assert [arg.arg for arg in function.args.args] == expected["args"]
        servers, max_tokens = _contract(function)
        assert servers == expected["servers"]
        assert max_tokens == expected["max_tokens"]
