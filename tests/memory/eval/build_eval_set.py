"""
Build a synthetic Korean trading-journal eval set per VERIFICATION_PLAN §1 Tier C.

Construction strategy (deterministic, offline):
    - 5 themes × 4 query-templates per theme = 20 queries (with unique phrases).
    - Each query gets 3 gold journals that ALSO contain the unique phrase.
    - 5 themes × 4 queries × 3 gold = 60 themed gold journals.
    - Plus 40 distractor journals (recent dates, mixed themes) for noise.
    - Total = 100 journals matching the spec.

Outputs:
    journals: [{id, user_id, text, ticker, ticker_name, theme}]
    queries:  [{query, gold_ids, theme}]
"""

from __future__ import annotations

import random
from typing import Dict, List

USER_ID = 999


# Each theme has multiple unique "signature phrases". Each phrase produces 3 gold
# journals + 1 query that looks for that phrase explicitly.
THEME_PHRASES: Dict[str, List[str]] = {
    "단타 후회": [
        "단타로 손해 본 적이 있어 기억에 남는다",
        "급등주 데이트레이딩 후회 깊이 남았다",
        "당일매도 스캘핑 손절만 당해 후회",
        "단타 진입 마이너스 큰 후회 기록",
    ],
    "장기보유 신뢰": [
        "장기보유 5년 신뢰 결심한 종목",
        "장기투자 시간이 친구 오래 들고",
        "장기 보유 단기 변동 흔들리지 말자",
        "오래 기다리면 결국 오른다 장기투자",
    ],
    "손절 어려움": [
        "물려서 손절 못해 손해 더 키움",
        "손절 타이밍 놓쳐 마이너스 깊게",
        "물타기만 하다가 더 깊게 물림",
        "손절이 진짜 어렵다 인정 안 됨",
    ],
    "분할매수": [
        "분할매수 전략으로 평단 낮추기",
        "조금씩 쪼개서 분할 매수 진입",
        "분할로 추가매수 평단 관리 핵심",
        "한 번에 들어가지 않고 분할 매수",
    ],
    "배당 선호": [
        "배당주 배당금 받으면서 보유",
        "배당락 전 매수 배당금 챙기기",
        "배당 안정적 현금흐름 종목 선호",
        "배당주 포트폴리오 확장 재투자",
    ],
}


# Each phrase → query (slight reword, but keeps the load-bearing keywords).
QUERY_FOR_PHRASE: Dict[str, str] = {
    # 단타 후회
    "단타로 손해 본 적이 있어 기억에 남는다": "내가 단타로 손해 본 적 기억",
    "급등주 데이트레이딩 후회 깊이 남았다": "급등주 데이트레이딩 후회 기록",
    "당일매도 스캘핑 손절만 당해 후회": "당일매도 스캘핑 손절 후회",
    "단타 진입 마이너스 큰 후회 기록": "단타 진입 마이너스 후회",
    # 장기보유 신뢰
    "장기보유 5년 신뢰 결심한 종목": "장기보유 5년 신뢰 결심",
    "장기투자 시간이 친구 오래 들고": "장기투자 시간이 친구 기록",
    "장기 보유 단기 변동 흔들리지 말자": "장기 보유 단기 변동 흔들리지 말자",
    "오래 기다리면 결국 오른다 장기투자": "오래 기다리면 결국 오른다",
    # 손절 어려움
    "물려서 손절 못해 손해 더 키움": "물려서 손절 못해 손해 키운 기록",
    "손절 타이밍 놓쳐 마이너스 깊게": "손절 타이밍 놓쳐 마이너스",
    "물타기만 하다가 더 깊게 물림": "물타기만 하다가 깊게 물림",
    "손절이 진짜 어렵다 인정 안 됨": "손절이 진짜 어렵다 인정",
    # 분할매수
    "분할매수 전략으로 평단 낮추기": "분할매수 전략 평단 낮추기",
    "조금씩 쪼개서 분할 매수 진입": "조금씩 쪼개서 분할 매수",
    "분할로 추가매수 평단 관리 핵심": "분할로 추가매수 평단 관리",
    "한 번에 들어가지 않고 분할 매수": "한 번에 들어가지 않고 분할",
    # 배당 선호
    "배당주 배당금 받으면서 보유": "배당주 배당금 받으면서 보유",
    "배당락 전 매수 배당금 챙기기": "배당락 전 매수 배당금",
    "배당 안정적 현금흐름 종목 선호": "배당 안정적 현금흐름 선호",
    "배당주 포트폴리오 확장 재투자": "배당주 포트폴리오 재투자",
}


