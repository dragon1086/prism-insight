#!/usr/bin/env python3
"""Weekday after-market KR buy-intensity recommendation (Luna).

Luna suggests tighten/hold/loosen for operator review. Telegram notify only.
Never writes .env, never restarts containers, never mutates buy_gate tables.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAST_PATH = PROJECT_ROOT / "logs" / "buy_intensity_luna_last.json"
DB_PATH = PROJECT_ROOT / "stock_tracking_db.sqlite"
REPORTS_DIR = PROJECT_ROOT / "reports"
LOGS_DIR = PROJECT_ROOT / "logs"
STATUS_DIR = PROJECT_ROOT / "status"

STANCES = ("tighten", "hold", "loosen")
STANCE_KO = {"tighten": "조임", "hold": "유지", "loosen": "완화"}
KO_TO_STANCE = {"조임": "tighten", "유지": "hold", "완화": "loosen"}
SUGGEST_KEYS = (
    "REGIME_MIN_SCORE_FLOOR",
    "REGIME_WEAK_NO_TOPDOWN",
    "REGIME_HIVOL_OVERRIDE",
)
_JSON_RE = re.compile(r"\{[\s\S]*\}")

logger = logging.getLogger("buy_intensity_luna")


def _json_load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def load_last(path: Path = LAST_PATH) -> dict[str, Any]:
    return _json_load(path) if path.is_file() else {}


def persist_last(record: dict[str, Any], path: Path = LAST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_recommendation(raw: str) -> dict[str, Any]:
    """Parse Luna output into stance + suggested env + Korean reason."""
    text = (raw or "").strip()
    payload: dict[str, Any] = {}
    match = _JSON_RE.search(text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                payload = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}

    stance = str(payload.get("stance") or "").strip().lower()
    if stance not in STANCES:
        for token, mapped in KO_TO_STANCE.items():
            if token in text:
                stance = mapped
                break
    if stance not in STANCES:
        raise ValueError("unrecognized buy-intensity stance")

    suggested: dict[str, str] = {}
    raw_suggested = payload.get("suggested_env") or payload.get("suggestions") or {}
    if isinstance(raw_suggested, dict):
        for key in SUGGEST_KEYS:
            if key in raw_suggested and raw_suggested[key] not in (None, ""):
                suggested[key] = str(raw_suggested[key]).strip()

    reason = str(payload.get("reason_ko") or payload.get("reason") or "").strip()
    if not reason:
        reason = text[:400]
    lines = [line.strip() for line in reason.splitlines() if line.strip()]
    reason = "\n".join(lines[:5])

    return {"stance": stance, "suggested_env": suggested, "reason_ko": reason}


def quiet_skip_reason(
    *,
    is_market_day: bool,
    has_afternoon_data: bool,
    last: dict[str, Any],
    rec: Optional[dict[str, Any]],
) -> Optional[str]:
    """Return a skip reason when Telegram should stay quiet."""
    if not is_market_day:
        return "holiday_or_weekend"
    if not has_afternoon_data:
        return "no_afternoon_data"
    if rec is None:
        return None
    if rec.get("stance") != "hold":
        return None
    if last.get("stance") != "hold":
        return None
    if dict(last.get("suggested_env") or {}) != dict(rec.get("suggested_env") or {}):
        return None
    return "unchanged_hold"


def read_env_flags() -> dict[str, str]:
    """Read-only snapshot of buy-intensity related flags. Never writes .env."""
    flags: dict[str, str] = {}
    for key in SUGGEST_KEYS:
        if key in os.environ:
            flags[key] = os.environ.get(key, "")
        else:
            flags[key] = "unset"
    return flags


def _latest_jsonl_regime(path: Path) -> dict[str, Any]:
    try:
        from observability.trading_context import latest_regime_snapshot

        return latest_regime_snapshot("KR", path=path)
    except Exception as exc:  # noqa: BLE001 - fail-soft input
        logger.warning("regime_history read failed: %s", exc)
        return {}


def _status_regime(status_dir: Path) -> dict[str, Any]:
    for name in ("kr_regime.json", "regime.json", "market_pulse.json"):
        payload = _json_load(status_dir / name)
        if payload:
            return payload
    return {}


def _pulse_state() -> Optional[str]:
    try:
        from cores.regime_policy import get_market_pulse_state

        return get_market_pulse_state("kr")
    except Exception as exc:  # noqa: BLE001 - fail-soft input
        logger.warning("market pulse read failed: %s", exc)
        return None


def _demo_portfolio(db_path: Path) -> dict[str, Any]:
    if not db_path.is_file():
        return {"available": False, "reason": "db_missing"}
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            account_key = None
            try:
                from trading import kis_auth as ka

                default_mode = str(ka.getEnv().get("default_mode", "demo")).strip().lower()
                svr = "vps" if default_mode == "demo" else "prod"
                account_key = ka.resolve_account(svr=svr, market="kr")["account_key"]
            except Exception as exc:  # noqa: BLE001
                logger.warning("demo account resolve failed: %s", exc)

            if account_key:
                rows = conn.execute(
                    "SELECT ticker, company_name, buy_price, current_price FROM stock_holdings "
                    "WHERE account_key = ?",
                    (account_key,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT ticker, company_name, buy_price, current_price FROM stock_holdings"
                ).fetchall()
            holdings = []
            for row in rows:
                buy = float(row["buy_price"] or 0)
                current = float(row["current_price"] or 0)
                pnl_pct = ((current - buy) / buy * 100) if buy else None
                holdings.append(
                    {
                        "ticker": row["ticker"],
                        "name": row["company_name"],
                        "buy_price": buy,
                        "current_price": current,
                        "pnl_pct": None if pnl_pct is None else round(pnl_pct, 2),
                    }
                )
            return {"available": True, "count": len(holdings), "holdings": holdings[:12]}
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("holdings read failed: %s", exc)
        return {"available": False, "reason": str(exc)}


def _afternoon_hints(session: date, reports_dir: Path, logs_dir: Path) -> dict[str, Any]:
    stamp = session.strftime("%Y%m%d")
    reports = sorted(reports_dir.glob(f"*_{stamp}_afternoon*")) if reports_dir.is_dir() else []
    log_path = logs_dir / f"kr_afternoon_{stamp}.log"
    hints: list[str] = []
    if log_path.is_file():
        try:
            tail = log_path.read_bytes()[-120_000:].decode("utf-8", errors="ignore")
            for line in tail.splitlines():
                lower = line.lower()
                if any(token in lower for token in ("skip", "skipped", "low-score", "low score")) or any(
                    token in line for token in ("기준 미달", "점수 미달", "스킵", "제외")
                ):
                    hints.append(line.strip()[:240])
                    if len(hints) >= 8:
                        break
        except OSError as exc:
            logger.warning("afternoon log read failed: %s", exc)
    return {
        "has_afternoon_data": bool(reports or log_path.is_file()),
        "report_count": len(reports),
        "log_present": log_path.is_file(),
        "skip_or_low_score_hints": hints,
    }


def collect_inputs(
    *,
    session: Optional[date] = None,
    root: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Gather fail-soft context. Missing sources become empty fields."""
    session = session or date.today()
    flags = read_env_flags()
    regime_log = _latest_jsonl_regime(root / "logs" / "regime_history.jsonl")
    status = _status_regime(root / "status")
    afternoon = _afternoon_hints(session, root / "reports", root / "logs")
    pulse = _pulse_state()
    return {
        "session": session.isoformat(),
        "env_flags": flags,
        "regime_history": {
            "regime": regime_log.get("regime") or regime_log.get("market_regime"),
            "confidence": regime_log.get("confidence") or regime_log.get("regime_confidence"),
            "primary_trend_regime": regime_log.get("primary_trend_regime"),
            "effective_entry_regime": regime_log.get("effective_entry_regime"),
            "swing_state": regime_log.get("swing_state"),
            "ts": regime_log.get("ts"),
        },
        "status": status or None,
        "market_pulse": pulse,
        "portfolio": _demo_portfolio(root / "stock_tracking_db.sqlite"),
        "afternoon": afternoon,
    }


