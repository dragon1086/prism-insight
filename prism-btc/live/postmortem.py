# live/postmortem.py — LLM 부검 게이트웨이 (학습 기어의 해석 레이어)
#
# 위치: 주문 경로 완전 밖. journal.py 가 추출한 결정적 facts 만 입력받아
# 해석(JSON)을 돌려준다. 이 모듈은 DB 를 직접 만지지 않는다.
#
# 프로바이더 체인 (전부 실패해도 트레이딩은 무관 — facts 는 이미 저장됨):
#   1. anthropic SDK  (ANTHROPIC_API_KEY 가 있고 SDK 설치 시)
#   2. claude CLI     (`claude -p`, Claude Code 구독 인증 재사용)
#   3. PostmortemUnavailable — journal 이 보류 상태 유지, 다음 틱 재시도
#
# 프롬프트 계약 (톱니바퀴 맞물림의 핵심):
#   - 숫자는 facts 에 있는 것만 인용 가능. 새 숫자 생성 금지.
#   - 전략 룰은 동결 — 변경 제안은 반드시 '검증 가능한 가설' 형태
#     (suggested_backtest 필드)로만. LLM 이 룰을 직접 바꿀 길은 없다.
#   - 출력은 고정 JSON 스키마. 파싱 실패 = 부검 실패 (재시도).
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from typing import Optional

log = logging.getLogger("live.postmortem")

# E2E 실측: 러너(+8R) 부검은 113s 소요 — 120s 는 경계선이라 180s 기본.
LLM_TIMEOUT_SEC = int(os.environ.get("BTC_POSTMORTEM_TIMEOUT_SEC", "180"))
DEFAULT_MODEL = os.environ.get("BTC_POSTMORTEM_MODEL", "sonnet")
_CLAUDE_FALLBACK = os.path.expanduser("~/.local/bin/claude")

def _strategy_brief() -> str:
    """동결 전략 요약 — LLM 에게 주는 불변 컨텍스트 (룰 전문이 아닌 의도 요약).

    ⚠ 요약이 부정확하면 LLM 이 가짜 이상징후를 보고한다 (E2E 검증에서 실증:
    게이트를 '4h/1d'로 잘못 적자 정상 진입을 '게이트 미충족 의심'으로 부검함).
    그래서 게이트 상수는 engine/config.py 에서 동적으로 가져온다 — 드리프트 불가.
    """
    try:
        from engine.config import ENTRY_SCORE_MIN, TS_MIN, TS_GATE_TFS
        gate = (f"멀티TF 정렬 score>={ENTRY_SCORE_MIN:.0f} + "
                f"추세강도>={TS_MIN:.1f} 게이트(적용 TF: {'/'.join(TS_GATE_TFS)} 만 — "
                f"그 외 TF 는 게이트 아님)")
    except Exception:  # noqa: BLE001 — config 임포트 불가 환경(단독 테스트 등)
        gate = "멀티TF 정렬 + 추세강도 게이트 (상수는 engine/config.py 참조)"
    return f"""\
[동결 전략 v3 — BTCUSDT 스윙 추세추종 (변경 금지, 평가 기준일 뿐)]
- 진입: {gate}, 롱/숏, 3트랜치(40/30/30) 피라미딩
- 청산: TP1(1R, 1/3) -> 1.5R 도달시 BE/트레일 활성 -> 12h MA10 트레일 무제한 (TP2/3 없음)
- 리스크: 트레이드당 고정 R, 레버 8~12x + 변동성 천장, 실펀딩 부담
- 6년 기대치: 손익비 2.29 / 승률 54% / PF 2.46 / 월 1~2회
- 설계 의도: 추세 꼬리를 길게 먹는다. 잦은 손절(-1R)은 정상 비용이며,
  소수의 +5R 이상 러너가 전체 수익을 만든다."""


