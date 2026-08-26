# PRISM Trading Observatory

## Contract

Trading processes append versioned JSON events to `logs/prism_events.jsonl`.
The append is fail-open and performs no network I/O. A separate shipper converts
events to OTLP/HTTP and advances its checkpoint only after a successful batch.

Required correlation fields:

- `event_id`, `event_type`, `timestamp`
- `trace_id`, `span_id`, optional `parent_event_id`
- optional `decision_id`, `position_id`
- `git_sha`, `policy_version`, `config_hash`
- `market`, `ticker`, `service`, `environment`

Sensitive attribute keys such as account, token, password, secret, cookie, and
authorization are replaced with `[REDACTED]` before the local append.

## Failure boundary

- ClickStack, network, tunnel, or shipper failure never blocks trading.
- Failed batches do not advance the checkpoint.
- One corrupt JSONL record is skipped and cannot block later records.
- ClickStack is not queried from any buy, sell, hard-stop, or fill path.

## Deployment topology

- `db-server`: PRISM pipeline, JSONL spool, shipper, SSH local-forward tunnel.
- `prism-backend`: resource-limited ClickStack container and an independent,
  token-authenticated ClickStack OTel Collector.
- ClickStack UI: backend localhost `18080`, proxied by authenticated Nginx `8443`.
- OTLP/HTTP: backend localhost `14318`, reachable from db-server only through SSH.

The tunnel target is supplied outside Git through
`/etc/prism-observability/tunnel.env` as `PRISM_BACKEND_HOST=...`.
The ingestion token is supplied outside Git through
`/etc/prism-observability/clickstack.env` as `OTLP_AUTH_TOKEN=...` and is also
configured on db-server as `PRISM_OBSERVABILITY_OTLP_TOKEN`.
The shipper sends this exact value in the `Authorization` header, matching the
ClickStack static bearer-token extension contract.
db-server loads the value from `/etc/prism-observability/shipper.env`, which
must remain mode `0600`.
The same environment file holds the dedicated `prism_otel` ClickHouse password;
only its SHA-256 hash is written to the mounted ClickHouse user configuration.
The XML user is restricted to the `default` observability database.
The mounted XML contains no plaintext password and must be readable by the
ClickHouse process (mode `0644`).

## Initial events

- `deployment.applied`
- `trigger.performance_feedback`

Decision, gate, execution, and position-outcome events are added incrementally
after the end-to-end transport and resource envelope are verified.

## Historical baseline

tools/backfill_observability.py backfills only verifiable facts:

- realized KR/US rows from the production trading-history tables
- completed watched-candidate 7/14/30-day outcomes
- recorded regime snapshots
- actual db-server pull timestamps from Git reflog

Every row is marked ingestion_mode=backfill, includes its source table or
reflog provenance, and receives a deterministic event ID. Historical prompts,
gates, and decision traces are not reconstructed.

## Curated dashboard snapshot

tools/export_observability_insights.py aggregates ClickHouse events into a
credential-free JSON snapshot. tools/publish_observability_insights.py
publishes it atomically to app-server every five minutes through the systemd
timer template.

The publisher uses a dedicated SSH identity and the unprivileged prism account
on app-server. Host, port, user, identity path, and destination stay outside Git
in /etc/prism-observability/dashboard-export.env.

The existing dashboard reads /observability_insights.json independently from
its portfolio JSON. Missing or delayed observability data hides only the new
panel and never breaks the existing dashboard.

## Rollback

1. Stop and disable `prism-observability-shipper` and tunnel units.
2. The trading pipeline continues; local event appends remain harmless.
3. Stop ClickStack with `docker compose down` without deleting its volumes.
4. Remove the Nginx `8443` site and firewall rule if external UI access is no
   longer needed.