TICKERS = [
    ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("035420", "NAVER"),
    ("035720", "카카오"), ("005380", "현대차"), ("051910", "LG화학"),
    ("068270", "셀트리온"), ("105560", "KB금융"), ("055550", "신한지주"),
    ("032830", "삼성생명"), ("207940", "삼성바이오"), ("006400", "삼성SDI"),
    ("373220", "LG에너지솔루션"), ("000270", "기아"), ("066570", "LG전자"),
    ("003550", "LG"), ("012330", "현대모비스"), ("017670", "SK텔레콤"),
    ("030200", "KT"), ("034730", "SK"),
]


def build_eval_set(seed: int = 1234) -> Dict[str, list]:
    rng = random.Random(seed)
    journals: List[Dict] = []
    queries: List[Dict] = []
    next_id = 1

    # Round-robin tickers.
    ticker_idx = 0

    # 1) Themed gold journals: 5 themes × 4 phrases × 3 gold each = 60 rows.
    for theme, phrases in THEME_PHRASES.items():
        for phrase in phrases:
            gold_ids: List[int] = []
            for variant in range(3):
                ticker, ticker_name = TICKERS[ticker_idx % len(TICKERS)]
                ticker_idx += 1
                # Vary surface form a bit but keep the unique phrase verbatim.
                journal_text = (
                    f"{ticker_name} 관련 메모 #{variant + 1}. "
                    f"{phrase}. 메모 작성: {theme}."
                )
                journals.append({
                    "id": next_id,
                    "user_id": USER_ID,
                    "text": journal_text,
                    "ticker": ticker,
                    "ticker_name": ticker_name,
                    "theme": theme,
                    "phrase": phrase,
                })
                gold_ids.append(next_id)
                next_id += 1
            queries.append({
                "query": QUERY_FOR_PHRASE[phrase],
                "gold_ids": sorted(gold_ids),
                "theme": theme,
            })

    # 2) Distractor journals: 40 rows of generic content (no unique phrases).
    distractor_templates = [
        "{ticker_name} 일반 매수 메모. 시장 보면서 신중히 진입.",
        "{ticker_name} 분기 실적 발표 대비 정리.",
        "{ticker_name} 거래량 급증 관찰. 차트 점검.",
        "{ticker_name} 외국인 매수 유입. 단기 모니터링.",
        "{ticker_name} 종목 코멘트 정리.",
    ]
    for i in range(40):
        ticker, ticker_name = TICKERS[ticker_idx % len(TICKERS)]
        ticker_idx += 1
        text = distractor_templates[i % len(distractor_templates)].format(ticker_name=ticker_name)
        journals.append({
            "id": next_id,
            "user_id": USER_ID,
            "text": text,
            "ticker": ticker,
            "ticker_name": ticker_name,
            "theme": "distractor",
            "phrase": None,
        })
        next_id += 1

    rng.shuffle(journals)
    # Re-assign ids in shuffled order so insertion order in DB == final id order.
    new_id = 1
    remap: Dict[int, int] = {}
    for j in journals:
        remap[j["id"]] = new_id
        j["id"] = new_id
        new_id += 1
    for q in queries:
        q["gold_ids"] = sorted(remap[gid] for gid in q["gold_ids"])

    return {"journals": journals, "queries": queries}


if __name__ == "__main__":
    data = build_eval_set()
    print(f"journals={len(data['journals'])}  queries={len(data['queries'])}")
    print("first query:", data["queries"][0])
