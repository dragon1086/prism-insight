# BTC 과거 복기 백업 복원

7일이 지나 운영 DB에서 정리된 복기 원문이 필요하면 검증된 별도 보관본을 복원한다.
백업 당시 DB 전체와 관련 Git 기록을 사용하므로 원문을 재구성하거나 누락값을 채우지 않는다.

db 서버의 백업 디렉터리에 있는 `offsite_ack.json`에서 `archive` 값을 확인한 뒤 실행한다.

```bash
cd /root/prism-insight
PYTHONPATH=prism-btc:. python3 tools/restore_btc_replay_archive.py archive-여기에_영수증_ID
```

성공 시 별도 서버의 `/root/prism-btc-replays/restore-.../` 경로를 반환한다. 그 안의
`snapshot.sqlite`는 해시와 SQLite 무결성을 검증한 복원본이며, `history.git`에는 복기 기록에
참조된 코드가 포함된다. `restore-proof.json`의 `source_head`와 `raw_sha256`으로 출처를 확인한다.
기존 운영 DB와 보관본은 변경하지 않는다. 이 복원에는 DB 크기 이상의 임시 여유 공간이 필요하고,
기존 복원 디렉터리가 있으면 추가 복원을 중단한다. 삭제는 자동으로 하지 않는다.

복기 테이프와 코드 복원은 시장 데이터·실제 체결 재현·수익성 입증을 대신하지 않는다.
