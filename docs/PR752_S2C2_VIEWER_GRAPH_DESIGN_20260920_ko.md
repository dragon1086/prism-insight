# S2c-2 설계: 공식 viewer 목차 관계와 부분 문서의 근거

## PRD 연결과 순서

상위 PRD R1/R2를 구현합니다. 먼저 공식 부모·자식 관계를 증명하고, 그다음 작은 실제 자식 본문의 모양을 확인한 뒤, 별도 수집·보고서 연결 설계를 진행합니다. 이 단계에서 임의 offset, 부모 제목 삽입, 운영 2 MiB 확대, 모델 입력 확대는 하지 않습니다.

## 원문 근거

현재 보존한 삼성생명 main의 SHA-256은 `ec707c5dcbbd8d3e2b45ab3d04c01003f1d8fa7f6095a224323a8f51c38c8daa`입니다. `makeToc()`의 `var treeData=[]`부터 마지막 `treeData.push(node1)`까지 정적 목차 생성 구문이 있고 jstree의 data로 전달됩니다. node 변수를 새 객체로 반복 초기화하므로 변수명이 아니라 객체 생성 시점을 구분해야 합니다.

실제 `nodeN['children'].push(nodeM)`가 eleId24의 직접 자식 54개(eleId25..78)를 증명합니다. offset 포함으로만 찾으면 마지막 사업결합이 누락됩니다. 기존 LG/카카오/HMM main도 같은 생성 문법이 관측됐습니다. 이것은 개발용 회귀 원문이며 새로운 검증군 합격이 아닙니다.

## 변경 범위와 API

- 신규 `prism_core/dart_viewer_tree.py`: `parse_viewer_tree(html, receipt_id, corp_code)` 순수 함수. 기존 `parse_viewer_nodes`, `_lex_js`, `_tree`를 재사용하며 기존 함수와 기본 수집 호출은 변경하지 않습니다.
- 반환: `version`, `main_sha256`, 원문 순서의 `nodes`(기존 viewer tuple + `parent_key`, `children_keys`), `root_keys`. key는 `(dcmNo, eleId)`를 충돌 없이 표현합니다. tuple과 viewer_url은 기존 검증 결과 그대로이며 숫자 offset을 재계산하지 않습니다.
- 반환 key는 `dcmNo:eleId` 문자열입니다. `nodes`는 list이며 각 node는 기존 flat node의 모든 필드와 `key`, `parent_key`(root면 None), `children_keys` list를 가집니다. 따라서 기존 flat node 필드의 구조를 바꾸지 않고 소비자가 key lookup을 만들 수 있습니다.
- 확인되지 않은 문법은 정적 `VIEWER_TREE_*` 실패 코드로 거절합니다. 빈 graph나 추측한 관계를 정상 결과로 반환하지 않습니다.
- 신규 회귀와 CI 등록. 생산 수집기·파서·보고서·매매 코드는 이번 단계에서 변경하지 않습니다.
- 신규 `tools/probe_dart_note_children.py`와 테스트: 아래 고정된 두 진단 대상만 일반 2 MiB recorder로 읽습니다. 코드 안의 진단 대상 제한은 운영 종목별 분기가 아닙니다.

## 좁은 문법과 검증

정확한 `makeToc` 함수 하나와 `treeData` 배열 초기화 하나, jstree data 소비 지점을 확인하고, 그 사이 목차 builder를 strict statement grammar로 검사합니다. JS는 실행하지 않습니다. 문자열/주석은 기존 lexer로 구분하며, 코드처럼 보이는 문자열·주석을 명령으로 해석하지 않습니다.

지원하는 builder statement는 객체 초기화 `var nodeN={}`, 허용된 literal property 쓰기, children 배열 초기화, 고정 id/cnt 표식, `children.push`와 `treeData.push`뿐입니다. 실제 id/cnt 표현은 원문으로 확인한 정확한 형태만 허용합니다. unknown statement/조건/반복/alias/동적 키/재할당/배열 변경은 거절합니다. 함수 범위 밖의 관계처럼 보이는 문구를 사용하지 않습니다.

### 고정 문법과 상태

공백과 lexer가 제거한 주석만 자유롭게 허용합니다. node 변수는 `node` 뒤 1~6자리 숫자입니다. bracket key는 단일/이중 따옴표의 고정 식별자이며 value는 JSON 이중 따옴표 문자열만 허용합니다.

