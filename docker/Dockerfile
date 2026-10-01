# syntax=docker/dockerfile:1.7
# A股量化交易系统：Web / API 服务 + 定时任务
#   docker compose -f docker/docker-compose.yml up -d
# 多阶段构建：先构建前端，再装 Python 依赖和 Playwright Chromium

FROM node:24-slim AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY apps/web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu tzdata fonts-noto-cjk \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# PyInstaller（打包桌面端）在容器里用不到
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    grep -viE '^pyinstaller' requirements.txt > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt

# 东方财富、同花顺、社交平台采集需要 Chromium；--with-deps 会装好系统依赖
RUN playwright install --with-deps chromium && chmod -R a+rX /ms-playwright

RUN groupadd -g 1000 quant && useradd -u 1000 -g quant -m quant

COPY main.py server.py ./
COPY api/ ./api/
COPY src/ ./src/
COPY scripts/ ./scripts/
# 默认配置放在镜像内的 defaults 目录，启动时复制进（可能挂载了的）config 目录
COPY config/settings.yaml.example config/stock_pool.yaml ./defaults/config/
COPY --from=web /web/dist ./apps/web/dist
COPY docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p config data logs && chown -R quant:quant /app

VOLUME ["/app/config", "/app/data", "/app/logs"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8000"]
