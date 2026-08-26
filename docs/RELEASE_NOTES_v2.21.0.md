# PRISM-INSIGHT v2.21.0 — Stance 공개 검증 · ClickStack 관측성 · 근거형 봇과 매매 개선

> **배포일**: 2026-08-27
> **범위**: `v2.20.0` (`fec9149`) → `v2.21.0` (`main`, PR #615 반영) · 커밋 202개 / PR 75개
> **규모**: 파일 260개, +29,572 / −2,017줄 · 2026-08-06–2026-08-27

## 한눈에 보기

이번 버전은 **판단을 공개해 장기 성과로 검증하는 Stance**, **운영 변화와 매매 결과를 연결하는
ClickStack 관측성**, **근거를 찾고 읽기 쉬운 결론까지 책임지는 봇**, **과차단과 후보 누락을 줄인
매매 경로**를 함께 다듬은 버전입니다.

- **Stance 공개 프로토콜**은 등록 이후의 판단만 불변 원장에 기록하고, 백필 없이 일별 평가와
  리더보드를 구성합니다.
- **카카오톡·텔레그램**은 종목 질문, 평가, 장중 원인, 출처 링크, 자동 배치 스토리와 토론 조정까지
  실제 대화 흐름을 보강했습니다.
- **KR·US 매매 판단**은 결정론적 게이트, 반등 파일럿, Oneil RS, 후보/실거래 성과 분리와
  Emerging Liquidity Lane을 추가했습니다.
- **Insight·Archive**는 긴 연구 작업을 끝까지 기다리고, 도구 출처와 근거를 강제하며, 넓은 검색은
  Archive 안에서만 수행하도록 경계를 세웠습니다.
- **ClickStack**은 매매를 막지 않는 fail-open 이벤트 수집, 180일 보존, 배포 이력, 후보/실거래 성과,
  Regime과 대시보드 Insights 탭을 연결했습니다.
- **시장 데이터·MCP**는 KIS 장중 투자자 추정치, Naver 수급 fallback, 공식 MCP 버전 고정과
  불필요한 cron 제거로 런타임을 실제 운영 상태에 맞췄습니다.
- **라이선스·배포 경계**는 이중 라이선스 동의 절차, 한국어 CLA, copyleft PDF 경로 제거와
  배포물별 제3자 고지 검사를 추가했습니다.
- 변경이 작았던 **BTC**도 미확정 spot 봉 재수집과 활성 스윙 포지션 보고를 별도 항목으로 남겼습니다.

## 1. 카카오톡·텔레그램·정식 리포트: 질문부터 결론까지

- 종료된 예측·순위 명령을 메뉴에서 제거하고, 분석 대기시간·도움말·일일 한도를 실제 동작과
  일치시켰습니다. 채팅방 전체 한도는 운영자가 명시하지 않으면 적용하지 않습니다.
- 종목 별칭과 자유 어순을 해석하고, 전망·당일 장중 변동 질문은 최신 직접 근거를 찾도록 했습니다.
  검증하지 못한 현재가·고저가·등락률은 답변에 쓰지 않습니다.
- 내부 evidence ID를 숨기고 매체명과 실제 URL을 맞췄습니다. 긴 출처 목록도 말풍선 경계에서
  항목이 갈라지지 않으며, 만료형 PDF 공개 링크를 다시 연결했습니다.
- 카카오와 텔레그램 정식 리포트 기본 모델을 Luna high로 맞추되 운영 환경의 명시값은 우선합니다.
  시장별 대화 컨텍스트를 분리하고, 리포트 섹션은 제한된 동시성으로 생성합니다.
- 실패한 캐시는 재사용하지 않고 KST 날짜 경계에서 만료합니다. 예약 배치도 병렬화해 오후 배치가
  리포트 생성 때문에 과도하게 늘어지지 않게 했습니다.
- 자연어 보유종목 평가, `만원` 단위 가격, 전망 질문과 Luna 검색 계획을 지원합니다. 자동 배치 스토리는
  큐·consumer·forwarder를 거쳐 재시작에도 이어지며, 생성 사실과 가상 브리핑임을 숨기지 않습니다.
- 명령 답변은 가능한 경우 한 방향 결론으로 끝납니다. 추가 결론 호출은 fail-open이므로 실패해도
  이미 만든 리포트를 버리지 않습니다.
- 실제 운영 systemd 유닛과 런북을 맞추고 수동 중복 기동을 금지했습니다. 텔레그램에는 문맥을 고려한
  토론 조정과 더 선명한 의견 문체, 안전한 plain-text 정규화를 추가했습니다.

## 2. Stance: 백필 없는 매매 판단 공개 프로토콜

- 수익률을 사후 신고하는 대신 **판단 선언 → 서버 수신 → 불변 원장 → 일별 마킹 → 리더보드**로
  이어지는 프로토콜을 구현했습니다. 부분매도, 목표 비중, 멱등 sequence, 취소·재시도 의미를
  명시해 서로 다른 클라이언트가 같은 결과를 계산할 수 있게 했습니다.
- v1의 정식 자산군은 **현물 주식**입니다. 시장 프로파일은 거래세, 가격 제한, 휴장일과 장 상태를
  코어에서 분리하며, 2026년 KRX 세율과 상·하한 방향 판정을 바로잡았습니다.
- SQLite 원장과 HTTP 서버는 한 writer와 영속 경로를 전제로 합니다. 일별 marker와 리더보드 JSON이
  없으면 기록이 멈추는 경계를 테스트하고 systemd 배포 예시를 추가했습니다.
- PRISM 실행 경로에는 Stance 선언 훅을 연결했지만 **기본은 꺼져 있습니다**. 외부 Stance 장애가
  주문을 막지 않도록 fail-open이며, 재시도 시 중복 선언이 생기지 않게 했습니다.
- 대시보드에 리더보드 탭을 추가하고 위험·노출·기록 품질·최신 판단을 함께 표시합니다. 등록 폼보다
  리더보드를 먼저 보여주고, 수동 등록은 접힌 선택 영역으로 내렸습니다.
- `/.well-known/stance.json`과 AI 에이전트 온보딩을 제공하며 비밀키는 저장소 밖 mode-0600 경로에
  보관하도록 안내합니다. 전략 프로필과 승인 기반 등록, 정적 SQL을 추가했습니다.
- 가장 중요한 약속은 **등록 전 기록을 업로드하거나 백필할 수 없다는 것**입니다. README와
  대시보드 문구도 단기 홍보보다 등록 이후 연속 기록과 전략 순위를 먼저 설명하도록 고쳤습니다.

## 3. KR·US 매매 판단·스크리닝·성과 피드백

- sideways이지만 Market Pulse가 `UPTREND`인 구간에서는 최소 점수 하한을 8에서 7로 완화하고,
  AI가 진입을 선택한 경계 점수 후보는 설정 주문액의 50%로만 시험하는 **상승 전환 파일럿**을
  추가했습니다. 약세 Regime, Skip, 섹터·슬롯·쿨다운 게이트는 그대로 유지합니다.
- KR·US 공통 결정론적 매수 게이트를 도입해 LLM 문구와 실제 진입 규칙이 어긋나지 않게 했습니다.
  KR 빈 scenario 응답은 제한적으로 재시도하고, 보유 중인 US 후보도 판단 결과를 사용자에게
  드러냅니다.
- 손절폭과 ATR/ADR 관계를 shadow 진단으로 기록하고, US에는 검증된 Oneil RS Rating을 활성화했습니다.
  시장 Regime은 트리거 배치의 결정론적 값을 매수 메시지까지 일관되게 사용합니다.
- KIS 응답의 빈 문자열·`None`·비정상 숫자는 안전한 float/int 변환을 거치며, 잘못된 보유 상태를
  임의의 0으로 확정하지 않고 unknown으로 보존합니다.
- 후보 성과와 실제 매매 성과를 분리했습니다. 후보는 7/14/30일 추적, 실제 매매는 승률·평균 손익·
  Profit Factor를 따로 계산하며, 성과 점수 조정은 기본 shadow입니다.
- 오후 상승률 트리거 안에 **Emerging Liquidity Lane**을 추가했습니다. 한국은 50~100억 원, 미국은
  5천만~1억 달러에서 하루 1개만 별도로 정규화해 기존 Standard 후보와 경쟁합니다. 최종 최대 3종목과
  10슬롯은 늘리지 않았고, RS·MA·돌파를 새 하드게이트로 만들지 않았습니다.
- 한국 Naver fallback도 50억 원 후보의 상세 OHLC를 가져오도록 맞췄으며, 결과 JSON에는 lane·순위·
  임계값을 기록합니다.

## 4. Insight·Archive: 오래 걸려도 근거를 잃지 않는 연구

- `/insight` 도움말과 중단 안내를 추가하고 nohup 대신 systemd 상주 서비스로 운영합니다.
- 실측 100초를 넘는 합성 작업이 90초 제한 때문에 버려지던 문제를 고쳐 기본 HTTP timeout을
  300초로 늘렸습니다. 합성 모델은 검증된 Sonnet 5를 기본으로 하되 환경변수로 교체할 수 있습니다.
- Sonnet 5가 도구 호출만 반복하지 않도록 iteration·프롬프트·최종 답변 경계를 조정하고, systemd
  PATH에서 무료 MCP 실행 파일을 찾지 못하던 문제를 고쳤습니다.
- 깨진 구조화 복구 결과가 이전 대화를 재생하지 않도록 했고, US 리포트 ingestion이
  `prism-us/cores` 네임스페이스에 가려지지 않게 복원했습니다.
- 검색 결과에는 도구 provenance와 실제 근거를 요구합니다. 연구 호출 수를 제한하면서 서로 다른
  종목 추천을 유지하고, 근거 없는 넓은 스크리닝은 외부 검색이 아니라 Archive 안에서만 수행합니다.
- 최종 텔레그램 출력은 XML 잔여물과 읽기 어려운 구조를 제거해 사람이 바로 읽을 수 있게 했습니다.

## 5. 시장 데이터·MCP·런타임 정합성

- KRX 로그인 없이 최근 투자자 수급을 읽는 Naver fallback을 추가하고, 레거시 리포트 소스 순서에도
  안전하게 삽입했습니다.
- 장중에는 KIS 투자자 추정치를 사용하고, 개인·기타 합계뿐 아니라 `기타법인 + 기타단체` 잔차도
  분리해 차트와 MCP에 제공합니다.
- US 리포트의 뉴스·시장 MCP를 복원하고, 외부 time MCP는 로컬 결정론적 시간 서버로 대체했습니다.
  OpenAI SDK 호환 버전도 고정했습니다.
- 공식 Perplexity MCP 1.2.0과 검증된 Firecrawl MCP 3.23.6을 핀으로 고정해 호스트별 `latest`
  드리프트를 막았습니다.
- 존재하지 않는 US 압축 runner를 cron에서 제거하고, Docker·MCP 문서를 실제 런타임과 맞췄습니다.
- 깨끗한 Stance 배포 worktree가 KIS 비밀 설정을 저장소 밖 `KIS_CONFIG_ROOT`에서 읽을 수 있게 했습니다.

## 6. ClickStack 관측성·Insights 대시보드

- 거래 경로는 로컬 JSONL에만 원자적으로 append하고, 별도 shipper가 SSH tunnel을 통해 ClickStack으로
  전송합니다. 네트워크·ClickStack·대시보드 장애는 매매 판단을 막지 않는 fail-open 경계입니다.
- ClickStack 컨테이너 자원 제한, 180일 TTL, 인증 Nginx, shipper token 파일, SELinux enforcing과
  Rocky Linux systemd 실행 사용자를 정리했습니다.
- 검증된 KR·US 거래, watchlist 후보 결과, Regime snapshot과 실제 서버 deploy reflog만 백필했습니다.
  백필 이벤트는 `ingestion_mode=backfill`로 구분해 실시간 이벤트처럼 보이지 않게 했습니다.
- 기존 앱의 Insights 탭을 관측 센터로 바꾸어 시장별 실제 매매·후보 성과, Trigger 비교, Regime 분포,
  데이터 품질과 배포 전후 14일 영향을 보여줍니다.
- 대시보드 snapshot은 제한된 ClickHouse transport로 생성하고 임시 파일 후 원자적으로 배포합니다.
  동적 실행 표면을 제거하고 systemd timer의 one-shot 재실행 시점을 조정했습니다.
- 2026-08-27 검증 snapshot은 총 2,149건(백필 2,140·실시간 9), 실제 매매 KR 136건·US 103건,
  후보 결과 KR 459건·US 657건을 포함합니다.

## 7. 이중 라이선스·PDF·제3자 고지

- CLA에 이중 라이선스 기여 동의를 명시하고 한국어 문서를 추가했습니다. 서명하지 않은 기여자에게는
  실제로 열 수 있는 동의 링크와 다음 행동을 안내합니다.
- 상업 배포 경계를 명확히 하기 위해 copyleft PDF 변환 fallback과 관련 의존성을 제거했습니다.
  Unicode·URL·Markdown 구두점·줄바꿈 회귀 테스트로 현재 Playwright 경로를 보호합니다.
- `THIRD_PARTY_NOTICES.md`와 라이선스 원문을 Docker·Python wheel·source archive에 포함하고,
  누락되면 CI가 실패하도록 했습니다.

## 8. Shadow 기능의 만료와 운영 결정

- Shadow 기능마다 종료일·최소 표본·검토 상태를 기록하고, 기한이 지나면 자동으로 계속 쌓이지 않게
  lifecycle 검사를 추가했습니다.
- cron 실행 시 import 경로가 달라도 동작하게 했고, 유지·종료·연장 같은 수동 결정과 이유를
  감사 가능한 형태로 남깁니다.
- Shadow는 관찰 상태일 뿐 자동 LIVE 승격을 의미하지 않습니다.

## 9. BTC: 작은 변경도 독립적으로 기록

- 아직 확정되지 않은 spot 봉을 cursor가 지나쳐 버리지 않고 다시 조회해 확정 봉으로 승격합니다.
- 텔레그램 보고서에 현재 활성 스윙 포지션을 포함해, 엔진 상태와 사용자 보고가 어긋나지 않게 했습니다.
- 기존 L-layer의 기본 비활성 상태는 바꾸지 않았습니다.

## 개발자용 상세 — 동일 가중치 커밋 집계

이번 분석은 `v2.20.0..v2.21.0`의 **모든 커밋을 오래된 순서부터 확인**하고, 커밋 하나를 모두
1표로 취급했습니다. 변경 줄 수, PR 크기, 작성자와 기능의 화제성을 가중치로 사용하지 않았습니다.
한 커밋이 여러 주제에 걸쳐도 주된 목적 하나에만 배정했고, 병합 커밋도 숨기지 않고 별도 한 표로
계산했습니다.

<details>
<summary>202개 커밋의 주제별 집계 펼치기</summary>

| 주제 | 동일 가중치 커밋 수 | 핵심 범위 |
|---|---:|---|
| 카카오·텔레그램·리포트 | 35 | 근거형 종목 질문, 출처·PDF, 병렬 리포트, 자동 스토리, moderation |
| Stance 공개 프로토콜·리더보드 | 34 | 불변 원장, 일별 마킹, HTTP, 대시보드, AI 온보딩, no-backfill |
| 트레이딩·스크리닝·피드백 | 11 | 반등 파일럿, 결정론 게이트, Oneil RS, 후보/실거래 분리, Emerging Lane |
| Insight·Archive | 11 | Sonnet 5, timeout, 도구 provenance, bounded research, US ingestion |
| 시장 데이터·MCP·런타임 | 11 | KIS 장중 수급, Naver fallback, MCP pin, cron·설정 경로 |
| ClickStack·대시보드 관측성 | 10 | fail-open spool/shipper, ClickStack, 백필, Insights, 배포 영향 |
| 라이선스·CLA·배포 경계 | 6 | 이중 라이선스 동의, PDF 경로, 제3자 고지 배포 검사 |
| Shadow 운영 거버넌스 | 3 | 만료, cron, 수동 lifecycle 결정 |
| BTC | 2 | spot 확정 봉 재조회, 활성 스윙 포지션 보고 |
| **병합 커밋** | **79** | PR merge와 직접·동기화 merge를 각각 별도 1표로 계산 |
| **합계** | **202** | `v2.20.0` 이후 PR #615까지의 제품 변경 전체 |

</details>

## 개발자용 상세 — PR별 변경 규모

아래 목록은 **PR 병합 역순**이며, 크기는 GitHub PR 메타데이터 기준 참고값입니다. 직접 커밋과
PR이 아닌 병합도 위 커밋 집계에는 포함하지만 이 표에는 억지로 PR 번호를 붙이지 않았습니다.

<details>
<summary>이번 범위의 PR 75개 전체 목록 펼치기</summary>

| PR | 주제 | 규모 |
|---|---|---|
| #615 | Emerging Liquidity screening lane | 4 files, +258/−18 |
| #614 | ClickStack component release 정렬 | 2 files, +2/−2 |
| #613 | 관측성 Insights 대시보드와 검증 백필 | 14 files, +1,944/−5 |
| #612 | Rocky SELinux에서 관측 서비스 실행 | 2 files, +6/−8 |
| #611 | shipper token 안전 로드 | 2 files, +5/−0 |
| #610 | ClickStack runtime ingestion 강화 | 4 files, +46/−5 |
| #609 | fail-open trading observability pipeline | 14 files, +1,068/−12 |
| #608 | 후보와 실제 Trigger 성과 피드백 분리 | 7 files, +629/−298 |
| #607 | 배포물별 LGPL·제3자 고지 보존 | 10 files, +1,183/−2 |
| #606 | copyleft PDF 변환 의존성 제거 | 4 files, +205/−44 |
| #605 | KIS 응답 safe float/int 변환 | 2 files, +108/−16 |
| #604 | CLA 서명 안내 링크 개선 | 2 files, +8/−2 |
| #601 | Stance 최신 판단 리더보드 표시 | 4 files, +83/−0 |
| #600 | PRISM Stance 판단 안정적 발행 | 7 files, +311/−42 |
| #599 | 상승 전환 파일럿으로 과차단 완화 | 9 files, +454/−19 |
| #598 | Stance 가치 중심 대시보드 제목 복원 | 1 file, +17/−4 |
| #597 | Stance no-backfill 약속 명시 | 6 files, +36/−21 |
| #596 | 등록보다 리더보드 먼저 표시 | 2 files, +38/−20 |
| #595 | README 후원 배치 보존 | 5 files, +80/−80 |
| #594 | README를 Stance 리더보드 중심으로 개편 | 9 files, +65/−50 |
| #593 | Stance 온보딩을 쉬운 말로 개정 | 5 files, +165/−113 |
| #592 | 전략 프로필·승인 온보딩 | 14 files, +436/−38 |
| #591 | 에이전트 온보딩·랭킹 시점 명확화 | 2 files, +53/−11 |
| #590 | Stance AI 에이전트 온보딩 | 6 files, +266/−6 |
| #589 | 외부 KIS 설정 경로 지원 | 3 files, +66/−4 |
| #588 | 읽기 쉬운 Insight 텔레그램 출력 | 3 files, +23/−1 |
| #587 | 넓은 Insight 스크리닝을 Archive로 제한 | 2 files, +41/−4 |
| #586 | 연구 호출 제한·추천 다양성 | 4 files, +203/−7 |
| #585 | Insight 근거·도구 provenance 강제 | 7 files, +341/−41 |
| #584 | US Archive 리포트 ingestion 복구 | 2 files, +63/−3 |
| #583 | 깨진 Insight 복구 이력 재생 차단 | 1 file, +36/−9 |
| #582 | Sonnet 5 궁합·systemd PATH 수정 | 2 files, +201/−15 |
| #581 | Insight timeout·Sonnet 5 전환 | 2 files, +50/−5 |
| #580 | Insight 도움말·중단 안내·systemd | 2 files, +123/−28 |
| #579 | 카카오 명령 답변 단일 결론 | 5 files, +560/−16 |
| #578 | 카카오 운영 systemd·수동 기동 금지 | 6 files, +108/−42 |
| #577 | Stance 서버·리더보드·대시보드 | 64 files, +10,120/−873 |
| #576 | MCP·Docker cron 문서 정렬 | 4 files, +92/−58 |
| #575 | Firecrawl MCP 3.23.6 고정 | 3 files, +32/−5 |
| #574 | Perplexity MCP 1.2.0 고정 | 2 files, +20/−5 |
| #573 | 존재하지 않는 US 압축 cron 제거 | 3 files, +24/−7 |
| #572 | BTC 미확정 spot 봉 재조회 | 2 files, +128/−15 |
| #571 | 보유 중 US 후보 판단 표시 | 2 files, +137/−1 |
| #570 | KIS 장중 개인·기타 잔차 공개 | 8 files, +127/−11 |
| #569 | US 뉴스·시장 MCP 복구 | 6 files, +150/−5 |
| #568 | KIS 장중 투자자 추정치 | 10 files, +337/−22 |
| #567 | Luna 검색 계획 | 8 files, +640/−36 |
| #566 | 종목 전망 질문 인식 | 2 files, +38/−0 |
| #565 | 만원 단위 평가 가격 해석 | 2 files, +27/−2 |
| #564 | 카카오 배치 스토리 자동 전달 | 20 files, +1,103/−63 |
| #563 | 자연어 보유종목 평가 | 8 files, +355/−108 |
| #562 | 투명한 비대화형 카카오 배치 브리핑 | 17 files, +450/−147 |
| #561 | KST 날짜 단위 리포트 캐시 | 2 files, +91/−6 |
| #560 | DB 오후 배치 리포트 병렬화 | 2 files, +101/−17 |
| #559 | 리포트 요청 해석·실패 캐시 거부 | 4 files, +106/−0 |
| #558 | 제한된 카카오 병렬 리포트 생성 | 4 files, +90/−7 |
| #557 | 시장별 텔레그램 리포트 대화 분리 | 2 files, +86/−21 |
| #556 | 텔레그램 정식 리포트 Luna high | 1 file, +6/−0 |
| #555 | 카카오 정식 리포트 Luna high | 2 files, +29/−0 |
| #554 | 레거시 리포트 소스 순서 보정 | 2 files, +14/−1 |
| #553 | 카카오 리포트 수급 fallback 활성화 | 2 files, +29/−1 |
| #552 | KRX 로그인 없는 최근 수급 fallback | 4 files, +201/−1 |
| #551 | 카카오 PDF 링크 복구 | 2 files, +29/−1 |
| #550 | 말풍선 분할 시 출처 항목 보존 | 2 files, +6/−3 |
| #549 | 내부 근거 ID 제거·출처 링크 압축 | 3 files, +41/−8 |
| #548 | 종목 별칭·자유 어순·URL 보존 | 3 files, +116/−18 |
| #547 | 매체명과 기사 URL 매칭 | 2 files, +5/−2 |
| #546 | 장중 종목 변동 원인 답변 | 2 files, +211/−7 |
| #545 | 종목 전망 최신성·출처 강화 | 3 files, +106/−11 |
| #544 | 채팅방 전체 한도 기본 해제 | 2 files, +16/−4 |
| #543 | 도움말·일일 한도 안내 | 4 files, +48/−8 |
| #542 | 분석 대기시간 안내 | 4 files, +13/−4 |
| #541 | 최신 근거 중심 종목 질문 | 2 files, +89/−5 |
| #540 | 평가 결과 가독성·말줄임 개선 | 2 files, +103/−8 |
| #539 | 종료된 카카오 명령 제거 | 4 files, +134/−1 |

</details>

## 검증 결과 — 코드와 운영 증거

- **범위 감사**: `v2.20.0` 이후 PR #615까지 202개 커밋을 오래된 순서로 확인하고, 비병합 123개를
  정확히 한 주제에 배정했습니다. 중복 0·누락 0이며 병합 79개를 별도로 계산했습니다.
- **PR 감사**: 실제 병합 PR 75개의 제목·파일·증감은 GitHub 메타데이터와 로컬 merge history를
  대조했습니다.
- **카카오**: 대표적으로 전체 카카오 회귀 380개, 자동 스토리 관련 346개·113개·43개 묶음이
  통과했습니다. 운영 Firecrawl에서 당일 직접 관련 기사와 최신 전망 검색도 확인했습니다.
- **Stance**: 리더보드·adapter·execution hook까지 214개 통과, 1개 skip. 판단 발행 경로는 별도
  47개 테스트와 Ruff를 통과했습니다.
- **트레이딩**: 반등 파일럿 KR 76개·US 11개, Emerging Lane 관련 KR 신뢰성 26개·US RS 17개·
  Naver fallback 11개가 통과했습니다. db-server 배포 파일은 main blob과 일치했습니다.
- **Insight**: grounding·tool budget·Archive-only·Telegram 가독성 관련 묶음이 각각 20~24개
  테스트를 통과했고, 실제 Sonnet 5 운영 질문으로 복구를 확인했습니다.
- **시장 데이터**: KIS 투자자 추정치 관련 80개, 개인·기타 잔차 관련 90개가 통과했고 표본 종목의
  KIS 필드 합계도 실측했습니다.
- **관측성**: 관련 72개와 후속 19개 테스트가 통과했습니다. shipper·tunnel·ClickStack·대시보드
  배포 이벤트가 ClickHouse까지 도달했고, 2026-08-27 snapshot 2,149건을 확인했습니다.
- **라이선스**: CLA 4개, PDF 회귀 10개, 배포물 라이선스 6개 테스트가 통과했습니다.

## 업데이트 방법

```bash
git fetch origin --tags
git checkout main
git pull --ff-only
```

운영 환경은 기존 KIS·LLM·Telegram/Kakao·ClickStack 비밀 설정을 계속 사용합니다. Stance를
연결하려면 별도 비밀키 경로와 단일 writer를 구성해야 하며, PRISM의 Stance hook은 명시적으로
활성화하기 전까지 주문 경로에 영향을 주지 않습니다.

## 참고 사항과 알려진 한계

- Stance v1은 현물 주식 기준이며 등록 이전 기록의 업로드·백필을 허용하지 않습니다.
- 상승 전환 파일럿은 특정 sideways+UPTREND 경계 후보에만 설정 주문액의 50%를 사용합니다.
- Trigger 성과 피드백과 손절 변동성 진단은 자동 정책 전환이 아니라 기본 shadow 관측입니다.
- ClickStack 대시보드는 결과·Regime·배포 중심입니다. 전체 스크리닝 유니버스의 단계별 탈락 사유와
  장중 시점 snapshot은 아직 수집하지 않습니다.
- US 오전 배치의 최대 3개는 보장 수량이 아닙니다. `Value-to-Cap Ratio`는 현재 `cap_df=None`이라
  비활성이고, 다른 후보가 없으면 1개만 선정될 수 있습니다.
- Emerging Liquidity Lane은 오후 상승률 트리거에만 적용되며, 별도 슬롯을 만들지 않습니다.
- GitHub 저장소 전체 pytest에는 import 시 `sys.exit()`하는 기존 스크립트형 테스트가 있어,
  관련 focused suite와 CI를 병합 기준으로 사용합니다.

## 텔레그램 공지

### 한국어

```
🚀 PRISM-INSIGHT v2.21.0 — Stance 공개 검증 · ClickStack 관측성 · 근거형 봇과 매매 개선

이번 버전은 매매 판단을 공개해 장기 성과로 검증하고, 운영 변경과 실제 결과를 한 화면에서
추적하며, 봇과 매매 파이프라인의 과차단·근거 부족을 함께 줄였습니다.

🏁 1) Stance 공개 프로토콜
· 등록 이후 판단만 불변 원장에 기록하고 일별 평가·리더보드 제공
· 백필 없는 기록, 전략 프로필, AI 에이전트 온보딩, 최신 판단 카드

📊 2) ClickStack 관측 센터
· fail-open 이벤트 수집, 180일 보존, 후보/실거래·Regime·배포 영향 대시보드
· 검증된 거래·후보·배포 기록만 백필하고 실시간 이벤트와 구분

📈 3) 매매·스크리닝
· KR·US 결정론 게이트, 상승 전환 50% 파일럿, US Oneil RS
· 후보/실거래 성과 분리, KR 50~100억·US $50M~$100M Emerging Lane

💬 4) 카카오·텔레그램·Insight
· 최신 근거 기반 종목 질문, 장중 원인, 출처·PDF, 자동 배치 스토리
· Insight Sonnet 5, 긴 연구 timeout 보정, 도구 출처와 읽기 쉬운 결론

🛡️ 5) 운영·배포 신뢰성
· KIS 장중 수급, MCP 버전 고정, Shadow 만료 관리
· 이중 라이선스 CLA, copyleft PDF 경로 제거, 배포물 제3자 고지 검사
· BTC 미확정 봉 재조회와 활성 스윙 포지션 보고
```

### English

```
🚀 PRISM-INSIGHT v2.21.0 — Public Stance verification · ClickStack observability · Grounded bots and trading

This release makes trading decisions verifiable over time, connects deployments to outcomes, and reduces
unsupported bot answers and over-blocked trading opportunities.

🏁 1) Stance protocol
· Immutable post-registration decisions, daily marking and strategy leaderboards
· No backfills, strategy profiles, AI-agent onboarding and latest-decision cards

📊 2) ClickStack observability
· Fail-open event shipping, 180-day retention, candidate/actual, regime and deployment-impact views
· Verified backfills remain distinguishable from live events

📈 3) Trading and screening
· Deterministic KR/US gates, a guarded 50% rebound pilot and US Oneil RS
· Candidate-vs-actual feedback plus KR 5B–10B / US $50M–$100M Emerging Lanes

💬 4) Kakao, Telegram and Insight
· Fresh evidence for stock questions, intraday explanations, sources, PDFs and automated batch stories
· Sonnet 5 Insight synthesis, longer research windows, tool provenance and readable conclusions

🛡️ 5) Operations and distribution
· KIS intraday flows, pinned MCPs and expiring shadow experiments
· Dual-license CLA, removal of copyleft PDF paths and third-party notice checks
· BTC unconfirmed-candle refetch and active swing-position reporting
```
