"""Opt-in agent initialization seams, NOT a runner or MCP capability audit.

Factories supply explicit plain mappings. Init-only settings ignore environment
and dotenv sources, including unused nested provider factories. The reviewed
profile/namespace must still enforce read-only tools and provider bridge policy.
The private root marker has schema_version=1, purpose=PRISM_AGENT_SHADOW,
market=KR/US and runtime_id; existing unmarked databases are never adopted.
Execution entrypoints remain blocked until a reviewed no-order adapter exists.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from urllib.parse import urlsplit

from mcp_agent.config import (
    Settings, OpenAISettings, MCPSettings, LoggerSettings,
    OpenTelemetrySettings, UsageTelemetrySettings,
)

ROOT_MARKER = ".prism-agent-isolation.json"
_ACCOUNT_FIELDS = {"name", "account_key", "market", "virtual"}
_SETTINGS_FIELDS = {
    "name", "description", "mcp", "execution_engine", "temporal", "anthropic", "bedrock",
    "cohere", "openai", "lm_studio", "workflow_task_modules", "workflow_task_retry_policies",
    "azure", "google", "otel", "logger", "usage_telemetry", "agents", "authorization", "oauth", "env",
}
_OPENAI_FIELDS = {"api_key", "reasoning_effort", "base_url", "user", "default_headers", "default_model"}


class _InitOnly:
    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings,
                                   dotenv_settings, file_secret_settings):
        return (init_settings,)


class _ExplicitOpenAISettings(_InitOnly, OpenAISettings):
    model_config = {**OpenAISettings.model_config, "env_file": None, "secrets_dir": None}


class _ExplicitSettings(_InitOnly, Settings):
    model_config = {**Settings.model_config, "env_file": None, "secrets_dir": None}
    # BaseSettings may turn nested model inputs back into dictionaries. Keep
    # revalidation on the init-only subtype, never ambient OpenAISettings.
    openai: _ExplicitOpenAISettings | None = None


def validated_mcp_settings(factory):
    """Type-validate explicit inputs; this is NOT proof of capability safety."""
    try:
        if not callable(factory):
            raise ValueError()
        config = factory()
        if type(config) is not dict or set(config) != {"openai", "mcp"}:
            raise ValueError()
        provider, mcp = config["openai"], config["mcp"]
        if type(provider) is not dict or not {"base_url", "api_key"} <= provider.keys():
            raise ValueError()
        if set(OpenAISettings.model_fields) != _OPENAI_FIELDS or not set(provider) <= _OPENAI_FIELDS:
            raise ValueError()
        if any(not isinstance(provider[key], str) or not provider[key].strip() for key in ("base_url", "api_key")):
            raise ValueError()
        url = urlsplit(provider["base_url"])
        if url.scheme not in {"http", "https"} or not url.netloc:
            raise ValueError()
        if type(mcp) is not dict or set(mcp) != {"servers"} or type(mcp["servers"]) is not dict:
            raise ValueError()
        if set(Settings.model_fields) != _SETTINGS_FIELDS:
            raise ValueError()
        # Explicit None suppresses nested BaseSettings default factories.
        return _ExplicitSettings(
            _env_file=None, openai=_ExplicitOpenAISettings(_env_file=None, **deepcopy(provider)),
            mcp=MCPSettings(**deepcopy(mcp)), execution_engine="asyncio",
            anthropic=None, bedrock=None, cohere=None, lm_studio=None, azure=None, google=None,
            temporal=None, agents=None, authorization=None, oauth=None,
            logger=LoggerSettings(type="none", transports=["none"], progress_display=False),
            otel=OpenTelemetrySettings(enabled=False, exporters=[]),
            usage_telemetry=UsageTelemetrySettings(enabled=False, enable_detailed_telemetry=False),
            workflow_task_modules=[], workflow_task_retry_policies={}, env=[],
        )
    except Exception:
        raise ValueError("Explicit isolated MCP settings mapping is invalid") from None


def configured_mcp_app(app_class, name, factory):
    """Version-sensitive SDK seam: explicit config must not load ambient dotenv."""
    instance = app_class(name=name, settings=validated_mcp_settings(factory))
    if not hasattr(instance, "_dotenv_loaded") or type(instance._dotenv_loaded) is not bool:
        raise ValueError("Unsupported MCPApp dotenv compatibility contract")
    # Per-instance SDK bypass, not global monkeypatching or environment mutation.
    instance._dotenv_loaded = True
    return instance


async def attach_isolated_llm(agent, llm_class, context):
    """Bind both Agent and LLM directly; never discover a global context."""
    if context is None:
        raise ValueError("Explicit isolated MCP context is not active")
    agent.context = context
    return await agent.attach_llm(lambda **kwargs: llm_class(context=context, **kwargs))


def require_execution_runtime(agent):
    if getattr(agent, "_isolated_runtime", None) is not None:
        raise RuntimeError("Virtual execution requires a reviewed no-order adapter")


def _validated_account(record, market=None):
    if (type(record) is not dict or set(record) != _ACCOUNT_FIELDS or record["virtual"] is not True
            or record["market"] not in {"kr", "us"} or (market and record["market"] != market.lower())
            or not isinstance(record["name"], str) or not record["name"].startswith("SHADOW ")
            or not re.fullmatch(r"[A-Za-z0-9가-힣 ._-]{1,64}", record["name"])
            or not isinstance(record["account_key"], str)
            or not re.fullmatch(r"virtual:[A-Za-z0-9._:-]{1,96}", record["account_key"])):
        raise ValueError("Invalid explicit virtual account record")
    return dict(record)


def virtual_account_label(account):
    return _validated_account(account)["name"] + " (가상 계정)"


def _private(path, *, directory=False):
    info = path.lstat()
    expected = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (not expected or info.st_mode & 0o077 or info.st_uid != os.getuid()
            or (not directory and info.st_nlink != 1)):
        raise ValueError("Isolation path must be private, owned, and unlinked")


def _paths(db_path, root_path, market):
    root = Path(root_path)
    path = Path(db_path)
    if os.name != "posix" or not root.is_absolute() or not path.is_absolute():
        raise ValueError("Isolation requires explicit absolute POSIX paths")
    try:
        _private(root, directory=True)
        root = root.resolve()
        if root.is_relative_to(Path(__file__).resolve().parents[1]):
            raise ValueError("Isolation root cannot be inside source repository")
        if not path.resolve().is_relative_to(root) or path.resolve() == root:
            raise ValueError("Database escapes isolation root")
        if path.suffix not in {".sqlite", ".sqlite3", ".db"}:
            raise ValueError("Isolated database filename required")
        current = path
        while True:
            if current.is_symlink():
                raise ValueError("Symlink in isolated database path")
            if current.resolve() == root:
                break
            if current.parent == current:
                raise ValueError("Database path cannot reach isolation root")
            current = current.parent
        if not path.parent.is_dir():
            raise ValueError("Database parent directory missing")
        marker = root / ROOT_MARKER
        _private(marker)
        if marker.stat().st_size > 4096:
            raise ValueError("Invalid isolation root marker")
        data = json.loads(marker.read_text())
        if (set(data) != {"schema_version", "purpose", "market", "runtime_id"}
                or type(data["schema_version"]) is not int or data["schema_version"] != 1
                or data["purpose"] != "PRISM_AGENT_SHADOW"
                or data["market"] != market or not isinstance(data["runtime_id"], str)
                or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", data["runtime_id"])):
            raise ValueError("Invalid isolation root marker")
        if path.exists():
            _private(path)
        return str(path.resolve()), root, data
    except (OSError, TypeError, KeyError, json.JSONDecodeError):
        raise ValueError("Private marked isolation root required") from None


@dataclass(frozen=True)
class IsolatedAgentRuntime:
    db_path: str
    root: Path
    market: str
    root_marker: dict
    _accounts: tuple

    def accounts(self):
        return deepcopy(list(self._accounts))

    def connect(self):
        path, _, marker = _paths(self.db_path, self.root, self.market)
        if marker != self.root_marker:
            raise ValueError("Isolation root identity changed")
        payload = json.dumps({"schema_version": 1, "root": marker, "accounts": self.accounts()},
                             sort_keys=True, separators=(",", ":"))
        marker_hash = hashlib.sha256(payload.encode()).hexdigest()
        if Path(path).exists():
            try:
                check = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)
                try:
                    row = check.execute("SELECT owner_hash FROM prism_virtual_runtime WHERE id=1").fetchone()
                finally:
                    check.close()
                if row != (marker_hash,):
                    raise ValueError("Foreign isolated database")
            except sqlite3.Error:
                raise ValueError("Existing unmarked database cannot be adopted") from None
        else:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.close(descriptor)
            initial = sqlite3.connect(path)
            try:
                initial.execute("CREATE TABLE prism_virtual_runtime (id INTEGER PRIMARY KEY CHECK(id=1), owner_hash TEXT NOT NULL)")
                initial.execute("INSERT INTO prism_virtual_runtime VALUES (1,?)", (marker_hash,))
                initial.commit()
            finally:
                initial.close()
        return sqlite3.connect(path)


def prepare_isolated_runtime(db_path, virtual_accounts, isolated_db_root, mcp_settings_factory, market):
    if mcp_settings_factory is not None and not callable(mcp_settings_factory):
        raise ValueError("MCP settings factory must be callable")
    if virtual_accounts is None:
        if isolated_db_root is not None or mcp_settings_factory is not None:
            raise ValueError("Virtual accounts, isolated root and MCP settings factory must be supplied together")
        return None
    if isolated_db_root is None or mcp_settings_factory is None:
        raise ValueError("Virtual accounts require isolated root and explicit MCP settings factory")
    if not isinstance(virtual_accounts, (list, tuple)) or not 1 <= len(virtual_accounts) <= 10:
        raise ValueError("One to ten explicit virtual accounts required")
    accounts = tuple(_validated_account(record, market) for record in virtual_accounts)
    if len({a["account_key"] for a in accounts}) != len(accounts) or len({a["name"] for a in accounts}) != len(accounts):
        raise ValueError("Duplicate virtual account identity")
    path, root, marker = _paths(db_path, isolated_db_root, market)
    return IsolatedAgentRuntime(path, root, market, marker, accounts)
