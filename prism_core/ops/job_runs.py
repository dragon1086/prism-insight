"""Durable job ownership and run-state records for the local ops database."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from uuid import uuid4

from prism_core.storage.database import transaction


@dataclass(frozen=True)
class LeaseClaim:
    """Result of one atomic attempt to own a job key."""

    acquired: bool
    lease_id: str
    job_key: str
    owner_id: str
    acquired_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class JobRunRecord:
    run_id: str
    job_key: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    payload_json: str


@dataclass(frozen=True)
class HealthAlertRecord:
    alert_id: str
    job_key: str
    state: str
    message: str
    created_at: datetime
    run_id: str | None


class JobRunStore:
    """Short-transaction repository over the versioned ``ops.sqlite`` schema."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def acquire_lease(
        self,
        *,
        job_key: str,
        owner_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> LeaseClaim:
        job_key = _identifier(job_key, "job_key")
        owner_id = _identifier(owner_id, "owner_id")
        current = _utc(now, "now")
        if not isinstance(lease_duration, timedelta) or lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be a positive timedelta")
        expires_at = current + lease_duration

        with transaction(self._connection):
            row = self._connection.execute(
                "SELECT lease_id, owner_id, acquired_at, expires_at "
                "FROM leases WHERE job_key = ?",
                (job_key,),
            ).fetchone()
            if row is not None and _stored_datetime(row[3]) > current:
                return LeaseClaim(
                    acquired=False,
                    lease_id=row[0],
                    job_key=job_key,
                    owner_id=row[1],
                    acquired_at=_stored_datetime(row[2]),
                    expires_at=_stored_datetime(row[3]),
                )

            if row is not None:
                self._connection.execute(
                    "UPDATE job_runs SET status = 'ABANDONED', finished_at = ? "
                    "WHERE job_key = ? AND status = 'RUNNING'",
                    (_iso(current), job_key),
                )

            lease_id = str(uuid4())
            values = (
                lease_id,
                job_key,
                owner_id,
                _iso(current),
                _iso(expires_at),
                _iso(current),
            )
            self._connection.execute(
                "INSERT INTO leases "
                "(lease_id, job_key, owner_id, acquired_at, expires_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(job_key) DO UPDATE SET "
                "lease_id=excluded.lease_id, owner_id=excluded.owner_id, "
                "acquired_at=excluded.acquired_at, expires_at=excluded.expires_at, "
                "updated_at=excluded.updated_at",
                values,
            )

        return LeaseClaim(
            acquired=True,
            lease_id=lease_id,
            job_key=job_key,
            owner_id=owner_id,
            acquired_at=current,
            expires_at=expires_at,
        )

    def start_run(
        self,
        *,
        run_id: str,
        lease: LeaseClaim,
        now: datetime,
        payload: Mapping[str, Any],
    ) -> JobRunRecord:
        run_id = _identifier(run_id, "run_id")
        current = _utc(now, "now")
        if not isinstance(lease, LeaseClaim) or not lease.acquired:
            raise ValueError("start_run requires a newly acquired lease")
        payload_json = _json_object(payload)

        with transaction(self._connection):
            _require_active_lease(self._connection, lease, current)
            row = self._connection.execute(
                "SELECT run_id, job_key, status, started_at, finished_at, payload_json "
                "FROM job_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                self._connection.execute(
                    "INSERT INTO job_runs "
                    "(run_id, job_key, status, started_at, finished_at, payload_json, created_at) "
                    "VALUES (?, ?, 'RUNNING', ?, NULL, ?, ?)",
                    (run_id, lease.job_key, _iso(current), payload_json, _iso(current)),
                )
                row = (
                    run_id,
                    lease.job_key,
                    "RUNNING",
                    _iso(current),
                    None,
                    payload_json,
                )
            elif row[1] != lease.job_key or row[5] != payload_json:
                raise ValueError("run_id is already bound to different immutable input")
        return _run_record(row)

    def heartbeat(
        self,
        *,
        run_id: str,
        lease: LeaseClaim,
        observed_at: datetime,
        lease_duration: timedelta,
        payload: Mapping[str, Any],
    ) -> LeaseClaim:
        run_id = _identifier(run_id, "run_id")
        current = _utc(observed_at, "observed_at")
        if not isinstance(lease_duration, timedelta) or lease_duration <= timedelta(0):
            raise ValueError("lease_duration must be a positive timedelta")
        payload_json = _json_object(payload)
        expires_at = current + lease_duration

        with transaction(self._connection):
            _require_active_lease(self._connection, lease, current)
            run = self._connection.execute(
                "SELECT job_key, status FROM job_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None or run[0] != lease.job_key or run[1] != "RUNNING":
                raise RuntimeError("heartbeat requires a running job owned by the lease")
            self._connection.execute(
                "UPDATE leases SET expires_at = ?, updated_at = ? "
                "WHERE lease_id = ? AND job_key = ? AND owner_id = ?",
                (
                    _iso(expires_at),
                    _iso(current),
                    lease.lease_id,
                    lease.job_key,
                    lease.owner_id,
                ),
            )
            self._connection.execute(
                "INSERT INTO heartbeats "
                "(heartbeat_id, run_id, observed_at, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(uuid4()), run_id, _iso(current), payload_json, _iso(current)),
            )

        return LeaseClaim(
            acquired=True,
            lease_id=lease.lease_id,
            job_key=lease.job_key,
            owner_id=lease.owner_id,
            acquired_at=lease.acquired_at,
            expires_at=expires_at,
        )

    def finish_run(
        self,
        *,
        run_id: str,
        lease: LeaseClaim,
        finished_at: datetime,
        succeeded: bool,
    ) -> JobRunRecord:
        run_id = _identifier(run_id, "run_id")
        current = _utc(finished_at, "finished_at")
        if type(succeeded) is not bool:
            raise ValueError("succeeded must be a boolean")
        status = "SUCCESS" if succeeded else "ERROR"

        with transaction(self._connection):
            _require_active_lease(self._connection, lease, current)
            changed = self._connection.execute(
                "UPDATE job_runs SET status = ?, finished_at = ? "
                "WHERE run_id = ? AND job_key = ? AND status = 'RUNNING'",
                (status, _iso(current), run_id, lease.job_key),
            ).rowcount
            if changed != 1:
                raise RuntimeError("finish_run requires the owned running job")
            self._connection.execute(
                "DELETE FROM leases "
                "WHERE lease_id = ? AND job_key = ? AND owner_id = ?",
                (lease.lease_id, lease.job_key, lease.owner_id),
            )
            row = self._connection.execute(
                "SELECT run_id, job_key, status, started_at, finished_at, payload_json "
                "FROM job_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:  # pragma: no cover - protected by the update transaction
            raise RuntimeError("completed run disappeared")
        return _run_record(row)

    def last_success(self, job_key: str) -> datetime | None:
        job_key = _identifier(job_key, "job_key")
        row = self._connection.execute(
            "SELECT finished_at FROM job_runs "
            "WHERE job_key = ? AND status = 'SUCCESS' "
            "ORDER BY finished_at DESC LIMIT 1",
            (job_key,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return _stored_datetime(row[0])

    def last_heartbeat(self, job_key: str) -> datetime | None:
        job_key = _identifier(job_key, "job_key")
        row = self._connection.execute(
            "SELECT heartbeats.observed_at FROM heartbeats "
            "JOIN job_runs ON job_runs.run_id = heartbeats.run_id "
            "WHERE job_runs.job_key = ? "
            "ORDER BY heartbeats.observed_at DESC LIMIT 1",
            (job_key,),
        ).fetchone()
        if row is None:
            return None
        return _stored_datetime(row[0])

    def latest_running_activity(self, job_key: str) -> datetime | None:
        job_key = _identifier(job_key, "job_key")
        started = self._connection.execute(
            "SELECT started_at FROM job_runs "
            "WHERE job_key = ? AND status = 'RUNNING' "
            "ORDER BY started_at DESC LIMIT 1",
            (job_key,),
        ).fetchone()
        heartbeat = self._connection.execute(
            "SELECT heartbeats.observed_at FROM heartbeats "
            "JOIN job_runs ON job_runs.run_id = heartbeats.run_id "
            "WHERE job_runs.job_key = ? AND job_runs.status = 'RUNNING' "
            "ORDER BY heartbeats.observed_at DESC LIMIT 1",
            (job_key,),
        ).fetchone()
        candidates = [
            _stored_datetime(row[0])
            for row in (started, heartbeat)
            if row is not None
        ]
        return max(candidates, default=None)

    def record_health_alert(
        self,
        *,
        job_key: str,
        state: str,
        message: str,
        created_at: datetime,
        run_id: str | None = None,
    ) -> HealthAlertRecord:
        job_key = _identifier(job_key, "job_key")
        if state not in {"ERROR", "RECOVERY"}:
            raise ValueError("health alert state must be ERROR or RECOVERY")
        if not isinstance(message, str) or not message.strip() or len(message) > 512:
            raise ValueError("health alert message must contain 1 to 512 characters")
        current = _utc(created_at, "created_at")
        normalized_run_id = None if run_id is None else _identifier(run_id, "run_id")
        alert_id = str(uuid4())
        payload_json = _json_object(
            {"job_key": job_key, "message": message.strip(), "state": state}
        )
        with transaction(self._connection):
            self._connection.execute(
                "INSERT INTO alerts "
                "(alert_id, run_id, severity, alert_kind, payload_json, created_at) "
                "VALUES (?, ?, ?, 'JOB_HEALTH', ?, ?)",
                (alert_id, normalized_run_id, state, payload_json, _iso(current)),
            )
        return HealthAlertRecord(
            alert_id=alert_id,
            job_key=job_key,
            state=state,
            message=message.strip(),
            created_at=current,
            run_id=normalized_run_id,
        )

    def record_health_delivery(
        self,
        *,
        alert: HealthAlertRecord,
        channel: str,
        created_at: datetime,
    ) -> None:
        if not isinstance(alert, HealthAlertRecord):
            raise ValueError("alert must be a health alert record")
        if channel not in {"TELEGRAM", "MACOS"}:
            raise ValueError("health delivery channel must be TELEGRAM or MACOS")
        current = _utc(created_at, "created_at")
        payload_json = _json_object(
            {
                "alert_id": alert.alert_id,
                "channel": channel,
                "job_key": alert.job_key,
                "state": alert.state,
            }
        )
        with transaction(self._connection):
            self._connection.execute(
                "INSERT INTO alerts "
                "(alert_id, run_id, severity, alert_kind, payload_json, created_at) "
                "VALUES (?, ?, ?, 'JOB_HEALTH_DELIVERY', ?, ?)",
                (str(uuid4()), alert.run_id, alert.state, payload_json, _iso(current)),
            )

    def latest_health_state(self, job_key: str) -> str | None:
        job_key = _identifier(job_key, "job_key")
        rows = self._connection.execute(
            "SELECT payload_json FROM alerts "
            "WHERE alert_kind = 'JOB_HEALTH_DELIVERY' "
            "ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row[0])
            except (TypeError, ValueError):
                continue
            if payload.get("job_key") == job_key and payload.get("state") in {
                "ERROR",
                "RECOVERY",
            }:
                return str(payload["state"])
        return None


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:-]{0,254}", value
    ) is None:
        raise ValueError(f"{field_name} has invalid identifier syntax")
    return value


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _stored_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return _utc(parsed, "stored datetime")


def _json_object(payload: object) -> str:
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")
    try:
        encoded = json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must be JSON serializable") from exc
    return encoded


def _require_active_lease(
    connection: sqlite3.Connection, lease: LeaseClaim, now: datetime
) -> None:
    row = connection.execute(
        "SELECT lease_id, owner_id, expires_at FROM leases WHERE job_key = ?",
        (lease.job_key,),
    ).fetchone()
    if (
        row is None
        or row[0] != lease.lease_id
        or row[1] != lease.owner_id
        or _stored_datetime(row[2]) <= now
    ):
        raise RuntimeError("job operation requires the active unexpired lease owner")


def _run_record(row: tuple[object, ...] | sqlite3.Row) -> JobRunRecord:
    return JobRunRecord(
        run_id=str(row[0]),
        job_key=str(row[1]),
        status=str(row[2]),
        started_at=_stored_datetime(str(row[3])),
        finished_at=None if row[4] is None else _stored_datetime(str(row[4])),
        payload_json=str(row[5]),
    )
