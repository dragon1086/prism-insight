# Stance

**매매 판단 공개 프로토콜** · `stance/1`

> 실적을 신고받지 말고, 판단을 미리 선언받아라. 성과는 서버가 계산한다.

시스템 트레이딩을 돌리는 사람은 많은데 누구 시스템이 더 나은지 비교할 방법이 없다.
수익률 인증은 조작할 수 있고, 검증하려면 계좌를 통째로 열어야 한다.

Stance 는 질문을 뒤집는다. **"얼마 벌었어?"** 대신 **"지금 뭘 살 건데?"** 를 묻는다.
이미 뱉은 말은 조작할 수 없고, 그 다음은 시장이 정해준다.

전체 설계는 **[spec/core-spec.md](spec/core-spec.md)** 에 있다. 이 README 는 코드 안내다.

---

## 코어

메시지 2개와 규칙 5개가 전부다.

```json
{ "protocol": "stance/1", "strategy": "my-strategy", "seq": 42,
  "symbol": "005930", "target_weight": 0.10 }
```

```json
{ "protocol": "stance/1", "strategy": "my-strategy", "seq": 43, "kind": "hold" }
```

목표 비중이 지금보다 크면 사는 것이고, 작으면 파는 것이다. `0` 이면 청산이다.
**매수와 매도라는 구분이 없다.**

규칙은 다섯이다 — 가격은 서버가 정한다 · 자산은 1.0 에서 시작한다 ·
비중 합은 1.0 을 넘을 수 없다 · 기록은 고치거나 지울 수 없다 ·
현실에서 불가능한 체결은 인정하지 않는다.

---

## 구조

```
stance/
├── spec/core-spec.md   표준 문서. 코어와 레이어 경계가 여기 정의되어 있다
├── server/
│   ├── models.py       원장에 기록되는 사실들. 계산이 없다
│   ├── engine.py       장부 재구성. 순수 함수이며 DB 를 모른다
│   ├── ledger.py       원장. append-only 와 해시체인을 DB 가 강제한다
│   ├── scoring.py      채점 프로파일 stance-score/1  ← 코어가 아니다. 갈아끼운다
│   ├── markets.py      시장 프로파일  ← 코어가 아니다. v1 지원 범위가 여기 있다
│   ├── service.py      서비스 계층. HTTP 를 모른다 — 그래서 프레임워크 없이 테스트된다
│   ├── api.py          HTTP 껍데기 (FastAPI). 얇게 유지한다
│   ├── leaderboard.py  원장 재생 → 채점 → 화면용 JSON
│   └── marker.py       하루 마감. 채점의 시간축을 만든다
├── client/client.py    참여자용. 목표비중 변환 헬퍼 포함
├── tests/              154개
└── demo.py             원장 → 재구성 → 채점 전체 흐름
```

`stance/` 안에서는 PRISM 코드를 **import 하지 않는다.**
그 규칙을 지키는 한 `git subtree split` 으로 별도 저장소로 그대로 뽑아낼 수 있다.
PRISM 연동은 바깥의 [`prism_core/stance_adapter.py`](../prism_core/stance_adapter.py) 한 파일만 안다.

---

## 실행

```bash
python3 stance/demo.py                    # 전체 흐름 데모 (의존성 없음)
python3 -m pytest stance/tests/ -q        # 테스트

pip install fastapi uvicorn               # 서버를 띄울 때만 필요하다
uvicorn stance.server.api:app --port 8800
```

**코어와 엔진은 외부 의존성이 없다.** 표준 라이브러리만으로 돈다.
FastAPI 는 HTTP 껍데기에만 필요하며, 없으면 관련 테스트는 자동으로 건너뛴다.

## 서버

```
POST /strategies    전략 등록 → 인증키 발급 (전략당 1회)
POST /stances       선언 접수  ← 참여자가 쓰는 유일한 쓰기 엔드포인트
GET  /portfolio     검산용 보유·자산 스냅샷
GET  /leaderboard   리더보드
GET  /markets       지원 시장과 각 보드의 규칙
```

**판정은 동기로 돌려준다.** 축소·거부를 몇 초 뒤에 알려주면
참여자는 이미 실계좌 주문을 낸 뒤이기 때문이다.

접수 순서가 중요하다 — **선언을 원장에 먼저 넣어 접수시각을 박고, 그 다음에 시세를 찍는다.**
접수시각이 권위 시각이므로 그보다 앞선 가격은 원리적으로 인정될 수 없어야 한다.

시세를 못 구하면 거부가 아니라 **보류**다. 소스 장애는 서버 책임이지 참여자 책임이 아니다.

