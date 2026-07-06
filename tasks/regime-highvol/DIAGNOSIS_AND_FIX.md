# KR regime 오판 진단 및 고변동·낙폭 override

작성: 2026-07-06 / 브랜치: `feature/regime-highvol-guard`

## 1. 증상

최근(특히 7월) 한국주식에서 **매수 직후 짧게 손절**하는 케이스가 빈번, 계좌 손실 누적.
사용자 직감: "trigger batch가 온건강세(moderate_bull)로 판단해 적극 매수한다."

## 2. 실제 시장 (2026-07 초, 뉴스 확인)

- KOSPI 종가: 7/1 8,303(-2.04%) → 7/2 7,648(**-7.89%**) → 7/3 8,088(+5.76%) →
  7/6 장중 8,300 → 7,900대 되밀림. 최근 1개월 **-6.38%**, 최근 1년 **+164%**.
- 즉 **고변동·급락형 휩쏘(whipsaw)** 국면. "온건강세" 아님.

## 3. 근본 원인 (실제 함수로 확증)

`cores/data_prefetch.py:_compute_kr_regime`는 **가격의 60/120일선 상대위치 + 2주 변화율**만
본다. 변동성·낙폭 항이 없다. 문제의 분기:

```python
if above_120:                              # 가격 > 120일선
    if above_60 and golden and change_2w_pct > 5:  regime = "strong_bull"
    elif above_60 or change_2w_pct >= 0:           regime = "moderate_bull"  # ← OR
    else:                                          regime = "sideways"
```

- 최근 1년 +164% 랠리로 **가격이 120·60일선 위에 붕 떠 있음**(이동평균 후행성).
- `moderate_bull`이 **OR 조건** → 2주 변화율이 마이너스여도 `above_60`이면 강세.

### 확증 (실제 `_compute_kr_regime` 을 7월형 종가 구조로 호출)

| 시나리오 | 2주변화 | 결과 라벨 |
|---|---|---|
| 7/2 급락 저점 | **-8.1%** | moderate_bull (conf 0.78) |
| 7/3 반등 | -4.7% | moderate_bull (conf 0.78) |
| 7/6 되밀림 | -6.5% | moderate_bull (conf 0.78) |

**2주 새 8% 폭락한 날조차 "온건강세"로 분류됨.** moderate_bull → 매수 문턱 60(관대,
`buy_quality.REGIME_THRESHOLDS`) + 슬롯 확대 → 고점매수 → 되밀림 손절 반복.

### 원장 근거 (stock_tracking_db.sqlite, 2025-10~2026-02, 69건)

- 승률 49%, 평균 승 +13.3% / 평균 패 -5.5% → 추세장에선 수익(누적 +258%p).
- 그러나 2일내 손실청산 17%, -5% 이상 손실 29%. 2/2~2/6 **닷새 새 5연속 손절**(휩쏘 군집).
- 결론: **추세장에선 이기고, 고변동·급락 국면에만 군집으로 얻어맞음.**

## 4. 수정 (최소·재사용 우선)

새 리스크 서브시스템을 만들지 않는다. 이미 있는 regime→매수문턱/슬롯/가중치 machinery를
그대로 쓰되, **regime 산출 한 곳에만** 가드를 추가.

`cores/data_prefetch.py`:
- `_high_vol_drawdown_override(closes, regime, confidence)` 순수 함수 추가.
- `_compute_kr_regime` 반환 직전 1회 호출. bull 계열이고
  **① 최근 10일 실현변동성 ≥ 2.5%** **AND ② 20일 고점 대비 낙폭 ≥ 8%** 이면
  `sideways`로 강등 + confidence 하향. `index_summary["highvol_drawdown_override"]`에 사유 기록.

### 왜 이 설계인가 (팀의 과거 결정 존중)

`data_prefetch.py:247-250`에 문서화된 결정: 분산일 기반 **기계적 regime 강등은
US melt-up에서 조기청산 손실을 유발**해 채택하지 않음(정보 주입만).

→ 본 override는 그 함정을 구조적으로 회피한다: **낙폭(≥8%) 조건**이 있어 melt-up
(신고가 부근 고변동, 낙폭≈0)은 **절대 발동하지 않음**. 즉 "급등장 조기청산"은 발생 불가,
"급락형 휩쏘에서만 매수 보수화"가 된다. 변동성 AND 낙폭 동시조건이라 느린 하락(저변동)이나
얕은 눌림에도 발동 안 함.

### 부수효과 (검토 완료)

- 강등은 `sideways`까지만(약세장 아님) → 매수만 보수화, 완전 매수중단은 아님.
- sell 측 영향: `PYRAMID_ALLOWED_REGIMES=(strong_bull,parabolic)`이라 moderate_bull→sideways는
  피라미딩에 영향 없음. loop_b 추세이탈/매도판단엔 소폭 보수화(급락장에선 바람직).

## 5. 검증

- 신규 단위테스트 `tests/test_regime_highvol_override.py` **14개 + 기존 distribution 6개 = 20 passed.**
  - 급락형 휩쏘→sideways 강등 / melt-up→미발동 / 평온상승→유지 / 저변동 하락→미발동 /
    non-bull 무변경 / 통합(_compute_kr_regime) before(moderate_bull)→after(sideways).
- 회귀: 이 환경에서 돌릴 수 있는 regime/distribution 테스트 전부 통과. (trigger_batch 계열
  테스트는 `krx_data_client` 미설치로 이 샌드박스에선 수집 불가 — 본 변경과 무관, 서버/venv 필요.)

## 6. 임계값은 TUNABLE — 배포 전 필수

`HIVOL_DD_VOL_PCT=2.5`, `HIVOL_DD_DRAWDOWN_PCT=8.0`, `HIVOL_DD_CONFIDENCE=0.55`는
시작값(placeholder). **`tools/regime_backtest.py`로 최근 수년 리플레이 → 강등 빈도·휩쏘 감소·
melt-up 미발동을 확인해 튜닝** 후 features.yaml 이관 권장. demo 배포 검증도 필요.

## 7. 남은 확증 (서버 데이터)

이 머신엔 orchestrator 미실행 → `logs/regime_history.jsonl`은 06-30 2줄(폴백)뿐,
7월 라벨은 분석 서버에 있음. 서버 `regime_history.jsonl`(7월)로 실제 라벨이
moderate/strong_bull 였는지 대조하면 100% 못박음.