def format_telegram(rec: dict[str, Any], inputs: dict[str, Any]) -> str:
    stance = rec["stance"]
    flags = inputs.get("env_flags") or {}
    current = ", ".join(f"{k}={v}" for k, v in flags.items())
    suggested = rec.get("suggested_env") or {}
    if suggested:
        suggest_line = ", ".join(f"{k}={v}" for k, v in suggested.items())
    else:
        suggest_line = "변경 제안 없음 (현재 유지)"
    regime = (inputs.get("regime_history") or {}).get("regime") or "unknown"
    conf = (inputs.get("regime_history") or {}).get("confidence")
    pulse = inputs.get("market_pulse") or "n/a"
    return (
        "🌙 Luna 장후 매수강도 추천\n"
        f"스탠스: {STANCE_KO[stance]} ({stance})\n"
        f"국면: {regime} / 신뢰도: {conf} / pulse: {pulse}\n"
        f"현재 플래그: {current}\n"
        f"권장(.env 자동적용 안 함): {suggest_line}\n"
        "이유:\n"
        f"{rec.get('reason_ko') or '-'}"
    )


def _build_prompt(inputs: dict[str, Any]) -> str:
    return (
        "당신은 KR 모의계좌 운영 보조입니다. 장후 한 번만, 매수강도를 추천합니다.\n"
        "출력은 JSON 하나만: {\"stance\":\"tighten|hold|loosen\","
        "\"suggested_env\":{\"REGIME_MIN_SCORE_FLOOR\":\"true|false|unchanged\","
        "\"REGIME_WEAK_NO_TOPDOWN\":\"true|false|unchanged\","
        "\"REGIME_HIVOL_OVERRIDE\":\"active|off|unchanged\"},"
        "\"reason_ko\":\"2~5줄 한국어 합쇼체\"}\n"
        "조임=tighten, 유지=hold, 완화=loosen.\n"
        "suggested_env는 제안일 뿐이며 시스템이 적용하지 않습니다. .env를 쓰라고 지시하지 마세요.\n"
        "buy_gate/buy_quality 테이블을 바꾸지 마세요. 종목 배치 분석을 하지 마세요.\n"
        f"입력:\n{json.dumps(inputs, ensure_ascii=False, default=str)[:6000]}"
    )


