#!/usr/bin/env python3
"""Render the Korean pipeline architecture diagrams as deterministic PNG files.

The diagrams deliberately keep all copy in Python data structures.  Tests can
therefore audit terminology and unsupported claims before the text is rasterised.
No SVG intermediate or output is produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


CANVAS = (1920, 1080)
BACKGROUND = "#F7F5EF"
INK = "#172033"
MUTED = "#526071"
PANEL = "#FFFFFF"
BORDER = "#D8DEE8"
SCREENING = "#1769AA"
ANALYSIS = "#7851A9"
TRADING = "#23835A"
FEEDBACK = "#C66A18"
WARNING = "#B83A3A"

STAGE_COLORS = {
    "전체 흐름": "#334E68",
    "1단계 · 종목 스크리닝": SCREENING,
    "2단계 · 종목 분석": ANALYSIS,
    "3단계 · 매매": TRADING,
    "4단계 · 피드백": FEEDBACK,
}

FONT_CANDIDATES = (
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/AssetsV2/com_apple.MobileAsset.Font7/"
    "bad9b4bf17cf1669dde54184ba4431c22dcad27b.eeba8.asset/AssetData/NanumGothic.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
)


@dataclass(frozen=True)
class CardSpec:
    title: str
    lines: tuple[str, ...]
    tone: str = "normal"
    note: str = ""


@dataclass(frozen=True)
class DiagramSpec:
    filename: str
    stage: str
    title: str
    subtitle: str
    cards: tuple[CardSpec, ...]
    glossary: str
    sources: tuple[str, ...]


def card(title: str, *lines: str, tone: str = "normal", note: str = "") -> CardSpec:
    return CardSpec(title=title, lines=tuple(lines), tone=tone, note=note)


def build_diagrams() -> list[DiagramSpec]:
    """Return the fourteen approved, code-audited diagram specifications."""

    return [
        DiagramSpec(
            filename="full-pipeline-overview.png",
            stage="전체 흐름",
            title="PRISM 투자 과정 한눈에 보기",
            subtitle="시장을 살피고 종목을 찾은 뒤, 분석·매매·복기까지 이어집니다.",
            cards=(
                card("1. 종목 찾기", "시장 환경 확인", "오전·오후 발견 조건 실행", "후보를 최대 3종목으로 압축"),
                card("2. 종목 분석", "가격·수급·실적·사업", "뉴스·주도주·시장까지", "여섯 방향에서 조사"),
                card("3. 매매", "신규 매수와 추가 매수", "보유와 매도 판단", "포트폴리오·주문 안전 확인"),
                card("4. 거래 복기", "매매 결과와 이유 기록", "반복되는 교훈 추출", "다음 판단에 관련 경험 제공"),
            ),
            glossary="읽는 순서: 종목 스크리닝 → 종목 분석 → 매매 → 피드백",
            sources=("stock_analysis_orchestrator.py", "prism-us/us_stock_analysis_orchestrator.py"),
        ),
        DiagramSpec(
            filename="market-pulse-batch-control-overview.png",
            stage="1단계 · 종목 스크리닝",
            title="윌리엄 오닐의 M: 지금은 주식을 사기 좋은 시장인가",
            subtitle="대표지수의 종가와 거래량으로 시장의 매수 환경을 세 단계로 나눕니다.",
            cards=(
                card("입력", "한국: KOSPI 대표지수", "미국: S&P 500", "사용 값: 종가와 거래량"),
                card("상승 흐름", "분산일 0~3개", "오전 분석 실행", "오후 분석 실행", tone="good"),
                card("매도 압력 증가", "분산일 4~5개", "오전 분석 실행", "오후 분석 실행", tone="caution"),
                card("시장 조정", "분산일 6개 이상 또는 큰 하락", "오전 분석 휴지", "오후 분석 실행", tone="danger"),
                card("계산에 실패하면", "상태를 알 수 없음으로 처리", "배치를 막지 않고 계속 실행", "안전한 실패 방식: fail-open"),
                card("기능 설정", "코드 기본값: 관찰만 하는 shadow", "운영 문서 기록: live", "배포 환경 설정에 따라 달라짐"),
            ),
            glossary="M(Market Direction): CAN SLIM에서 ‘전체 시장의 방향’을 뜻합니다.",
            sources=("cores/market_pulse.py", "cores/regime_policy.py", "docs/FEATURE_FLAGS.md"),
        ),
        DiagramSpec(
            filename="distribution-day-state-transitions.png",
            stage="1단계 · 종목 스크리닝",
            title="분산일: 거래량이 늘면서 대표지수가 하락한 날",
            subtitle="기관성 매도 압력이 의심되는 날을 세어 시장이 약해지는 흐름을 포착합니다.",
            cards=(
                card("분산일로 세는 조건", "대표지수 종가가 전일보다", "0.2% 이상 하락", "동시에 거래량은 전일보다 증가"),
                card("언제까지 세나", "최근 25거래일만 집계", "그날 종가보다 이후 5% 오르면", "해당 분산일은 집계에서 제외"),
                card("상태가 나빠지는 길", "0~3개: 상승 흐름", "4~5개: 매도 압력 증가", "6개 이상: 시장 조정", tone="danger"),
                card("급락 예외", "분산일이 적더라도", "기준 고점에서 10%를 초과해 하락하면", "바로 시장 조정으로 전환", tone="danger"),
                card("상승 확인일", "반등 4일 차 이후", "하루 1.25% 이상 상승", "거래량까지 늘면 상승 흐름 복귀", tone="good"),
                card("고점 회복", "조정에 들어가기 전 고점을", "종가로 넘어도 상승 흐름 복귀", "조정은 분산일 감소만으로 풀리지 않음", tone="good"),
            ),
            glossary="분산일은 기관이 팔았다는 확정 증거가 아니라, 큰 매도 압력을 추정하는 시장 신호입니다.",
            sources=("cores/market_pulse.py", "tests/test_market_pulse.py"),
        ),
        DiagramSpec(
            filename="screening-six-triggers-overview.png",
            stage="1단계 · 종목 스크리닝",
            title="오전·오후에 종목을 찾는 6가지 방법",
            subtitle="발견 조건마다 찾으려는 움직임과 계산법이 다릅니다.",
            cards=(
                card("오전 1 · 거래량 급증", "전일보다 거래량 30% 이상 증가", "시초가보다 현재가가 높은 종목", "거래량 증가 60% + 거래량 40%"),
                card("오전 2 · 갭 상승 후 강세", "시초가가 전일 종가보다 1% 이상 높음", "장중 상승 흐름 유지", "갭 50% + 장중 상승 30% + 거래대금 20%"),
                card("오전 3 · 자금 집중", "거래대금 ÷ 시가총액", "기업 크기보다 큰 자금이 몰린 상승 종목", "비율 50% + 거래대금 30% + 상승 20%"),
                card("오후 1 · 하루 상승률 상위", "전일 종가보다 3~15% 상승", "장중 상승 60% + 거래대금 40%", "과도한 폭등 종목은 제외"),
                card("오후 2 · 장 마감 무렵 강세", "종가가 당일 고가에 가까움", "전일보다 거래량 증가", "마감 강도 50% + 거래량 30% + 거래대금 20%"),
                card("오후 3 · 거래량 늘어난 횡보", "거래량 50% 이상 증가", "하루 등락은 ±5% 안", "20일선 아래로 크게 밀린 종목 제외"),
            ),
            glossary="KR 공통 바닥 조건: 거래대금 100억 원 이상. 미국은 같은 구조에 별도 달러 기준을 씁니다.",
            sources=("trigger_batch.py", "prism-us/us_trigger_batch.py"),
        ),
        DiagramSpec(
            filename="candidate-screening-reranking-overview.png",
            stage="1단계 · 종목 스크리닝",
            title="많은 후보에서 최종 1~3종목을 고르는 과정",
            subtitle="기본 발견 조건에 시장 상황을 더하고, 네 가지 점수로 후보를 다시 세웁니다.",
            cards=(
                card("1. 후보 모으기", "오전 또는 오후 기본 조건 3개", "상황별 추가 조건 최대 2개", "각 조건에서 상위 후보 수집"),
                card("2. 상황별 추가 후보", "주도 업종 대표주", "횡보·하락장 역발상 가치주", "KR과 US의 활성 조건은 일부 다름"),
                card("3. 네 가지 재정렬 점수", "발견 조건 자체 점수", "예상 수익과 손실의 비율", "후보군 상대강도", "20일선 대비 과열 정도"),
                card("4. 시장에 맞춘 비중", "상승장: 상대강도 비중 확대", "횡보·하락장: 과열 감점 확대", "실제 가중치는 시장 체제별 표 적용"),
                card("5. 두 갈래에서 선택", "주도 업종 안의 강한 종목", "업종과 무관한 개별 강자", "중복 종목은 한 번만 선택"),
                card("6. 최종 후보", "기본 상한은 최대 3종목", "시장 체제에 따라 두 갈래 자리 배분", "파일럿 기능이 켜지면 1종목으로 제한"),
            ),
            glossary="재정렬(reranking): 처음 찾은 후보를 추가 점수로 다시 줄 세우는 과정입니다.",
            sources=("trigger_batch.py", "prism-us/us_trigger_batch.py", "cores/rs_rating.py"),
        ),
        DiagramSpec(
            filename="trading-regime-entry-overview.png",
            stage="1단계 · 종목 스크리닝",
            title="두 가지 시장 판단은 서로 하는 일이 다릅니다",
            subtitle="이름은 비슷하지만 하나는 배치 횟수, 다른 하나는 후보 선택 강도를 조절합니다.",
            cards=(
                card("오닐식 시장 매수 환경", "상승 흐름", "매도 압력 증가", "시장 조정", "오전·오후 분석 실행 여부 결정"),
                card("스크리닝용 시장 체제", "강한 상승장 · 완만한 상승장", "횡보장", "완만한 하락장 · 강한 하락장", "후보 점수와 자리 배분 결정"),
                card("과열 가속 상승", "공통 여섯 번째 체제가 아님", "강한 상승장에 수익률·트리거 조건을 추가", "매수 프롬프트 안에서만 별도 행 사용", tone="caution"),
                card("시장 체제가 바꾸는 것", "상대강도와 과열 점수의 비중", "주도 업종 후보 자리 수", "선택 기능: 약한 장의 추격 억제"),
                card("Market Pulse가 바꾸는 것", "시장 조정 때 오전 분석 휴지", "상태·분산일을 매수 판단에 제공", "보유 종목 매도 규칙은 바꾸지 않음"),
            ),
            glossary="시장 체제(regime): 상승·횡보·하락의 강도를 다섯 단계로 나눈 분류입니다.",
            sources=("cores/regime_policy.py", "trigger_batch.py", "cores/agents/trading_agents.py"),
        ),
        DiagramSpec(
            filename="screening-analysis-deep-dive.png",
            stage="2단계 · 종목 분석",
            title="선별된 종목을 여섯 방향에서 조사합니다",
            subtitle="최종 후보마다 서로 다른 보고서를 만든 뒤 투자전략과 핵심 요약을 조립합니다.",
            cards=(
                card("1. 주가와 거래량", "추세와 이동평균선", "지지·저항 가격", "거래량과 과열 정도"),
                card("2. 투자 주체", "한국: 기관·외국인·개인 매매", "미국: 기관 보유 현황", "누가 사고파는지 확인"),
                card("3. 실적과 재무", "분기·연간 매출과 이익", "ROE와 부채비율", "실적 전망과 어닝 서프라이즈"),
                card("4. 사업과 경쟁력", "주요 제품과 매출 구성", "시장점유율과 경쟁 우위", "경영 변화와 연구개발"),
                card("5. 뉴스와 주도주", "최근 호재·악재와 새 사업", "같은 업종 주도주 2~3개", "업종 흐름과 지속 가능성"),
                card("6. 시장과 업종", "대표지수와 시장 체제", "주도·부진 업종", "거시 위험과 직접 영향"),
            ),
            glossary="데이터는 KRX·yfinance 사전 수집을 우선하고, 부족한 부분만 웹 조사로 보완합니다.",
            sources=("cores/analysis.py", "cores/agents/", "prism-us/cores/us_analysis.py"),
        ),
        DiagramSpec(
            filename="can-slim-company-supply-checks.png",
            stage="2단계 · 종목 분석",
            title="CAN SLIM ① 기업 성장과 수급을 묻는 네 가지 질문",
            subtitle="C·A·N은 AI 보고서 중심이고, S는 발견 조건과 수급 자료가 함께 뒷받침합니다.",
            cards=(
                card("C · 최근 분기", "최근 분기 매출과 이익이 좋아지는가", "보고서와 AI 판단 중심", "고전적 EPS 성장률 하드 게이트는 없음"),
                card("A · 여러 해의 성장", "여러 해에 걸쳐 이익이 성장했는가", "ROE·매출 성장도 확인", "고전적 연간 EPS 기준은 코드로 강제하지 않음"),
                card("N · 새로운 계기", "신제품·새 사업·새로운 최고가가 있는가", "뉴스와 가격 보고서로 조사", "AI가 지속 가능성을 판단"),
                card("S · 주식 수급", "거래량과 매수세가 상승을 받치는가", "여섯 발견 조건과 수급 보고서", "분산일도 함께 사용", tone="good"),
            ),
            glossary="하드 게이트: 조건을 통과하지 못하면 AI 판단과 관계없이 자동 탈락시키는 코드 규칙입니다.",
            sources=("cores/agents/trading_agents.py", "prism-us/cores/agents/trading_agents.py"),
        ),
        DiagramSpec(
            filename="can-slim-leadership-market-checks.png",
            stage="2단계 · 종목 분석",
            title="CAN SLIM ② 주도력·기관·시장 방향을 묻는 세 가지 질문",
            subtitle="L과 M은 계산 로직이 비교적 강하고, I는 시장별로 구할 수 있는 자료의 범위가 다릅니다.",
            cards=(
                card("L · 주도주인가", "업종 안에서 앞서가는 종목인가", "후보군 상대강도와 주도 업종을 계산", "고전적 다개월 RS 등급은 선택 기능", tone="good"),
                card("I · 기관 관심이 있는가", "기관·외국인의 관심과 수급을 조사", "한국과 미국의 제공 자료가 다름", "자료가 없을 수 있어 AI 판단 비중이 큼"),
                card("M · 시장이 돕는가", "전체 시장이 매수에 유리한가", "5단계 시장 체제 + 오닐식 Market Pulse", "계산 결과를 매수 프롬프트에도 제공", tone="good"),
            ),
            glossary="CAN SLIM은 성장주를 실적·새로운 계기·수급·주도력·기관·시장으로 점검하는 오닐의 체계입니다.",
            sources=("cores/rs_rating.py", "cores/regime_policy.py", "cores/agents/trading_agents.py"),
        ),
        DiagramSpec(
            filename="entry-gates-overview.png",
            stage="3단계 · 매매",
            title="신규 매수: 최종 결정 전에 확인하는 조건",
            subtitle="하나의 CAN SLIM 판단과 여러 코드 안전장치가 차례로 작동합니다.",
            cards=(
                card("1. 분석 보고서 입력", "여섯 분석 보고서", "개별 종목 추세 사실", "시장 체제·분산일", "과거 매매 경험"),
                card("2. CAN SLIM 매수 판단", "기업 기초 체력", "추세와 상승 동력", "목표가·손절가·손익비", "시장에 맞는 최소 점수"),
                card("3. 종목 자체 추세", "50·60일선 아래인지", "하락하는 20일선에서 크게 밀렸는지", "떨어지는 종목의 성급한 매수 차단", tone="danger"),
                card("4. 재진입·중복 확인", "최근 반복 손절 여부", "매도 뒤 재진입 제한", "이미 보유한 종목의 추가 매수 조건"),
                card("5. 포트폴리오 확인", "최대 10칸과 남은 자리", "같은 업종 집중", "주문 진행 중 여부", "계좌별 보유 상태"),
                card("6. 최종 결과", "모든 조건 충족: 매수", "하나라도 핵심 조건 미달: 매수하지 않음", "별도 관심종목 대기 결과는 없음", tone="good"),
            ),
            glossary="손익비: 목표 수익을 감수할 손실로 나눈 값입니다. 높을수록 기대 보상이 큽니다.",
            sources=("cores/agents/trading_agents.py", "stock_tracking_agent.py", "reentry_cooldown.py"),
        ),
        DiagramSpec(
            filename="pyramiding-portfolio-overview.png",
            stage="3단계 · 매매",
            title="수익 중인 종목을 추가 매수할 때",
            subtitle="피라미딩은 잘 가는 종목에만 제한적으로 한 칸을 더 쓰는 방식입니다.",
            cards=(
                card("포트폴리오 기본", "기본 최대 10칸", "첫 매수와 추가 매수를 각각 한 행으로 기록", "두 경로가 같은 자리 한도를 사용"),
                card("1. 이미 보유 중인가", "같은 종목을 보유한 경우만 추가 매수 검토", "처음 사는 종목은 신규 매수 경로"),
                card("2. 충분히 수익 중인가", "기존 평균 매수가보다 수익률 5% 이상", "손실 종목이나 작은 반등에는 물타기하지 않음", tone="good"),
                card("3. 시장이 허용하는가", "강한 상승장·과열 가속 상승만 허용", "완만한 상승·횡보·하락장에서는 차단", tone="caution"),
                card("4. 횟수와 집중도", "한 종목은 최초 1회 + 추가 최대 2회", "전체 10칸 한도 확인", "같은 업종 집중 상태 확인"),
                card("조정 직후 예외", "파일럿 재진입 기능이 켜진 첫 5거래일", "신규 진입은 배치당 1종목", "보유 종목 추가 매수는 동결"),
            ),
            glossary="피라미딩(pyramiding): 손실 종목이 아니라 수익 중인 종목의 비중을 단계적으로 늘리는 방식입니다.",
            sources=("tracking/helpers.py", "stock_tracking_agent.py", "cores/regime_policy.py"),
        ),
        DiagramSpec(
            filename="trading-exit-overview.png",
            stage="3단계 · 매매",
            title="언제 팔 것인가: 손실은 짧게, 수익은 오래",
            subtitle="급한 위험은 코드가 먼저 막고, 나머지는 추세와 기업 변화를 함께 판단합니다.",
            cards=(
                card("1. 기업 중대 사건", "상장폐지·거래정지·공개매수 등", "보유 근거를 무너뜨리는 사건을 최우선 확인", "뉴스와 관리종목 정보 사용", tone="danger"),
                card("2. 긴급 손절", "시나리오 손절가 도달", "또는 매수가 대비 절대 -7%", "AI 판단보다 먼저 전량 청산", tone="danger"),
                card("3. 추세 이탈", "손실 중 50일선 이탈", "고점 대비 추적 손절", "약한 시장에서 목표가 도달", "연속 확인 또는 종가 확인"),
                card("4. AI 종합 판단", "가격·거래량과 기업 실적", "뉴스·공시·시장·업종 변화", "보유 또는 청산 결정"),
                card("5. 주문 실행", "모의 보유 기록과 실계좌 수량 확인", "매도 주문과 메시지 처리", "미체결은 별도 주문 관리로 전달"),
                card("6. 매도 뒤 기록", "매도가·수익률·청산 이유", "결정 근거와 시장 상황", "매매일지와 다음 판단에 저장"),
            ),
            glossary="추적 손절: 수익이 커질수록 손절 기준도 함께 올려, 이미 난 수익을 지키는 방식입니다.",
            sources=("cores/oneil_fallback.py", "stock_tracking_agent.py", "tools/trend_exit_seller.py"),
        ),
        DiagramSpec(
            filename="position-protection-loops.png",
            stage="3단계 · 매매",
            title="정규 분석 배치와 별도로 움직이는 보호 장치",
            subtitle="도구는 독립 실행되도록 설계됐지만, 실제 가동에는 cron과 기능 설정이 필요합니다.",
            cards=(
                card("긴급 손절", "시나리오 손절가 또는 절대 -7%", "코드 기본값: 주문 없는 SHADOW", "운영 문서: LIVE로 기록", tone="danger"),
                card("추세 이탈 매도", "50일선·추적 손절·목표가 확인", "연속 일봉 또는 종가 확인", "코드 기본값 SHADOW · 운영 문서 LIVE"),
                card("미체결 주문 관리", "실제 미체결 수량을 다시 조회", "가격 정정 또는 취소", "코드·운영 문서 모두 SHADOW", tone="caution"),
                card("배치와의 관계", "시장 조정으로 오전 분석이 쉬어도", "별도 프로세스로 실행 가능", "매수 후보 탐색과 직접 연결되지 않음"),
                card("배포 조건", "별도 cron 등록 필요", "저장소 docker/crontab에는 미등록", "기능 플래그와 실계좌 검증 필요"),
            ),
            glossary="SHADOW: 실제 주문은 내지 않고 ‘실행했다면 어땠을지’ 로그만 남기는 관찰 모드입니다.",
            sources=("tools/hardstop_seller.py", "tools/trend_exit_seller.py", "tools/fill_chaser.py"),
        ),
        DiagramSpec(
            filename="feedback-reentry-overview.png",
            stage="4단계 · 피드백",
            title="매매가 끝난 뒤 기록은 다음 판단에 어떻게 쓰이나",
            subtitle="거래 경험을 저장하고 관련 교훈을 다시 보여주지만, 시스템이 스스로 규칙을 배포하지는 않습니다.",
            cards=(
                card("1. 거래 이력", "매수·매도 가격과 수익률", "진입 근거와 청산 이유", "시장·종목 상황 태그"),
                card("2. 매매 회고", "계획과 실제 결과 비교", "잘한 점과 개선할 점", "감정·상태 메모"),
                card("3. 반복 교훈", "성공·실수 패턴을 짧게 정리", "종목·상황별 교훈으로 저장", "오래되거나 중복된 기억은 정리"),
                card("4. 다음 판단에 제공", "새 후보와 관련된 과거 경험 검색", "매수·매도 프롬프트에 참고 정보 제공", "결정은 현재 데이터와 함께 다시 수행"),
                card("5. 재진입 제한", "최근 매도 결과와 경과 시간 확인", "손실 매도 뒤 성급한 재매수 방지", "기능 상태에 따라 관찰 또는 차단"),
                card("중요한 경계", "자율 강화학습이 아님", "프롬프트나 코드 규칙을 스스로 수정하지 않음", "규칙 변경은 검토와 배포가 필요", tone="caution"),
            ),
            glossary="피드백: 과거 거래를 다음 판단의 참고자료로 되돌려 주는 과정입니다.",
            sources=("tracking/journal.py", "cores/agents/trading_journal_agent.py", "reentry_cooldown.py"),
        ),
    ]


def _font_path() -> str:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    raise RuntimeError("No Korean-capable font found")


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = _font_path()
    # Apple SD Gothic Neo exposes faces by index. Face 8 is bold on current macOS;
    # fall back to the default face for fonts that do not expose that index.
    try:
        return ImageFont.truetype(path, size=size, index=8 if bold and path.endswith(".ttc") else 0)
    except OSError:
        return ImageFont.truetype(path, size=size)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    if not text:
        return [""]
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if _text_width(draw, trial, font) <= width:
            current = trial
            continue
        if current:
            lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines


def _rounded(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    fill: str,
    outline: str,
    width: int = 2,
    radius: int = 24,
) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _tone_color(tone: str, stage_color: str) -> str:
    return {
        "good": TRADING,
        "caution": FEEDBACK,
        "danger": WARNING,
        "normal": stage_color,
    }.get(tone, stage_color)


def _card_positions(count: int) -> list[tuple[int, int, int, int]]:
    left, right = 68, 1852
    top, bottom = 238, 876
    gap_x, gap_y = 28, 28
    rows = 1 if count == 3 else 2
    cols = 3 if count in (3, 5, 6) else 2
    card_w = (right - left - gap_x * (cols - 1)) // cols
    card_h = (bottom - top - gap_y * (rows - 1)) // rows
    positions: list[tuple[int, int, int, int]] = []
    for index in range(count):
        row = index // cols
        col = index % cols
        if count == 5 and row == 1:
            row_items = 2
            row_width = card_w * row_items + gap_x
            row_left = left + ((right - left) - row_width) // 2
            x1 = row_left + col * (card_w + gap_x)
        else:
            x1 = left + col * (card_w + gap_x)
        y1 = top + row * (card_h + gap_y)
        positions.append((x1, y1, x1 + card_w, y1 + card_h))
    return positions


def _draw_card(
    draw: ImageDraw.ImageDraw,
    spec: CardSpec,
    box: tuple[int, int, int, int],
    stage_color: str,
    index: int,
) -> None:
    x1, y1, x2, y2 = box
    accent = _tone_color(spec.tone, stage_color)
    _rounded(draw, box, fill=PANEL, outline=BORDER, width=2)
    draw.rounded_rectangle((x1, y1, x2, y1 + 72), radius=24, fill=accent)
    draw.rectangle((x1, y1 + 38, x2, y1 + 72), fill=accent)

    badge = (x1 + 22, y1 + 16, x1 + 62, y1 + 56)
    draw.ellipse(badge, fill="#FFFFFF")
    number_font = _font(25, bold=True)
    number = str(index + 1)
    nb = draw.textbbox((0, 0), number, font=number_font)
    draw.text(
        (badge[0] + (40 - (nb[2] - nb[0])) / 2, badge[1] + (40 - (nb[3] - nb[1])) / 2 - nb[1]),
        number,
        font=number_font,
        fill=accent,
    )

    title_font = _font(33, bold=True)
    title_lines = _wrap(draw, spec.title, title_font, x2 - x1 - 104)
    title_text = title_lines[0]
    draw.text((x1 + 78, y1 + 17), title_text, font=title_font, fill="#FFFFFF")

    body_font = _font(30)
    cursor_y = y1 + 94
    max_width = x2 - x1 - 52
    for raw in spec.lines:
        wrapped = _wrap(draw, raw, body_font, max_width - 30)
        for line_index, line in enumerate(wrapped):
            if line_index == 0:
                draw.ellipse((x1 + 27, cursor_y + 12, x1 + 37, cursor_y + 22), fill=accent)
            draw.text((x1 + 50, cursor_y), line, font=body_font, fill=INK)
            cursor_y += 42
        cursor_y += 8

    if spec.note:
        note_font = _font(25)
        note_lines = _wrap(draw, spec.note, note_font, max_width)
        note_y = y2 - 36 * len(note_lines) - 20
        draw.line((x1 + 26, note_y - 10, x2 - 26, note_y - 10), fill=BORDER, width=2)
        for line in note_lines:
            draw.text((x1 + 27, note_y), line, font=note_font, fill=MUTED)
            note_y += 36


def render_diagram(spec: DiagramSpec, output_path: Path) -> Path:
    image = Image.new("RGB", CANVAS, BACKGROUND)
    draw = ImageDraw.Draw(image)
    stage_color = STAGE_COLORS[spec.stage]

    # Stage chip
    chip_font = _font(29, bold=True)
    chip_w = _text_width(draw, spec.stage, chip_font) + 54
    _rounded(draw, (68, 42, 68 + chip_w, 96), fill=stage_color, outline=stage_color, radius=27)
    draw.text((95, 51), spec.stage, font=chip_font, fill="#FFFFFF")

    title_font = _font(55, bold=True)
    title_lines = _wrap(draw, spec.title, title_font, 1745)
    title_y = 115
    for line in title_lines[:2]:
        draw.text((68, title_y), line, font=title_font, fill=INK)
        title_y += 66

    subtitle_font = _font(30)
    draw.text((70, 199), spec.subtitle, font=subtitle_font, fill=MUTED)

    positions = _card_positions(len(spec.cards))
    for index, (card_spec, position) in enumerate(zip(spec.cards, positions)):
        _draw_card(draw, card_spec, position, stage_color, index)

    # Glossary ribbon
    _rounded(draw, (68, 908, 1852, 980), fill="#EEF3F8", outline="#CAD5E2", radius=18)
    glossary_label_font = _font(27, bold=True)
    glossary_font = _font(27)
    draw.text((94, 926), "쉬운 말 풀이", font=glossary_label_font, fill=stage_color)
    draw.text((272, 926), spec.glossary, font=glossary_font, fill=INK)

    # Source footer
    source_font = _font(22)
    source_text = "코드 근거  " + " · ".join(spec.sources)
    draw.text((70, 1020), source_text, font=source_font, fill=MUTED)
    verified = "코드·프롬프트 검토 기준 2026-07-30"
    verified_w = _text_width(draw, verified, source_font)
    draw.text((1850 - verified_w, 1020), verified, font=source_font, fill=MUTED)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)
    return output_path


def render_all(output_dir: Path) -> list[Path]:
    return [render_diagram(spec, output_dir / spec.filename) for spec in build_diagrams()]


def _default_output_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "images" / "architecture"


def main() -> None:
    paths = render_all(_default_output_dir())
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
