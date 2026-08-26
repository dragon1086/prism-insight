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
- `prism-backend`: resource-limited ClickStack container.
- ClickStack UI: backend localhost `18080`, proxied by authenticated Nginx `8443`.
- OTLP/HTTP: backend localhost `14318`, reachable from db-server only through SSH.

The tunnel target is supplied outside Git through
`/etc/prism-observability/tunnel.env` as `PRISM_BACKEND_HOST=...`.

## Initial events

- `deployment.applied`
- `trigger.performance_feedback`

Decision, gate, execution, and position-outcome events are added incrementally
after the end-to-end transport and resource envelope are verified.

## Rollback

1. Stop and disable `prism-observability-shipper` and tunnel units.
2. The trading pipeline continues; local event appends remain harmless.
3. Stop ClickStack with `docker compose down` without deleting its volumes.
4. Remove the Nginx `8443` site and firewall rule if external UI access is no
   longer needed.
