"""Append-only ledger outbox and pure account evidence; no sender/order imports.

Claiming grants permission once and immediately persists UNKNOWN. A crash before
or after sending cannot be distinguished; UNKNOWN is never automatically retried.
This is at-most-one automatic send attempt, NOT exactly-once Telegram delivery.
Only a separately guarded test-chat sender may consume these frozen notices.
"""
import hashlib
import json
from decimal import Decimal

from prism_core.strategy_ledger import LedgerError, _dump, _number, _text, _time
from prism_core.strategy_ledger_execution import project_account_target
from prism_core.strategy_ledger_messages import format_campaign

EXECUTION_STATUSES = frozenset({"UNKNOWN", "SUBMITTED", "ACCEPTED", "REJECTED", "PARTIAL", "FILLED", "CANCELLED"})


def _known_ref(value):
    return isinstance(value, str) and bool(value.strip()) and value.strip().upper() not in {"UNKNOWN", "MISSING", "[REDACTED]"}


def _active_buy_unresolved(record):
    """No inference that an active order with zero reservation is completed."""
    if record.get("side") != "BUY" or record.get("status") not in {"SUBMITTED", "ACCEPTED", "PARTIAL"}:
        return False
    target, reserved = record.get("submitted_target_pct"), record.get("reserved_buy_notional")
    return target is None or reserved is None or _number(target) <= 0 or _number(reserved) <= 0


def _id(kind, *parts):
    return "ledger-" + kind + ":" + hashlib.sha256(_dump(parts).encode()).hexdigest()


def freeze_notice(ledger, db, source_event_id, campaign_id, category):
    """Called only inside the source strategy/execution transaction."""
    headings = {"BUY": "전략 배분 기록", "SELL": "전략 축소·청산 기록",
                "PILOT_STATE": "파일럿 상태 변경", "EXECUTION": "계좌 집행 증거 변경"}
    if category not in headings:
        raise LedgerError("unsupported notice category")
    campaign = ledger._get(db, "campaigns", campaign_id)
    snapshot = ledger._snapshot(db, campaign["book_id"])
    if snapshot["mode"] not in {"SHADOW", "VALIDATION", "VALIDATION_ONLY"}:
        raise LedgerError("notice delivery is validation/test-chat only")
    source = db.execute("SELECT payload FROM events WHERE id=?", (source_event_id,)).fetchone()
    if not source:
        raise LedgerError("notice source event missing")
    payload = json.loads(source[0])
    occurred_at = payload.get("occurred_at") or payload.get("observed_at") or payload.get("pilot", {}).get("entry_at")
    source_observed_at = _time(occurred_at)
    snapshot_evidence_at = max(
        [source_observed_at, _time(campaign["last_event_at"]),
         _time(snapshot.get("last_accounting_at") or campaign["last_event_at"])]
        + [_time(record["observed_at"]) for record in snapshot["executions"]]
    )
    notice_id = _id("notice", source_event_id, campaign_id, category)
    state = campaign.get("pilot", {}).get("state")
    heading = headings[category] + (f" · {state}" if state in {"PILOT_50", "WAIT", "FULL_100", "ADD_CANCELLED", "ADD_EXPIRED", "EXITED"} else "")
    cumulative_note = ""
    if category == "EXECUTION" and payload.get("cumulative_fill_quantity") is not None:
        cumulative_note = f"\n해당 주문의 누적 확인 체결: {_number(payload['cumulative_fill_quantity']):g}주"
        if payload.get("status") == "CANCELLED":
            cumulative_note += " · 취소 전에 확인된 체결은 유지됩니다."
    frozen = {"kind": "strategy_notice", "notice_id": notice_id,
              "source_event_id": source_event_id, "campaign_id": campaign_id,
              "book_id": campaign["book_id"], "category": category,
              "created_at": snapshot_evidence_at, "source_observed_at": source_observed_at,
              "snapshot_evidence_at": snapshot_evidence_at, "clock_basis": "EVIDENCE_CLOCK_NOT_WALL_CLOCK",
              "delivery_scope": "TEST_CHAT_ONLY",
              "text": heading + "\n원천 관측 시각: " + source_observed_at
                      + "\n고정 원장 증거 기준 시각: " + snapshot_evidence_at
                      + cumulative_note + "\n" + format_campaign(snapshot, campaign_id)}
    ledger._event(db, notice_id, frozen)