async def call_luna(inputs: dict[str, Any]) -> str:
    from cores.llm.agent_bridge import ensure_openai_agents_configured
    from cores.llm.backends.openai_agents_backend import OpenAIAgentsBackend
    from cores.llm.config_loader import load_report_mcp_registry
    from cores.llm.ports import AgentSpec, LLMParams
    from report_model_config import REPORT_AUX_EFFORT, REPORT_MODEL

    ensure_openai_agents_configured()
    backend = OpenAIAgentsBackend(load_report_mcp_registry())
    spec = AgentSpec(
        name="buy_intensity_luna",
        instructions=(
            "Recommend KR after-hours buy intensity only. Return one JSON object. "
            "Do not call tools. Do not analyze individual names in depth."
        ),
        model=REPORT_MODEL,
        mcp_servers=(),
        params=LLMParams(
            max_tokens=700,
            reasoning_effort=REPORT_AUX_EFFORT or "low",
            max_iterations=1,
            parallel_tool_calls=False,
        ),
    )
    result = await asyncio.wait_for(backend.run(spec, _build_prompt(inputs)), timeout=90)
    return result.text or ""


async def maybe_send_telegram(message: str, *, enabled: bool) -> bool:
    if not enabled:
        logger.info("Telegram disabled (--dry-run/--no-telegram)")
        return False
    from telegram_bot_agent import TelegramBotAgent
    from telegram_config import TelegramConfig

    cfg = TelegramConfig(use_telegram=True)
    cfg.validate_or_raise()
    agent = TelegramBotAgent(token=cfg.bot_token)
    return bool(await agent.send_message(cfg.channel_id, message, parse_mode="Markdown"))


