"""Research-only SQLite MCP with three read contracts and connection-level denial.

The parent must bind the intended arm DB and enforce its model namespace/mount
boundary. This service is NOT proof of whole-process or whole-OS isolation.
It opens fresh read-only connections so committed agent-side changes remain
visible; it never creates a database or substitutes a frozen portfolio.
"""
import argparse
import asyncio
from contextlib import closing
import math
from pathlib import Path
import sqlite3
import sys
import time
import uuid

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

MAX_SQL_BYTES = 16384
MAX_CELL_OR_ROW_BYTES = 65536
PURE_FUNCTIONS = frozenset({
    "abs", "round", "coalesce", "ifnull", "nullif", "length", "lower", "upper",
    "trim", "ltrim", "rtrim", "substr", "substring", "replace", "instr",
    "like", "glob", "typeof", "date", "datetime", "julianday", "strftime",
    "unixepoch", "timediff", "sum", "total", "avg", "count", "min", "max",
    "group_concat", "json_extract", "json_valid", "json_type",
    "json_array_length", "->", "->>", "sqlite_version",
})
ERROR_CODES = frozenset({
    "INVALID_DATABASE", "INVALID_ARGUMENTS", "INVALID_QUERY", "QUERY_REJECTED",
    "QUERY_TIMEOUT", "ROW_LIMIT", "RESULT_LIMIT", "UNKNOWN_TABLE", "UNKNOWN_TOOL",
    "UNSUPPORTED_SQLITE_RUNTIME",
})


class ReadOnlyError(ValueError):
    def __init__(self, code):
        self.code = code if code in ERROR_CODES else "QUERY_REJECTED"
        super().__init__(self.code)


def read_authorizer(action, first, second, database, _trigger, *, main_tables=frozenset()):
    """Default deny, including ATTACH, writes, transaction control and functions."""
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and database == "main":
        return sqlite3.SQLITE_OK
    # SQLite's COUNT(*) optimization sometimes omits the database name. Only
    # allow that form for a table verified on this fresh main connection.
    if action == sqlite3.SQLITE_READ and database is None and second == "" and first in main_tables:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and (second or first or "").lower() in PURE_FUNCTIONS:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and first == "table_info":
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _disable_extensions(conn):
    setter = getattr(conn, "enable_load_extension", None)
    if callable(setter):
        setter(False)
        return
    # Some Python builds omit the setter while SQLite still exposes the SQL
    # function. Verify its fresh-connection disabled state BEFORE installing
    # our authorizer. Only the exact authorization denial is accepted.
    probe = Path("/") / (".prism-extension-probe-" + uuid.uuid4().hex + "-absent")
    if probe.exists():
        raise ReadOnlyError("UNSUPPORTED_SQLITE_RUNTIME")
    try:
        conn.execute("SELECT load_extension(?)", (str(probe),))
    except sqlite3.Error as error:
        if str(error).strip().lower() == "not authorized":
            return
    raise ReadOnlyError("UNSUPPORTED_SQLITE_RUNTIME")


