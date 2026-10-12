FROM python:3.12-slim

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app

# 依赖从 pyproject.toml 读取，避免在两处维护同一份依赖列表
COPY pyproject.toml ./
RUN python -c "import tomllib,subprocess,sys; deps=tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; subprocess.check_call([sys.executable,'-m','pip','install','--retries','10','--timeout','120',*deps])"

COPY . .
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
