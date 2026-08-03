"""Path heuristics for the MCP diagnostic.

The point of the tool is that its output can be diffed across hosts, so a
false positive is as harmful as a missed failure — the first version flagged a
URL, an npm package spec, and a correctly-resolved sqlite path as missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.mcp_doctor import _base_dir, _check_env, _looks_like_path


@pytest.mark.parametrize(
    "arg",
    [
        "perplexity-ask/dist/index.js",
        "/abs/path/to/index.js",
        "../stock_tracking_db.sqlite",
        "./local.py",
        "config.yaml",
    ],
)
def test_filesystem_paths_are_recognized(arg):
    assert _looks_like_path(arg) is True


@pytest.mark.parametrize(
    "arg",
    [
        "http://localhost:8000/sse",
        "https://example.test/thing",
        "@mzxrai/mcp-webresearch@latest",
        "firecrawl-mcp",
        "mcp-server-sqlite",
        "-y",
        "--directory",
        "run",
    ],
)
def test_urls_packages_and_flags_are_not_paths(arg):
    assert _looks_like_path(arg) is False


def test_directory_flag_moves_the_resolution_base():
    """`uv --directory sqlite run ...` makes ../db resolve from sqlite/."""

    root = Path("/repo")
    args = ("--directory", "sqlite", "run", "mcp-server-sqlite", "--db-path", "../x.sqlite")

    assert _base_dir(args, root) == root / "sqlite"


def test_without_a_directory_flag_the_base_is_the_repo_root():
    root = Path("/repo")

    assert _base_dir(("-y", "firecrawl-mcp"), root) == root


class TestEnvReporting:
    """Values must never appear — two were leaked into a chat log this way."""

    def test_env_reference_reports_name_and_whether_it_is_set(self, monkeypatch):
        monkeypatch.setenv("SOME_KEY", "super-secret-value")

        [entry] = _check_env({"SOME_KEY": "${SOME_KEY}"})

        assert entry == {"key": "SOME_KEY", "source": "${SOME_KEY}", "set": True}

    def test_unset_reference_is_reported_as_unset(self, monkeypatch):
        monkeypatch.delenv("ABSENT_KEY", raising=False)

        [entry] = _check_env({"ABSENT_KEY": "${ABSENT_KEY}"})

        assert entry["set"] is False

    def test_inline_literals_are_flagged_without_exposing_them(self):
        [entry] = _check_env({"PERPLEXITY_API_KEY": "pplx-realsecretvalue"})

        assert entry["source"] == "inline"
        assert entry["set"] is True
        assert "realsecret" not in repr(entry)

    def test_blank_inline_value_counts_as_unset(self):
        [entry] = _check_env({"EMPTY": "   "})

        assert entry["set"] is False

    def test_raw_yaml_reveals_a_reference_the_loader_already_substituted(
        self, monkeypatch
    ):
        """The loader interpolates before the registry exists.

        Without the raw block, a `${VAR}` reference and a hardcoded credential
        are indistinguishable — and telling them apart is the entire point of
        migrating secrets out of the config file.
        """

        monkeypatch.setenv("PERPLEXITY_API_KEY", "resolved-secret")
        interpolated = {"PERPLEXITY_API_KEY": "resolved-secret"}
        raw = {"PERPLEXITY_API_KEY": "${PERPLEXITY_API_KEY}"}

        [entry] = _check_env(interpolated, raw)

        assert entry["source"] == "${PERPLEXITY_API_KEY}"
        assert entry["set"] is True
        assert "resolved-secret" not in repr(entry)

    def test_a_hardcoded_credential_is_still_reported_as_inline(self):
        interpolated = {"PERPLEXITY_API_KEY": "pplx-hardcoded"}
        raw = {"PERPLEXITY_API_KEY": "pplx-hardcoded"}

        [entry] = _check_env(interpolated, raw)

        assert entry["source"] == "inline"