1. 함수 선언은 인자 없는 `function makeToc() {` 하나입니다. 함수 시작의 첫 두 문장은 순서대로 `cnt = 0;`, `var treeData = [];`여야 합니다. 임의 조건이나 래퍼를 건너뛰지 않습니다.
2. `var nodeN = {};`는 새로운 epoch를 만듭니다. 같은 변수의 이전 epoch가 있다면 이미 봉인돼 있어야 합니다.
3. 관측한 4개 fixture의 584개 객체가 모두 `text, id, rcpNo, dcmNo, eleId, offset, length, dtd, tocNo, atocId` 순서의 10개 필드를 정확히 한 번씩 가집니다. 이 순서를 필수 계약으로 고정하며 선택 필드는 없습니다. 기존 flat parser가 검증한 필수 viewer 필드와 값 및 원문 순서가 완전히 같아야 합니다. id/tocNo/atocId는 1~14자리 숫자 JSON 문자열만 허용하고 서로 또는 eleId와의 동일성은 가정하지 않습니다. 원문에서도 tocNo와 eleId가 다른 경우가 있습니다.
4. 모든 필드를 쓴 뒤 `cnt++;`를 정확히 한 번 소비하면 필드 쓰기 단계가 끝납니다. 이것만 JSON literal 외 표현식의 유일한 예외입니다. cnt의 숫자를 실행·추정하지 않고 구조 표식으로만 사용합니다. 다른 증감/대입/참조는 builder에서 금지합니다.
5. 필드 완료 후 `nodeN['children'] = [];`를 최대 한 번 허용합니다. leaf는 생략 가능합니다. parent push 이전에는 초기화가 필요합니다. 초기화 이후 필드 추가는 금지합니다.
6. `nodeN['children'].push(nodeM);`은 미봉인 parent와 필드가 완료된 미봉인 child를 연결합니다. child는 즉시 봉인되어 필드/자식 변경과 중복 연결을 거절합니다. 완성한 descendants를 가진 child를 부모에 연결할 수 있습니다.
7. `treeData.push(nodeN);`은 완료한 미봉인 객체를 root로 봉인합니다. root와 child의 이중 첨부도 거절합니다.

builder 종료점은 다음 실제 `$j('#listTree').jstree(` 호출의 시작입니다. 문자열/주석 내부의 호출은 사용할 수 없고, 이전 모든 문장을 cursor로 소비해야 합니다. 그 호출 인자는 객체 literal이며 중첩 깊이를 세어 **직접 `core` 객체의 직접 `data` 속성 값이 식별자 treeData 하나**인지 확인합니다. data 중복, 다른 객체의 data, 표현식/함수/alias 값을 거절합니다. 정확한 sink 밖에서 nodeN/treeData를 참조하거나 수정하는 makeToc 후속 코드도 거절합니다. cnt에 대한 후속 읽기는 UI 렌더링에만 쓰이는 별도 구간이며 graph 필드를 바꿀 수 없습니다.

범용 JS 객체 parser를 추가하는 대신 확인한 4개 fixture에 공통인 아래 sink 전체를 고정된 토큰열로 검사합니다. 공백·주석과 단순 key 문자열의 따옴표 종류만 정규화하며 property의 순서·값·중첩은 동일해야 합니다. 따라서 위 직접 core/data 조건을 더 좁게 충족합니다. 함수 이후 구간은 nodeN/treeData 참조가 없어야 하며 cnt의 허용 참조도 `if (cnt > 200)`와 `if (cnt == 0)`의 읽기만입니다.

```javascript
var jsTree = $j('#listTree').jstree({
  'core': {'multiple': false, 'themes': {'icons': false}, 'data': treeData}
});
```

함수·sink 경계 인식에서 정규식 literal을 문자열처럼 잘못 해석해서는 안 됩니다. 지원하지 않는 `/.../`·template·정규식으로 위장한 경계는 거절하고 comments/JSON strings만 확실하게 분리합니다. 필요한 lexical subset을 벗어나면 정상 데이터를 놓치더라도 관계 증명을 실패시킵니다.

### 구현 중 확인한 외부 lexical profile 보완

