# live/healthcheck.py — 운영 이상감지 watchdog (정상이면 조용, 문제만 경보)
#
# 시스템이 스스로 자기 건강을 점검하고, 문제가 있을 때만 텔레그램으로 운영자에게
# 경보한다. 정상이면 아무것도 보내지 않는다(조용). --daily 플래그면 하루 1회
# "정상 가동 중" 한 줄 요약만 보내 안심시킨다.
#
# 점검 항목 (run_healthcheck):
#   1) 데몬 정지     — 최신 heartbeat 이벤트 ts 나이 > 70분 (또는 기록 없음) → alert
#   2) 에러 폭주     — 최근 2시간 error 이벤트 > 5건 → alert (최근 1건 메시지 첨부)
#   3) 시세 갱신 정지 — last_processed_30m_ns 나이 > 90분 → alert (없으면 warn)
#   4) 자산 이상     — latest_equity 기록 없음 → warn / demo & equity<=0 → alert
#   5) 포지션 고착   — entry_time 20일+ 경과 포지션 → warn
#   6) 섀도우-데모 괴리 — demo&shadow equity 둘 다 있고 |차이|>15% → warn
#
# 안전 원칙: 모든 SQL/전송 실패를 흡수한다. 어떤 예외도 밖으로 던지지 않는다.
# 토큰/채널 미설정 시 stdout 폴백 (크래시 금지). 시간 기준은 now 인자 주입으로
# 결정적 테스트가 가능하다.
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timezone

from live import tracking
from live.telegram_reporter import _send, _load_env

log = logging.getLogger("live.healthcheck")

# --- 임계값 (운영 경험으로 조정 가능) ---
_HEARTBEAT_MAX_MIN = 70       # 데몬 정지 의심 (틱 누락) 임계 (분)
_ERROR_WINDOW_HOURS = 2       # 에러 폭주 관측 창 (시간)
_ERROR_MAX_COUNT = 5          # 이 개수 초과면 폭주
_PRICE_MAX_MIN = 90           # 시세/처리 정지 임계 (분)
_POSITION_STALE_DAYS = 20     # 장기 미청산 포지션 임계 (일)
_SHADOW_DIVERGENCE_PCT = 15.0 # 섀도우-데모 괴리 경보 임계 (%)

_OPS_CHANNEL_KEYS = ("BTC_OPS_CHANNEL_ID", "TELEGRAM_CHANNEL_ID")


# ---------------------------------------------------------------------------
# 시간 helpers — 전부 실패 흡수, 파싱 불가 시 None.
# ---------------------------------------------------------------------------

def _now(now: datetime | None) -> datetime:
    """기준 시각. 주입 없으면 현재 UTC. tz-naive 는 UTC 로 간주."""
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


def _parse_ts(ts) -> datetime | None:
    """ISO 문자열을 tz-aware datetime 으로. 실패 시 None."""
    if ts is None:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001 — 파싱 실패는 무해, 호출측이 처리
        return None


def _age_minutes(ts, now: datetime) -> float | None:
    """ts(ISO) 와 now 사이 경과 분. 파싱 불가 시 None."""
    dt = _parse_ts(ts)
    if dt is None:
        return None
    return (now - dt).total_seconds() / 60.0


def _ns_age_minutes(ns, now: datetime) -> float | None:
    """epoch 나노초 정수와 now 사이 경과 분. 변환 불가 시 None."""
    try:
        secs = int(ns) / 1e9
    except Exception:  # noqa: BLE001
        return None
    dt = datetime.fromtimestamp(secs, tz=timezone.utc)
    return (now - dt).total_seconds() / 60.0


# ---------------------------------------------------------------------------
# 개별 점검 — 각 함수는 이슈 dict 를 0/1개 반환 (없으면 None). 전부 실패 흡수.
# ---------------------------------------------------------------------------