class StrategyLedgerOutbox:
    def __init__(self, ledger):
        self.ledger = ledger

    @staticmethod
    def _read(db, event_id):
        row = db.execute("SELECT payload FROM events WHERE id=?", (event_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _view(self, db, notice_id):
        notice = self._read(db, notice_id)
        if not notice or notice.get("kind") != "strategy_notice":
            raise LedgerError("unknown notice")
        claim = self._read(db, _id("claim", notice_id))
        ack = self._read(db, _id("ack", notice_id))
        return {**notice, "status": "SENT" if ack else "UNKNOWN" if claim else "READY",
                "claim_id": claim["claim_id"] if claim else None,
                "receipt_ref": ack["receipt_ref"] if ack else None}

    def pending(self):
        return self.list_notices(status="READY")

    def list_notices(self, *, status=None):
        """UNKNOWN remains inspectable for manual reconciliation, never resend."""
        if status not in {None, "READY", "UNKNOWN", "SENT"}:
            raise LedgerError("unsupported notice status")
        with self.ledger._transaction() as db:
            notices = []
            for event_id, payload in db.execute("SELECT id, payload FROM events ORDER BY rowid"):
                if json.loads(payload).get("kind") == "strategy_notice":
                    notice = self._view(db, event_id)
                    if status is None or notice["status"] == status:
                        notices.append(notice)
            return notices

    def status(self, notice_id):
        with self.ledger._transaction() as db:
            return self._view(db, notice_id)

    def claim(self, notice_id, claim_id, claimed_at):
        claimed_at, claim_id = _time(claimed_at), _text(claim_id)
        with self.ledger._transaction() as db:
            notice = self._view(db, notice_id)
            if notice["status"] != "READY":
                return {**notice, "send_authorized": False}
            if claimed_at < notice["created_at"]:
                raise LedgerError("claim predates notice")
            self.ledger._event(db, _id("claim", notice_id), {
                "kind": "notice_claim", "notice_id": notice_id, "claim_id": claim_id,
                "claimed_at": claimed_at, "status": "UNKNOWN",
            })
            return {**self._view(db, notice_id), "send_authorized": True}

    def ack(self, notice_id, claim_id, receipt_ref, acknowledged_at):
        """Retries must reuse the original receipt AND acknowledgment timestamp."""
        acknowledged_at = _time(acknowledged_at)
        if not _known_ref(receipt_ref):
            raise LedgerError("known receipt evidence required")
        with self.ledger._transaction() as db:
            notice = self._view(db, notice_id)
            claim = self._read(db, _id("claim", notice_id))
            if not claim or claim["claim_id"] != claim_id:
                raise LedgerError("ack requires the original send claim")
            if acknowledged_at < claim["claimed_at"]:
                raise LedgerError("ack predates claim")
            self.ledger._event(db, _id("ack", notice_id), {
                "kind": "notice_ack", "notice_id": notice_id, "claim_id": claim_id,
                "receipt_ref": _text(receipt_ref), "acknowledged_at": acknowledged_at,
            })
            return {**notice, "status": "SENT", "receipt_ref": receipt_ref}


def execution_change(previous, current):
    """Ignore repeat polls/source clocks; changes in evidence remain meaningful."""
    if previous is not None and _time(current["observed_at"]) <= _time(previous["observed_at"]):
        return False  # Preserve late/equal-clock evidence without a stale status notice.
    fields = ("status", "confirmed_quantity", "confirmed_price", "side",
              "cumulative_fill_quantity", "cumulative_fill_notional", "reserved_buy_notional")
    return previous is None or any(previous.get(key) != current.get(key) for key in fields)


def previous_execution(db, campaign_id, execution_profile_ref, intent_ref):
    latest = None
    for data, in db.execute("SELECT data FROM executions ORDER BY rowid DESC"):
        record = json.loads(data)
        if (record.get("campaign_id") == campaign_id and record.get("execution_profile_ref") == execution_profile_ref
                and record.get("intent_ref") == intent_ref):
            if latest is None or _time(record["observed_at"]) > _time(latest["observed_at"]):
                latest = record
    return latest


def record_account_evidence(ledger, event_id, campaign_id, *, execution_profile_ref,
                            intent_ref, side, status, observed_at, evidence_source,
                            cumulative_fill_quantity=None, cumulative_fill_notional=None,
                            reserved_buy_notional=None, submitted_target_pct=None):
    """Persist broker-supplied cumulative PER-INTENT evidence, never infer fills.

    Cancellation does not erase prior fills. A poller must provide final cumulative
    values and reservation release explicitly; missing values remain unknown.
    confirmed_* display fields are populated only for PARTIAL/FILLED evidence.
    """
    if side not in {"BUY", "SELL"} or status not in EXECUTION_STATUSES:
        raise LedgerError("invalid account side/status")
    if not all(_known_ref(value) for value in (execution_profile_ref, intent_ref, evidence_source)):
        raise LedgerError("explicit account, intent and evidence references required")
    values = [None if value is None else str(_number(value)) for value in
              (cumulative_fill_quantity, cumulative_fill_notional, reserved_buy_notional, submitted_target_pct)]
    quantity, notional, reserved, target = values
    if target is not None and Decimal(target) > 100:
        raise LedgerError("submitted target exceeds 100")
    if (quantity is None) != (notional is None):
        raise LedgerError("cumulative quantity/notional must be supplied together")
    if quantity is not None and ((Decimal(quantity) == 0) != (Decimal(notional) == 0)):
        raise LedgerError("inconsistent cumulative fill quantity/notional")
    if side == "SELL" and reserved is not None and Decimal(reserved) != 0:
        raise LedgerError("sell intent cannot carry a buy reservation")
    source_status = status
    if status in {"PARTIAL", "FILLED"} and (quantity is None or Decimal(quantity) == 0):
        status = "UNKNOWN"
    if _active_buy_unresolved({"side": side, "status": status, "submitted_target_pct": target,
                               "reserved_buy_notional": reserved}):
        status = "UNKNOWN"
    payload = {
        "kind": "account_execution", "campaign_id": _text(campaign_id),
        "execution_profile_ref": _text(execution_profile_ref), "intent_ref": _text(intent_ref),
        "side": side, "status": status, "source_status": source_status, "observed_at": _time(observed_at),
        "evidence_source": _text(evidence_source), "quantity_semantics": "CUMULATIVE_PER_INTENT",
        "cumulative_fill_quantity": quantity, "cumulative_fill_notional": notional,
        "reserved_buy_notional": reserved, "submitted_target_pct": target,
        "confirmed_quantity": quantity if status in {"PARTIAL", "FILLED"} and quantity is not None and Decimal(quantity) > 0 else None,
        "confirmed_price": str(Decimal(notional) / Decimal(quantity)) if status in {"PARTIAL", "FILLED"} and quantity is not None and Decimal(quantity) > 0 else None,
    }
    with ledger._transaction() as db:
        campaign = ledger._get(db, "campaigns", campaign_id)
        applied = ledger._event(db, event_id, payload)
        if applied:
            previous = previous_execution(db, campaign_id, execution_profile_ref, intent_ref)
            db.execute("INSERT INTO executions VALUES (?,?,?)", (event_id, campaign_id, _dump({"event_id": event_id, **payload})))
            if execution_change(previous, payload):
                ledger._notice(db, event_id, campaign_id, "EXECUTION")
        return {**ledger._snapshot(db, campaign["book_id"]), "event_applied": applied}


def reconcile_account_evidence(records, *, campaign_id, execution_profile_ref, coverage_complete):
    """Latest cumulative record per intent, NOT sum of successive poll snapshots.

    Coverage is a caller attestation. Missing final fill/reservation evidence,
    decreasing counters or conflicting equal-clock records block projection.
    Empty history is UNKNOWN, not proof of zero account holdings/orders.
    """
    grouped, unknown = {}, coverage_complete is not True or not _known_ref(execution_profile_ref)
    for record in records:
        if not isinstance(record, dict):
            unknown = True
            continue
        if record.get("campaign_id") != campaign_id or record.get("execution_profile_ref") != execution_profile_ref:
            continue
        intent = record.get("intent_ref")
        if (not _known_ref(intent) or record.get("quantity_semantics") != "CUMULATIVE_PER_INTENT"
                or record.get("status") not in EXECUTION_STATUSES):
            unknown = True
            continue
        try:
            _time(record.get("observed_at"))
            for key in ("cumulative_fill_quantity", "cumulative_fill_notional", "reserved_buy_notional", "submitted_target_pct"):
                if record.get(key) is not None:
                    _number(record[key])
            if record.get("submitted_target_pct") is not None and _number(record["submitted_target_pct"]) > 100:
                raise LedgerError("invalid submitted target")
            q, n = record.get("cumulative_fill_quantity"), record.get("cumulative_fill_notional")
            if (q is None) != (n is None) or (q is not None and ((_number(q) == 0) != (_number(n) == 0))):
                raise LedgerError("inconsistent cumulative evidence")
            if record.get("side") == "SELL" and record.get("reserved_buy_notional") is not None and _number(record["reserved_buy_notional"]) != 0:
                raise LedgerError("invalid sell reservation")
        except (ValueError, TypeError):
            unknown = True
            continue
        grouped.setdefault(intent, []).append(record)
    buy_quantity = sell_quantity = confirmed = reserved = target = Decimal(0)
    for history in grouped.values():
        history = sorted(history, key=lambda r: (_time(r["observed_at"]), r.get("event_id", "")))
        previous_quantity = previous_notional = previous_target = Decimal(0)
        side, last_clock, last_signature = None, None, None
        for record in history:
            if record.get("side") not in {"BUY", "SELL"} or (side is not None and side != record["side"]):
                unknown = True
            side = record.get("side")
            clock = _time(record["observed_at"])
            signature = tuple(record.get(k) for k in ("side", "status", "cumulative_fill_quantity", "cumulative_fill_notional", "reserved_buy_notional", "submitted_target_pct"))
            if clock == last_clock and signature != last_signature:
                unknown = True
            last_clock, last_signature = clock, signature
            if record.get("submitted_target_pct") is not None:
                submitted = _number(record["submitted_target_pct"])
                if submitted < previous_target:
                    unknown = True
                previous_target = max(previous_target, submitted)
            if record.get("cumulative_fill_quantity") is not None and record.get("cumulative_fill_notional") is not None:
                quantity, notional = _number(record["cumulative_fill_quantity"]), _number(record["cumulative_fill_notional"])
                if quantity < previous_quantity or notional < previous_notional:
                    unknown = True
                previous_quantity, previous_notional = max(previous_quantity, quantity), max(previous_notional, notional)
        latest = history[-1]
        if (latest.get("status") == "UNKNOWN" or _active_buy_unresolved(latest) or not _known_ref(latest.get("evidence_source"))
                or latest.get("cumulative_fill_quantity") is None or latest.get("cumulative_fill_notional") is None
                or latest.get("reserved_buy_notional") is None or latest.get("submitted_target_pct") is None
                or (latest.get("status") in {"PARTIAL", "FILLED"} and previous_quantity == 0)):
            unknown = True
        if side == "BUY":
            buy_quantity += previous_quantity
            confirmed += previous_notional
            reserved += _number(latest["reserved_buy_notional"]) if latest.get("reserved_buy_notional") is not None else 0
            target = max(target, previous_target)
        elif side == "SELL":
            sell_quantity += previous_quantity
    unknown = unknown or not grouped or sell_quantity > buy_quantity
    return {"execution_profile_ref": execution_profile_ref, "campaign_id": campaign_id,
            "coverage_complete": coverage_complete is True, "unknown_execution": unknown,
            "confirmed_buy_notional": None if unknown else str(confirmed),
            "reserved_buy_notional": None if unknown else str(reserved),
            "previously_submitted_target_pct": None if unknown else str(target),
            "residual_quantity": None if unknown else str(buy_quantity - sell_quantity),
            "known_buy_quantity": str(buy_quantity), "known_sell_quantity": str(sell_quantity),
            "quantity_semantics": "LATEST_CUMULATIVE_PER_INTENT"}


def project_account_from_evidence(snapshot, campaign_id, *, account_evidence,
                                  account_unit_budget, limit_price):
    """Pure overlay adapter; no account amount changes strategy allocation."""
    if snapshot.get("mode") not in {"SHADOW", "VALIDATION", "VALIDATION_ONLY"}:
        return {"status": "BLOCKED", "quantity": 0, "no_order": True, "reason": "NON_VALIDATION_BOOK"}
    campaign = next((c for c in snapshot["campaigns"] if c["campaign_id"] == campaign_id), None)
    if campaign is None:
        raise LedgerError("unknown campaign")
    if campaign["status"] == "CLOSED" or campaign["add_permission"] != "AVAILABLE":
        return {"status": "BLOCKED", "quantity": 0, "no_order": True, "reason": "STRATEGY_NOT_ADDABLE"}
    required = {"confirmed_buy_notional", "reserved_buy_notional", "previously_submitted_target_pct", "unknown_execution", "execution_profile_ref"}
    if (not isinstance(account_evidence, dict) or not required <= account_evidence.keys()
            or account_evidence.get("campaign_id") != campaign_id or account_evidence.get("coverage_complete") is not True
            or type(account_evidence.get("unknown_execution")) is not bool
            or not _known_ref(account_evidence.get("execution_profile_ref"))):
        return {"status": "BLOCKED", "quantity": 0, "no_order": True, "reason": "INCOMPLETE_ACCOUNT_EVIDENCE"}
    if account_unit_budget is None:
        return {"status": "BLOCKED", "quantity": 0, "no_order": True, "reason": "MISSING_ACCOUNT_BUDGET"}
    return project_account_target(
        execution_profile_ref=account_evidence["execution_profile_ref"], account_unit_budget=account_unit_budget,
        target_pct=campaign["target_pct"], limit_price=limit_price,
        confirmed_buy_notional=account_evidence["confirmed_buy_notional"],
        reserved_buy_notional=account_evidence["reserved_buy_notional"],
        previously_submitted_target_pct=account_evidence["previously_submitted_target_pct"],
        unknown_execution=account_evidence["unknown_execution"],
    )