실제 script에는 makeToc 밖에 `.replace(/[^0-9]/g, '')`와 `(leftPanelWidth / window.innerWidth * 100)`, `(rightPanelWidth / window.innerWidth * 100)`가 있습니다. 독립 critic는 다음 좁은 보완을 OKAY로 검토했습니다. 회사나 발생 횟수 조건은 사용하지 않습니다.

- 함수 경계 추출 전에 전체 script의 미확인 slash를 거절합니다. 문자열·주석 속 유사 문구는 허용 근거가 아닙니다.
- 정확한 `.replace(` 문맥의 `/[^0-9]/g` 전체 토큰만 같은 길이 공백으로 마스킹합니다. 다른 pattern/flags/표현은 미지원입니다.
- 두 division은 위 완전한 괄호 표현식만 허용합니다. 위치를 보존하고 함수 추출 후 makeToc 바깥인지 다시 확인합니다.
- 허용 regex/division 정상 사례, 함수 앞 가짜 경계/중괄호 regex, pattern/flags/call 문맥 변형, makeToc 안으로 이동한 division, 추가 slash, 문자열·주석 위장을 회귀로 추가합니다.

### 첫 코드 리뷰의 차단 피드백과 수정 계약

최초 코드 리뷰는 REQUEST CHANGES였습니다. sink 뒤의 식별자 금지 목록만으로는 `eval` 또는 jstree accessor를 통한 간접 변경을 막지 못했고, 문자열 검색으로는 함수 표현식·중첩 함수·script 간 연결·동명 재정의를 거절하지 못했습니다. 실제 반례 5개를 먼저 실패로 고정했습니다. 아래 계약이 기존의 느슨한 suffix/함수 검색 설명보다 우선합니다.

1. script 요소별 경계를 유지하여 독립적으로 lexing합니다. makeToc 전체는 한 script 안에서 닫혀야 하며, 다른 script와 이어 붙이지 않습니다. 모든 script의 함수명 재정의/동적 실행 참조도 검사합니다.
2. makeToc는 brace-depth0의 **statement 시작에 있는 인자 없는 function declaration** 하나여야 합니다. `var x = function makeToc`, 조건/다른 함수 안 선언, 괄호/배열/표현식 안 선언은 거절합니다. 별도 top-level initPage 선언 하나의 직접 body에 있는 standalone `makeToc();` 호출 하나만 다른 active 참조로 허용합니다. 호출이 실제 브라우저에서 실행됐다는 주장은 하지 않습니다.
3. bare/compound/property binding, 추가 declaration, alias capture, 다른 script의 재정의를 거절합니다. escaped identifier는 계속 미지원입니다. active `eval`, `Function`, `execScript`는 거절하며, setTimeout 이름 전체를 금지하지는 않습니다. 문자열 인자를 실행하는 timer 형태는 미지원입니다.
4. sink 이후를 임의 statement로 허용하지 않습니다. 조사한 4개 원문에서 마지막 literal viewDoc 호출의 첫 6개 인자만 다르고, 그 외 postlude의 공백/주석 제외 token열은 정확히 동일했습니다. 해당 **전체 고정 UI token-template 또는 그 canonical SHA-256 서명**과 일치해야 합니다. 동적 함수 호출 하나라도 추가되거나 UI 문장이 바뀌면 실패합니다.
5. postlude 순서는 loaded.jstree handler(select/open_all 및 select_node handler), 빈목차 UI 처리, 관측된 빈 검색어 block, 마지막 literal viewDoc 호출입니다. 마지막 호출은 7개 JSON string 인자이고 마지막은 빈 문자열이며, 첫 6개 tuple은 같은 graph의 검증된 root node 하나와 정확히 같아야 합니다. 이 호출만 placeholder로 치환해 UI template을 비교합니다. 금융 본문이나 전체 HTML을 code fixture로 커밋하지 않습니다.
6. initPage의 호출 경계·top-level function statement를 cursor/token depth로 검증합니다. 문자열 속 이름과 active identifier를 구분하고, 추적 binding으로 사용되는 property 문자열도 놓치지 않습니다. 외부 라이브러리의 실행이나 외부 script 전체의 의미까지 증명하는 것이 아니라, 이 문서에 선언된 literal metadata construction과 명시적 관계만 증명합니다.