def _check_daemon(conn, mode: str, now: datetime) -> dict | None:
    """1) 데몬 정지: 최신 heartbeat ts 나이 > 70분 (또는 기록 없음) → alert."""
    try:
        r = conn.execute(
            "SELECT ts FROM btc_events WHERE mode=? AND kind='heartbeat' "
            "ORDER BY id DESC LIMIT 1",
            (mode,),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if r is None:
        return {"level": "alert", "code": "daemon_down",
                "msg": "데몬 정지 의심(틱 누락) — 하트비트 기록 없음"}
    age = _age_minutes(r["ts"], now)
    if age is None:
        return None
    if age > _HEARTBEAT_MAX_MIN:
        return {"level": "alert", "code": "daemon_down",
                "msg": f"데몬 정지 의심(틱 누락) — 마지막 하트비트 {age:.0f}분 전"}
    return None


def _check_error_burst(conn, mode: str, now: datetime) -> dict | None:
    """2) 에러 폭주: 최근 2시간 error 이벤트 > 5건 → alert (최근 1건 첨부)."""
    try:
        rows = conn.execute(
            "SELECT ts, message FROM btc_events WHERE mode=? AND level='error' "
            "ORDER BY id DESC LIMIT 200",
            (mode,),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return None
    recent = []
    for row in rows:
        age = _age_minutes(row["ts"], now)
        if age is not None and 0 <= age <= _ERROR_WINDOW_HOURS * 60:
            recent.append(row)
    if len(recent) > _ERROR_MAX_COUNT:
        last_msg = str(recent[0]["message"])[:120]
        return {"level": "alert", "code": "error_burst",
                "msg": (f"에러 폭주 — 최근 {_ERROR_WINDOW_HOURS}시간 {len(recent)}건 "
                        f"(최근: {last_msg})")}
    return None


def _check_price_stale(conn, mode: str, now: datetime) -> dict | None:
    """3) 시세 갱신 정지: last_processed_30m_ns 나이 > 90분 → alert (없으면 warn)."""
    try:
        ns = tracking.get_meta(conn, "last_processed_30m_ns", mode)
    except Exception:  # noqa: BLE001
        return None
    if ns is None:
        return {"level": "warn", "code": "price_stale",
                "msg": "시세/처리 정지 — 처리된 30m 봉 기록 없음"}
    age = _ns_age_minutes(ns, now)
    if age is None:
        return None
    if age > _PRICE_MAX_MIN:
        return {"level": "alert", "code": "price_stale",
                "msg": f"시세/처리 정지 — 마지막 처리 {age:.0f}분 전"}
    return None


def _check_equity(conn, mode: str, now: datetime) -> dict | None:
    """4) 자산 이상: 기록 없음 → warn / demo & equity<=0 → alert."""
    try:
        eq = tracking.latest_equity(conn, mode)
    except Exception:  # noqa: BLE001
        return None
    if eq is None:
        return {"level": "warn", "code": "equity_missing",
                "msg": "자산 기록 없음 — 자산 곡선 미집계(6시간+ 의심)"}
    if mode == "demo" and eq <= 0:
        return {"level": "alert", "code": "equity_zero",
                "msg": f"자산 이상 — 평가금액 {eq:,.0f} (0 이하)"}
    return None


def _check_stale_positions(conn, mode: str, now: datetime) -> dict | None:
    """5) 포지션 고착: entry_time 20일+ 경과 포지션 → warn."""
    try:
        positions = tracking.load_open_positions(conn, mode)
    except Exception:  # noqa: BLE001
        return None
    stale = 0
    for p in positions:
        dt = _parse_ts(p.entry_time)
        if dt is None:
            continue
        age_days = (now - dt).total_seconds() / 86400.0
        if age_days > _POSITION_STALE_DAYS:
            stale += 1
    if stale > 0:
        return {"level": "warn", "code": "stale_position",
                "msg": f"장기 미청산 포지션 {stale}건 ({_POSITION_STALE_DAYS}일+ 경과)"}
    return None


def _check_shadow_divergence(conn, mode: str, now: datetime) -> dict | None:
    """6) 섀도우-데모 괴리: demo&shadow equity 둘 다 있고 |차이|>15% → warn."""
    if mode != "demo":
        return None
    try:
        demo_eq = tracking.latest_equity(conn, "demo")
        shadow_eq = tracking.latest_equity(conn, "shadow")
    except Exception:  # noqa: BLE001
        return None
    if demo_eq is None or shadow_eq is None or shadow_eq == 0:
        return None
    diff_pct = 100.0 * (demo_eq - shadow_eq) / abs(shadow_eq)
    if abs(diff_pct) > _SHADOW_DIVERGENCE_PCT:
        return {"level": "warn", "code": "shadow_divergence",
                "msg": (f"이론(섀도우) 대비 큰 괴리 {diff_pct:+.1f}% — "
                        f"체결/슬리피지 점검")}
    return None


_CHECKS = (
    _check_daemon,
    _check_error_burst,
    _check_price_stale,
    _check_equity,
    _check_stale_positions,
    _check_shadow_divergence,
)


# ---------------------------------------------------------------------------
# 핵심 점검 — 이슈 리스트 반환 (빈 리스트 = 정상).
# ---------------------------------------------------------------------------

def run_healthcheck(conn, mode: str = "demo", now: datetime | None = None) -> list[dict]:
    """모든 점검을 실행해 이슈 리스트를 반환한다. 빈 리스트 = 정상.

    각 이슈는 {level: "warn"|"alert", code, msg}. 시간 기준은 now 주입으로 결정적.
    개별 점검의 예외는 흡수되어 다른 점검을 막지 않는다.
    """
    ref = _now(now)
    issues: list[dict] = []
    for check in _CHECKS:
        try:
            issue = check(conn, mode, ref)
        except Exception as exc:  # noqa: BLE001 — 1개 점검 실패가 전체를 못 막음
            log.warning("healthcheck %s 실패 (흡수): %s", check.__name__, exc)
            continue
        if issue:
            issues.append(issue)
    return issues


# ---------------------------------------------------------------------------
# 메시지 빌드 — 한국어, 운영자용, 명확.
# ---------------------------------------------------------------------------

_CODE_TAG = {
    "daemon_down": "데몬정지",
    "error_burst": "에러폭주",
    "price_stale": "시세정지",
    "equity_missing": "자산없음",
    "equity_zero": "자산이상",
    "stale_position": "포지션고착",
    "shadow_divergence": "괴리",
}


def _build_alert_message(issues: list[dict], mode: str) -> str:
    """이슈 리스트 → 운영자용 경보 Markdown. alert/warn 모두 포함."""
    has_alert = any(i["level"] == "alert" for i in issues)
    head = "🚨 *BTC 자동매매 이상감지*" if has_alert else "⚠️ *BTC 자동매매 점검 경고*"
    lines = [head, f"_모드: {mode}_", ""]
    for i in issues:
        mark = "🔴" if i["level"] == "alert" else "🟡"
        tag = _CODE_TAG.get(i["code"], i["code"])
        lines.append(f"{mark} [{tag}] {i['msg']}")
    lines.append("")
    lines.append("_확인 필요_")
    return "\n".join(lines)


def _build_daily_message(mode: str) -> str:
    """--daily 정상 요약 — 안심용 1줄."""
    return f"✅ BTC 자동매매 정상 가동 중 (모드: {mode})"


# ---------------------------------------------------------------------------
# 채널/전송 — 운영자 DM 권장 (BTC_OPS_CHANNEL_ID > TELEGRAM_CHANNEL_ID).
# ---------------------------------------------------------------------------

def _resolve_ops_channel() -> str | None:
    """운영자 채널: BTC_OPS_CHANNEL_ID > TELEGRAM_CHANNEL_ID > None.

    notifier/리포터의 공개 채널과 분리 — 이상감지는 운영자 DM 으로 보내는 게 안전.
    """
    for key in _OPS_CHANNEL_KEYS:
        v = os.environ.get(key)
        if v:
            return v
    return None


def _dispatch(message: str) -> None:
    """메시지 1건 전송. 토큰/채널 없으면 _send 가 stdout 폴백한다. 실패 흡수."""
    try:
        _load_env()
    except Exception:  # noqa: BLE001 — env 로드 실패해도 환경에 이미 있을 수 있음
        pass
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel = _resolve_ops_channel()
    try:
        asyncio.run(_send(token, channel, message))
    except Exception as exc:  # noqa: BLE001 — 전송 실패 절대 비전파
        log.warning("healthcheck 전송 실패 (흡수): %s", exc)


# ---------------------------------------------------------------------------
# 핵심 진입점 — 점검 + (이슈 있을 때만) 경보 + 이력 기록.
# ---------------------------------------------------------------------------

def notify_health(conn, mode: str = "demo", send: bool = True,
                  daily: bool = False, now: datetime | None = None) -> dict:
    """건강 점검 후 이슈가 있으면 텔레그램 경보(정상이면 조용).

    - 이슈 있음 → 운영자용 경보 메시지 전송 (send=True 일 때).
    - 이슈 없음 → 전송 안 함. 단 daily=True 면 "정상 가동 중" 1줄 전송.
    - 점검 결과는 btc_events(kind='health') 로도 기록 (alert→error, 아니면 info).
    - 모든 전송 실패 흡수, 예외 비전파. 반환은 {"issues", "sent", "level"} (디버그용).
    """
    issues = run_healthcheck(conn, mode, now=now)
    has_alert = any(i["level"] == "alert" for i in issues)
    result = {"issues": len(issues), "sent": False,
              "level": "alert" if has_alert else ("warn" if issues else "ok")}

    # 이력 기록 (감사용) — 실패 흡수.
    try:
        ev_level = "error" if has_alert else "info"
        if issues:
            summary = "; ".join(f"[{i['code']}] {i['msg']}" for i in issues)
        else:
            summary = "정상 (이슈 0)"
        tracking.log_event(conn, "health", summary[:500], level=ev_level, mode=mode)
    except Exception as exc:  # noqa: BLE001 — 기록 실패가 경보를 막지 않음
        log.warning("health 이벤트 기록 실패 (흡수): %s", exc)

    if not send:
        return result

    if issues:
        message = _build_alert_message(issues, mode)
        _dispatch(message)
        result["sent"] = True
    elif daily:
        _dispatch(_build_daily_message(mode))
        result["sent"] = True

    return result


# ---------------------------------------------------------------------------
# CLI — python -m live.healthcheck [--mode demo] [--daily] [--no-send]
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="prism-btc 운영 이상감지 watchdog")
    parser.add_argument("--mode", default="demo",
                        choices=["shadow", "demo", "live"])
    parser.add_argument("--daily", action="store_true",
                        help="이슈 없어도 '정상 가동 중' 1줄 전송 (매일 1회 안심용)")
    parser.add_argument("--no-send", action="store_true",
                        help="전송하지 않고 stdout 출력만")
    parser.add_argument("--root-db", default=None, help="root tracking db 경로")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    conn = tracking.get_connection(args.root_db)
    try:
        tracking.ensure_schema(conn)
        if args.no_send:
            issues = run_healthcheck(conn, args.mode)
            # 이력은 남기되 전송은 안 함.
            notify_health(conn, args.mode, send=False)
            if issues:
                print(_build_alert_message(issues, args.mode))
            else:
                print(_build_daily_message(args.mode) + " (이슈 0)")
        else:
            res = notify_health(conn, args.mode, send=True, daily=args.daily)
            log.info("healthcheck: %s", res)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
