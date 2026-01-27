# PRISM-INSIGHT Docker Image
# Ubuntu 24.04 기반 AI 주식 분석 시스템

FROM ubuntu:24.04

# 환경 변수 설정
ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Seoul \
    LANG=ko_KR.UTF-8 \
    LANGUAGE=ko_KR:ko \
    LC_ALL=ko_KR.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHON_VERSION=3.12 \
    ENABLE_CRON=true

# 작업 디렉토리 설정
WORKDIR /app

# 시스템 패키지 업데이트 및 기본 도구 설치 (cron 포함)
RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
    python3=${PYTHON_VERSION}* \
    python3-pip \
    python3-venv \
    python3-full \
    git \
    curl \
    wget \
    ca-certificates \
    gnupg \
    locales \
    tzdata \
    fonts-nanum \
    fonts-nanum-coding \
    fonts-nanum-extra \
    vim \
    nano \
    cron \
    && locale-gen ko_KR.UTF-8 \
    && update-locale LANG=ko_KR.UTF-8 \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22.x LTS 설치
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    npm install -g npm@latest && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# UV (Python 패키지 관리자) 설치
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    echo 'export PATH="$HOME/.cargo/bin:$PATH"' >> /root/.bashrc

# PATH에 UV 추가
ENV PATH="/root/.cargo/bin:$PATH"

# Python 가상환경 생성
RUN python3 -m venv /app/venv

# 가상환경 활성화
ENV PATH="/app/venv/bin:$PATH"

# Git 리포지토리 클론 (feature/prism-us 브랜치)
RUN git clone -b feature/prism-us https://github.com/dragon1086/prism-insight.git /app/prism-insight

# 작업 디렉토리 변경
WORKDIR /app/prism-insight

# Python 의존성 설치 (setuptools for pykrx compatibility)
RUN pip install --no-cache-dir --upgrade pip setuptools && \
    pip install --no-cache-dir -r requirements.txt

# Playwright 브라우저 설치 (Chromium만)
RUN playwright install --with-deps chromium

# perplexity-ask MCP 서버 설치
WORKDIR /app/prism-insight/perplexity-ask
RUN npm install && \
    npm run build

# 다시 메인 디렉토리로 이동
WORKDIR /app/prism-insight

# 한글 폰트 설치 (Ubuntu용)
RUN python3 ./cores/ubuntu_font_installer.py || true

# 폰트 캐시 갱신
RUN fc-cache -fv && \
    python3 -c "import matplotlib.font_manager as fm; fm.fontManager.rebuild()" || true

# 설정 파일 복사 (예시 파일들)
RUN cp .env.example .env && \
    cp mcp_agent.config.yaml.example mcp_agent.config.yaml && \
    cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml

# SQLite 데이터베이스 디렉토리 생성
RUN mkdir -p /app/prism-insight/sqlite && \
    touch /app/prism-insight/stock_tracking_db.sqlite

# 로그 및 결과물 디렉토리 생성
RUN mkdir -p /app/prism-insight/reports \
             /app/prism-insight/pdf_reports \
             /app/prism-insight/html_reports \
             /app/prism-insight/charts \
             /app/prism-insight/logs \
             /app/prism-insight/telegram_messages/sent

# Docker 설정 디렉토리 생성 및 파일 복사
RUN mkdir -p /app/prism-insight/docker

# Crontab 및 Entrypoint 스크립트 복사 (이미지 빌드 시)
COPY docker/crontab /app/prism-insight/docker/crontab
COPY docker/entrypoint.sh /app/prism-insight/docker/entrypoint.sh

# Entrypoint 스크립트 실행 권한 부여
RUN chmod +x /app/prism-insight/docker/entrypoint.sh && \
    chmod 644 /app/prism-insight/docker/crontab

# 권한 설정
RUN chmod -R 755 /app/prism-insight

# Cron 로그 파일 생성 (cron 출력 확인용)
RUN touch /var/log/cron.log

# 헬스체크
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python3 -c "import sys; sys.exit(0)" && service cron status || exit 1

# 기본 셸 변경
SHELL ["/bin/bash", "-c"]

# Entrypoint 설정
ENTRYPOINT ["/app/prism-insight/docker/entrypoint.sh"]

# 기본 명령어 (없으면 entrypoint가 컨테이너를 유지)
CMD []