```bash
curl -X POST localhost:8800/stances \
  -H "Authorization: Bearer $STANCE_KEY" \
  -d '{"protocol":"stance/1","seq":42,"symbol":"005930","target_weight":0.1}'
```

### 배포 — 상주 프로세스다

Stance 서버는 **계속 떠 있어야 하는 앱 서버**다. 배치 스크립트가 아니다.
저장소의 다른 상주 서비스와 같은 방식으로 systemd 에 올린다.

```bash
sudo install -d -o prism -g prism -m 0750 /var/lib/prism-stance
sudo cp deploy/systemd/prism-stance.service.example \
        /etc/systemd/system/prism-stance.service
sudo systemctl enable --now prism-stance
curl -s localhost:8800/health      # durable 이 true 인지 확인할 것
```

KIS 시세를 붙이려면 `stance_server.py` 로 띄운다. 그냥 `uvicorn` 으로 띄우면
시세 제공자가 없어 **모든 선언이 보류(pending)로만 쌓인다.**

```bash
STANCE_DB=/var/lib/prism-stance/ledger.db python -m stance_server
```

**하루 마감을 cron 에 걸어야 한다.** 이것이 채점의 시간축을 만든다.
돌지 않으면 자산 추이가 비어 운영일수·투자비중·위험지표가 전부 0 으로 남는다 —
**리더보드가 죽는다.**

```cron
40 15 * * 1-5  cd /opt/prism-insight && .venv/bin/python -m stance.server.marker --market KRX
```

그 밖에 지켜야 할 것이 둘 있다.

**① 원장 경로를 반드시 지정한다.** `STANCE_DB` 를 비우면 작업 디렉터리에 파일을 만들고
경고를 남긴다. `:memory:` 로 두면 프로세스가 죽는 순간 원장이 통째로 사라져
**"기록은 고치거나 지울 수 없다" 는 규칙 ④ 가 무너진다.**

**② 워커를 늘리지 않는다.** 장부를 프로세스 메모리에 캐시하므로 워커가 여럿이면
한쪽에서 접수한 선언이 다른 쪽 장부에 반영되지 않아 현금 판정(축소 수락)이 어긋난다.
SQLite 다중 프로세스 쓰기 경합도 생긴다. 수평 확장이 필요해지면
장부 캐시를 프로세스 밖으로 빼고 원장을 Postgres 로 옮겨야 한다.

---

## 왜 원장과 계산장부를 나누는가

원장은 **고칠 수 없고**, 계산장부는 **언제든 다시 만든다.**

```
[원장]  stances · quotes · market_events        ← INSERT 만. 해시체인으로 봉인
   ↓  replay()
[계산장부]  보유현황 · 자산추이 · 지표 · 순위    ← 언제든 지우고 다시 만든다
```

이 분리가 세 가지를 동시에 해결한다.

- **봉인이 완전해진다.** 채점의 입력이 되는 모든 사실이 원장에 있다.
  체결가는 봉인 대상이 아니라 원장으로부터 재계산되는 값이다.
- **재계산이 가능해진다.** 채점 버그가 나와도 계산장부만 다시 만들면 된다.
- **누구든 검증할 수 있다.** 원장만 공개하면 제3자가 순위 전체를 독립적으로 재현한다.
  운영자가 순위를 건드리면 즉시 드러난다.

시세를 원장에 넣는 것이 특히 중요하다. 시세는 그 순간에만 존재하며 사후에 다시 조회할 수 없다.

---

## 채점은 코어가 아니다

`scoring.py` 는 통째로 교체 가능한 레이어다.

어떤 지표가 좋은 전략을 가려내는지에는 정답이 없다.
수익을 위험으로 나누면 현금을 많이 든 전략이 구조적으로 유리하고,
시장 대비 초과수익으로 재면 국면에 따라 노출 방향에 베팅하게 된다.
노출 5% 와 85% 를 하나의 숫자로 줄 세우려면 어느 쪽이 좋은지를 미리 정해야 하는데,
그 결정 자체가 편향이다.

그래서 **정답을 고르지 않고 갈아끼울 수 있게** 만들었다.
채점 방식은 앞으로도 계속 논쟁될 것이고, 그 논쟁이 프로토콜을 흔들면 안 된다.

같은 이유로 순위를 하나의 숫자로 줄이지 않는다. 하나로 줄이면
반드시 그 숫자를 겨냥한 조작이 생긴다. 대신 모든 지표 옆에
**평균 투자비중**을 항상 함께 표시한다 — "노출 얼마로 낸 점수인지"가 보여야 해석이 된다.