class ReadOnlyDatabase:
    def __init__(self, db_path, *, max_rows=200, max_result_bytes=131072, query_timeout=2.0):
        try:
            path = Path(db_path)
            if not path.is_absolute() or path.is_symlink() or not path.is_file():
                raise ValueError()
            self.path = path.resolve(strict=True)
        except (ValueError, TypeError, OSError):
            raise ReadOnlyError("INVALID_DATABASE") from None
        if (type(max_rows) is not int or not 1 <= max_rows <= 1000
                or type(max_result_bytes) is not int or not 1 <= max_result_bytes <= 1048576
                or isinstance(query_timeout, bool) or not isinstance(query_timeout, (int, float))
                or not math.isfinite(query_timeout) or not 0 < query_timeout <= 5):
            raise ReadOnlyError("INVALID_ARGUMENTS")
        self.max_rows, self.max_result_bytes, self.query_timeout = max_rows, max_result_bytes, query_timeout

    def _connect(self, progress=None):
        try:
            conn = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=min(0.25, self.query_timeout))
        except sqlite3.Error:
            raise ReadOnlyError("INVALID_DATABASE") from None
        try:
            conn.row_factory = sqlite3.Row
            if progress is not None:
                conn.set_progress_handler(progress, 1000)
            _disable_extensions(conn)
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_CELL_OR_ROW_BYTES)
            conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_BYTES)
            conn.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 128)
            conn.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 100)
            conn.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 16)
            conn.setlimit(sqlite3.SQLITE_LIMIT_VDBE_OP, 50000)
            conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
            conn.execute("PRAGMA query_only=ON")
            if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
                raise ReadOnlyError("UNSUPPORTED_SQLITE_RUNTIME")
            names = conn.execute("SELECT name FROM main.sqlite_master WHERE type IN ('table','view')").fetchmany(1001)
            if len(names) > 1000:
                raise ReadOnlyError("UNSUPPORTED_SQLITE_RUNTIME")
            main_tables = frozenset(row[0] for row in names) | {"sqlite_master", "sqlite_schema"}
            conn.set_authorizer(lambda *args: read_authorizer(*args, main_tables=main_tables))
            return conn
        except Exception:
            conn.close()
            raise ReadOnlyError("UNSUPPORTED_SQLITE_RUNTIME") from None

    def _execute(self, query, parameters=()):
        deadline = time.monotonic() + self.query_timeout
        timed_out = False
        def progress():
            nonlocal timed_out
            timed_out = time.monotonic() >= deadline
            return 1 if timed_out else 0
        try:
            with closing(self._connect(progress)) as conn:
                with closing(conn.cursor()) as cursor:
                    cursor.execute(query, parameters)
                    records = cursor.fetchmany(self.max_rows + 1)
                    if len(records) > self.max_rows:
                        raise ReadOnlyError("ROW_LIMIT")
                    result = [dict(row) for row in records]
                    if len(str(result).encode("utf-8")) > self.max_result_bytes:
                        raise ReadOnlyError("RESULT_LIMIT")
                    if time.monotonic() >= deadline:
                        raise ReadOnlyError("QUERY_TIMEOUT")
                    return result
        except ReadOnlyError:
            if timed_out:
                raise ReadOnlyError("QUERY_TIMEOUT") from None
            raise
        except (sqlite3.Error, MemoryError, OverflowError, UnicodeError):
            raise ReadOnlyError("QUERY_TIMEOUT" if timed_out or time.monotonic() >= deadline else "QUERY_REJECTED") from None

    def list_tables(self):
        return self._execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")

    def describe_table(self, table_name):
        try:
            valid = isinstance(table_name, str) and bool(table_name) and len(table_name.encode("utf-8")) <= 256
        except UnicodeError:
            valid = False
        if not valid:
            raise ReadOnlyError("UNKNOWN_TABLE")
        existing = self._execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        if not existing:
            raise ReadOnlyError("UNKNOWN_TABLE")
        escaped = table_name.replace('"', '""')
        return self._execute('PRAGMA table_info("' + escaped + '")')

    def read_query(self, query):
        try:
            valid = isinstance(query, str) and 0 < len(query.encode("utf-8")) <= MAX_SQL_BYTES
        except UnicodeError:
            valid = False
        # Compatibility syntax check only; the connection/authorizer enforce safety.
        if not valid or not query.strip().upper().startswith("SELECT"):
            raise ReadOnlyError("INVALID_QUERY")
        return self._execute(query)


def read_tools():
    return [
        types.Tool(name="list_tables", description="List tables in the bound read-only database.",
                   inputSchema={"type": "object", "properties": {}, "additionalProperties": False}),
        types.Tool(name="describe_table", description="Describe columns of an existing table.",
                   inputSchema={"type": "object", "properties": {"table_name": {"type": "string"}},
                                "required": ["table_name"], "additionalProperties": False}),
        types.Tool(name="read_query", description="Run a bounded read-only SELECT. Narrow results with LIMIT; excess rows are rejected, not truncated.",
                   inputSchema={"type": "object", "properties": {"query": {"type": "string"}},
                                "required": ["query"], "additionalProperties": False}),
    ]


async def call_read_tool(database, name, arguments):
    try:
        if type(arguments) is not dict:
            raise ReadOnlyError("INVALID_ARGUMENTS")
        if name == "list_tables":
            if arguments:
                raise ReadOnlyError("INVALID_ARGUMENTS")
            result = await asyncio.to_thread(database.list_tables)
        elif name == "describe_table":
            if set(arguments) != {"table_name"}:
                raise ReadOnlyError("INVALID_ARGUMENTS")
            result = await asyncio.to_thread(database.describe_table, arguments["table_name"])
        elif name == "read_query":
            if set(arguments) != {"query"}:
                raise ReadOnlyError("INVALID_ARGUMENTS")
            result = await asyncio.to_thread(database.read_query, arguments["query"])
        else:
            raise ReadOnlyError("UNKNOWN_TOOL")
        return types.CallToolResult(content=[types.TextContent(type="text", text=str(result))], isError=False)
    except ReadOnlyError as error:
        return types.CallToolResult(content=[types.TextContent(type="text", text="Error: " + error.code)], isError=True)
    except Exception:
        return types.CallToolResult(content=[types.TextContent(type="text", text="Error: QUERY_REJECTED")], isError=True)


def create_server(database):
    server = Server("isolated-sqlite-readonly")
    lock = asyncio.Semaphore(1)
    @server.list_tools()
    async def list_tools():
        return read_tools()
    # Validate ourselves so SDK schema errors cannot echo raw arguments.
    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        async with lock:
            return await call_read_tool(database, name, arguments if arguments is not None else {})
    return server


async def serve(db_path):
    database = ReadOnlyDatabase(db_path)
    server = create_server(database)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        raise ReadOnlyError("INVALID_ARGUMENTS")


def main():
    try:
        parser = _SafeArgumentParser(description=__doc__)
        parser.add_argument("--db-path", required=True)
        args = parser.parse_args()
        asyncio.run(serve(args.db_path))
    except Exception:
        print("Error: READ_ONLY_SERVICE_UNAVAILABLE", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
