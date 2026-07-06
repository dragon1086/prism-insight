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
- `_compute_kr_regime` 반환 직전 1회 호출. bull 계열이고 **3중 조건**
  **① 최근 10일 실현변동성 ≥ 2.5% AND ② 20일 고점 대비 낙폭 ≥ 8% AND ③ 최근 10일 순변화 ≤ -3%**
  이면 `sideways`로 강등 + confidence 하향. `index_summary["highvol_drawdown_override"]`에 사유 기록.

  ⚠️ **조건 ③(순변화)이 반드시 필요**: 스윕 검증에서 ①+②만으로는 **낙폭 0%인 횡보·급등형
  고변동장도 강등**되는 것이 발견됨(고변동이면 20일 고점이 자동으로 높아져 낙폭 게이트가 무력화).
  순변화 ≤ -3% 조건을 추가해 "실제로 하락 중"일 때만 발동하도록 교정 → melt-up/횡보 오강등 제거.
  (dd=0% 행이 재스윕에서 전부 moderate_bull 유지됨을 확인.)

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

## 5. 미국(prism-us) 동일 조치

`prism-us/cores/data_prefetch.py:_compute_us_regime`도 **동일 결함**: VIX를 strong_bull/
strong_bear 조건엔 쓰지만 **moderate_bull 분기는 `above_50 OR change_4w>=0`로 KR과 같은
사각지대**. → 동일한 `_high_vol_drawdown_override`를 병렬 포크로 추가(실현변동성+낙폭,
시장 무관 대칭 구현). 강등은 sideways까지, melt-up은 낙폭 조건으로 배제.

주의(설계상 정상): US에서 가격이 **50일선 아래로 내려가면 기존 로직이 이미 sideways로
강등**한다. 따라서 override는 "50일선 위를 유지하면서 최근 고점 대비 급락형 고변동"인
사각지대에서만 추가로 발동한다(KR도 60일선 기준 동일 성격).

향후 개선 여지: US는 VIX(선행 변동성 지표)가 이미 있으므로, 실현변동성 대신/함께 VIX를
override 조건에 결합 가능. 지금은 KR과 대칭 유지를 위해 실현변동성+낙폭으로 통일.

## 6. 검증

- KR 단위테스트 `tests/test_regime_highvol_override.py` **8 passed**
  (helper 6 + 통합 2: 급락휩쏘→sideways, melt-up→미발동, 평온상승→유지, 저변동하락→미발동,
   non-bull 무변경, short-series 안전).
- US 스모크 `prism-us/tests/test_regime_highvol_override.py` **5 passed**.
- 회귀: 기존 `tests/test_distribution_days.py` 통과(강등 없음 유지). 이 샌드박스에서
  돌릴 수 있는 regime/distribution 테스트 전부 통과.
- 한계: `trigger_batch`/`krx_data_client` 의존 테스트와 `tools/regime_backtest.py`
  다년 리플레이는 이 샌드박스에선 실행 불가(모듈/네트워크 미비). **서버/venv 필요.**

## 6. 임계값은 TUNABLE — 배포 전 필수

`HIVOL_DD_VOL_PCT=2.5`, `HIVOL_DD_DRAWDOWN_PCT=8.0`, `HIVOL_DD_CONFIDENCE=0.55`는
시작값(placeholder). **`tools/regime_backtest.py`로 최근 수년 리플레이 → 강등 빈도·휩쏘 감소·
melt-up 미발동을 확인해 튜닝** 후 features.yaml 이관 권장. demo 배포 검증도 필요.

## 7. 남은 확증 (서버 데이터)

이 머신엔 orchestrator 미실행 → `logs/regime_history.jsonl`은 06-30 2줄(폴백)뿐,
7월 라벨은 분석 서버에 있음. 서버 `regime_history.jsonl`(7월)로 실제 라벨이
moderate/strong_bull 였는지 대조하면 100% 못박음.