def _tunables_menu() -> str:
    """자동 검증 가능한 '손잡이 메뉴' — LLM 가설을 화이트리스트로 유도.

    현재값은 런타임 모듈 속성에서 읽는다 (활성 오버라이드 반영된 유효값).
    """
    try:
        from research.overrides import TUNABLES
        import importlib
        lines = []
        for name, t in TUNABLES.items():
            mod = importlib.import_module(t.targets[0][0])
            cur = getattr(mod, t.targets[0][1], t.frozen)
            rng = (f"{t.lo}~{t.hi}" if t.kind == "float"
                   else "|".join(t.choices))
            lines.append(f"- {name} (현재 {cur}, 허용 {rng}) — {t.note}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001 — 메뉴 생성 실패 시에도 부검은 진행
        return "(메뉴 로드 실패 — testable 가설 생성 불가, 관찰만 기록하라)"


class PostmortemUnavailable(RuntimeError):
    """LLM 프로바이더가 하나도 없음 — 보류(재시도 카운트 미증가) 신호."""


class PostmortemFailed(RuntimeError):
    """LLM 은 응답했으나 계약 위반(파싱/스키마 실패) — 재시도 카운트 증가."""


# ---------------------------------------------------------------------------
# 프로바이더
# ---------------------------------------------------------------------------

def _try_anthropic_sdk(prompt: str) -> Optional[str]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic  # noqa: PLC0415
    except ImportError:
        return None
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=os.environ.get("BTC_POSTMORTEM_SDK_MODEL", "claude-sonnet-4-6"),
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
        timeout=LLM_TIMEOUT_SEC,
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def _try_claude_cli(prompt: str) -> Optional[str]:
    exe = shutil.which("claude") or (
        _CLAUDE_FALLBACK if os.path.exists(_CLAUDE_FALLBACK) else None)
    if exe is None:
        return None
    # 중첩 세션 가드: Claude Code 세션 안에서 호출돼도 독립 단발 실행이 되도록
    # CLAUDE* 환경변수를 제거한다 (데몬 환경에서는 원래 없음 — 무해).
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    # -p(print) 모드, 프롬프트는 stdin — 도구 없이 단발 응답.
    proc = subprocess.run(
        [exe, "-p", "--output-format", "json", "--model", DEFAULT_MODEL],
        input=prompt, capture_output=True, text=True, timeout=LLM_TIMEOUT_SEC,
        env=env,
    )
    if proc.returncode != 0:
        raise PostmortemFailed(
            f"claude cli rc={proc.returncode}: "
            f"stderr={proc.stderr[:200]!r} stdout={proc.stdout[:200]!r}")
    try:
        envelope = json.loads(proc.stdout)
        return envelope.get("result", "")
    except json.JSONDecodeError:
        return proc.stdout  # 구버전 CLI 등 — 원문 그대로 파서에 넘긴다


def _call_llm(prompt: str) -> tuple[str, str]:
    """(응답 텍스트, 프로바이더명). 모든 프로바이더 부재 시 PostmortemUnavailable."""
    text = _try_anthropic_sdk(prompt)
    if text is not None:
        return text, "anthropic-sdk"
    text = _try_claude_cli(prompt)
    if text is not None:
        return text, "claude-cli"
    raise PostmortemUnavailable("no LLM provider (anthropic SDK / claude CLI)")


# ---------------------------------------------------------------------------
# JSON 계약 파서
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """응답에서 첫 최상위 JSON 객체 추출. 코드펜스/잡담 내성."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = [fence.group(1)] if fence else []
    brace = text.find("{")
    if brace != -1:
        candidates.append(text[brace:text.rfind("}") + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    raise PostmortemFailed(f"no parseable JSON in response: {text[:200]!r}")


def _validate_analysis(obj: dict) -> dict:
    required = ["situation_analysis", "judgment_evaluation", "one_line_summary"]
    missing = [k for k in required if k not in obj]
    if missing:
        raise PostmortemFailed(f"analysis missing keys: {missing}")
    try:
        obj["confidence_score"] = min(1.0, max(0.0, float(obj.get("confidence_score", 0.5))))
    except (TypeError, ValueError):
        obj["confidence_score"] = 0.5
    if not isinstance(obj.get("lessons"), list):
        obj["lessons"] = []
    if not isinstance(obj.get("pattern_tags"), list):
        obj["pattern_tags"] = []
    return obj


# ---------------------------------------------------------------------------
# 트레이드 부검
# ---------------------------------------------------------------------------

def _build_prompt(facts: dict, active_lessons: list) -> str:
    lessons_txt = "\n".join(
        f"- [{l['status']}/{l['category']}] {l['lesson']}" for l in active_lessons
    ) or "(아직 없음)"
    return f"""너는 시스템 트레이딩 부검 분석가다. 아래 종결 트레이드 1건을 부검하라.

{_strategy_brief()}

[절대 규칙]
1. 숫자는 아래 facts 에 있는 값만 인용한다. 새로운 숫자를 계산하거나 만들지 마라.
2. 전략 룰은 동결 상태다. 룰 변경 아이디어는 lessons 의 suggested_backtest 로만
   제안할 수 있고, 그 형식은 반드시 아래 '손잡이 메뉴'의 JSON 객체
   {{"param": "<메뉴의 이름>", "value": <허용범위 내 값>}} 하나다.
   메뉴 밖 아이디어(구조 변경 등)는 testable=false 로 text 에만 적어라 —
   그것은 사람 리뷰 대기열로 가고, 메뉴 안 가설만 자동 검증된다.
3. -1R 손절 자체는 실패가 아니다 (승률 54% 전략의 정상 비용).
   평가 대상은 "시스템이 설계 의도대로 작동했는가"와 "집행 품질"이다.
4. 출력은 아래 JSON 스키마 하나만. 다른 텍스트 금지. 모든 값은 한국어.

[손잡이 메뉴 — 자동 백테스트 검증 가능한 유일한 변경 통로]
{_tunables_menu()}

[결정적 사실 (숫자의 유일한 출처)]
{json.dumps(facts, ensure_ascii=False, indent=1)}

[기존 활성 교훈 (중복 교훈 재생성 금지)]
{lessons_txt}

[출력 JSON 스키마]
{{
  "situation_analysis": "진입~청산 구간에서 무슨 일이 있었나 (facts 의 entry/exit_context, excursion 근거)",
  "judgment_evaluation": "시스템이 설계 의도대로 작동했나. 기대 분포(baseline) 대비 이 트레이드의 위치",
  "execution_quality": "체결/비용 품질 (r_decomposition 의 fee_r, funding_r, self_check_residual 근거)",
  "lessons": [
    {{"category": "entry|exit|sizing|regime|execution",
      "text": "교훈 한 문장",
      "testable": true,
      "suggested_backtest": {{"param": "손잡이 메뉴의 이름", "value": 2.5}}
    }}
  ],
  "pattern_tags": ["짧은 태그"],
  "one_line_summary": "한 줄 요약",
  "confidence_score": 0.0
}}"""


def analyze(facts: dict, active_lessons: Optional[list] = None) -> tuple[dict, str, int]:
    """facts → (해석 JSON, 프로바이더, 소요 ms). DB 비접촉."""
    prompt = _build_prompt(facts, active_lessons or [])
    t0 = time.monotonic()
    text, provider = _call_llm(prompt)
    ms = int((time.monotonic() - t0) * 1000)
    analysis = _validate_analysis(_extract_json(text))
    return analysis, provider, ms


# ---------------------------------------------------------------------------
# 주간 기억압축
# ---------------------------------------------------------------------------

def _build_weekly_prompt(entries: list, lessons: list) -> str:
    compact = [
        {"trade_id": e["trade_id"],
         "one_line": e.get("one_line"),
         "net_r": e["facts"].get("r_decomposition", {}).get("net_r"),
         "exit_reason": e["facts"].get("identity", {}).get("exit_reason"),
         "capture_ratio": e["facts"].get("excursion", {}).get("capture_ratio")}
        for e in entries
    ]
    lessons_txt = "\n".join(
        f"- [{l['status']}/{l['category']}] {l['lesson']}" for l in lessons
    ) or "(없음)"
    return f"""너는 시스템 트레이딩 연구 책임자다. 지난 7일의 매매일지와 누적 교훈을 압축해
"백테스트로 검증 가능한 가설"만 추려라.

{_strategy_brief()}

[절대 규칙]
1. 자동 검증 가능한 가설은 suggested_backtest 를 아래 '손잡이 메뉴'의
   {{"param": "<이름>", "value": <허용범위 내 값>}} JSON 객체로 적은 것뿐이다.
   메뉴 밖 아이디어는 testable=false 로 text 에만 (사람 리뷰 대기열).
2. 단일 트레이드의 우연을 일반화하지 마라 — 반복 관찰된 패턴만 가설로 승격.
3. 룰 변경은 여기서 일어나지 않는다. 가설 생성까지만이 너의 권한이다.
4. 출력은 JSON 하나만. 한국어.

[손잡이 메뉴 — 자동 백테스트 검증 가능한 유일한 변경 통로]
{_tunables_menu()}

[지난 7일 일지 요약]
{json.dumps(compact, ensure_ascii=False, indent=1)}

[누적 활성 교훈]
{lessons_txt}

[출력 JSON 스키마]
{{
  "summary": "이번 주 트레이딩 시스템 동작 한 단락 요약",
  "hypotheses": [
    {{"category": "entry|exit|sizing|regime|execution",
      "text": "가설 한 문장",
      "testable": true,
      "suggested_backtest": {{"param": "손잡이 메뉴의 이름", "value": 2.5}}
    }}
  ],
  "retire_suggestions": ["더 이상 유효하지 않아 보이는 기존 교훈 (있다면)"]
}}"""


def weekly_compress(entries: list, lessons: list) -> tuple[dict, str, int]:
    prompt = _build_weekly_prompt(entries, lessons)
    t0 = time.monotonic()
    text, provider = _call_llm(prompt)
    ms = int((time.monotonic() - t0) * 1000)
    obj = _extract_json(text)
    if not isinstance(obj.get("hypotheses"), list):
        obj["hypotheses"] = []
    return obj, provider, ms
