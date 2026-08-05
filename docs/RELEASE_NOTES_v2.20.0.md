# PRISM-INSIGHT v2.20.0 — 카카오톡 Prism Lounge · KRX/KIS 시세 전환 · 근거 기반 리포트

> **배포일**: 2026-08-06
> **범위**: `v2.19.0`(`d4eaaf8`) → `v2.20.0` (`main`, PR #535 반영) · 커밋 155개 / PR 51개 (#478–#534)
> **규모**: 파일 224개, +33,042 / −1,934줄 · 2026-07-21–2026-08-05

## 한눈에 보기

이번 버전은 **시세 제공처가 막혀도 분석을 멈추지 않는 구조**와 **카카오톡에서 실제로
사용할 수 있는 리포트 흐름**을 함께 다듬은 버전입니다.

- 국내 시세를 한 곳에 의존하지 않고 **KIS 현재 시세 + KRX Open API 과거 시세 + 보조 경로**로
  이어 붙였습니다.
- 카카오톡 **Prism Lounge**의 Gateway, 온보딩, 자연어 명령, 무료 질문, 캠페인 전달과
  PDF 리포트 링크까지 한 흐름으로 정리했습니다.
- 주간 Firecrawl과 일간 시장 근거에 **출처·최신성·실패 사실을 함께 기록**해, 모르는 것을
  아는 것처럼 말하지 않도록 했습니다.
- BTC 라운드 6 스윙 레인을 실거래 경로와 분리해 검증하고, 약한 실험 결과는 자동으로 꺼두었습니다.
- 양봉·MA200·거래일·매수 신호 격리를 보강해, 선별과 리포트가 서로 오염되지 않도록 했습니다.

## 1. 카카오톡 Prism Lounge를 실제 사용 흐름으로 ⭐

- **Gateway 실 payload에 맞춘 이벤트 매핑**: 카카오가 보내는 실제 WebSocket 이벤트를 기준으로
  수신 경로를 고쳤습니다.
- **온보딩과 자연어 명령**: 방에 처음 들어온 사용자가 무엇을 할 수 있는지 안내하고,
  정해진 버튼뿐 아니라 자연어 질문도 처리합니다.
- **무료 질문·캠페인 전달**: 질문을 리포트 분석 경로로 연결하고, 원격 큐에 안전하게 넣어
  재시작 뒤에도 이어서 처리합니다.
- **렌더러와 링크 정리**: 분석 종류에 맞는 렌더러로 보내며, 만료되는 PDF 링크와 한글 파일명을
  올바르게 전달합니다.
- **막다른 버튼 제거**: 실제로 동작하지 않는 탭은 노출하지 않고, 탭 테스트로 상호작용 가능 여부를
  확인하는 문서를 남겼습니다.

## 2. KRX 차단을 견디는 시장 데이터 소스 체인

- **현재 시세**는 KIS 다종목 스냅샷을 우선 사용합니다. 최대 30종목 묶음 조회로 호출 횟수를
  줄이고, 누락·거절 사유를 숨기지 않습니다.
- **과거 시세와 유니버스**는 KRX Open API를 공식 경로로 사용하며, 공개 시각을 관측값으로
  기록합니다. 같은 날 데이터가 아직 공개되지 않았으면 전 거래일 자료로 안전하게 처리합니다.
- KRX가 막히거나 데이터가 비어 있으면 **Naver·차트·소스 체인**으로 단계적으로 내려가며,
  종목명이 코드로 퇴행하지 않도록 보강했습니다.
- KRX에 거래일 계산을 묶어 두지 않아, KRX 장애 중에도 국내 배치가 0건으로 끝나지 않습니다.
- 452개 유니버스 등가성 하네스와 장애 생존 하네스를 추가해, 차단 상태를 로컬에서 반복 검증할 수
  있게 했습니다.

## 3. 근거 기반 주간·일간 인텔리전스

- Firecrawl 주간 흐름에서 **뉴스 채널, 시장 전체 투자자 수급, 출처·최신성 필터**를 복원하고,
  SDK 버전에 따라 검색 옵션이 사라지지 않도록 폴백을 정리했습니다.
- 주간 요약은 1차 자료를 우선하고, 오래된 문서가 섞이면 신선도 문제를 드러냅니다.
- 봇의 일간 5개 명령에 검증 수치를 연결하고, 시장 등락률은 전일 종가 기준으로 계산합니다.
- 타임아웃된 팩트 빌드가 중복 실행되지 않도록 **Temporal Gate**를 넣었습니다.
- 근거를 찾지 못한 경우에는 movers를 억지로 붙이지 않고, 근거 부족 사실을 그대로 남깁니다.

## 4. BTC 스윙 레인: 실거래와 실험을 분리

- 라운드 6 스윙 레인에 거래소 실행, 데모 환경 변수, 복구 후 알림 재무장과 시세 수집 보호를
  추가했습니다.
- 리스크 스케일 1.5배 실험은 별도 경로로 넣어 기존 흐름과 분리했습니다.
- L-layer는 round8b 견고성 판정이 **WEAK**로 나와 기본 비활성화했습니다. 코드는 남아 있지만
  이번 버전에서 실제 판단에 사용하지 않습니다.

## 5. 매매 판단과 스크리닝의 안전장치

- 상승 중인 승자를 목표가에서 조기 청산하던 `trend_exit` 조건을 수정했습니다.
- KR·US 매수 트리거에서 **음봉을 뽑지 않도록** 양봉 필터를 통일했습니다.
- MA200을 계산할 수 없으면 값을 꾸며내지 않고 “없음”으로 표시합니다.
- 매매일을 KRX 응답에만 의존하지 않게 했고, 신호 격리 테스트로 한 시장의 결과가 다른 시장에
  섞이지 않도록 했습니다.
- publish guard 문서에 CI의 빈틈과 미해결 사유를 명시해, 통과를 과장하지 않도록 했습니다.

## 6. 운영·MCP·알림 안정성

- MCP 서버 준비 상태와 호스트별 credential 문제를 진단하는 도구를 추가했습니다.
- 텔레그램 재시도·멱등 재기동·요청 timeout 중복 제거를 정리했습니다.
- 설정을 중앙화하고, 임시 가상환경·캐시가 저장소에 섞이지 않도록 정리했습니다.
- MCP 리포트 경로는 KRX 스크래핑 대신 소스 체인을 사용하며, `.env` 로드 실패를 삼키지 않습니다.

## 7. 파이프라인을 읽고 검증할 수 있게

- 파이프라인·트레이딩 진화·학습 이력 인포그래픽을 새로 고치고, 생성기가 CI에서 재현 가능하도록
  만들었습니다.
- 카카오봇 설계 문서에는 Gateway(WebSocket ingress)와 REST outbound의 단일 봇 모델, 실제 서버
  배포 위치, live contract 확인 결과를 함께 기록했습니다.

## 개발자용 상세 — 동일 가중치 커밋 집계

이번 분석은 **커밋 하나를 모두 1표로 취급**했습니다. 변경 줄 수, PR 크기, 작성자, 기능의
중요도를 가중치로 사용하지 않았습니다. 한 커밋이 여러 주제에 걸쳐도 주된 목적 하나에만 배정해
중복 집계를 막았고, 병합 커밋도 숨기지 않고 별도 항목으로 한 표씩 세었습니다.

<details>
<summary>155개 커밋의 주제별 집계 펼치기</summary>

| 주제 | 동일 가중치 커밋 수 | 핵심 범위 |
|---|---:|---|
| 카카오톡·리포트 서비스 | 31 | Prism Lounge Gateway, 온보딩, 자연어 명령, 캠페인·PDF·렌더링 |
| 시장 데이터·KRX/KIS | 18 | KIS 스냅샷, KRX Open API, 소스 체인, Naver fallback, 장애 생존 |
| 근거 기반 인텔리전스 | 15 | Firecrawl 품질, 일간 근거, 최신성, Temporal Gate |
| BTC 실험 레인 | 10 | round6 swing lane, 거래소 실행, 리스크 스케일, L-layer 비활성화 |
| 트레이딩·스크리닝 안전성 | 6 | trend exit, 양봉 필터, MA200, 거래일·신호 격리 |
| 아키텍처·문서·가시화 | 11 | 파이프라인/진화 인포그래픽, 설계·검증 문서 |
| 운영·품질·보안 | 12 | MCP 진단, retry, idempotency, 설정·캐시 정리 |
| **병합 커밋** | **52** | 각 merge commit을 별도 1표로 계산 |
| **합계** | **155** | `v2.19.0..HEAD` 전체 |

</details>

## 개발자용 상세 — PR별 변경 규모 (참고)

아래 표는 변경 줄 수가 큰 순서가 아니라, **PR이 병합된 순서의 역순**입니다. 크기는 각 PR의
병합 커밋을 기준으로 계산한 참고값이며, 위의 동일 가중치 분석에는 사용하지 않았습니다.
즉, 큰 PR 하나가 작은 PR 여러 개보다 더 중요한 것으로 간주되지 않습니다.

<details>
<summary>이번 범위의 PR별 전체 목록 펼치기</summary>

| PR | 주제 | 규모 |
|---|---|---|
| #534 | KIS 다종목 현재 시세 스냅샷을 스크리닝에 연결 | 6 files, +455/−41 |
| #533 | MCP 리포트 경로에서 중복 펀더멘털 도구 제거 | 2 files, +25/−25 |
| #532 | KRX 스크래핑 대신 시장 데이터 소스 체인을 쓰는 MCP 서버 | 3 files, +549/−5 |
| #531 | 다일 OHLCV를 KRX→대체 소스 체인으로 전환 | 3 files, +238/−33 |
| #530 | KRX 없이도 종목명을 보존하는 배치 fallback | 4 files, +234/−9 |
| #529 | KRX 차단 상태에서 배치 완주를 확인하는 장애 하네스 | 1 file, +181 |
| #528 | KRX에 묶이지 않은 배치 거래일 해석 | 3 files, +221/−4 |
| #527 | KR·US 매수 트리거의 음봉 제외 | 3 files, +278/−5 |
| #526 | 452개 유니버스 등가성 검증 하네스 | 2 files, +739 |
| #525 | MA200을 제공하거나 없음으로 명시 | 5 files, +248/−3 |
| #524 | KIS 시장 데이터 어댑터 | 3 files, +576/−1 |
| #523 | KRX Open API 공식 스냅샷 어댑터 | 4 files, +647 |
| #522 | 시장 데이터 소스 어댑터 구조 정리 | 8 files, +856/−228 |
| #521 | KRX 차단 시 차트 데이터 fallback | 2 files, +234/−6 |
| #520 | KRX 차단 대응 클라이언트 패키지 업데이트 | 1 file, +1/−1 |
| #519 | Kakao 분석 결과 평가·피드백 흐름 | 9 files, +725/−35 |
| #518 | Kakao 자연어 명령 | 10 files, +383/−33 |
| #517 | Kakao 방 온보딩 | 8 files, +431/−33 |
| #516 | Kakao 캠페인 큐·전달 기반 | 3 files, +82/−21 |
| #500 | Prism Lounge 캠페인 기반 구조 | 103 files, +16,206/−215 |
| #515 | 중복 팩트 빌드를 막는 Temporal Gate | 3 files, +595/−10 |
| #514 | 일간 근거 생성 latency 보정 | 3 files, +39/−2 |
| #513 | 근거 기반 사실 질문·검증 수치 | 4 files, +863/−37 |
| #512 | Firecrawl 주간 품질 보강 | 7 files, +482/−32 |
| #510 | screening tripwire와 stop wording | 1 file, +107 |
| #511 | US 스크리닝 양봉 필터 | 2 files, +127/−2 |
| #507 | 파이프라인 인포그래픽 재설계 | 19 files, +190/−13 |
| #506 | 트레이딩 진화 인포그래픽 | 9 files, +208 |
| #505 | 파이프라인 아키텍처 PNG 재설계 | 19 files, +1,552/−137 |
| #503 | 시장별 신호 격리 테스트 | 6 files, +305 |
| #502 | 상승 중 승자의 조기 목표가 청산 수정 | 5 files, +319/−3 |
| #501 | 파이프라인 아키텍처 문서 갱신 | 15 files, +1,258/−1,350 |
| #499 | 로컬 uv 캐시 정리 | 1 file, +19/−1 |
| #498 | Firecrawl 품질 모니터 | 2 files, +250/−1 |
| #497 | Firecrawl Document metadata 호환성 | 1 file, +25/−6 |
| #496 | Firecrawl SDK 옵션 호환성 | 1 file, +63/−24 |
| #495 | 주간 Firecrawl 뉴스·수급 채널 복원 | 2 files, +168/−27 |
| #494 | 주간 Firecrawl 1차 자료·최신성 보정 | 4 files, +661/−62 |
| #490 | BTC L-layer 비활성화 | 1 file, +1/−1 |
| #489 | BTC L-layer 실험 경로 | 8 files, +311/−5 |
| #488 | BTC 리스크 스케일 1.5배 실험 | 3 files, +12/−4 |
| #487 | US Telegram 발송 재시도 | 1 file, +41/−17 |
| #486 | proxy tracing 노이즈 정리 | 2 files, +57/−1 |
| #485 | 레짐 하한선의 데이터 소스 통일 | 2 files, +33/−2 |
| #484 | US Market Pulse hook import 수정 | 2 files, +28/−1 |
| #483 | BTC 스윙 fallback 복구 후 재무장 | 2 files, +15/−1 |
| #482 | BTC 스윙 demo 환경 변수 명시 | 4 files, +13/−9 |
| #481 | Naver 국내 시세 snapshot fallback | 5 files, +860/−20 |
| #480 | KRX snapshot 일시 오류 재시도 | 1 file, +24/−1 |
| #479 | BTC 스윙 거래소 실행 경로 | 4 files, +456/−53 |
| #478 | BTC round6 스윙 레인 | 6 files, +875 |

</details>

## 검증 결과 — 코드보다 중요한 운영 증거

- **KIS 단일 종목 호출 부하 시험**: 2,686회 시도 중 2,663회 성공. 23회는 KIS `EGW00201`
  rate 오류로 거절되어, 09:30 단일 호출 방식은 운영 기본값으로 채택하지 않았습니다.
- **KIS 공식 다종목 스냅샷**: 30종목 묶음 호출로 2,686/2,686 성공, 누락 0건. 90회 호출,
  총 24.625초로 확인했습니다.
- **공식 종목 마스터**: ETP 2종을 제외한 현재 유니버스 2,687종. 전일 유니버스 2,686종은
  모두 포함되며 신규 동일자 종목 1종이 추가됩니다.
- **통합 smoke**: `source=kis+krx_openapi`, 현재 2,687종·전일 2,686종을 약 38.5초에 구성했습니다.
  삼성전자 시세가 정상이고, 주문은 발생하지 않았습니다.
- **회귀 테스트**: 관련 로컬 테스트 69개, 서버 테스트 27개 통과. CI Python 3.10/3.11/3.12와
  Codacy도 모두 통과했습니다.
- **데이터 공개 시각**: KRX Open API 당일 자료는 장 마감 직후가 아니라 다음 날 아침에
  공개될 수 있음을 확인했습니다. 현재 시세와 과거 시세를 분리한 이유입니다.

## 업데이트 방법

```bash
git fetch origin --tags
git checkout main
git pull --ff-only
```

운영 환경에서는 기존 **KIS 인증 설정**이 유효해야 합니다. 새 비밀값을 추가하는 변경은 없지만,
KIS가 응답하지 않을 때는 KRX Open API와 보조 소스가 자동으로 이어받는지 배포 후 확인해야 합니다.

## 참고 사항

- **v2.20.0 발행 기준**: 이 문서는 `v2.20.0` 태그와 함께 GitHub Release에 게시되는 정식
  릴리즈 노트입니다.
- KIS 다종목 스냅샷은 로컬 전 종목 검증을 통과했지만, 장중 09:30 실운영 첫 회차는 별도로
  관찰해야 합니다.
- KRX Open API의 당일 자료는 장 마감 직후가 아니라 다음 공개 시각에 나타날 수 있습니다.
  그래서 현재 시세와 과거 시세의 소스가 다를 수 있습니다.
- BTC L-layer는 **WEAK 판정으로 꺼져 있으며**, 이번 릴리즈에서 자동 매매 판단에 사용되지 않습니다.
- 카카오톡 실제 채널 심사·운영 계정 설정은 코드 배포와 별도 절차입니다.

## 텔레그램 공지

### 한국어

```
🚀 PRISM-INSIGHT v2.20.0 — 카카오톡 Prism Lounge · KRX/KIS 시세 전환 · 근거 기반 리포트

이번 버전은 시세가 막혀도 멈추지 않고, 카카오톡에서 실제로 리포트를 쓰는 흐름을 다듬었습니다.

💬 1) 카카오톡 Prism Lounge
· Gateway 실 payload 매핑, 온보딩, 자연어 명령, 무료 질문·캠페인 전달
· 분석 종류별 렌더러, 만료 PDF 링크, 한글 파일명, 막다른 탭 제거

📈 2) KRX/KIS 시세 소스 체인
· KIS 현재 시세 + KRX Open API 과거 시세 + Naver/차트 fallback
· KRX 차단 중에도 배치·리포트가 0건으로 끝나지 않도록 보강

🧾 3) 근거 기반 인텔리전스
· Firecrawl 1차 자료·최신성 필터, 일간 검증 수치, 중복 실행 방지 Temporal Gate

🛡️ 4) 매매 안전성
· 양봉·MA200·거래일·신호 격리 보강, 상승 중 승자 조기 청산 수정
· BTC L-layer는 WEAK 판정으로 꺼둔 상태
```

### English

```
🚀 PRISM-INSIGHT v2.20.0 — Kakao Prism Lounge · KRX/KIS market-data chain · Grounded reports

This release keeps analysis running through data-source outages and makes the Kakao report flow usable end to end.

💬 1) Kakao Prism Lounge
· Live Gateway payload mapping, onboarding, natural-language commands, free questions and campaigns
· Correct renderers, expiring PDF links, Korean filenames, and no dead-end taps

📈 2) KRX/KIS market-data chain
· KIS current quotes + KRX Open API historical data + Naver/chart fallbacks
· Batches and reports survive KRX blocking instead of silently returning zero results

🧾 3) Grounded intelligence
· Primary-source and recency filters, daily verified facts, and a Temporal Gate against duplicate builds

🛡️ 4) Trading safety
· Bullish-candle, MA200, trade-date and signal-isolation guards; fixed premature winner exits
· BTC L-layer remains disabled after a WEAK robustness verdict
```
