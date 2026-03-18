import streamlit as st
from datetime import datetime
import re
from pathlib import Path
import markdown
import base64
import sys
import os

# 현재 파일의 디렉토리를 Python path에 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

from email_sender import send_email
from queue import Queue
from threading import Thread
import uuid

# 보고서 저장 디렉토리 설정
REPORTS_DIR = Path(__file__).parent.parent.parent / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

# 작업 큐 및 스레드 풀 설정
analysis_queue = Queue()

class AnalysisRequest:
    def __init__(self, stock_code: str, company_name: str, email: str, reference_date: str):
        self.id = str(uuid.uuid4())
        self.stock_code = stock_code
        self.company_name = company_name
        self.email = email
        self.reference_date = reference_date
        self.status = "pending"
        self.result = None

class ModernStockAnalysisApp:
    def __init__(self):
        self.setup_page()
        self.initialize_session_state()
        self.start_background_worker()

    def setup_page(self):
        """페이지 설정 및 커스텀 CSS 적용"""
        st.set_page_config(
            page_title="analysis.stocksimulation.kr | AI 주식 분석 에이전트",
            page_icon="📊",
            layout="wide",
            # Open Graph 메타데이터 추가
            menu_items={
                'Get Help': None,
                'Report a bug': None,
                'About': """
                # analysis.stocksimulation.kr
                AI 주식 분석 에이전트
                """
            }
        )

        # Open Graph 태그 직접 주입
        og_html = """
        <head>
            <title>analysis.stocksimulation.kr | AI 주식 분석 에이전트</title>
            <meta property="og:title" content="analysis.stocksimulation.kr | AI 주식 분석 에이전트" />
            <meta property="og:description" content="AI 주식 분석 에이전트" />
            <meta property="og:image" content="https://media.istockphoto.com/id/2045262949/ko/%EC%82%AC%EC%A7%84/excited-businessman-raises-hands-and-punches-air-while-celebrating-successful-deal-stock.jpg?s=2048x2048&w=is&k=20&c=XtdmbV6gILRK1ahoMOf0_SFC256rgHyiaID_FeW4ojU=" />
            <meta property="og:url" content="https://analysis.stocksimulation.kr" />
            <meta property="og:type" content="website" />
            <meta property="og:site_name" content="analysis.stocksimulation.kr" />
        </head>
        """
        st.markdown(og_html, unsafe_allow_html=True)

        # 커스텀 CSS 적용
        self.apply_custom_styles()

    def apply_custom_styles(self):
        """모던한 디자인을 위한 커스텀 CSS 스타일 적용"""
        st.markdown("""
        <style>
            /* 전체 페이지 스타일 */
            .main {
                background-color: #fafafa;
                padding: 1.5rem;
            }
            
            /* 제목 및 헤더 스타일 */
            h1, h2, h3 {
                font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, Roboto, 'Helvetica Neue', sans-serif;
                color: #1E293B;
                font-weight: 700;
            }
            h1 {
                font-size: 2.5rem;
                margin-bottom: 1.5rem;
                padding-bottom: 1rem;
                border-bottom: 1px solid #E2E8F0;
            }
            h2 {
                font-size: 1.8rem;
                margin-top: 2rem;
                margin-bottom: 1rem;
            }
            h3 {
                font-size: 1.3rem;
                margin-top: 1.5rem;
                color: #334155;
            }
            
            /* 카드 컨테이너 스타일 */
            .card {
                background-color: white;
                border-radius: 12px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
                padding: 1.5rem;
                margin-bottom: 1.5rem;
                border: 1px solid #F1F5F9;
                transition: transform 0.2s ease, box-shadow 0.2s ease;
            }
            .card:hover {
                transform: translateY(-3px);
                box-shadow: 0 8px 16px rgba(0, 0, 0, 0.08);
            }
            
            /* 폼 요소 스타일 */
            .stTextInput > div > div > input {
                border-radius: 8px;
                height: 2.8rem;
                border: 1px solid #E2E8F0;
            }
            .stTextInput > div > div > input:focus {
                border-color: #0EA5E9;
                box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.2);
            }
            .stDateInput > div > div > input {
                border-radius: 8px;
            }
            
            /* 버튼 스타일 */
            .stButton > button {
                background-color: #0EA5E9;
                color: white;
                border-radius: 8px;
                height: 3rem;
                font-weight: 600;
                border: none;
                transition: all 0.2s ease;
            }
            .stButton > button:hover {
                background-color: #0284C7;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(2, 132, 199, 0.2);
            }
            .stButton > button:active {
                transform: translateY(0);
            }
            
            /* 선택 요소 스타일 */
            .stSelectbox > div > div {
                border-radius: 8px;
                border: 1px solid #E2E8F0;
            }
            
            /* 사이드바 스타일 */
            .css-1d391kg, .css-1om1kqc, .css-1n76uvr {
                background-color: #F8FAFC;
                padding: 2rem 1rem;
            }
            
            /* 상태 메시지 스타일 */
            .stAlert {
                border-radius: 8px;
                padding: 1rem;
            }
            .success {
                background-color: #ECFDF5;
                color: #065F46;
                border: 1px solid #D1FAE5;
            }
            .error {
                background-color: #FEF2F2;
                color: #991B1B;
                border: 1px solid #FEE2E2;
            }
            .warning {
                background-color: #FFFBEB;
                color: #92400E;
                border: 1px solid #FEF3C7;
            }
            .info {
                background-color: #EFF6FF;
                color: #1E40AF;
                border: 1px solid #DBEAFE;
            }
            
            /* 테이블 스타일 */
            .dataframe {
                font-family: 'Pretendard', -apple-system, system-ui, sans-serif;
                width: 100%;
                border-collapse: collapse;
            }
            .dataframe th {
                background-color: #F1F5F9;
                padding: 0.75rem 1rem;
                text-align: left;
                font-weight: 600;
                color: #334155;
                border-top: 1px solid #E2E8F0;
                border-bottom: 1px solid #CBD5E1;
            }
            .dataframe td {
                padding: 0.75rem 1rem;
                border-bottom: 1px solid #E2E8F0;
            }
            .dataframe tr:nth-child(even) {
                background-color: #F8FAFC;
            }
            
            /* 다운로드 링크 스타일 */
            a {
                color: #0EA5E9;
                text-decoration: none;
                font-weight: 500;
                transition: all 0.2s ease;
            }
            a:hover {
                color: #0284C7;
                text-decoration: underline;
            }
            a[download] {
                display: inline-block;
                background-color: #F1F5F9;
                color: #334155;
                font-weight: 600;
                padding: 0.5rem 1rem;
                border-radius: 6px;
                margin-right: 0.5rem;
                border: 1px solid #E2E8F0;
                text-decoration: none;
            }
            a[download]:hover {
                background-color: #E2E8F0;
                text-decoration: none;
            }
            
            /* 프로그레스 표시 스타일 */
            .stProgress > div > div {
                background-color: #0EA5E9;
            }
            
            /* 마크다운 본문 스타일 */
            .markdown-body {
                font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, sans-serif;
                color: #334155;
                line-height: 1.7;
            }
            .markdown-body pre {
                background-color: #F1F5F9;
                border-radius: 8px;
                padding: 1rem;
            }
            .markdown-body table {
                width: 100%;
                border-collapse: collapse;
                margin: 1rem 0;
            }
            .markdown-body table th,
            .markdown-body table td {
                padding: 0.5rem 1rem;
                border: 1px solid #E2E8F0;
            }
            .markdown-body table th {
                background-color: #F1F5F9;
            }
            
            /* 이미지 스타일 */
            img {
                border-radius: 8px;
                box-shadow: 0 4px 8px rgba(0, 0, 0, 0.05);
            }
            
            /* 헤더 스타일 */
            .header {
                display: flex;
                flex-direction: column;
                align-items: center;
                padding: 1.5rem 0;
                margin-bottom: 2rem;
                text-align: center;
            }
            .logo-container {
                display: flex;
                align-items: center;
                justify-content: center;
                margin-bottom: 0.5rem;
            }
            .logo {
                font-size: 2.5rem;
                margin-right: 0.75rem;
            }
            .app-title {
                font-family: 'Pretendard', -apple-system, system-ui, sans-serif;
                font-size: 2.5rem;
                font-weight: 800;
                color: #0EA5E9;
                letter-spacing: -0.03em;
            }
            .app-description {
                font-size: 1.1rem;
                color: #64748B;
                margin-top: 0.3rem;
                font-weight: 400;
            }
            
            /* 사이드바 헤더 */
            .sidebar-header {
                display: flex;
                align-items: center;
                margin-bottom: 1.5rem;
            }
            .sidebar-logo {
                font-size: 1.8rem;
                margin-right: 0.5rem;
            }
            .sidebar-title {
                font-size: 1.3rem;
                font-weight: 700;
                color: #0EA5E9;
            }
            
            /* 상태 카드 */
            @keyframes progress-animation {
                0% { width: 0%; }
                20% { width: 20%; }
                40% { width: 40%; }
                60% { width: 60%; }
                80% { width: 80%; }
                100% { width: 40%; }
            }
            
            .status-card {
                display: flex;
                align-items: flex-start;
                padding: 1rem;
                border-radius: 8px;
                margin-bottom: 1rem;
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
            }
            .status-icon {
                font-size: 1.5rem;
                margin-right: 1rem;
                margin-top: 0.25rem;
            }
            .status-details {
                flex: 1;
            }
            .status-title {
                font-size: 1.1rem;
                font-weight: 600;
                margin-bottom: 0.3rem;
            }
            .status-info {
                color: #4B5563;
                margin-bottom: 0.5rem;
            }
            .status-card.pending {
                background-color: #FFFBEB;
                border: 1px solid #FEF3C7;
            }
            .status-card.completed {
                background-color: #ECFDF5;
                border: 1px solid #D1FAE5;
            }
            .status-card.failed {
                background-color: #FEF2F2;
                border: 1px solid #FEE2E2;
            }
            .status-progress-container {
                height: 6px;
                background-color: rgba(251, 191, 36, 0.3);
                border-radius: 3px;
                overflow: hidden;
                margin-top: 0.5rem;
            }
            .status-progress-bar {
                height: 100%;
                background-color: #F59E0B;
                width: 40%;
                border-radius: 3px;
                animation: progress-animation 2s infinite alternate;
            }
            
            /* 기능 리스트 스타일 */
            .feature-list {
                list-style-type: none;
                padding: 0;
                margin: 0;
            }
            .feature-list li {
                display: flex;
                align-items: center;
                margin-bottom: 0.8rem;
            }
            .feature-icon {
                font-size: 1.2rem;
                margin-right: 0.7rem;
                width: 24px;
                text-align: center;
            }
            .feature-title {
                font-weight: 600;
                margin-right: 0.5rem;
            }
            
            /* 시간 표시 스타일 */
            .estimate-time {
                display: flex;
                align-items: center;
                margin-bottom: 0.5rem;
            }
            .time-icon {
                font-size: 1.5rem;
                margin-right: 1rem;
            }
            .time-details {
                flex: 1;
            }
            .time-title {
                font-size: 0.9rem;
                color: #64748B;
            }
            .time-value {
                font-size: 1.5rem;
                font-weight: 700;
                color: #0EA5E9;
            }
            .delivery-note {
                color: #64748B;
                font-size: 0.9rem;
                margin-top: 0.3rem;
            }
            
            /* 폼 카드 */
            .form-card, .report-card, .filter-card {
                background-color: white;
                border-radius: 12px;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
                padding: 1.5rem;
                margin-bottom: 1.5rem;
                border: 1px solid #F1F5F9;
            }
            
            /* 마크다운 미리보기 */
            .markdown-preview {
                padding: 1rem;
                border: 1px solid #E2E8F0;
                border-radius: 8px;
                background-color: #F8FAFC;
                max-height: 600px;
                overflow-y: auto;
            }
        </style>
        """, unsafe_allow_html=True)

    def add_app_header(self):
        """앱 헤더와 브랜딩 추가"""
        st.markdown("""
        <div class="header">
            <div class="logo-container">
                <div class="logo">📊</div>
                <div class="app-title">analysis.stocksimulation.kr</div>
            </div>
            <div class="app-description">
                AI 주식 분석 에이전트
            </div>
        </div>
        """, unsafe_allow_html=True)

    def create_card(self, title, content, icon=None):
        """카드 컴포넌트 생성"""
        icon_html = f'<div class="card-icon">{icon}</div>' if icon else ''
        
        st.markdown(f"""
        <div class="card">
            <div class="card-header">
                {icon_html}
                <div class="card-title">{title}</div>
            </div>
            <div class="card-content">
                {content}
            </div>
        </div>
        <style>
            .card-header {{
                display: flex;
                align-items: center;
                margin-bottom: 1rem;
            }}
            .card-icon {{
                font-size: 1.5rem;
                margin-right: 0.8rem;
                color: #0EA5E9;
            }}
            .card-title {{
                font-size: 1.2rem;
                font-weight: 600;
                color: #1E293B;
            }}
            .card-content {{
                color: #334155;
                line-height: 1.6;
            }}
        </style>
        """, unsafe_allow_html=True)

    def initialize_session_state(self):
        """세션 상태 초기화"""
        if 'requests' not in st.session_state:
            st.session_state.requests = {}
        if 'processing' not in st.session_state:
            st.session_state.processing = False

    def start_background_worker(self):
        """백그라운드 작업자 시작"""
        def worker():
            while True:
                request = analysis_queue.get()
                try:
                    self.process_analysis_request(request)
                except Exception as e:
                    print(f"Error processing request {request.id}: {str(e)}")
                finally:
                    analysis_queue.task_done()

        for _ in range(5):  # 5개의 워커 스레드 시작
            Thread(target=worker, daemon=True).start()

    def process_analysis_request(self, request: AnalysisRequest):
        """분석 요청 처리"""
        try:
            # 캐시된 보고서 확인
            is_cached, cached_content, cached_file = self.get_cached_report(
                request.stock_code, request.reference_date
            )

            if is_cached:
                # 캐시된 보고서가 있으면 바로 이메일 전송
                send_email(request.email, cached_content)
                request.result = f"캐시된 분석 보고서가 이메일로 전송되었습니다. (파일: {cached_file.name})"
            else:
                # 별도 프로세스로 분석 실행
                import subprocess
                import tempfile
                import json

                # 프로젝트 루트 디렉토리와 streamlit 디렉토리 경로
                project_root = str(Path(__file__).parent.parent.parent.absolute())
                streamlit_dir = str(Path(__file__).parent.absolute())

                # 요청 정보를 임시 파일에 저장
                with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                    request_info = {
                        'stock_code': request.stock_code,
                        'company_name': request.company_name,
                        'reference_date': request.reference_date,
                        'output_file': f"reports/{request.stock_code}_{request.company_name}_{request.reference_date}_gpt5.4-mini.md",
                        'email': request.email
                    }
                    json.dump(request_info, f)
                    request_file = f.name

                # 별도 프로세스 실행
                subprocess.Popen([
                    "python", "-c",
                    f'''
import asyncio, json, os, sys

# Python path 설정
project_root = "{project_root}"
streamlit_dir = "{streamlit_dir}"
sys.path.insert(0, project_root)
sys.path.insert(0, streamlit_dir)

# 작업 디렉토리 변경
os.chdir(project_root)

print(f"Working directory: {{os.getcwd()}}")
print(f"Python path: {{sys.path[:3]}}")

try:
    from cores.main import analyze_stock
    print("Successfully imported analyze_stock")
except ImportError as e:
    print(f"Failed to import analyze_stock: {{e}}")
    exit(1)

try:
    from email_sender import send_email
    print("Successfully imported send_email")
except ImportError as e:
    print(f"Failed to import send_email: {{e}}")
    exit(1)

# 요청 정보 로드
with open("{request_file}", "r") as f:
    info = json.load(f)

# 분석 실행
async def run():
    try:
        print(f"Starting analysis for {{info['company_name']}} ({{info['stock_code']}})")
        report = await analyze_stock(
            company_code=info["stock_code"],
            company_name=info["company_name"],
            reference_date=info["reference_date"]
        )
        
        # 결과 저장
        with open(info["output_file"], "w", encoding="utf-8") as f:
            f.write(report)
        print(f"Report saved to {{info['output_file']}}")
        
        # 이메일 전송 
        if send_email(info["email"], report):
            print(f"Email sent successfully to {{info['email']}}")
        else:
            print(f"Failed to send email to {{info['email']}}")
        
        # 임시 파일 삭제
        os.remove("{request_file}")
        print("Analysis completed successfully")
        
    except Exception as e:
        print(f"Error during analysis: {{e}}")
        import traceback
        traceback.print_exc()

asyncio.run(run())
'''
                ], cwd=project_root)

                request.result = f"분석이 시작되었습니다. 완료 후 이메일로 결과가 전송됩니다."

            request.status = "completed"

        except Exception as e:
            request.status = "failed"
            request.result = f"분석 중 오류가 발생했습니다: {str(e)}"

    @staticmethod
    def get_cached_report(stock_code: str, reference_date: str) -> tuple[bool, str, Path | None]:
        """캐시된 보고서 검색"""
        report_pattern = f"{stock_code}_*_{reference_date}*.md"
        matching_files = list(REPORTS_DIR.glob(report_pattern))

        if matching_files:
            latest_file = max(matching_files, key=lambda p: p.stat().st_mtime)
            with open(latest_file, "r", encoding="utf-8") as f:
                return True, f.read(), latest_file
        return False, "", None

    @staticmethod
    def save_report(stock_code: str, company_name: str, reference_date: str, content: str) -> Path:
        """보고서를 파일로 저장"""
        filename = f"{stock_code}_{company_name}_{reference_date}_gpt4o.md"
        filepath = REPORTS_DIR / filename

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        return filepath

    def submit_analysis(self, stock_code: str, company_name: str, email: str, reference_date: str) -> str:
        """분석 요청 제출"""
        request = AnalysisRequest(stock_code, company_name, email, reference_date)
        st.session_state.requests[request.id] = request
        analysis_queue.put(request)
        return request.id

    def render_modern_analysis_form(self):
        """모던한 디자인의 분석 요청 폼"""
        # 커스텀 헤더 추가
        self.add_app_header()

        # 앱 설명 카드 (텍스트만 사용)
        st.markdown("### 🤖 AI 주식 분석 에이전트 서비스")
        st.markdown("이 서비스는 AI를 활용하여 종목을 심층 분석하고 전문가 수준의 투자 분석 보고서를 자동으로 생성합니다. 회사 정보와 이메일을 입력하시면 분석이 완료된 후 결과가 이메일로 전송됩니다.")

        # 두 개의 열로 나누어 레이아웃 구성
        col1, col2 = st.columns([2, 1])

        with col1:
            # 분석 요청 카드
            st.markdown("## 분석 요청")

            with st.form("analysis_form"):
                form_col1, form_col2 = st.columns(2)

                with form_col1:
                    company_name = st.text_input("회사명", placeholder="예: 삼성전자")
                    email = st.text_input("이메일 주소", placeholder="결과를 받을 이메일")

                with form_col2:
                    stock_code = st.text_input("종목코드", placeholder="예: 005930 (6자리)")
                    today = datetime.now().date()
                    analysis_date = st.date_input(
                        "분석 기준일",
                        value=today,
                        max_value=today
                    )

                # FAQ 토글
                with st.expander("📌 자주 묻는 질문"):
                    st.markdown("""
                    **Q: 분석은 얼마나 걸리나요?**  
                    A: 일반적으로 5-10분 정도 소요됩니다.
                    
                    **Q: 어떤 정보가 포함되나요?**  
                    A: 주가 분석, 재무제표 분석, 경쟁사 비교, 투자 지표, 뉴스 분석 등이 포함됩니다.
                    
                    **Q: 결과는 어떻게 받나요?**  
                    A: 입력한 이메일로 결과가 전송되며, 이 사이트의 '보고서 보기' 메뉴에서도 확인 가능합니다.
                    """)

                # 디자인된 제출 버튼
                submit_col1, submit_col2, submit_col3 = st.columns([1, 2, 1])
                with submit_col2:
                    submitted = st.form_submit_button("분석 시작", use_container_width=True)

            # 폼 제출 처리
            if submitted:
                if not self.validate_inputs(company_name, stock_code, email):
                    return

                reference_date = analysis_date.strftime("%Y%m%d")
                request_id = self.submit_analysis(stock_code, company_name, email, reference_date)
                st.success("분석이 요청되었습니다. 완료되면 이메일로 결과가 전송됩니다. 이후 이 웹사이트에 재접속 후 '보고서 보기' 메뉴에서도 보실 수 있습니다.")

        with col2:
            # 분석 정보 카드 (네이티브 컴포넌트 사용)
            st.markdown("### ✨ 분석 내용")
            features = [
                {"icon": "📊", "title": "기술적 분석", "desc": "주가 패턴 및 모멘텀 분석"},
                {"icon": "💰", "title": "재무 분석", "desc": "종합적인 재무제표 분석"},
                {"icon": "🏢", "title": "경쟁사 비교", "desc": "동종업계 내 상대적 위치 평가"},
                {"icon": "📈", "title": "투자 지표", "desc": "PER, PBR, ROE 등 핵심 투자지표"},
                {"icon": "📰", "title": "뉴스 분석", "desc": "최신 뉴스 및 시장 반응 분석"}
            ]

            for feature in features:
                st.markdown(f"{feature['icon']} **{feature['title']}** - {feature['desc']}")

            # 분석 완료 예상 시간 (네이티브 컴포넌트 사용)
            st.markdown("### 분석 예상 시간")
            st.markdown("⏱️ **5-10분**")
            st.markdown("분석 완료 후 이메일로 전송됩니다")

        # 분석 상태 섹션
        if st.session_state.requests:
            self.render_request_status()

    def render_request_status(self):
        """요청 상태를 표시하는 메서드"""
        st.markdown("## 📋 진행 중인 분석")

        # 요청 목록을 상태별로 분류
        pending_requests = []
        completed_requests = []
        failed_requests = []

        for request_id, request in st.session_state.requests.items():
            if request.status == "pending":
                pending_requests.append(request)
            elif request.status == "completed":
                completed_requests.append(request)
            elif request.status == "failed":
                failed_requests.append(request)

        # 진행 중인 요청 표시
        if pending_requests:
            for request in pending_requests:
                st.info(f"⏳ {request.company_name} ({request.stock_code}) - 분석 진행 중... (약 5-10분 소요)")

        # 완료된 요청 표시
        if completed_requests:
            for request in completed_requests:
                st.success(f"✅ {request.company_name} ({request.stock_code}) - {request.result}")

        # 실패한 요청 표시
        if failed_requests:
            for request in failed_requests:
                st.error(f"❌ {request.company_name} ({request.stock_code}) - {request.result}")

    def render_modern_report_viewer(self):
        """모던한 디자인의 보고서 뷰어"""
        # 커스텀 헤더 추가
        self.add_app_header()
        
        # 보고서 뷰어 소개
        intro_content = """
        <p>과거에 생성된 분석 보고서를 검색하고 열람할 수 있습니다. 
        종목코드로 검색하거나 목록에서 선택하여 보고서를 확인하세요.</p>
        """
        self.create_card("보고서 뷰어", intro_content, "📑")
        
        # 검색 및 필터 영역
        col1, col2 = st.columns([1, 3])
        
        with col1:
            st.markdown('<div class="filter-card">', unsafe_allow_html=True)
            st.subheader("보고서 검색")
            search_code = st.text_input("종목코드로 검색", placeholder="예: 005930")
            
            # 저장된 보고서 목록 가져오기
            reports = list(REPORTS_DIR.glob("*.md"))
            
            if search_code:
                reports = [r for r in reports if search_code in r.stem]
            
            if not reports:
                st.warning("저장된 보고서가 없습니다.")
                st.markdown('</div>', unsafe_allow_html=True)
                return
            
            # 보고서 분류
            st.markdown("### 보고서 분류")
            report_dates = {}
            
            for report in reports:
                # 파일 수정 날짜 기준으로 분류
                mod_date = datetime.fromtimestamp(report.stat().st_mtime).strftime('%Y-%m-%d')
                if mod_date not in report_dates:
                    report_dates[mod_date] = []
                report_dates[mod_date].append(report)
            
            # 날짜별 보고서 개수 표시
            for date, date_reports in sorted(report_dates.items(), reverse=True):
                st.markdown(f"**{date}** ({len(date_reports)}개)")
            
            st.markdown('</div>', unsafe_allow_html=True)
        
        with col2:
            # 보고서 선택 및 표시 영역
            st.markdown('<div class="report-card">', unsafe_allow_html=True)
            st.subheader("보고서 목록")
            
            # 보고서 정렬 (최신순)
            reports.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            
            # 보고서 선택을 위한 현대적인 UI
            report_options = [f"{r.stem} ({datetime.fromtimestamp(r.stat().st_mtime).strftime('%Y-%m-%d %H:%M')})" for r in reports]
            report_dict = dict(zip(report_options, reports))
            
            selected_report_name = st.selectbox(
                "보고서 선택",
                options=report_options
            )
            
            if selected_report_name:
                selected_report = report_dict[selected_report_name]
                
                # 보고서 메타데이터 표시
                report_meta_col1, report_meta_col2 = st.columns(2)
                with report_meta_col1:
                    st.markdown(f"**생성일시:** {datetime.fromtimestamp(selected_report.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')}")
                with report_meta_col2:
                    st.markdown(f"**파일 크기:** {selected_report.stat().st_size / 1024:.1f} KB")
                
                # 다운로드 버튼 영역
                st.markdown("### 다운로드 옵션")
                download_col1, download_col2 = st.columns(2)
                with download_col1:
                    st.markdown(self.get_download_link(selected_report, 'md'), unsafe_allow_html=True)
                with download_col2:
                    st.markdown(self.get_download_link(selected_report, 'html'), unsafe_allow_html=True)
                
                # 보고서 미리보기
                st.markdown("### 보고서 미리보기")
                
                with open(selected_report, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # 스타일이 적용된 마크다운으로 보여주기
                st.markdown('<div class="markdown-preview">', unsafe_allow_html=True)
                st.markdown(content)
                st.markdown('</div>', unsafe_allow_html=True)
            
            st.markdown('</div>', unsafe_allow_html=True)

    def validate_inputs(self, company_name: str, stock_code: str, email: str) -> bool:
        """입력값 유효성 검사"""
        if not company_name:
            st.error("회사명을 입력해주세요.")
            return False

        if not self.is_valid_stock_code(stock_code):
            st.error("올바른 종목코드를 입력해주세요 (6자리 숫자).")
            return False

        if not self.is_valid_email(email):
            st.error("올바른 이메일 주소를 입력해주세요.")
            return False

        return True

    @staticmethod
    def is_valid_stock_code(code: str) -> bool:
        return bool(re.match(r'^\d{6}$', code))

    @staticmethod
    def is_valid_email(email: str) -> bool:
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))

    @staticmethod
    def get_download_link(file_path: Path, file_format: str) -> str:
        """다운로드 링크 생성"""
        with open(file_path, 'r', encoding='utf-8') as f:
            data = f.read()

        if file_format == 'html':
            # 마크다운을 HTML로 변환
            html_content = markdown.markdown(
                data,
                extensions=['markdown.extensions.fenced_code', 'markdown.extensions.tables']
            )
            b64 = base64.b64encode(html_content.encode()).decode()
            extension = 'html'
        else:
            b64 = base64.b64encode(data.encode()).decode()
            extension = 'md'

        filename = f"{file_path.stem}.{extension}"
        return f'<a href="data:file/{extension};base64,{b64}" download="{filename}">💾 {extension.upper()} 형식으로 다운로드</a>'

    def main(self):
        """메인 애플리케이션 실행"""
        # 사이드바 디자인 개선
        st.sidebar.markdown("""
        <div class="sidebar-header">
            <div class="sidebar-logo">📊</div>
            <div class="sidebar-title">analysis.stocksimulation.kr</div>
        </div>
        """, unsafe_allow_html=True)
        
        st.sidebar.title("메뉴")
        
        # 모던한 사이드바 메뉴
        menu_options = {
            "분석 요청": "📝",
            "보고서 보기": "📚"
        }
        
        menu = st.sidebar.radio(
            "선택",
            list(menu_options.keys()),
            format_func=lambda x: f"{menu_options[x]} {x}"
        )
        
        # 앱 버전 및 소셜 링크
        st.sidebar.markdown("---")
        st.sidebar.markdown("#### 서비스 정보")
        st.sidebar.markdown("버전: v1.0.2")
        st.sidebar.markdown("© 2025 https://analysis.stocksimulation.kr")
        
        # 메인 콘텐츠 렌더링
        if menu == "분석 요청":
            self.render_modern_analysis_form()
        else:
            self.render_modern_report_viewer()

if __name__ == "__main__":
    app = ModernStockAnalysisApp()
    app.main()
