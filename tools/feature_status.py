"""
Feature gate runtime status reporter — reads actual .env + crontab to determine
LIVE / SHADOW / OFF state for each feature gate in prism-insight.

Intended-state registry (source of truth): docs/FEATURE_FLAGS.md
This script reports ACTUAL runtime state so it can be cross-checked against that
document.

Usage:
    python tools/feature_status.py           # aligned text table (default)
    python tools/feature_status.py --json    # machine-readable dict
    python tools/feature_status.py --check   # exits non-zero if any gate is OFF
                                             # when it should be LIVE (optional)

READ-ONLY: never writes .env, never places orders, no network calls.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# ── Load .env from project root (best-effort) ────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent

# Make the repo root importable even when invoked as `python tools/feature_status.py`
# (sys.path[0] would be tools/, so `from cores.llm...` in _vision_available would
# fail and wrongly report vision 미가용). Idempotent.
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_dotenv() -> None:
    """Load .env into os.environ if python-dotenv is available; silently skip."""
    env_path = _ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(dotenv_path=env_path, override=False)
    except ImportError:
        # Fallback: manual parse (no dependencies required)
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val


def _get_crontab() -> str:
    """Return crontab -l output; return empty string on any failure."""
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


def _cron_has_script(crontab_text: str, script_name: str) -> bool:
    """Return True if script_name appears in an active (uncommented) cron line."""
    for line in crontab_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if script_name in stripped:
            return True
    return False


def _cron_get_inline_env(crontab_text: str, var_name: str) -> str:
    """Return the value of var_name if it appears as an inline env assignment
    (e.g. ``VAR=value``) on any active (uncommented) crontab line.
    Returns empty string if not found.
    """
    prefix = f"{var_name}="
    for line in crontab_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for token in stripped.split():
            if token.startswith(prefix):
                return token[len(prefix):]
    return ""


# ── Feature definitions ───────────────────────────────────────────────────────
# Each tuple: (feature_id, label_ko, decision_fn)
# decision_fn(env, crontab) -> (state, evidence)
# state: "LIVE" | "SHADOW" | "OFF" | "미스케줄"

def _decide_oauth_llm(env: dict, crontab: str):
    mode = env.get("PRISM_OPENAI_AUTH_MODE", "")
    if mode == "chatgpt_oauth":
        return "LIVE", f"PRISM_OPENAI_AUTH_MODE={mode} (env)"

    # Also check for inline env assignment on active crontab lines
    cron_mode = _cron_get_inline_env(crontab, "PRISM_OPENAI_AUTH_MODE")
    if cron_mode == "chatgpt_oauth":
        return "LIVE", f"PRISM_OPENAI_AUTH_MODE={cron_mode} (crontab inline)"

    # Determine label: API key mode vs fully unset
    effective_mode = mode or cron_mode
    if effective_mode and effective_mode != "chatgpt_oauth":
        return "OFF", f"PRISM_OPENAI_AUTH_MODE={effective_mode} (API 키 모드, chatgpt_oauth 필요)"
    return "OFF", f"PRISM_OPENAI_AUTH_MODE=(unset) (chatgpt_oauth 필요)"


def _decide_loop_a(env: dict, crontab: str):
    # Canonical HARDSTOP_* with deprecated LOOP_A_* alias fallback.
    live = (env.get("HARDSTOP_LIVE") or env.get("LOOP_A_LIVE") or "").lower()
    enabled = (env.get("HARDSTOP_ENABLED") or env.get("LOOP_A_ENABLED") or "true").lower()
    has_cron = _cron_has_script(crontab, "loop_a_hardstop.py")

    if enabled == "false":
        return "OFF", f"HARDSTOP_ENABLED=false (킬스위치 ON)"
    if live == "true" and has_cron:
        return "LIVE", f"HARDSTOP_LIVE=true, cron=있음"
    if live == "true" and not has_cron:
        return "미스케줄", f"HARDSTOP_LIVE=true but cron=없음"
    if live != "true" and has_cron:
        return "SHADOW", f"HARDSTOP_LIVE={live or '(unset)'}, cron=있음"
    return "OFF", f"HARDSTOP_LIVE={live or '(unset)'}, cron=없음"


def _decide_loop_b(env: dict, crontab: str):
    # Canonical TREND_EXIT_* with deprecated LOOP_B_* alias fallback.
    live = (env.get("TREND_EXIT_LIVE") or env.get("LOOP_B_LIVE") or "").lower()
    enabled = (env.get("TREND_EXIT_ENABLED") or env.get("LOOP_B_ENABLED") or "").lower()
    has_cron = _cron_has_script(crontab, "loop_b_trend_exit.py")

    if enabled == "false":
        return "OFF", "TREND_EXIT_ENABLED=false"
    if live == "true" and has_cron:
        return "LIVE", "TREND_EXIT_LIVE=true, cron=있음"
    if live == "true" and not has_cron:
        return "미스케줄", "TREND_EXIT_LIVE=true but cron=없음"
    if not has_cron:
        return "미스케줄", f"cron=없음, TREND_EXIT_LIVE={live or '(unset)'}"
    return "SHADOW", f"TREND_EXIT_LIVE={live or '(unset)'}, cron=있음"


def _decide_loop_c(env: dict, crontab: str):
    # Canonical FILL_CHASER_* with deprecated LOOP_C_* alias fallback.
    live = (env.get("FILL_CHASER_LIVE") or env.get("LOOP_C_LIVE") or "").lower()
    enabled = (env.get("FILL_CHASER_ENABLED") or env.get("LOOP_C_ENABLED") or "").lower()
    has_cron = _cron_has_script(crontab, "loop_c_fill_chaser.py")

    if enabled == "false":
        return "OFF", "FILL_CHASER_ENABLED=false"
    if live == "true" and has_cron:
        return "LIVE", "FILL_CHASER_LIVE=true, cron=있음"
    if live == "true" and not has_cron:
        return "미스케줄", "FILL_CHASER_LIVE=true but cron=없음"
    if not has_cron:
        return "미스케줄", f"cron=없음, FILL_CHASER_LIVE={live or '(unset)'}"
    return "SHADOW", f"FILL_CHASER_LIVE={live or '(unset)'}, cron=있음"


def _decide_vision_pipeline(env: dict, crontab: str):
    vision = env.get("PRISM_FEATURE_VISION", "").lower()
    shadow = env.get("PRISM_VISION_SHADOW", "").lower()
    if vision == "on":
        if shadow == "true":
            return "SHADOW", "PRISM_FEATURE_VISION=on, PRISM_VISION_SHADOW=true"
        return "LIVE", "PRISM_FEATURE_VISION=on, PRISM_VISION_SHADOW=(unset/false)"
    val = vision if vision else "(unset)"
    return "OFF", f"PRISM_FEATURE_VISION={val}"


def _decide_vision_buy_qa(env: dict, crontab: str):
    vision = env.get("PRISM_FEATURE_VISION", "").lower()
    shadow = env.get("PRISM_VISION_SHADOW", "").lower()
    if vision == "on" and shadow == "true":
        return "SHADOW", "PRISM_FEATURE_VISION=on + PRISM_VISION_SHADOW=true"
    if vision == "on" and shadow != "true":
        return "LIVE", "PRISM_FEATURE_VISION=on, PRISM_VISION_SHADOW!=true"
    return "OFF", f"PRISM_FEATURE_VISION={vision or '(unset)'}"


def _vision_available(env: dict) -> bool:
    """Mirror cores.llm.capabilities.vision_available() = vision on + real API key.

    PRISM_FEATURE_VISION is read from the parsed .env dict — this reporter does
    NOT load .env into os.environ, so calling capabilities.vision_available()
    directly would read an empty os.environ and wrongly report 미가용. The real
    API key is resolved via capabilities, which falls back to
    mcp_agent.secrets.yaml when OPENAI_API_KEY is not exported (OAuth mode).
    Returns False if capabilities can't be imported (keeps the reporter robust).
    """
    if env.get("PRISM_FEATURE_VISION", "").lower() != "on":
        return False
    envkey = env.get("OPENAI_API_KEY", "").strip()
    if envkey and envkey != "chatgpt-oauth-placeholder":
        return True
    try:
        from cores.llm.capabilities import _secrets_api_key
        return bool(_secrets_api_key())
    except Exception:
        return False


def _decide_vision_publish(env: dict, crontab: str):
    """S6 subscriber-facing insight-image broadcast.

    The broadcast (cores/llm/features/insight_broadcast.py, wired into the KR/US
    orchestrators) fires only when BOTH gates are true:
      - PRISM_FEATURE_INSIGHT_IMAGE=on  (independent broadcast gate)
      - vision_available()              (PRISM_FEATURE_VISION=on + real API key)
    """
    insight = env.get("PRISM_FEATURE_INSIGHT_IMAGE", "").lower()
    if insight != "on":
        return "OFF", f"PRISM_FEATURE_INSIGHT_IMAGE={insight or '(unset)'}"
    if not _vision_available(env):
        return "OFF", "PRISM_FEATURE_INSIGHT_IMAGE=on but vision 미가용(PRISM_FEATURE_VISION=on + API 키 필요)"
    return "LIVE", "PRISM_FEATURE_INSIGHT_IMAGE=on + vision 가용"


# Registry: (id, korean label, decision function)
FEATURES = [
    ("oauth_llm",        "OAuth LLM 백엔드(ChatGPT 구독)",          _decide_oauth_llm),
    ("loop_a",           "Loop A — 고빈도 하드스톱",                  _decide_loop_a),
    ("loop_b",           "Loop B — 50MA 추세이탈",                   _decide_loop_b),
    ("loop_c",           "Loop C — 미체결 추격",                     _decide_loop_c),
    ("vision_pipeline",  "비전 배관·렌더QA (S1/S2)",                  _decide_vision_pipeline),
    ("vision_buy_qa",    "비전 매수 품질검사 (S3/S3.5)",               _decide_vision_buy_qa),
    ("vision_publish",   "비전 이미지 발행 (S6)",                     _decide_vision_publish),
]


def evaluate_all(env: dict | None = None, crontab: str | None = None) -> list[dict]:
    """Return a list of dicts with keys: id, label, state, evidence."""
    if env is None:
        env = dict(os.environ)
    if crontab is None:
        crontab = _get_crontab()

    results = []
    for feat_id, label, fn in FEATURES:
        try:
            state, evidence = fn(env, crontab)
        except Exception as exc:
            state, evidence = "unknown", f"오류: {exc}"
        results.append({"id": feat_id, "label": label, "state": state, "evidence": evidence})
    return results


# ── Formatters ────────────────────────────────────────────────────────────────

_STATE_EMOJI = {"LIVE": "●", "SHADOW": "◐", "OFF": "○", "미스케줄": "⚠"}


def _print_table(results: list[dict]) -> None:
    col_label = max(len(r["label"]) for r in results) + 2
    col_state = max(len(r["state"]) for r in results) + 2
    header = f"{'기능':<{col_label}} {'상태':<{col_state}} 근거"
    print(header)
    print("─" * (col_label + col_state + 40))
    for r in results:
        mark = _STATE_EMOJI.get(r["state"], " ")
        print(f"{r['label']:<{col_label}} {mark} {r['state']:<{col_state - 2}} {r['evidence']}")


def _print_json(results: list[dict]) -> None:
    out = {r["id"]: {"state": r["state"], "evidence": r["evidence"]} for r in results}
    print(json.dumps(out, ensure_ascii=False, indent=2))


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="prism-insight feature gate runtime status (READ-ONLY)"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--check", action="store_true",
        help="Exit non-zero if any expected-LIVE gate is not LIVE"
    )
    args = parser.parse_args()

    _load_dotenv()
    results = evaluate_all()

    if args.json:
        _print_json(results)
    else:
        _print_table(results)

    if args.check:
        non_live = [r for r in results if r["state"] != "LIVE"]
        if non_live:
            print(
                f"\n[CHECK] {len(non_live)}개 게이트가 LIVE 아님: "
                + ", ".join(r["id"] for r in non_live),
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
