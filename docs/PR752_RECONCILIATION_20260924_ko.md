# PR #752 최종 이관 대조표 (2026-09-24)

## 판정 범위와 원칙

이 문서는 PR을 통째로 병합하지 않고 필요한 구현을 후속 배포로 옮기기 위한 경로별 기록이다. “같음”은 파일 바이트 비교 결과이며, “다름”만으로 누락이나 퇴행이라고 판정하지 않는다. 운영 기준은 배포된 것으로 전달받은 Git 커밋 `831a3f6c`이고 서버의 현재 상태를 이 문서 작성자가 재조회하지는 않았다. 후보는 감사 시점 `b86e5b872f11e313ecf669f1c354445af43f2c1a`다. 이후 변경·배포 여부는 최종 배포 기록으로 추가 증명해야 한다.

- 구 PR head: `58ec071f`.
- 보존할 작업 디렉터리: `/Users/rocky/work/prism-insight-us-full-validation`.
- 후보: `/Users/rocky/work/prism-insight-dart-depth`.
- 구 PR과 운영의 merge-base: `47b58e1556b066af9a652d1dc6d7b7a1ddf3be2d`.
- PR 변경 188개, 로컬 미커밋 100개, 합집합 265개 경로를 모두 아래에 수록했다.
- 로컬 미커밋 경로명+내용 결합 SHA-256: `eec524a3365839a9c51f68b22f4823860f53b58368513ac0a7223bdebec5309e` (삭제 항목은 `<DELETED>` 표지).
- 미커밋 원본을 삭제·되돌림·정리하지 않았다. 문서 작성 외 코드 수정, 네트워크, 모델 호출, 운영 작업은 하지 않았다.

## 결론: 필요한 이관과 남은 승인 조건

### 이미 사용하거나 최신 구현으로 대체한 부분

1. DART 신원·목록·viewer·주석 범위·HTML/병합 표/codec·materiality는 운영에 있다. 원래 파일과 동일한 것과 후속 수정을 받은 것을 아래에서 구분한다. `filing_html.py`의 로컬 수정본도 운영과 바이트가 같다.
2. 원문 source-tree와 표 해독은 아래와 같이 후보에 필요한 순수 구현만 이관했다. 이름이 달라졌다고 빠진 것이 아니다.
   - `tools/source_tree_catalog.py` → `prism_core/dart_source_tree_catalog.py`: 원문 트리 기반 구현 재사용, 해독 안내 추가.
   - `tools/source_tree_routing.py` → `prism_core/dart_source_tree_routing.py`: 원문 단위 배정 원리 재사용, 세 집필 역할 통합.
   - `cores/report_specialist_evidence.py` → `prism_core/dart_specialist_roles.py`: 기존 전문 주제 분류 재사용. 구 7개 작성 호출을 그대로 활성화한 것은 아니다.
   - `cores/report_table_evidence.py` → `prism_core/dart_source_table_evidence.py`: 병합 셀 정렬/역변환 재사용. 현재 `dart_chapter_sources.py`가 용량·선택 원문 보존·기간/헤더 보조자료를 조합한다.
   - 구 실험 작성/전달 파이프라인 대신 `cores/dart_deep_analysis.py`와 현재 `cores/analysis.py`, `report_generator.py`, `telegram_ai_bot.py`가 장·캐시·평가·BUY 전달을 담당한다.
3. 미국 분석가/매매 프롬프트·차트의 일부 로컬 최종본은 이미 운영과 동일하다. 나머지는 SEC/공개지표 등 후속 운영 수정이 있으므로 구 WIP로 덮어쓰지 않는다.
4. 경쟁사 숫자 비교는 새 `prism_core/kr_peer_comparison.py`가 같은 기간·공급자·실적/예상 구분을 검사한다. 구 `research_comparison.py`는 관측 구조 검사일 뿐 수집/사실 판정을 하지 않으므로 통째로 가져와도 미확인 문제가 해결되지 않는다.

### 의도적으로 재활성화하지 않을 부분

