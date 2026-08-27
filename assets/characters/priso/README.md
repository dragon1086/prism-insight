# 프리소(Priso) 에셋

프리소는 PRISM-INSIGHT의 공식 마스코트입니다. 이 디렉터리에는 검수를 마친 기준 에셋만 버전별로 보관합니다.

## 현재 승인 버전

현재 승인 버전은 `v1.0`입니다.

- `priso_master_transparent.png`: 투명 배경 마스터. 합성이나 배경 변경이 필요한 작업에 사용합니다.
- `priso_identity_gray.png`: 회색 배경 정체성 참조. 외형 일관성을 비교할 때 사용합니다.
- `priso_fullbody_white.png`: 흰 배경 전신 이미지.
- `priso_face_closeup.png`: 얼굴 확대 이미지.
- `manifest.json`: 기준 파일 목록과 SHA-256 해시.

이미지를 수정하거나 파생 이미지를 만들 때는 `priso_identity_gray.png`를 정체성 기준으로 삼고, 원본 합성에는 `priso_master_transparent.png`를 사용합니다.

## 버전 정책

- 승인된 에셋은 `vMAJOR.MINOR` 디렉터리에 고정하며 같은 버전의 파일을 덮어쓰지 않습니다.
- 새 외형이나 액세서리를 적용한 에셋은 별도 버전으로 추가합니다.
- 머리띠를 적용한 `v1.1`은 생성 및 시각 검수 중입니다. 검수를 마치기 전에는 이 저장소의 승인 에셋으로 사용하지 않습니다.
- 에셋을 가져오거나 배포하기 전에는 해당 버전의 `manifest.json`과 파일 해시가 일치하는지 확인합니다.

## 출처

`v1.0`은 Hermes 제작 워크플로를 통해 만든 뒤 `prism-video-factory/assets/characters/priso/v1.0/`에서 동일한 파일과 해시로 가져왔습니다.
