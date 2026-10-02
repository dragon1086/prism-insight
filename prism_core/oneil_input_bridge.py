"""Compose add-only inputs; never gate or replace the independent protection loop."""

from copy import deepcopy

from prism_core.oneil_adaptive_policy import (
    V1_VERSION,
    _hash,
    _num,
    _time,
    _validate,
    evidence_version,
)


def assemble_evidence(
    *, plan, setup_input, intraday_input, quote, gates, now, source="mechanical"
):
    result = dict(
        status="MISSING",
        reason_codes=[],
        evidence=None,
        execution_authorized=False,
        independent_protection_required=True,
    )

    def fail(code):
        return dict(result, reason_codes=[code])

    try:
        _validate(plan)
        current = _time(now)
        if (
            setup_input.get("status") != "OK"
            or setup_input.get("setup") != plan["setup"]
        ):
            return fail("SETUP_NOT_BOUND_TO_FROZEN_PLAN")
        if (
            intraday_input.get("status") != "OK"
            or intraday_input.get("kind") != "LIVE_CAPTURE"
            or intraday_input.get("usable_for_prospective") is not True
        ):
            return fail("NO_PROSPECTIVE_INTRADAY_INPUT")
        if (
            intraday_input["symbol"] != plan["symbol"]
            or intraday_input["price_basis_ref"] != plan["setup"]["price_basis_ref"]
        ):
            return fail("INTRADAY_IDENTITY_MISMATCH")
        if plan["policy_version"] == V1_VERSION and intraday_input.get("volume") is None:
            # Frozen v1 plans keep the strict matched-volume input contract.
            return fail("MATCHED_VOLUME_REQUIRED_FOR_V1")
        as_of = _time(intraday_input["as_of"])
        if (
            not as_of <= _time(intraday_input["retrieved_at"]) <= current
            or (current - as_of).total_seconds() > 600
        ):
            return fail("INTRADAY_CLOCK_INVALID")
        if not isinstance(quote, dict) or not isinstance(gates, dict):
            return fail("CURRENT_QUOTE_OR_GATES_MISSING")
        q = {k: quote[k] for k in ("price", "observed_at", "source_ref")}
        g = {
            k: gates[k]
            for k in (
                "observed_at",
                "source_ref",
                "admission",
                "risk",
                "RR",
                "sector",
                "slot",
                "market_pulse",
                "regime",
            )
        }
        _num(q["price"], True)
        if (
            not as_of <= _time(q["observed_at"]) <= current
            or (current - _time(q["observed_at"])).total_seconds() > 120
        ):
            return fail("QUOTE_CLOCK_INVALID")
        if not 0 <= (current - _time(g["observed_at"])).total_seconds() <= 120:
            return fail("GATE_CLOCK_INVALID")
        if (
            source not in {"regular", "mechanical"}
            or not q["source_ref"]
            or not g["source_ref"]
        ):
            return fail("CURRENT_SOURCE_MISSING")
        facts = dict(
            contract_version=evidence_version(plan),
            symbol=plan["symbol"],
            source=source,
            price_basis_ref=plan["setup"]["price_basis_ref"],
            quote=q,
            gates=g,
            source_ref=_hash([plan["plan_hash"], intraday_input["input_hash"], q, g]),
        )
        facts.update(
            {
                k: deepcopy(intraday_input[k])
                for k in ("bars", "volume", "market_window")
            }
        )
        if plan["policy_version"] != V1_VERSION:
            # B3 (v2/v3-ae): absent trend stays absent; the policy blocks adds, not the first entry.
            facts["trend"] = deepcopy(intraday_input.get("trend"))
        return dict(
            result,
            status="OK",
            evidence=facts,
            authority="CALLER_ATTESTED_NOT_AUTHENTICATED",
        )
    except (KeyError, ValueError, TypeError, AttributeError):
        return fail("REQUIRED_INPUT_INVALID_OR_MISSING")