- TradingView 코드·설정·테스트: 사용자 제거 결정 유지. 삭제를 누락으로 보지 않는다.
- `cores/report_fact_registry.py`, `cores/report_specialists.py`, 구 `report_specialist_agents.py` 및 V2~V4 출력 계약: 실패한 구조화 집필 실험을 운영에 되살리지 않는다. 원본·테스트는 연구 보존한다. `PR752_KR_FINISH_US_RESEARCH_SCOPE_20260923_ko.md`의 중단 결정과 현 후보의 실제 실패 검증 기록을 따른다.
- `report_source_budget.py`의 6KB 원문 노트 제한과 구 insight-prefetch용 tool budget/profile을 새 심층 원문 경로에 무작정 이식하지 않는다. 선택 원문 무손실 공급과 상충한다. 새 경로는 자체 역할/총용량 검사 및 실패 표시를 사용한다.
- `snapshot_price_leaders.py`와 구 trigger/orchestrator 변경: 스크리닝 변경이 아니라 관측된 장중 업종군 수익률 설명용 opt-in이다. 동일 기간 경쟁사 종가 수익률, 사업 선도, 눌림목 SHADOW를 입증하지 않는다. 이번 DART/재무 peer 범위의 필수 이관에서 제외하고 연구 보존한다.
- 구 SEC inline/검색 회복/insight manifest 흐름은 별도 opt-in 연구 파이프라인이다. 현재 미국 공식 자료 경로를 덮어쓰지 않고 보존한다. 미래 SEC 세그먼트·검색 회복 작업에서 독립 평가할 수 있으나 현재 한국 DART 집필 오류 해결의 전제는 아니다.

### 반드시 해결할 내용 (추가 구 코드 복사와 별개)

추가로 가져와야만 현재 DART/재무 peer 요구가 작동하는 구 소스 파일은 이 감사에서 발견하지 못했다. 그러나 **기존 PR의 유용한 구조가 이관됐다는 것과 생성문 숫자가 맞는 것은 별개**다. 현재 후보의 연도·합계/개별 열·영업외 손익 오귀속에 대한 실제 원문 대조가 배포 차단 조건이다. 기업별 수동 숫자 치환으로 자동 생성 성공을 주장하지 않는다.

## #752를 닫을 수 있는 조건

- 위 재사용 구현과 현재 수정분이 후속 PR의 정확한 head에 포함되고 CI/내용 검증을 통과한다.
- 경쟁사 실제 수집, DART 주요 금액·기간·조건, PDF→BUY 및 `/evaluate` 보존을 확인한다.
- DB/app의 동일 배포 커밋, 봇 재시작/정상 시작, 원래 cron/주문 정책 보존을 기록한다.
- #752에 이 대조표와 후속 PR/배포 커밋, 명시적 제외·연구 보존 경로를 연결한 다음 **superseded로 close**한다. “35개 커밋과 WIP 전부 운영 반영”이라고 쓰지 않는다.
- 원 PR branch와 100개 dirty WIP는 별도 명시적 폐기 승인 없이 유지한다. PR close는 코드/연구 파일 삭제 승인이 아니다.
- 별도 SHADOW 눌림목 설계는 TODO로만 남기며 이 배포에서 운영 규칙을 바꾸지 않는다.

## 경로별 판독법

각 줄의 `[PR/WIP]`는 구 PR 또는 미커밋 목록에 포함됐다는 의미다. `운영/후보=같음`은 비교 대상인 구 작업 디렉터리의 최종 내용과 동일하다. `구PR동일`은 WIP와는 다르지만 PR head 원본과 같다. `변경됨`은 파일은 있으나 내용이 다르다. `없음`은 해당 tree에 없다. 연구 보존 항목을 운영 완료로 계산하지 않는다.


## 소스·실행·설정 경로 (84개)

