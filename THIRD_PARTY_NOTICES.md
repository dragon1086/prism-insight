# Third-Party Open Source Notices / 제3자 오픈소스 고지

PRISM-INSIGHT distributions may include the third-party components listed
below. These components remain subject to their own license terms. The
PRISM-INSIGHT license does not replace or restrict those terms.

PRISM-INSIGHT 배포물에는 아래의 제3자 오픈소스 구성요소가 포함될 수
있습니다. 각 구성요소에는 해당 구성요소의 라이선스가 적용되며,
PRISM-INSIGHT의 라이선스는 그 조건을 대체하거나 제한하지 않습니다.

The installed Python source for these components is included in distributions
that contain the PRISM-INSIGHT Python environment. Exact installed versions are
recorded in the Python package metadata. Corresponding release source is also
available from each upstream project and its PyPI release page.

PRISM-INSIGHT의 Python 실행 환경을 포함하는 배포물에는 아래 구성요소의
설치된 Python 소스 코드가 함께 들어 있습니다. 정확한 설치 버전은 Python
패키지 메타데이터에서 확인할 수 있으며, 같은 버전의 배포 소스는 각
프로젝트와 PyPI 배포 페이지에서도 받을 수 있습니다.

## Included components / 포함 구성요소

<!-- package: python-telegram-bot -->
### python-telegram-bot

- License / 라이선스: GNU Lesser General Public License v3.0 only
  (`LGPL-3.0-only`)
- Copyright: Copyright © 2015-2026 Leandro Toledo and contributors
- Use in PRISM-INSIGHT / 사용 형태: unmodified, separately installed Python
  library / 수정하지 않은 별도 설치형 Python 라이브러리
- Project / 프로젝트: <https://python-telegram-bot.org/>
- Source / 소스 코드: <https://github.com/python-telegram-bot/python-telegram-bot>
- Release files / 배포 소스: <https://pypi.org/project/python-telegram-bot/#files>

<!-- package: frozendict -->
### frozendict

- License / 라이선스: GNU Lesser General Public License v3.0
  (`LGPL-3.0`)
- Copyright: Marco Sulla and contributors
- Use in PRISM-INSIGHT / 사용 형태: unmodified transitive dependency of
  `yfinance` / 수정하지 않은 `yfinance`의 하위 의존성
- Project and source / 프로젝트 및 소스 코드:
  <https://github.com/Marco-Sulla/python-frozendict>
- Release files / 배포 소스: <https://pypi.org/project/frozendict/#files>

## License texts / 라이선스 원문

- [GNU General Public License v3.0](licenses/third-party/GPL-3.0.txt)
- [GNU Lesser General Public License v3.0](licenses/third-party/LGPL-3.0.txt)

No source files from these libraries are copied into the PRISM-INSIGHT source
tree, and PRISM-INSIGHT does not modify them. When a distribution includes
these libraries, recipients may replace them with compatible modified versions
and may reverse engineer the combined work to debug such modifications to the
extent required by the LGPL.

PRISM-INSIGHT 저장소에는 위 라이브러리의 소스 파일을 복사하지 않았으며,
라이브러리 자체도 수정하지 않았습니다. 이 라이브러리가 포함된 배포물을
받은 이용자는 LGPL이 보장하는 범위에서 호환되는 수정 버전으로 교체하고,
그 수정 내용을 디버깅하기 위한 역공학을 할 수 있습니다.

For questions or a copy of the corresponding library source, contact
<dragon1086@naver.com>.

해당 라이브러리의 소스 코드 제공이나 라이선스에 관한 문의는
<dragon1086@naver.com>으로 보내주시기 바랍니다.
