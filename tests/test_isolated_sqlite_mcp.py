import ast
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import types

from tools.isolated_sqlite_mcp import (
    ReadOnlyDatabase, ReadOnlyError, call_read_tool, read_tools, read_authorizer, create_server,
)
from tools import isolated_sqlite_mcp as implementation


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "arm.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE positions (ticker TEXT, price REAL, scenario TEXT)")
        conn.execute("INSERT INTO positions VALUES (?,?,?)", ("SYNTHETIC", 100, '{"sector":"test"}'))
        conn.execute("CREATE TABLE numbers (value INTEGER)")
        conn.executemany("INSERT INTO numbers VALUES (?)", [(i,) for i in range(1000)])
    return path


def test_tools_advertise_exact_three_read_contracts():
    tools = read_tools()
    assert [t.name for t in tools] == ["list_tables", "describe_table", "read_query"]
    assert tools[0].inputSchema["properties"] == {}
    assert tools[1].inputSchema["required"] == ["table_name"]
    assert tools[2].inputSchema["required"] == ["query"]


def test_real_read_results_and_live_committed_updates(database):
    db = ReadOnlyDatabase(database)
    assert {"name": "positions"} in db.list_tables()
    assert [c["name"] for c in db.describe_table("positions")] == ["ticker", "price", "scenario"]
    assert db.read_query("SELECT ticker, json_extract(scenario, '$.sector') AS sector FROM positions") == [
        {"ticker": "SYNTHETIC", "sector": "test"}]
    with sqlite3.connect(database) as writer:
        writer.execute("UPDATE positions SET price=110")
    assert db.read_query("SELECT price FROM positions") == [{"price": 110.0}]


@pytest.mark.parametrize("query", [
    "INSERT INTO positions VALUES ('x',1,'{}')",
    "UPDATE positions SET price=0", "DELETE FROM positions", "DROP TABLE positions",
    "CREATE TABLE bad (value TEXT)", "PRAGMA query_only=OFF",
    "ATTACH DATABASE ':memory:' AS other", "DETACH DATABASE main",
    "SELECT load_extension('/SECRET_EXTENSION_PATH')",
    "SELECT writefile('/SECRET_PATH', 'data')",
    "SELECT readfile('/SECRET_PATH')",
    "SELECT * FROM pragma_writable_schema(1)",
    "WITH data AS (SELECT 1) SELECT * FROM data",
])
def test_forbidden_sql_cannot_change_database_or_echo_query(database, query):
    before = database.read_bytes()
    db = ReadOnlyDatabase(database)
    with pytest.raises(ReadOnlyError) as error:
        db.read_query(query)
    assert "SECRET" not in str(error.value)
    assert query not in str(error.value)
    assert database.read_bytes() == before


@pytest.mark.parametrize("query", [
    "UPDATE positions SET price=0", "PRAGMA query_only=OFF",
    "ATTACH DATABASE ':memory:' AS other", "SELECT load_extension('bad')",
])
def test_authorizer_blocks_even_when_select_prefix_guard_bypassed(database, query):
    db = ReadOnlyDatabase(database)
    with pytest.raises(ReadOnlyError):
        db._execute(query)
    assert db.read_query("SELECT price FROM positions") == [{"price": 100.0}]


def test_uri_readonly_survives_removing_additional_connection_guards(database):
    db = ReadOnlyDatabase(database)
    conn = db._connect()
    try:
        conn.set_authorizer(None)
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        conn.execute("PRAGMA query_only=OFF")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("UPDATE positions SET price=0")
    finally:
        conn.close()


def test_missing_path_never_creates_directory_or_database(tmp_path):
    path = tmp_path / "missing" / "missing.sqlite"
    with pytest.raises(ReadOnlyError, match="INVALID_DATABASE"):
        ReadOnlyDatabase(path)
    assert not path.parent.exists()


def test_symlink_database_is_rejected(database, tmp_path):
    link = tmp_path / "link.sqlite"
    link.symlink_to(database)
    with pytest.raises(ReadOnlyError, match="INVALID_DATABASE"):
        ReadOnlyDatabase(link)


def test_table_identifier_cannot_inject_sql_or_echo_paths(database):
    with pytest.raises(ReadOnlyError, match="UNKNOWN_TABLE"):
        ReadOnlyDatabase(database).describe_table('positions); ATTACH "/SECRET_PATH" AS extra; --')
    assert ReadOnlyDatabase(database).read_query("SELECT COUNT(*) AS count FROM positions") == [{"count": 1}]


def test_row_result_sql_and_time_limits_are_explicit(database):
    db = ReadOnlyDatabase(database, max_rows=2)
    with pytest.raises(ReadOnlyError, match="ROW_LIMIT"):
        db.read_query("SELECT value FROM numbers")
    assert len(db.read_query("SELECT value FROM numbers LIMIT 2")) == 2
    tiny = ReadOnlyDatabase(database, max_result_bytes=16)
    with pytest.raises(ReadOnlyError, match="RESULT_LIMIT"):
        tiny.read_query("SELECT ticker FROM positions")
    with pytest.raises(ReadOnlyError, match="INVALID_QUERY"):
        db.read_query("SELECT '" + "x" * 20000 + "'")
    timed = ReadOnlyDatabase(database, query_timeout=0.001)
    with pytest.raises(ReadOnlyError, match="QUERY_TIMEOUT"):
        timed.read_query("SELECT sum(a.value*b.value*c.value) FROM numbers a, numbers b, numbers c")