async def run_once(
    *,
    dry_run: bool = False,
    no_telegram: bool = False,
    root: Path = PROJECT_ROOT,
    last_path: Path = LAST_PATH,
    env_path: Path | None = None,
    luna_fn: Optional[Callable[[dict[str, Any]], Any]] = None,
    market_day_fn: Optional[Callable[[], bool]] = None,
) -> dict[str, Any]:
    if env_path is not None:
        # Read-only load. Never open .env for write.
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=str(env_path), override=False)

    session = date.today()
    if market_day_fn is None:
        from check_market_day import is_market_day

        is_open = bool(is_market_day(session))
    else:
        is_open = bool(market_day_fn())

    inputs = collect_inputs(session=session, root=root)
    has_afternoon = bool((inputs.get("afternoon") or {}).get("has_afternoon_data"))
    last = load_last(last_path)

    skip = quiet_skip_reason(
        is_market_day=is_open,
        has_afternoon_data=has_afternoon,
        last=last,
        rec=None,
    )
    if skip in {"holiday_or_weekend", "no_afternoon_data"}:
        logger.info("quiet skip (%s); no Telegram", skip)
        return {"status": "quiet", "reason": skip, "telegram": False}

    raw = await (luna_fn or call_luna)(inputs)
    rec = parse_recommendation(raw)
    skip = quiet_skip_reason(
        is_market_day=is_open,
        has_afternoon_data=has_afternoon,
        last=last,
        rec=rec,
    )
    record = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "session": session.isoformat(),
        "stance": rec["stance"],
        "suggested_env": rec["suggested_env"],
        "reason_ko": rec["reason_ko"],
        "inputs_digest": {
            "regime": (inputs.get("regime_history") or {}).get("regime"),
            "confidence": (inputs.get("regime_history") or {}).get("confidence"),
            "flags": inputs.get("env_flags"),
        },
        "quiet_reason": skip,
        "env_written": False,
    }
    persist_last(record, last_path)

    if skip:
        logger.info("quiet skip (%s); recommendation persisted, no Telegram", skip)
        return {"status": "quiet", "reason": skip, "recommendation": rec, "telegram": False}

    message = format_telegram(rec, inputs)
    logger.info("recommendation %s\n%s", rec["stance"], message)
    sent = False
    if not dry_run and not no_telegram:
        sent = await maybe_send_telegram(message, enabled=True)
    else:
        logger.info("skip Telegram send (dry_run=%s no_telegram=%s)", dry_run, no_telegram)
    return {"status": "ok", "recommendation": rec, "telegram": sent, "message": message}


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run a single after-hours recommendation")
    parser.add_argument("--dry-run", action="store_true", help="Print/log only; do not send Telegram")
    parser.add_argument("--no-telegram", action="store_true", help="Disable Telegram even if configured")
    return parser.parse_args(argv)


async def _async_main(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=str(PROJECT_ROOT / ".env"), override=False)

    proxy_started = False
    if os.getenv("PRISM_OPENAI_AUTH_MODE") == "chatgpt_oauth":
        try:
            from cores.chatgpt_proxy import inject_env, start_proxy, stop_proxy

            inject_env()
            proxy_started = await start_proxy()
        except Exception as exc:  # noqa: BLE001
            logger.warning("oauth proxy not started: %s", exc)
    try:
        result = await run_once(dry_run=args.dry_run, no_telegram=args.no_telegram)
        logger.info("done: %s", {k: result.get(k) for k in ("status", "reason", "telegram")})
        return 0
    finally:
        if proxy_started:
            try:
                from cores.chatgpt_proxy import stop_proxy

                await stop_proxy()
            except Exception:
                pass


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    if not args.once:
        logger.warning("cron/entry expected --once; running a single pass anyway")
        args.once = True
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