추가 회귀: 두 간접 mutation, function expression, if/outer function wrapper, script 사이에 잘린 함수, 같은/다른 script의 재정의, 잘못된 root default viewer tuple, UI tail 문장/문자열 변경, string timer, 정상 4개 고정 원문. raw input 길이도 UTF-8 인코딩 전에 검사하여 큰 문자열 복제를 먼저 만들지 않습니다. 이 수정의 설계 재검토와 새 코드 리뷰가 끝나기 전에는 live probe를 실행하지 않습니다.

### 두 번째 코드 리뷰의 자원·timer 피드백

앞선 두 경계 문제는 해결됐지만, 외부 script에 1,000,000개 세미콜론을 넣어 50,000문장 한도를 우회하는 반례가 발견됐습니다. 별도 실행의 peak RSS가 약227 MiB였습니다. 모든 script에 공유되는 **250,000 token / 50,000 세미콜론 statement** 누적 예산을 항목 append 전에 적용합니다. whitespace/comment는 제외하고, no-semicolon 입력도 token 상한을 피할 수 없습니다. script마다 counter를 초기화하지 않습니다. `re.Match`를 전부 목록에 보존하지 않고 필요한 정수 offset만 보존하며, 같은 builder를 다시 token화하지 않고 이미 제한한 token slice를 재사용합니다.

이는 2 MiB 입력·2,000 객체·깊이32 상한을 완화하지 않습니다. 각 상한은 독립적인 최대치이며 그 이하의 모든 조합을 무조건 지원한다는 뜻은 아닙니다. 다중 script 분산·구두점 위주·no-semicolon 입력의 조기 거절과 정상 4개 source를 회귀로 고정합니다.

또 변수로 전달한 문자열 timer가 통과하는 반례가 발견됐습니다. callback임을 확인하지 못한 변수·표현식은 거절해야 하며, 실제 관측된 bootstrap callback 예외도 별도 근거와 좁은 계약을 확인한 뒤 허용합니다. 후속 설계 확정 전 임의 이름을 callback으로 가정하지 않습니다.

4개 원문에서 확인한 timer 지원 계약을 다음처럼 고정합니다. 일반 변수·문자열·표현식 callback, alias·property timer 접근은 거절합니다. `s`라는 이름만으로 callback임을 인정하지 않습니다.

- 보통의 timer는 bare `setTimeout(function(){...}, 500)`만 허용합니다. ordinary anonymous function의 인자 목록이 비어 있어야 하고, 균형 잡힌 함수 body 바로 뒤에 comma·literal500·call 종료가 와야 합니다. 추가 인자, `.bind/.call`, arrow/async/generator, 함수 호출 결과는 미지원입니다. body는 기존 전역 binding/동적 실행 검사를 계속 받습니다.
- 유일한 변수 callback 예외는 아래 **별도 script 전체의 고정 token-template 일치**입니다. 공백·주석 외에 하나라도 추가/변경되면 예외를 적용하지 않습니다. 같은 변수명의 다른 script나 부분 문자열을 근거로 하지 않습니다.

```javascript
(function(){
  var s=function(){
    __flash__removeCallback=function(i,n){if(i)i[n]=null;};
    window.setTimeout(s,10);
  };
  s();
})();
```

회귀는 정상 callback과 위 shim, 일반 변수/표현식, 같은 이름의 문자열, shim 앞뒤 변경·재대입·alias, delay/인자 수/함수 문법 변경 및 기존 실제 4개 source를 포함합니다. 이 좁은 형태 검증은 임의 브라우저 JavaScript 실행 전체를 증명하지 않습니다.

- 각 새 객체 epoch의 모든 필수 필드는 정확히 한 번 존재하고 기존 검증 node와 동일해야 합니다.
- push는 해당 시점에 살아 있는 객체를 가리켜야 합니다. 객체가 재생성되면 이전 객체와 새 객체를 구분합니다. 필드·자식 목록은 올바른 순서로 완성되어야 합니다.
- 모든 객체는 루트 또는 부모 하나에 정확히 한 번 연결돼야 합니다. 중복 edge, 자기 참조, cycle, 여러 부모, 연결되지 않은 객체, 다른 dcmNo/receipt 부모·자식은 거절합니다.
- 실제 허용되는 자식 구성 후 부모 push 순서는 지원하되, 완성된/소비된 객체의 뒤늦은 mutation은 거절합니다. 검증된 JSON literal 이외의 표현식이나 실행 결과는 추정하지 않습니다.
- HTML/script 2 MiB, 최대 객체 2,000개, 깊이 32, statement 50,000개를 먼저 제한하고 선형에 가까운 스캔을 유지합니다. 전체 graph 검사 실패 시 관계를 일부라도 반환하지 않습니다.

