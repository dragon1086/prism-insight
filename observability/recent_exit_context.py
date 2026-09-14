"""Read-only strategy exit facts for prompts; no broker calls or decision gates."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_TABLES = {"KR": "trading_history", "US": "us_trading_history"}
_KST = ZoneInfo("Asia/Seoul")


def _clock(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    # Existing strategy DB writers use server-local KST for both markets.
    return (parsed.replace(tzinfo=_KST) if parsed.tzinfo is None else parsed).astimezone(_KST)


def build_recent_exit_facts(cursor, ticker, *, market, account_key=None, now=None):
    facts = {
        "status": "SOURCE_UNAVAILABLE", "market": market,
        "basis": "strategy_history_not_broker_fills", "window_calendar_days": 14,
        "latest_exit_at": None, "exit_day_count_14_calendar_days": None,
        "stop_exit_day_count_14_calendar_days": None,
        "confirmed_stop_exit_day_count_14_calendar_days": None,
        "count_basis": "distinct_exit_calendar_days_conservative_lower_bound",
        "limitation": "Same-day exits may be pyramided legs or separate stop episodes; distinct days are a lower bound, not exact trade episodes. Fewer than two stop days does NOT prove fewer than two stop episodes. 14 calendar days is not 10 trading days; <=5 trading days requires a verified calendar.",
    }
    try:
        table = _TABLES[market]
        observed = _clock(now or datetime.now(_KST))
        facts["observed_at"] = observed.isoformat()
        columns = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()}
        if not {"ticker", "sell_date"} <= columns:
            raise ValueError("history schema unavailable")
        if "account_key" not in columns or not account_key:
            raise ValueError("strategy scope unavailable")
        kind = "exit_kind" if "exit_kind" in columns else "NULL"
        rows = cursor.execute(
            f"SELECT sell_date, {kind} FROM {table} WHERE ticker = ? AND account_key = ? ORDER BY sell_date DESC LIMIT 1001",
            (ticker, account_key),
        ).fetchall()
        if len(rows) > 1000:
            raise ValueError("history coverage limit")
        exits = [(_clock(row[0]), str(row[1] or "").lower()) for row in rows]
        if any(at > observed for at, _ in exits):
            raise ValueError("future history timestamp")
        recent = [(at, kind) for at, kind in exits if at >= observed - timedelta(days=14)]
        stop_days = {at.date() for at, kind in recent if kind == "stop"}
        unknown = sum(kind not in {"stop", "trend_exit", "target", "ai"} for _, kind in recent)
        facts.update(
            status="OK" if exits else "NO_HISTORY",
            source=table,
            latest_exit_at=max((at for at, _ in exits), default=None).isoformat() if exits else None,
            exit_day_count_14_calendar_days=len({at.date() for at, _ in recent}),
            stop_exit_day_count_14_calendar_days=None if unknown else len(stop_days),
            confirmed_stop_exit_day_count_14_calendar_days=len(stop_days),
            unclassified_exit_rows_14_calendar_days=unknown,
        )
    except Exception as error:  # noqa: BLE001 - never affect trading, never expose payload
        facts["error_type"] = type(error).__name__
    return facts


def append_recent_exit_context(agent, ticker, context, *, market):
    """Preserve optional narrative lessons; fresh canonical facts cannot be omitted."""
    if not getattr(agent, "enable_journal", False):
        return context
    try:
        account_key = agent._account_scope()[0]
    except Exception:  # noqa: BLE001
        account_key = None
    facts = build_recent_exit_facts(getattr(agent, "cursor", None), ticker,
                                    market=market, account_key=account_key)
    return (
        "### SAME_TICKER_STRATEGY_EXIT_FACTS\n"
        "NO_HISTORY means the scoped strategy history was queried successfully and had no exits; "
        "SOURCE_UNAVAILABLE is not zero history. Journal lessons can exist without same-ticker trades. "
        "Do not call these supplied facts NOT_IN_INPUT. This is context, not a new entry gate. "
        "한국어 답변에서는 조회 결과 이력 없음과 조회 불가를 구분해 자연어로 설명하고 내부 상태 코드를 그대로 나열하지 마십시오.\n"
        + json.dumps(facts, ensure_ascii=False, sort_keys=True) + "\n\n"
        + (context if isinstance(context, str) else "")
    )
