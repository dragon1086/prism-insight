#!/usr/bin/env python3
"""
Weekly Firecrawl Intelligence — 품질 자동 점검

주간 리포트가 "돌긴 도는데 품질이 조용히 무너지는" 상황을 잡는다.
과거 실제로 겪은 실패들이 그대로 체크 항목이 되어 있다:

  - SDK 버전 차이로 검색 옵션이 통째로 무시됨 (최신성 필터 무력화)
  - 응답 형태 변경으로 결과가 정규화 단계에서 전부 버려짐 (27건 -> 1건)
  - 블로그 글의 수치를 인용
  - "제공된 검색 결과에는 없습니다" 같은 자료수집 메타 언급 노출
  - KRX/네이버 조회 실패로 검증 수치가 통째로 빠짐

설계 원칙: **조용하면 정상.** 문제가 있을 때만 텔레그램 알림을 보낸다.
모니터링 대상을 하나 더 늘리는 게 아니라, 기존 주간 크론 뒤에 붙여서
사람이 매주 들여다볼 필요가 없게 만드는 것이 목적이다.

크론 (별도 항목 추가 없이 기존 줄 뒤에 이어붙임):
  0 11 * * 0 cd /root/prism-insight && python weekly_firecrawl_intelligence.py \
      >> logs/weekly_firecrawl_intelligence.log 2>&1; \
      cd /root/prism-insight && python tools/firecrawl_quality_check.py \
      >> logs/firecrawl_quality.log 2>&1

사용:
  python tools/firecrawl_quality_check.py            # 점검 후 이상시 알림
  python tools/firecrawl_quality_check.py --dry-run  # 알림 없이 결과만 출력
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

LOG_PATH = ROOT / "logs" / "weekly_firecrawl_intelligence.log"
REPORT_PATH = ROOT / "logs" / "weekly_firecrawl_last_report.txt"

BOT_TOKEN = os.getenv("OAUTH_ALERT_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
# 공개 구독 채널(TELEGRAM_CHANNEL_ID)로는 절대 보내지 않는다. 운영자 채팅 전용.
ALERT_CHAT_ID = os.getenv("FIRECRAWL_ALERT_CHAT_ID") or os.getenv("SUBSCRIBER_ALERT_CHAT_ID", "")

# 리포트에 노출되면 안 되는 자료수집 메타 표현
_META_PHRASES = (
    "제공된 검색",
    "검색 결과에는",
    "확인되지 않음",
    "확인되지 않습니다",
    "재확인이 필요",
    "검색 결과 내",
)
_BLOG_HOSTS = ("blog.naver.com", "tistory.com", "cafe.naver.com", "brunch.co.kr", "dcinside")

# 임계값 — 정상 실행 실측치(결과 23~28건, 컨텍스트 약 59K자) 대비 여유를 둔 하한
MIN_UNIQUE_RESULTS = 8
MIN_CONTEXT_CHARS = 8000
MIN_REPORT_CHARS = 2000
MIN_DATED_RATIO = 0.15


@dataclass
class Findings:
    failures: list[str] = field(default_factory=list)
    stats: list[str] = field(default_factory=list)

    def fail(self, msg: str) -> None:
        self.failures.append(msg)

    def stat(self, msg: str) -> None:
        self.stats.append(msg)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _tail_run(log: str) -> str:
    """로그에서 가장 최근 실행분만 잘라낸다 (로그는 append-only)."""
    marker = "Weekly intelligence period:"
    idx = log.rfind(marker)
    return log[idx:] if idx != -1 else log


def check_log(run_log: str, f: Findings) -> None:
    if not run_log.strip():
        f.fail("주간 실행 로그가 비어 있음 — 크론이 돌지 않았을 가능성")
        return

    # 검색 옵션이 SDK에 거부되면 최신성/도메인 필터가 사라진다
    dropped = re.findall(r"does not support '([^']+)'", run_log)
    if dropped:
        f.fail(f"검색 옵션이 SDK에 거부됨: {sorted(set(dropped))} — firecrawl-py 버전 확인 필요")
    if "ScrapeOptions unavailable" in run_log:
        f.fail("ScrapeOptions 사용 불가 — 기사 본문이 스크래핑되지 않음")

    # 결과를 받아놓고 정규화에서 버리는 조용한 붕괴
    if "collapsed to" in run_log:
        f.fail("검색 결과가 정규화 단계에서 대량 유실됨 — 응답 형태 변경 의심")

    multi = re.findall(r"-> (\d+) unique results \((\d+) dated\)", run_log)
    if not multi:
        f.fail("검색 실행 기록을 찾을 수 없음")
    for i, (uniq, dated) in enumerate(multi):
        uniq, dated = int(uniq), int(dated)
        label = "KR" if i == 0 else "US"
        f.stat(f"{label} 검색 결과 {uniq}건 (발행일 {dated}건)")
        if uniq < MIN_UNIQUE_RESULTS:
            f.fail(f"{label} 검색 결과 {uniq}건 — 임계값 {MIN_UNIQUE_RESULTS}건 미만")
        elif uniq and dated / uniq < MIN_DATED_RATIO:
            f.fail(f"{label} 발행일 있는 결과 {dated}/{uniq}건 — news 채널이 죽었을 가능성")

    contexts = [int(c) for c in re.findall(r"Search context built: \d+ articles, (\d+) chars", run_log)]
    for i, chars in enumerate(contexts):
        label = "KR" if i == 0 else "US"
        f.stat(f"{label} 컨텍스트 {chars:,}자")
        if chars < MIN_CONTEXT_CHARS:
            f.fail(f"{label} 컨텍스트 {chars:,}자 — 임계값 {MIN_CONTEXT_CHARS:,}자 미만")

    if "[facts] KR facts empty" in run_log:
        f.fail("KRX 검증 수치를 전혀 확보하지 못함 — 지수/등락률이 근거 없이 서술될 위험")
    if "[facts] US facts empty" in run_log:
        f.fail("미국 검증 수치를 전혀 확보하지 못함")
    if re.search(r"\[facts\] (KOSPI|KOSDAQ) investor fetch failed", run_log):
        f.stat("수급 데이터 확보 실패 (네이버 파싱) — 리포트는 방향성만 서술")

    if re.search(r"Traceback \(most recent call last\)", run_log):
        f.fail("실행 중 예외 발생 — 로그 확인 필요")


def check_report(report: str, f: Findings) -> None:
    if not report.strip():
        f.fail("리포트 스냅샷이 없음 — 리포트가 생성되지 않았거나 저장 실패")
        return

    body = report
    f.stat(f"리포트 {len(body):,}자")

    if len(body) < MIN_REPORT_CHARS:
        f.fail(f"리포트가 {len(body):,}자로 지나치게 짧음")

    for section in ("한국시장 인텔리전스", "미국시장 인텔리전스"):
        if section not in body:
            f.fail(f"'{section}' 섹션 누락")

    if "투자 참고용" not in body:
        f.fail("면책조항 누락")

    if "리포트 생성에 실패했습니다" in body:
        f.fail("리포트 본문에 생성 실패 메시지가 포함됨")

    hits = [p for p in _META_PHRASES if p in body]
    if hits:
        f.fail(f"자료수집 메타 언급이 독자에게 노출됨: {hits}")

    blogs = [h for h in _BLOG_HOSTS if h in body]
    if blogs:
        f.fail(f"블로그/커뮤니티 출처가 리포트에 인용됨: {blogs}")

    # 대상 기간이 헤더에 실제로 박혀 있는지
    if not re.search(r"대상 기간: \d{4}\.\d{2}\.\d{2}", body):
        f.fail("대상 기간 헤더 누락 — 기간이 모호한 리포트")


def send_alert(text: str) -> bool:
    if not BOT_TOKEN or not ALERT_CHAT_ID:
        print(
            "[firecrawl-quality] BOT_TOKEN 또는 FIRECRAWL_ALERT_CHAT_ID 미설정 — "
            "알림을 보내지 못했습니다. .env에 FIRECRAWL_ALERT_CHAT_ID를 지정하세요."
        )
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": ALERT_CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"[firecrawl-quality] 알림 전송 실패 {resp.status_code}: {resp.text[:200]}")
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - alerting must not raise
        print(f"[firecrawl-quality] 알림 전송 오류: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="주간 Firecrawl 리포트 품질 점검")
    parser.add_argument("--dry-run", action="store_true", help="알림 없이 결과만 출력")
    args = parser.parse_args()

    f = Findings()
    check_log(_tail_run(_read(LOG_PATH)), f)
    check_report(_read(REPORT_PATH), f)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[firecrawl-quality] {stamp}")
    for s in f.stats:
        print(f"  · {s}")

    if not f.failures:
        print("  ✅ 이상 없음")
        return 0

    lines = [f"🚨 PRISM 주간 Firecrawl 품질 경고 ({stamp})", ""]
    lines += [f"❌ {msg}" for msg in f.failures]
    if f.stats:
        lines += ["", "📊 측정값"] + [f"· {s}" for s in f.stats]
    lines += ["", "로그: logs/weekly_firecrawl_intelligence.log"]
    alert = "\n".join(lines)

    print(alert)
    if not args.dry_run:
        send_alert(alert)
    return 1


if __name__ == "__main__":
    sys.exit(main())