## 테스트와 실제 검증

RED부터 시작합니다. 재사용 변수의 정상 두 부모/여러 자식, 순서와 원본 tuple 보존, 부모 범위를 4만큼 벗어나는 마지막 자식의 explicit edge, 주석·문자열 위장, 누락/중복/다중 부모/cycle, push 전 정의 부재, unknown assignment/call, dot-write/동적키/alias, 객체 재사용 시 과거 edge 불변, 범위 밖 mutation, 서로 다른 문서와 상한을 회귀로 고정합니다.

기존 viewer parser 및 fixture 회귀, Ruff·구문·diff, 독립 코드 리뷰 후 4개 저장 main의 same-hash 오프라인 graph를 확인합니다. 실제 삼성생명에서 부모 eleId24의 직접 자식 54개와 마지막78을 포함함을 확인합니다. 금융 사실이나 목차 밖 전 문서 완전성을 증명하는 것은 아닙니다.

그다음 **2 MiB 이하 자식 2건만** 일반 private recorder로 읽는 제한된 probe를 실행합니다. 최대2 GET, 합계4 MiB, timeout60초, 재시도·redirect·인증·압축 없음. 대상은 대표적인 큰 하위 주석과 마지막 주석이며, 검증 graph의 URL만 사용합니다. 둘 중 하나가 전송·정책 오류면 즉시 중단합니다. 기존 부모 원문/과거 incomplete fixture를 변경하지 않습니다.

probe 진입점은 `python tools/probe_dart_note_children.py --live --out <새 Git 밖 경로>`입니다. 입력 main은 기존 private fixture의 위 SHA와 manifest hash가 일치하는 `response-0010.bin`으로 고정합니다. `--live` 없이는 파일 생성/HTTP 호출이 없습니다. target은 parent key `11213317:24`의 직접 자식 중 `11213317:47`(15-3)과 `11213317:78`(마지막37)이며 이 순서로 요청합니다. URL은 graph에서만 얻습니다.

도구는 기존 `FixtureRecorder`의 2 MiB cap/경로/쓰기를 재사용하고 자체 loop가 두 target만 실행합니다. 기존 무재시도 httpx transport와 identity encoding을 사용하며 loop 전체를 `asyncio.wait_for(..., 60)`로 제한합니다. 각 raw chunk마다 합계4 MiB를 넘으면 중단합니다. recorder의 더 큰 합계 상한에 의존하지 않습니다. HTTP/encoding/한도/전송 오류에서 `success=False`로 봉인하고 complete=false/marker/replay거절을 유지합니다. 완성된 이전 응답은 남기되 초과 응답의 prefix는 저장하지 않습니다. 최상위 취소 시 기존 `_settle` 및 `recorder.invalidate`로 finalizer를 회수한 뒤 실패 상태를 유지합니다.

probe 회귀는 고정 target의 부모 관계/본문 URL 변조, source hash 불일치, live 부재, 2 GET 상한과 합계4 MiB, 첫 실패 시 둘째 미실행, timeout·취소 중 봉인·쓰기 실패, 부분 원문 미저장, recorder replay의 요청 순서/바이트 일치를 포함합니다. 실제 child scope와 본문 구조는 취득 전 추측하지 않습니다.

자식의 실제 제목·scope·단위·조건과 해시·원문 위치를 확인한 뒤 다음 collector→adapter 설계를 작성합니다. 자식 54개를 기존28호출 안에서 전부 읽을 수 있다고 주장하지 않습니다. 선택된 부분·누락·본문 범위를 명시하고, 필수 근거와 예산의 충돌은 S3에서 별도로 평가합니다.

## 원복 및 완료 범위

새 모듈/테스트/CI만 원복하면 기존 동작은 그대로입니다. 저장 원문은 삭제하지 않습니다. graph와 probe 완료는 대형 주석의 운영 지원 완료가 아니며, 한국·미국 최종 품질 및 배포 게이트는 그대로 남습니다.
