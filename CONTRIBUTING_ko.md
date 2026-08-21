# 기여 가이드라인

PRISM-INSIGHT 프로젝트에 기여해주셔서 감사합니다! 🎉

## 기여자 라이선스 계약(CLA)

PRISM-INSIGHT는 AGPL-3.0과 별도 상업 라이선스를 함께 운영합니다. 이중
라이선스에 필요한 권리 관계를 명확히 하기 위해, 모든 Pull Request 기여자는
병합 전에 [개인 기여자 라이선스 계약](CLA.md)에 동의해야 합니다.

CLA는 기여물의 저작권을 양도하는 계약이 아닙니다. 기여자는 저작권을 유지하고,
프로젝트가 해당 기여물을 오픈소스 및 상업 조건으로 배포하는 데 필요한 권한을
부여합니다.

서명이 필요하면 CLA Assistant가 Pull Request에 안내 댓글을 남깁니다. CLA를
읽은 후 Pull Request에 다음 문구를 정확히 댓글로 작성해 주세요.

```text
I have read the CLA Document and I hereby sign the CLA
```

서명은 원칙적으로 CLA 버전별 1회만 필요합니다. 한 Pull Request에 여러 작성자의
기여가 포함되면 모든 작성자가 서명해야 합니다. 또한 기여자는 해당 기여물을
제출할 권한이 있어야 합니다. 고용주나 제3자가 관련 권리를 보유할 수 있다면,
서명 전에 필요한 허가를 받아야 합니다.

## 📋 기여 방법

### 1. 이슈 제기
다음과 같은 경우 GitHub Issues를 통해 제기해주세요:
- 🐛 **버그 발견**: 예상과 다르게 동작하는 경우
- 💡 **기능 제안**: 새로운 기능 아이디어
- 📚 **문서 개선**: README나 코드 주석 개선 제안
- ❓ **질문**: 사용법이나 설정 관련 문의

### 2. Pull Request 제출

#### 기본 절차
1. **프로젝트 포크**: GitHub에서 Fork 버튼 클릭
2. **로컬에 클론**: `git clone https://github.com/dragon1086/prism-insight.git`
3. **브랜치 생성**: `git checkout -b feature/새로운기능`
4. **변경사항 작성**: 코드 수정 및 테스트
5. **커밋**: `git commit -m "feat: 새로운 기능 추가"`
6. **푸시**: `git push origin feature/새로운기능`
7. **Pull Request 생성**: GitHub에서 PR 생성

#### PR 제출 전 체크리스트
- [ ] [CLA](CLA.md) 확인 및 동의
- [ ] 코드가 정상적으로 실행되는지 확인
- [ ] 새로운 기능에 대한 간단한 테스트 수행
- [ ] 코드에 적절한 주석 추가
- [ ] README 업데이트 (필요한 경우)

## 🔧 개발 환경 설정

### 기본 설정
```bash
# 저장소 클론
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight

# 의존성 설치
pip install -r requirements.txt

# 설정 파일 준비
cp .env.example .env
cp config.py.example config.py
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
```

### 테스트 환경
```bash
# 기본 분석 테스트
python cores/main.py

# 개별 모듈 테스트
python trigger_batch.py morning INFO --output test_results.json
```

## 📝 코딩 규칙

### 코드 스타일
- **언어**: Python 3.10+ 호환
- **들여쓰기**: 4칸 스페이스
- **네이밍**: 
  - 변수/함수: `snake_case`
  - 클래스: `PascalCase`
  - 상수: `UPPER_CASE`

### 주석 및 문서화
```python
def analyze_stock(company_code: str, company_name: str, reference_date: str = None):
    """
    주식 종합 분석 보고서 생성
    
    Args:
        company_code: 종목 코드 (예: "005930")
        company_name: 회사명 (예: "삼성전자")
        reference_date: 분석 기준일 (YYYYMMDD 형식, 기본값: 오늘)
    
    Returns:
        str: 생성된 최종 보고서 마크다운 텍스트
    """
```

