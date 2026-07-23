#!/usr/bin/env python3
"""Audit PRISM broker imports and direct order-call boundaries with Python AST.

The audit is intentionally static and side-effect free: target files are parsed as
text and are never imported. Existing legacy trading surfaces are inventoried,
while any new Phase 1 broker dependency or production direct order call fails.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


BROKER_MODULES = frozenset(
    {
        "trading.domestic_stock_trading",
        "trading.us_stock_trading",
        "us_stock_trading",
        "prism_core.execution_service",
    }
)
DIRECT_ORDER_METHODS = frozenset({"async_buy_stock", "async_sell_stock"})

# Existing unsafe examples/tests are retained only as an explicit migration
# inventory. Adding another path requires a reviewed source change here.
LEGACY_DANGEROUS_FILES = frozenset(
    {
        "examples/messaging/gcp_pubsub_subscriber_example.py",
        "examples/messaging/redis_subscriber_example.py",
        "tests/quick_test.py",
        "tests/test_async_trading.py",
    }
)
LEGACY_PHASE2_BOUNDARY_FILES = frozenset({"prism_core/execution_service.py"})
LEGACY_BROKER_IMPORT_FILES = frozenset(
    {
        "cores/archive/data_enricher.py",
        "cores/corporate_status.py",
        "examples/generate_dashboard_json.py",
        "prism-us/us_pending_order_batch.py",
        "prism-us/us_stock_tracking_agent.py",
        "stock_tracking_agent.py",
        "stock_tracking_enhanced_agent.py",
        "tools/check_kr_pending_readiness.py",
        "tools/fill_chaser.py",
        "tools/hardstop_seller.py",
        "tools/trend_exit_seller.py",
        "tracking/helpers.py",
    }
)
LEGACY_BROKER_ADAPTER_FILES = frozenset(
    {
        "prism-us/trading/us_stock_trading.py",
        "trading/domestic_stock_trading.py",
        "trading/portfolio_telegram_reporter.py",
    }
)
APPROVED_PHASE2_ADAPTER_FILES: frozenset[str] = frozenset()
TEST_BROKER_REFERENCE_FILES = frozenset(
    {
        "prism-us/tests/quick_test_us.py",
        "prism-us/tests/test_fill_chaser_us.py",
        "prism-us/tests/test_multi_account_us.py",
        "prism-us/tests/test_phase6_trading.py",
        "prism-us/tests/test_trend_exit_seller_us.py",
        "prism-us/tests/test_us_account_total_asset.py",
        "prism-us/tests/test_us_stock_tracking_agent_process_reports.py",
        "tests/test_domestic_trading_time_windows.py",
        "tests/test_execution_service.py",
        "tests/test_fill_chaser.py",
        "tests/test_hardstop_seller.py",
        "tests/test_kr_pending_entry.py",
        "tests/test_kr_pending_exit.py",
        "tests/test_multi_account_domestic.py",
        "tests/test_order_intents.py",
        "tests/test_parallel_trading_batch.py",
        "tests/test_positions.py",
        "tests/test_prism_core_time_windows.py",
        "tests/test_stock_tracking_agent_process_reports.py",
        "tests/test_trend_exit_seller.py",
    }
)
SKIPPED_DIRECTORY_NAMES = frozenset(
    {".git", ".venv", ".venv-bt", "venv", "env", "node_modules", "__pycache__"}
)


@dataclass(frozen=True, order=True)
class AuditIssue:
    path: str
    line: int
    code: str
    detail: str


@dataclass(frozen=True)
class BrokerBoundaryReport:
    phase1_import_violations: tuple[AuditIssue, ...]
    direct_call_violations: tuple[AuditIssue, ...]
    legacy_dangerous_inventory: tuple[AuditIssue, ...]
    legacy_import_inventory: tuple[AuditIssue, ...]
    test_broker_inventory: tuple[AuditIssue, ...]
    legacy_adapter_inventory: tuple[AuditIssue, ...]
    approved_phase2_inventory: tuple[AuditIssue, ...]
    parse_violations: tuple[AuditIssue, ...]

    @property
    def violations(self) -> tuple[AuditIssue, ...]:
        return tuple(
            sorted(
                self.phase1_import_violations
                + self.direct_call_violations
                + self.parse_violations
            )
        )


def _root_error_report(detail: str) -> BrokerBoundaryReport:
    return BrokerBoundaryReport(
        phase1_import_violations=(),
        direct_call_violations=(),
        legacy_dangerous_inventory=(),
        legacy_import_inventory=(),
        test_broker_inventory=(),
        legacy_adapter_inventory=(),
        approved_phase2_inventory=(),
        parse_violations=(AuditIssue(".", 1, "AUDIT_ROOT_ERROR", detail),),
    )


@dataclass(frozen=True)
class _Finding:
    path: str
    line: int
    kind: str
    detail: str


def _is_broker_module(module: str) -> bool:
    return module in BROKER_MODULES or any(
        module.endswith(f".{broker_module}") for broker_module in BROKER_MODULES
    )


def _constant_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _dynamic_import_target(
    node: ast.Call, dynamic_import_names: frozenset[str]
) -> str | None:
    if not node.args:
        return None
    is_import = (
        isinstance(node.func, ast.Name) and node.func.id in dynamic_import_names
    ) or (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in {"__import__", "import_module"}
    )
    if not is_import:
        return None
    target = _constant_string(node.args[0])
    return target if target and _is_broker_module(target) else None


def _direct_order_method(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Attribute):
        return node.func.attr if node.func.attr in DIRECT_ORDER_METHODS else None
    if isinstance(node.func, ast.Name):
        return node.func.id if node.func.id in DIRECT_ORDER_METHODS else None
    if not isinstance(node.func, ast.Call):
        return None
    getter = node.func
    if not (
        isinstance(getter.func, ast.Name)
        and getter.func.id == "getattr"
        and len(getter.args) >= 2
    ):
        return None
    method = _constant_string(getter.args[1])
    return method if method in DIRECT_ORDER_METHODS else None


def _iter_python_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*.py")):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in SKIPPED_DIRECTORY_NAMES for part in relative.parts):
            continue
        yield path


def _scan_file(root: Path, path: Path) -> tuple[list[_Finding], AuditIssue | None]:
    relative = path.relative_to(root).as_posix()
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative)
    except (OSError, UnicodeError, SyntaxError) as exc:
        line = getattr(exc, "lineno", None) or 1
        return [], AuditIssue(relative, line, "PARSE_ERROR", type(exc).__name__)

    findings: list[_Finding] = []
    dynamic_import_names = {"__import__", "import_module"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module not in {
            "builtins",
            "importlib",
        }:
            continue
        for alias in node.names:
            if alias.name in {"__import__", "import_module"}:
                dynamic_import_names.add(alias.asname or alias.name)
    frozen_dynamic_import_names = frozenset(dynamic_import_names)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_broker_module(alias.name):
                    findings.append(
                        _Finding(relative, node.lineno, "import", alias.name)
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported_modules = [module]
            imported_modules.extend(
                f"{module}.{alias.name}" if module else alias.name
                for alias in node.names
            )
            for imported_module in imported_modules:
                if _is_broker_module(imported_module):
                    findings.append(
                        _Finding(
                            relative,
                            node.lineno,
                            "import",
                            imported_module,
                        )
                    )
                    break
        elif isinstance(node, ast.Call):
            imported_module = _dynamic_import_target(
                node, frozen_dynamic_import_names
            )
            if imported_module is not None:
                findings.append(
                    _Finding(relative, node.lineno, "import", imported_module)
                )
            method = _direct_order_method(node)
            if method is not None:
                findings.append(
                    _Finding(relative, node.lineno, "direct_call", method)
                )
    return findings, None


def _is_test_inventory_path(path: str) -> bool:
    return path in TEST_BROKER_REFERENCE_FILES


def _is_legacy_adapter(path: str) -> bool:
    return path in LEGACY_BROKER_ADAPTER_FILES


def _is_approved_adapter(path: str) -> bool:
    return path in APPROVED_PHASE2_ADAPTER_FILES


def audit_repository(root: str | Path) -> BrokerBoundaryReport:
    """Return deterministic violations and explicit legacy/Phase 2 inventories."""

    root_path = Path(root).resolve()
    if not root_path.is_dir():
        return _root_error_report("audit root is not an existing directory")
    python_files = tuple(_iter_python_files(root_path))
    if not python_files:
        return _root_error_report("audit root contains no Python files")

    phase1_imports: list[AuditIssue] = []
    direct_calls: list[AuditIssue] = []
    legacy: list[AuditIssue] = []
    legacy_imports: list[AuditIssue] = []
    test_inventory: list[AuditIssue] = []
    legacy_adapters: list[AuditIssue] = []
    phase2: list[AuditIssue] = []
    parse_errors: list[AuditIssue] = []

    for path in python_files:
        findings, parse_error = _scan_file(root_path, path)
        if parse_error is not None:
            parse_errors.append(parse_error)
            continue

        for finding in findings:
            if finding.path in LEGACY_DANGEROUS_FILES:
                legacy.append(
                    AuditIssue(
                        finding.path,
                        finding.line,
                        "LEGACY_DANGEROUS",
                        finding.detail,
                    )
                )
                continue

            if _is_test_inventory_path(finding.path):
                test_inventory.append(
                    AuditIssue(
                        finding.path,
                        finding.line,
                        "TEST_BROKER_REFERENCE",
                        finding.detail,
                    )
                )
                continue

            if _is_legacy_adapter(finding.path):
                legacy_adapters.append(
                    AuditIssue(
                        finding.path,
                        finding.line,
                        "LEGACY_ADAPTER_REFERENCE",
                        finding.detail,
                    )
                )
                continue

            if _is_approved_adapter(finding.path):
                phase2.append(
                    AuditIssue(
                        finding.path,
                        finding.line,
                        "APPROVED_PHASE2_ADAPTER_REFERENCE",
                        finding.detail,
                    )
                )
                continue

            if finding.kind == "import" and (
                finding.path in LEGACY_BROKER_IMPORT_FILES
                or finding.path in LEGACY_PHASE2_BOUNDARY_FILES
            ):
                legacy_imports.append(
                    AuditIssue(
                        finding.path,
                        finding.line,
                        "LEGACY_BROKER_IMPORT",
                        finding.detail,
                    )
                )
                continue

            if finding.kind == "import":
                phase1_imports.append(
                    AuditIssue(
                        finding.path,
                        finding.line,
                        "PHASE1_BROKER_IMPORT",
                        finding.detail,
                    )
                )
                continue

            if finding.kind == "direct_call":
                direct_calls.append(
                    AuditIssue(
                        finding.path,
                        finding.line,
                        "DIRECT_BROKER_CALL",
                        finding.detail,
                    )
                )

    return BrokerBoundaryReport(
        phase1_import_violations=tuple(sorted(phase1_imports)),
        direct_call_violations=tuple(sorted(direct_calls)),
        legacy_dangerous_inventory=tuple(sorted(legacy)),
        legacy_import_inventory=tuple(sorted(legacy_imports)),
        test_broker_inventory=tuple(sorted(test_inventory)),
        legacy_adapter_inventory=tuple(sorted(legacy_adapters)),
        approved_phase2_inventory=tuple(sorted(phase2)),
        parse_violations=tuple(sorted(parse_errors)),
    )


def _format_issue(issue: AuditIssue) -> str:
    return f"{issue.path}:{issue.line}: {issue.code}: {issue.detail}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    report = audit_repository(args.root)

    print("PRISM broker boundary audit")
    print(f"legacy dangerous findings: {len(report.legacy_dangerous_inventory)}")
    for issue in report.legacy_dangerous_inventory:
        print(f"  INVENTORY {_format_issue(issue)}")
    print(f"legacy broker imports: {len(report.legacy_import_inventory)}")
    print(f"test broker references: {len(report.test_broker_inventory)}")
    print(f"legacy adapter references: {len(report.legacy_adapter_inventory)}")
    print(f"approved Phase 2 adapter findings: {len(report.approved_phase2_inventory)}")

    if report.violations:
        print(f"violations: {len(report.violations)}")
        for issue in report.violations:
            print(f"  VIOLATION {_format_issue(issue)}")
        return 1

    print("violations: 0")
    print("PASS: configured AST broker boundary checks found no violations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
