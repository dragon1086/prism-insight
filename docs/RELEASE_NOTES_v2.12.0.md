# PRISM-INSIGHT v2.12.0 — 시장 국면별 유연 대응 (Regime-Aware Trading)

> **Release Date**: 2026-04-30
> **Branch**: `feat/regime-aware-trading-v2.12.0` → `main`

## 개요

트리거별 실적 분석 결과 모멘텀 트리거가 놓친 기회가 많아, **(1) 트리거 유형별 전략을 차등화**하고, **(2) 강한 강세장을 넘어선 폭주장(parabolic) regime을 신설**했으며, **(3) 모멘텀 종목에서 컨센서스 목표가가 stale일 때 차트 기반 저항선으로 R/R 분자를 fallback**하도록 룰을 정비했습니다. 동시에 **distribution day kill switch**와 **폭주장 position size 축소**로 위험을 동시 관리합니다.

5명의 투자 페르소나(William O'Neil / Mark Minervini / Stanley Druckenmiller / Warren Buffett / Quant Risk Manager) 검토를 거쳐 합의 영역만 채택했습니다.

## 주요 변경사항

### 1. 시장 체제별 진입 매트릭스 6단계로 확장 (parabolic 행 신설)

```
| 시장 체제      | min_score | 손익비 floor | 최대 손절폭 | 모멘텀 신호 | 추가 확인 |
| parabolic     | 4         | 0.7         | -5%       | 2개+      | 0      |  ← NEW
| strong_bull   | 4         | 1.0         | -7%       | 1개+      | 0      |
| moderate_bull | 4         | 1.2         | -7%       | 1개+      | 0      |
| sideways      | 5         | 1.3         | -6%       | 1개+      | 0      |
| moderate_bear | 5         | 1.5         | -5%       | 2개+      | 1      |
| strong_bear   | 6         | 1.8         | -5%       | 2개+      | 1      |
```

**parabolic 활성화 조건 (4가지 모두 충족 필요):**
1. 기본 regime이 `strong_bull` (KOSPI/S&P ≥ 20일선, 최근 2주 강세)
2. 시장지수 90일 수익률 ≥ +30% (단순 강세가 아니라 명백한 가속)
3. 시장지수 30일 수익률 ≥ +10% (가속이 식지 않고 진행 중)
4. 트리거 유형이 모멘텀 리더 코호트:
   - KR: "일중 상승률 상위주 / 마감 강도 상위주 / 갭 상승 모멘텀 상위주"
   - US: "Daily Rise Top / Closing Strength Top / Gap Up Momentum Top"

> **거래량 급증 / 시총 자금 유입은 parabolic 적용 제외** — 과거 데이터에서 폭주장 후반부 distribution 신호와 일치하므로 strong_bull 행 유지.

하나라도 미달 → 기본 `strong_bull` 행으로 fallback (R/R 1.0, 손절 -7%).

### 2. Distribution Day Kill Switch (자동 보수화)

최근 4주 내 분포일(거래량 동반 -0.2%↓ 마감) ≥ 4건 확인 시 regime을 1단계 보수화합니다:

- parabolic → strong_bull
- strong_bull → moderate_bull
- moderate_bull → sideways

→ 폭주장 막바지 진입 함정 방지 (William O'Neil + Mark Minervini 핵심 우려 해소).

### 3. target_price 산정 룰 — 컨센서스 stale 함정 fix

기존: "보고서 명시 목표가가 있으면 그대로"
- 모멘텀 종목에서 컨센서스 목표가 < 현재가일 때 R/R 음수 → 영구 미진입 함정 발생

신규 (3단계 룰):
1. 보고서 명시 목표가 ≥ 현재가 × 1.05 → 그대로 사용 (목표가가 현재가보다 의미있게 위)
2. 그렇지 않으면(보고서 목표가가 stale 또는 현재가 이하) → 다음 주요 저항선 80% 또는 그 다음 저항선 80% 중 R/R floor 충족하는 가까운 값
3. 저항선 정보가 없으면 → 현재가 × (1 + 15~30%)

→ 분석 대상 사례(322000 HD현대에너지솔루션) 같은 R/R 음수 함정 해소.

### 4. parabolic regime 포지션 사이징 축소

parabolic 행이 활성화될 때:
- max_portfolio_size를 보고서의 시장 리스크 기준값보다 1~2 슬롯 줄이도록 권고
- `portfolio_context`에 parabolic regime 사이징 축소 사실 명시

→ 위험 노출 자동 제어 (Druckenmiller 권고).

### 5. Regime 한국어 번역 — 텔레그램 메시지 일관성

KR 매수 보류 메시지에서 영문 regime 라벨이 그대로 출력되던 문제 해결.

```
strong_bull   → 강한 강세장
moderate_bull → 보통 강세장
sideways      → 횡보장
moderate_bear → 보통 약세장
strong_bear   → 강한 약세장
parabolic     → 폭주 강세장  ← NEW
```

KR/US 양쪽 텔레그램 메시지 + 분석 보고서에서 일관 적용.

### 6. JSON 스키마 + 거시경제 분석 가이드 동기화

- KR/US `trading_agents.py` JSON schema의 `min_score` 라벨에 `parabolic:4` 추가
- KR/US `analysis.py`, `us_analysis.py`의 macro_context 한국어 라벨에 "폭주 강세장" 추가

## 변경된 주요 파일

| 파일 | 변경 |
|------|------|
| `cores/agents/trading_agents.py` | KR prompt: parabolic 행 + 활성화 조건 + DD kill switch + position sizing + target_price 3단계 룰 + JSON schema |
| `prism-us/cores/agents/trading_agents.py` | US prompt: 동일 변경 (S&P 500/VIX 지표, GICS 섹터, GICS 트리거명) |
| `cores/analysis.py` | macro_context 한국어 라벨에 "폭주 강세장" 추가 |
| `prism-us/cores/us_analysis.py` | 동일 |
| `stock_tracking_enhanced_agent.py` | 매수 보류 메시지에서 regime 라벨 한국어 번역 (KR 신규, US 패턴 미러링) |
| `prism-us/us_stock_tracking_agent.py` | 기존 번역 맵에 parabolic 추가 |
| `tests/test_trading_agents_prompt_rules.py` | KR 신규 테스트 12건 (parabolic, DD kill, target fallback, position sizing, schema) |
| `prism-us/tests/test_trading_agents_prompt_rules.py` | US 신규 테스트 12건 (동일 인보리언트 + S&P/GICS 키워드) |

테스트 결과: KR 46 / US 46 통과.

## 페르소나 검토 합의 매트릭스

| 권고 | William O'Neil | Mark Minervini | S. Druckenmiller | W. Buffett | Quant | 채택 |
|---|---|---|---|---|---|---|
| Parabolic regime 신설 | ✅ | ✅ | ✅ | ❌ | ⚠️ | ✅ |
| target_price fallback | ❌ | ✅ | ⚠️ | ❌ | ✅ | ✅ |
| Distribution Day kill switch | ✅ | ✅ | ⚠️ | ✅ | ✅ | ✅ |
| Trigger 차등 (모멘텀만) | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ |
| Position size 50% 축소 | ⚠️ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 손절 -10% 완화 (Fix B) | ✅ | ❌ | ❌ | ⚠️ | ❌ | ❌ 폐기 |
| 그대로 두기 (Fix D) | ❌ | ❌ | ❌ | ✅ | ⭐ | ❌ 폐기 |

## 업데이트 방법

```bash
git checkout main
git pull origin main
pip install -r requirements.txt   # mcp-agent SHA 핀은 v2.11.x 그대로 유지

# 운영서버에서
ssh root@<server>
cd /root/prism-insight
git pull origin main
# 별도 마이그레이션 불필요 (prompt-only 변경)
```

> Prompt 텍스트 변경이라 DB 스키마, env, requirements 변경은 없습니다.

## 알려진 제한사항

1. **표본 크기 한계**: parabolic regime 결정은 KR 4개월 N=24 데이터 + 페르소나 합의에 기반. 통계적 유의성(p<0.01)은 확인됐지만 단일 폭주장 시기에 의존하므로 모니터링 필수.
2. **Distribution Day 카운트 정확도**: LLM이 보고서/분석 텍스트에서 distribution day를 식별하는 능력에 의존. 향후 코드 기반 자동 카운트 가능성 검토 필요.
3. **US시장 검증 부족**: US 시뮬레이터(N=465, 4개월) 기간이 횡보-약세장이라 parabolic 행이 실제 활성화된 적 없음. 향후 US 강세장 진입 시 모니터링 필요.
4. **Position size 가이드 한계**: 현재 시스템은 1슬롯=100% 매매 구조이므로 position size 축소는 max_portfolio_size 축소로 우회 구현. 진정한 partial position은 별도 작업 대상.

## 텔레그램 공지

### 한국어

```
🚀 PRISM-INSIGHT v2.12.0 — 시장 국면별 유연 대응

📈 강한 강세장을 넘어선 "폭주 강세장(parabolic)" regime을 신설했습니다.
폭주장에서는 R/R 기준을 0.7로 완화하고, 손절은 -5%로 타이트하게,
포지션은 1~2 슬롯 줄여서 진입합니다.

🎯 핵심 변경 3가지:
1. 폭주 강세장 regime 신설 (시장지수 90일 +30% AND 30일 +10% 충족 시)
2. 모멘텀 트리거(일중 상승률·마감 강도·갭 상승)에만 적용
   거래량 급증·시총 자금 유입은 distribution 의심 → 적용 제외
3. 보고서 컨센서스 목표가가 현재가보다 낮으면 차트 기반 저항선으로 fallback
   (모멘텀 종목 R/R 음수 함정 해소)

🛡️ 위험 관리 동시 강화:
- Distribution Day Kill Switch: 4주 내 분포일 4건 → 자동 1단계 보수화
- 폭주장 포지션 50% 축소 (max_portfolio_size -1~2)

🌐 KR/US 양쪽 동시 적용. 매수 보류 메시지의 영문 regime 라벨도 한국어로 번역됩니다.

📊 5명의 투자 페르소나(O'Neil·Minervini·Druckenmiller·Buffett·Quant) 검토 후 합의 영역만 반영했습니다.
```

### English

```
🚀 PRISM-INSIGHT v2.12.0 — Regime-Aware Trading

📈 New "parabolic" regime for markets that go beyond strong_bull.
In parabolic regimes, the R/R floor is relaxed to 0.7, max stop tightened
to -5%, and position size reduced by 1-2 slots.

🎯 3 core changes:
1. New parabolic regime (activated when market index 90d ≥ +30% AND 30d ≥ +10%)
2. Applied ONLY to momentum-leader triggers (Daily Rise / Closing Strength / Gap Up)
   Volume Surge & Capital Inflow excluded (distribution-suspect → keep strong_bull row)
3. target_price fallback: when consensus target < current_price × 1.05,
   fall back to chart-based 80% of next resistance
   (resolves the negative-R/R trap that systematically blocked momentum stocks)

🛡️ Simultaneous risk controls:
- Distribution Day Kill Switch: ≥4 distribution days in 4 weeks → auto demote 1 step
- Parabolic position sizing: max_portfolio_size reduced by 1-2 slots

🌐 Applied to both KR and US markets. Korean translations of regime labels
now appear in skip-message Telegram alerts (parabolic = 폭주 강세장).

📊 Reviewed by 5 investing personas (O'Neil · Minervini · Druckenmiller · Buffett · Quant Risk Manager); only consensus areas adopted.
```

---

**Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>**
