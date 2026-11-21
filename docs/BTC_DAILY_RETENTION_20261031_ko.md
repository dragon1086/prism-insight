# BTC 7일 정리의 백업 증거와 실행 시각

정기 백업이 원격 복원 검증에 성공한 뒤에만 해당 날짜의 전용 디렉터리에 정리 모드 `0` 또는 `1`을 작은 비밀 파일로 기록한다. 실패한 백업이나 잘못된 설정에는 표식을 만들지 않는다. 모드 `1`은 이벤트 증거까지 검증된 경우에만 정리하고, 모드 `0`은 기존 tape 전용 경로를 쓴다. 최신 당일 백업이 없거나 표식·원격 증거가 불완전하면 이전 날짜로 대체하지 않고 중단한다.

db-server의 `prism-btc-daily-retention.timer`는 서울 22:02:40에 한 번 실행하고 재부팅 뒤에도 등록을 유지한다. 예정 시각 밖에서 재부팅 후 실행되면 DB나 백업을 읽기 전에 보류한다. 실제 삭제에는 기존 원격 archive lease, ClickStack 전달·cursor, 현재 원문 해시, BTC 무포지션·미확정 없음, 공간·WAL·실행기 조건을 그대로 적용한다. WAL일 때만 정해진 미국 B3 서비스의 읽기 전용 연결을 허용하며 미국 정규장 및 마감 후 처리 시간과 최근 비거래 완료 기록도 확인한다. 그 외 reader·백업은 계속 차단한다.

배포는 전체 CI 성공 후 db-server에서만 한다. Git ff-only 반영 후 저장소의 service/timer unit을 systemd에 설치하고 timer를 활성화한 뒤 `systemctl is-enabled`, `is-active`, 다음 실행 시각, cron 불변을 확인한다. unit 설치·활성화가 실패하면 정리 완료로 표시하지 않는다. 백업에서 timer 비활성은 CRITICAL로 기록하지만 성공한 백업은 보존한다. timer 등록, 실제 백업, 실행 결과, 삭제량과 장기 증가량은 각각 확인한다. 처리 상한·누락된 전달 증거·유지할 cursor 등으로 뒤에 더 확인할 자료가 있으면 `MORE_TO_SCAN`으로 보고한다. 재부팅 후 안전 시각을 놓치거나 증거·flat 조건이 맞지 않으면 정리는 다음 검증된 회차까지 지연될 수 있다.

SELinux enforcing 서버에서는 `/root` 아래 unit 파일의 링크를 systemd가 읽지 못할 수 있다. 검증한 unit 두 개를 `/etc/systemd/system/`에 동일 내용으로 설치하고 `restorecon`으로 `systemd_unit_file_t`를 적용한다. 저장소 unit 변경 시 설치본도 일치시킨다. Python은 systemd가 접근할 수 있는 `/usr/bin/env`를 통해 운영 Python 경로를 실행한다. 타이머 활성화와 실제 service 실행 확인은 별도다.
