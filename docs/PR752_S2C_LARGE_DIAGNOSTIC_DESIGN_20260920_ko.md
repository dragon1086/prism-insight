# S2c-1: 승인된 대형 주석 원문 1건의 진단용 취득

## 승인과 범위

사용자는 휴대폰 OAuth 복구 후, 운영의 2 MiB 상한은 유지하면서 삼성생명 연간 주석을 별도 비공개 진단용으로 최대 8 MiB까지 취득하는 데 동의했습니다. 이번 작업은 원문 확보와 구조 확인뿐입니다. 원문 전체를 운영 파서·모델·보고서로 전달하거나 기존 수집/replay 상한을 올리지 않습니다.

대상은 이전에 선택한 receipt `20260331004244`의 viewer tuple `dcmNo=11213317, eleId=24, offset=977339, length=14241233, dtd=dart4.xsd`입니다. URL length는 HTML 응답 바이트 수가 아닙니다. 기존 불완전 capture는 보존하며 새 원문은 현재 관측본이지 과거 응답과 동일함을 증명하지 않습니다.

## 설계

- 별도 `tools/capture_dart_large_section.py`와 테스트를 추가합니다. 운영 코드는 변경하지 않습니다.
- 대상 viewer URL을 상수로 고정합니다. 임의 URL·회사·본문 범위·상한을 입력으로 받지 않습니다. `--live` 없이는 호출하지 않습니다. 기존 request allowlist로 URL도 검사합니다.
- HTTPX AsyncClient로 HTTPS GET 1회만 수행합니다. 인증·쿠키·환경 proxy·redirect·재시도·압축 응답을 사용하지 않습니다. HTTP 200/identity encoding만 받고 다른 상태는 본문 없이 거절합니다.
- asyncio.wait_for로 네트워크 전체 60초, 개별 timeout 10초를 적용합니다. 실제 raw chunk를 누적하기 전에 개별 8 MiB를 검사합니다. UTF-8은 엄격히 확인합니다. 초과·중단·잘못된 인코딩은 완성 원문을 저장하지 않습니다.
- 기존 fixture `_path`/`_write`의 Git 밖/심볼릭 링크 거절과 O_EXCL/0600 쓰기를 재사용합니다. 새 디렉터리는 0700이며 INCOMPLETE marker를 먼저 만듭니다.
- 동기 CLI가 asyncio.run으로 네트워크 수신을 끝낸 뒤, 동기 파일 쓰기를 수행합니다. 별도 백그라운드 쓰기나 취소된 to_thread를 만들지 않습니다. body→manifest를 순서대로 fsync하고 marker 제거를 마지막에 합니다. 쓰기/프로세스 중단은 marker를 남기며 성공으로 표시하지 않습니다.
- 파일은 `body.bin`과 작은 manifest만 만듭니다. manifest에는 고정 URL·한도·HTTP 상태·관측/종료 시각·원문 hash·크기·정적 실패 코드·생산 입력 사용 금지를 기록합니다. 일반 fixture manifest와 다른 schema/kind이므로 기존 strict replay에서 사용하지 않습니다.
- 로그·반환은 원문 없는 metadata만 출력합니다. 예외 메시지·헤더·인증값·본문을 출력하지 않습니다. 원문은 Git/Telegram 첨부에서 제외합니다.

## 검증 및 피드백

먼저 다음 실패/경계 회귀를 고정한 후 구현합니다: 실제 streamed 2 MiB 초과/8 MiB 이하 성공, 8 MiB 정확한 경계, 초과 시 부분 파일 없음, redirect/403/압축 거절, 네트워크 오류/timeout, 잘못된 UTF-8, 기존 경로/심볼릭 링크/Git 경로 거절, 저장 실패 시 marker 유지, live 확인 부재에서 HTTP 0회. 호출 횟수 1과 auth/cookie 미전송도 확인합니다.

Ruff·구문·diff·기존 fixture 회귀와 독립 리뷰 후 승인된 대상 1건만 live 취득합니다. 성공하면 hash와 바이트 수를 기록하고 오프라인 full DOM으로 문서 제목/표/노드 규모를 확인합니다. full DOM은 진단 oracle이며 운영 파서를 대체하지 않습니다. 문서의 마지막까지 받았는지와 명시적 주석 범위·조건 구성을 확인하되 금융 품질 합격을 주장하지 않습니다.

다시 403/429/전송 오류 또는 8 MiB 초과라면 우회/반복하지 않고 사용자에게 보고합니다. 안전한 내용 단위 분할은 확보한 원문에 기반한 다음 별도 설계이며, 새 검증군을 종목별 튜닝에 사용했다고 숨기지 않습니다.