- `.github/workflows/ci.yml` [PR/WIP] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `cores/agents/__init__.py` [PR/WIP] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `cores/agents/report_agent.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `cores/agents/report_specialist_agents.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `cores/analysis.py` [PR/WIP] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `cores/llm/backends/openai_agents_backend.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `cores/llm/ports.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `cores/llm/tool_result_budget.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `cores/report_calculations.py` [WIP] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `cores/report_fact_registry.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `cores/report_generation.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `cores/report_specialist_evidence.py` [WIP] — 이관·수정 → prism_core/dart_specialist_roles.py; 운영=없음, 후보=없음.
- `cores/report_specialists.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `cores/report_table_evidence.py` [WIP] — 이관·수정 → prism_core/dart_source_table_evidence.py; 운영=없음, 후보=없음.
- `prism-us/cores/agents/__init__.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `prism-us/cores/agents/company_info_agents.py` [WIP] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `prism-us/cores/agents/trading_agents.py` [WIP] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `prism-us/cores/data_prefetch.py` [PR/WIP] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `prism-us/cores/stock_chart.py` [WIP] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `prism-us/cores/us_analysis.py` [PR/WIP] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `prism-us/cores/us_data_client.py` [WIP] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `prism_core/dart_identity.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `prism_core/dart_public_filings.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `prism_core/dart_report_evidence.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `prism_core/dart_section_html.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `prism_core/dart_viewer_tree.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `prism_core/filing_catalog.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `prism_core/filing_html.py` [PR/WIP] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `prism_core/filing_html_codec.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `prism_core/filing_html_policy.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `prism_core/filing_html_projection.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `prism_core/filing_html_tables.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `prism_core/filing_materiality.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `prism_core/filing_report_evidence.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `prism_core/filing_selection.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `prism_core/filing_structure.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `prism_core/filing_table_projection.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `prism_core/material_filing_selection.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `prism_core/report_insight_manifest.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `prism_core/report_insight_prefetch.py` [PR/WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `prism_core/report_research_context.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `prism_core/report_research_prefetch.py` [PR/WIP] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `prism_core/report_source_budget.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `prism_core/report_source_tree_context.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `prism_core/research_comparison.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `prism_core/research_table_observations.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `prism_core/search_discovery_fallback.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `prism_core/sec_inline_evidence.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `prism_core/sec_public_filings.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `prism_core/sec_report_evidence.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `prism_core/snapshot_price_leaders.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `prism_core/source_observation_quality.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `prism_core/tradingview_collection.py` [PR/WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `prism_core/tradingview_credentials.py` [PR/WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `prism_core/tradingview_evidence.py` [PR/WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `prism_core/tradingview_transport.py` [PR/WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `stock_analysis_orchestrator.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `tools/audit_report_mcp_capabilities.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/capture_dart_fixture.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/capture_dart_large_section.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/configure_report_research.py` [PR/WIP] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `tools/dart_fixture_transport.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/evaluate_filing_extraction.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/evaluate_filing_report_quality.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/evaluate_general_filing_reports.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/evaluate_large_filing_html.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/evaluate_source_first_gold.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/evaluate_source_tree_gold.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/evaluate_us_inline_cohort.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/evidence_window_roundtrip.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/extracted_window_bridge.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/isolated_responses_proxy.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `tools/offline_split_delivery.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/plan_evidence_windows.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/probe_dart_filings.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/probe_dart_note_children.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/run_kr_role_split_ab.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/run_kr_source_tree_e2e.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/source_tree_catalog.py` [WIP] — 이관·수정 → prism_core/dart_source_tree_catalog.py; 운영=없음, 후보=없음.
- `tools/source_tree_delivery.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/source_tree_routing.py` [WIP] — 이관·수정 → prism_core/dart_source_tree_routing.py; 운영=없음, 후보=없음.
- `tools/summarize_role_ab.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/validate_dart_cohort.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `trigger_batch.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.

## 테스트 경로 (103개)

- `cores/llm/tests/test_ci_sdk_pins.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `cores/llm/tests/test_tool_result_budget.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_buy_gate.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `tests/test_capture_dart_fixture.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_capture_dart_large_section.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_dart_fixture_html_limits.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_dart_fixture_transport.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_dart_html_limits.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `tests/test_dart_identity.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_dart_note_fragment_collection.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_dart_note_fragment_context.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `tests/test_dart_public_filings.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_dart_report_evidence.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `tests/test_dart_section_collection.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_dart_viewer_tree.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_evidence_window_plan.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_evidence_window_roundtrip.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_extracted_window_bridge.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_filing_catalog.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_filing_extraction_evaluation.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_filing_grid_delivery.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_filing_grid_packing.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_filing_html.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_filing_html_codec.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_filing_html_grid_codec.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_filing_html_layout_context.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_filing_html_limits.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_filing_html_nested_layout.py` [WIP] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_filing_html_note_scope.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_filing_html_projection.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_filing_html_streaming.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_filing_html_streaming_adversarial.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_filing_html_tables.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_filing_materiality.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_filing_metadata_compaction.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_filing_provenance_compaction.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_filing_report_evidence.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_filing_report_quality.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_filing_selection.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_filing_statement_routing.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_filing_structure.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_filing_table_projection.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_general_filing_report_evaluation.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_general_research_adversarial.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_general_research_discovery.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_html_projection_delivery.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_insight_leader_rendering.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_isolated_responses_proxy.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `tests/test_kr_insight_leaders_integration.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_kr_role_split_ab.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_kr_source_tree_e2e.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_large_filing_evaluation.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_latest_filing_report_wiring.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_material_filing_selection.py` [PR] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_material_filing_wiring.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_material_html_retrieval.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_material_report_consumers.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_offline_split_delivery.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_probe_dart_filings.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_probe_dart_note_children.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_report_calculations.py` [WIP] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_report_fact_registry.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_report_insight_manifest.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_report_insight_prefetch.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_report_insight_wiring.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_report_mcp_capability_audit.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_report_research_prefetch.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `tests/test_report_source_budget.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_report_source_tree_context.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_report_sources_without_tradingview.py` [WIP] — 명시적 폐기/제거 이력 보존; 운영=변경됨, 후보=변경됨.
- `tests/test_report_specialist_evidence.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_report_specialists.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_report_table_evidence.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_report_v3_contract.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_report_v4_contract.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_research_comparison.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_research_excerpt_integrity.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `tests/test_research_profile_wiring.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_research_scoped_config.py` [PR] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `tests/test_research_table_observations.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_search_discovery_fallback.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_sec_inline_evidence.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_sec_public_filings.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_sec_report_evidence.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_snapshot_price_leaders.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_source_first_gold.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_source_observation_quality.py` [PR/WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_source_tree_catalog.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_source_tree_delivery.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_source_tree_gold.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_source_tree_routing.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_summarize_role_ab.py` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_tradingview_collection.py` [PR/WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `tests/test_tradingview_credentials.py` [PR/WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `tests/test_tradingview_document_discovery.py` [PR/WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `tests/test_tradingview_evidence.py` [WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `tests/test_tradingview_transport.py` [PR/WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `tests/test_us_analyst_data_quality.py` [WIP] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `tests/test_us_evidence_pipeline_contract.py` [WIP] — 최신 수정/대체 유지; 운영=변경됨, 후보=변경됨.
- `tests/test_us_inline_cohort.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_us_material_report_consumer.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tests/test_us_pdf_buy_handoff.py` [WIP] — 재사용(바이트 동일); 운영=같음, 후보=같음.
- `tests/test_validate_dart_cohort.py` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.

## 문서·인수인계 경로 (75개)

- `docs/DART_ACQUISITION_CONTRACT_20260920.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/DART_PARSER_QUALITY_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/DART_PUBLIC_FILING_ACQUISITION_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/FILING_EXTRACTION_EVALUATION_20260919_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/GENERALIZATION_AND_NEWS_RECOVERY_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/GENERALIZATION_NEWS_CONTRACTS_20260920.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/GENERAL_COMPETITIVE_RESEARCH_20260918_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/GENERAL_FILING_ROBUSTNESS_PLAN_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/GENERAL_FILING_ROBUSTNESS_RESULTS_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/LARGE_FILING_ROBUSTNESS_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/LATEST_FILING_REPORT_PLAN_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/LATEST_FILING_REPORT_VALIDATION_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/MATERIAL_DISCLOSURES_AND_TRADING_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/MCP_INSIGHT_PREFETCH_20260919_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_BOUNDED_PARSER_REPAIR_PLAN_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_BOUNDED_PARSER_REPAIR_RESULTS_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_COMPLETION_PRD_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_EVIDENCE_WINDOW_PROTOCOL_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_EVIDENCE_WINDOW_RESULTS_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_EXECUTION_LEDGER_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_EXTRACTED_WINDOW_BRIDGE_PROTOCOL_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_EXTRACTED_WINDOW_BRIDGE_RESULTS_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_FINAL_INPUT_QUALITY_DESIGN_20260921_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_FINAL_INPUT_QUALITY_RESULTS_20260921_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_FRESH_KR_SOURCE_FIRST_PROTOCOL_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_FRESH_KR_SOURCE_FIRST_RESULTS_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_HTML_8MIB_DESIGN_20260921_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_HTML_8MIB_RESULTS_20260921_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_KR_FINISH_US_RESEARCH_SCOPE_20260923_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_KR_LOCAL_E2E_PROTOCOL_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_KR_LOCAL_E2E_RESULTS_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_PACKET_BUDGET_COMPARISON_DESIGN_20260921_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_PACKET_BUDGET_COMPARISON_RESULTS_20260921_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_ROLE_SPLIT_AB_PROTOCOL_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S1_FIXTURE_DESIGN_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S2A_SCOPE_DESIGN_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S2B_WIDE_GEOMETRY_DESIGN_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S2C2_VIEWER_GRAPH_DESIGN_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S2C2_VIEWER_GRAPH_RESULTS_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S2C3_FRAGMENT_CONTEXT_DESIGN_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S2C3_FRAGMENT_CONTEXT_RESULTS_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S2C4_FRAGMENT_COLLECTION_DESIGN_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S2C4_FRAGMENT_COLLECTION_RESULTS_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S2C5_LAYOUT_CONTEXT_DESIGN_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S2C5_LAYOUT_CONTEXT_RESULTS_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S2C_LARGE_DIAGNOSTIC_DESIGN_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S2C_LARGE_DIAGNOSTIC_RESULTS_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S3A_HTML_CODEC_DESIGN_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S3A_HTML_CODEC_RESULTS_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S3_NEXT_DESIGN_NOTES_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_S3_SAMSUNG_LIFE_GOLD_20260920_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_SOURCE_TREE_PROTOCOL_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_SOURCE_TREE_RESULTS_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_SPLIT_DELIVERY_DESIGN_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_SPLIT_DELIVERY_RESULTS_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_WHOLE_RECORD_GRID_DESIGN_20260921_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_WHOLE_RECORD_GRID_RESULTS_20260921_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_WINDOW_ROUNDTRIP_PROTOCOL_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/PR752_WINDOW_ROUNDTRIP_RESULTS_20260921_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/REMOVE_TRADINGVIEW_ANALYST_DATA_PLAN_20260923_ko.md` [WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `docs/REMOVE_TRADINGVIEW_ANALYST_DATA_RESULT_20260923_ko.md` [WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `docs/REPORT_RESEARCH_ASIS_TOBE_20260919_ko.md` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/TRADINGVIEW_CAPABILITY_AUDIT_20260918_ko.md` [WIP] — 명시적 폐기/제거 이력 보존; 운영=변경됨, 후보=변경됨.
- `docs/TRADINGVIEW_CREDENTIAL_AND_FILING_CONTRACTS_20260919.md` [PR/WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `docs/TRADINGVIEW_MODULE_CONTRACTS_20260919.md` [PR/WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `docs/TRADINGVIEW_OPTIONAL_REPORT_DESIGN_20260917_ko.md` [WIP] — 명시적 폐기/제거 이력 보존; 운영=변경됨, 후보=변경됨.
- `docs/TRADINGVIEW_P3B_P4A_20260919_ko.md` [PR/WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `docs/TRADINGVIEW_PHASED_IMPLEMENTATION_20260919_ko.md` [PR/WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `docs/TRADINGVIEW_REMOVAL_20260923_ko.md` [WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `docs/TRADINGVIEW_REPORT_AND_MICRO_REVIEW_20260918_ko.md` [WIP] — 명시적 폐기/제거 이력 보존; 운영=변경됨, 후보=변경됨.
- `docs/TRADINGVIEW_USAGE_DECISION_20260920_ko.md` [PR/WIP] — 명시적 폐기/제거 이력 보존; 운영=없음, 후보=없음.
- `docs/US_PIPELINE_ACCOUNT_ROLLBACK_PLAN_20260923_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `docs/US_PIPELINE_ACCOUNT_ROLLBACK_RESULT_20260923_ko.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `handoff.md` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `handoff.me` [WIP] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.

## 고정 입력 경로 (3개)

- `tools/fixtures/dart_generalization_20260920.json` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/fixtures/filing_generalization_round3_20260920.json` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.
- `tools/fixtures/large_filing_provider_20260920.json` [PR] — 연구 보존(운영 미이관); 운영=없음, 후보=없음.

## 감사 검증

- `git diff --name-only <merge-base> 58ec071f`와 구 worktree `git status --porcelain -uall`의 합집합을 자동 생성해 목록 누락을 방지했다.
- 각 경로에 `git show 831a3f6c:<path>`, `git show 58ec071f:<path>`, 후보/구 WIP 파일 바이트를 대조했다.
- `trigger_batch.py`, `stock_analysis_orchestrator.py`, report agent/LLM tool-budget 변경, source-tree/grid 구현 차이와 실패 인수인계를 직접 검토했다.
- 이 작업은 Markdown 문서만 추가한다. Python 진단·빌드·실행 테스트는 대상이 아니다. `git diff --check`와 265개 경로 포함 검사를 수행한다. 생성 보고서의 투자 정확성이나 운영 배포 성공을 이 문서만으로 인증하지 않는다.