def test_dangerous_allocation_function_denied(database):
    with pytest.raises(ReadOnlyError, match="QUERY_REJECTED"):
        ReadOnlyDatabase(database).read_query("SELECT randomblob(1000000000)")


@pytest.mark.asyncio
async def test_dispatch_preserves_text_row_format_and_fixed_errors(database):
    db = ReadOnlyDatabase(database)
    result = await call_read_tool(db, "read_query", {"query": "SELECT price FROM positions"})
    assert not result.isError
    assert ast.literal_eval(result.content[0].text) == [{"price": 100.0}]
    for name, args in [
        ("write_query", {"query": "DELETE FROM positions"}),
        ("create_table", {"query": "CREATE TABLE bad(x)"}),
        ("read_query", {"query": "SELECT * FROM SECRET_MISSING_TABLE"}),
        ("read_query", {"query": "SELECT 1", "path": "/SECRET_PATH"}),
    ]:
        error = await call_read_tool(db, name, args)
        assert error.isError
        assert "SECRET" not in error.content[0].text
        assert error.content[0].text.startswith("Error: ")


def test_count_optimization_only_allows_verified_main_tables():
    assert read_authorizer(sqlite3.SQLITE_READ, "positions", "", None, None) == sqlite3.SQLITE_DENY
    assert read_authorizer(sqlite3.SQLITE_READ, "positions", "", None, None,
                           main_tables={"positions"}) == sqlite3.SQLITE_OK
    assert read_authorizer(sqlite3.SQLITE_READ, "positions", "", "temp", None,
                           main_tables={"positions"}) == sqlite3.SQLITE_DENY


def test_vacuum_into_and_attach_cannot_create_external_files(database, tmp_path):
    db = ReadOnlyDatabase(database)
    for command in ("VACUUM INTO", "ATTACH DATABASE"):
        target = tmp_path / ("forbidden-" + command.split()[0] + ".sqlite")
        query = command + " '" + str(target) + "'" + (" AS other" if command.startswith("ATTACH") else "")
        with pytest.raises(ReadOnlyError):
            db._execute(query)
        assert not target.exists()


@pytest.mark.asyncio
async def test_actual_sdk_handlers_advertise_only_read_tools_and_fixed_error(database):
    server = create_server(ReadOnlyDatabase(database))
    advertised = await server.request_handlers[types.ListToolsRequest](types.ListToolsRequest(method="tools/list"))
    assert [t.name for t in advertised.root.tools] == ["list_tables", "describe_table", "read_query"]
    capabilities = server.create_initialization_options().capabilities
    assert capabilities.prompts is None and capabilities.resources is None
    response = await server.request_handlers[types.CallToolRequest](types.CallToolRequest(
        method="tools/call", params=types.CallToolRequestParams(name="read_query",
            arguments={"query": "SELECT * FROM SECRET_MISSING_TABLE"})))
    assert response.root.isError
    assert response.root.content[0].text == "Error: QUERY_REJECTED"


@pytest.mark.parametrize("args", [
    ["--db-path", "/SECRET_MISSING_DIRECTORY/SECRET_DATABASE.sqlite"],
    ["--unrecognized-secret", "/SECRET_PATH"],
])
def test_cli_errors_never_echo_paths_or_arguments(args, tmp_path):
    script = Path(__file__).resolve().parents[1] / "tools/isolated_sqlite_mcp.py"
    result = subprocess.run([sys.executable, str(script), *args], cwd=tmp_path,
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip() == "Error: READ_ONLY_SERVICE_UNAVAILABLE"


@pytest.mark.parametrize("proof", ["disabled", "unverified_error", "unexpected_success"])
def test_missing_extension_setter_requires_actual_disabled_sql_proof(database, monkeypatch, proof):
    original_connect = sqlite3.connect
    probes = []
    class WithoutSetter:
        def __init__(self, conn):
            object.__setattr__(self, "conn", conn)
        def __getattr__(self, name):
            if name == "enable_load_extension":
                raise AttributeError(name)
            return getattr(self.conn, name)
        def __setattr__(self, name, value):
            setattr(self.conn, name, value)
        def execute(self, query, parameters=()):
            if query == "SELECT load_extension(?)":
                probes.append(parameters[0])
                if proof == "unverified_error":
                    raise sqlite3.OperationalError("SECRET_UNVERIFIED_EXTENSION_ERROR")
                if proof == "unexpected_success":
                    return self.conn.execute("SELECT 1")
            return self.conn.execute(query, parameters)
    monkeypatch.setattr(implementation.sqlite3, "connect",
                        lambda *args, **kwargs: WithoutSetter(original_connect(*args, **kwargs)))
    db = ReadOnlyDatabase(database)
    if proof == "disabled":
        assert db.read_query("SELECT COUNT(*) AS count FROM positions") == [{"count": 1}]
        with pytest.raises(ReadOnlyError):
            db.read_query("SELECT load_extension('unsafe')")
        with pytest.raises(ReadOnlyError):
            db._execute("UPDATE positions SET price=0")
    else:
        with pytest.raises(ReadOnlyError, match="UNSUPPORTED_SQLITE_RUNTIME") as error:
            db.read_query("SELECT 1")
        assert "SECRET" not in str(error.value)
    assert probes and all(Path(p).is_absolute() and not Path(p).exists() for p in probes)