### 커밋 메시지 규칙
```bash
feat: 새로운 기능 추가
fix: 버그 수정
docs: 문서 수정
style: 코드 포맷팅 (기능 변경 없음)
refactor: 코드 리팩토링
test: 테스트 추가
chore: 빌드, 설정 관련
```

## 🎯 기여 영역

### 우선순위 높음
- 🤖 **AI 에이전트 개선**: 분석 성능 향상
- 📊 **매매 시뮬레이션**: 매매 시뮬레이터 수익률 개선
- 🐛 **버그 수정**: 안정성 개선
- 📚 **문서화**: 사용법 가이드 개선

### 환영하는 기여
- 🏢 **기업 분석 확장**: 새로운 분석 섹션이나 기존 분석 내용 개선
- 🌐 **국제화**: 영어 지원, 해외 주식 분석, 소스코드 글로벌화
- 🚀 **성능 최적화**: 분석 및 매매 시뮬레이터 성능 개선
- 🔧 **설정 개선**: 더 쉬운 설정 방법

### 주의사항
- **API 키 관련**: 실제 API 키를 코드에 포함하지 마세요
- **대용량 파일**: 생성된 보고서나 차트 이미지는 커밋하지 마세요
- **외부 의존성**: 새로운 라이브러리 추가 시 issue에서 먼저 논의해주세요

## 🚫 하지 말아야 할 것

- ❌ 실제 API 키나 토큰을 코드에 포함
- ❌ 개인정보나 민감한 데이터 포함
- ❌ 저작권 침해 가능성이 있는 코드
- ❌ 의도적으로 시스템을 손상시키는 코드
- ❌ 투자 권유나 단정적 투자 조언

## 🔍 코드 리뷰 과정

### 리뷰 기준
1. **기능성**: 의도한 대로 동작하는가?
2. **안정성**: 에러 처리가 적절한가?
3. **가독성**: 다른 개발자가 이해하기 쉬운가?
4. **성능**: 불필요한 리소스 사용은 없는가?
5. **보안**: API 키나 민감정보 노출은 없는가?

### 리뷰 시간
- 일반적으로 **1-7일** 내에 리뷰 진행
- 긴급한 버그 수정의 경우 **24시간** 내 우선 리뷰

## 🐛 버그 리포트

### 좋은 버그 리포트 예시
```markdown
**버그 설명**
급등주 포착 시 특정 종목에서 분석이 중단됩니다.

**재현 방법**
1. `python stock_analysis_orchestrator.py --mode morning` 실행
2. 종목 "123456" 분석 시 오류 발생

**예상 결과**
정상적으로 분석 완료

**실제 결과**
KeyError: 'current_price' 오류 발생

**환경 정보**
- OS: macOS 14.0
- Python: 3.9.7
- 오류 로그: [첨부]
```

## 💡 기능 제안

### 제안 시 포함할 내용
- **배경**: 왜 이 기능이 필요한가?
- **기능 설명**: 구체적으로 어떤 기능인가?
- **사용 예시**: 어떻게 사용할 것인가?
- **우선순위**: 얼마나 중요한가?

## 🎉 기여자 인정

기여해주신 모든 분들은 다음과 같이 인정받습니다:
- **README.md**에 기여자 목록 추가
- **릴리즈 노트**에 기여 내용 명시
- **GitHub Contributions** 그래프에 반영

## 📞 소통 채널

- **GitHub Issues**: 버그, 기능 제안, 질문
- **Pull Request**: 코드 리뷰 및 토론
- **Discussions**: 일반적인 아이디어 공유

## ⚖️ 행동 강령

- 🤝 **존중**: 모든 기여자를 존중하며 건설적인 피드백 제공
- 🌟 **포용**: 다양한 배경과 경험을 가진 기여자 환영
- 📚 **학습**: 실수는 배움의 기회로 받아들임
- 🎯 **집중**: 프로젝트 목표에 부합하는 기여 추구

---

**다시 한번 PRISM-INSIGHT에 기여해주셔서 감사합니다! 🚀**

문의사항이 있으시면 GitHub Issues를 통해 언제든 연락주세요.
