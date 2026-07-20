FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

RUN groupadd --system verifierforge \
    && useradd --system --gid verifierforge --home /app verifierforge

WORKDIR /app
COPY requirements-api.txt ./
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements-api.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY config ./config
COPY core ./core
COPY data ./data
COPY scripts ./scripts

RUN chown -R verifierforge:verifierforge /app
USER verifierforge

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:' + __import__('os').environ.get('PORT', '8000') + '/healthz', timeout=3)"

CMD ["bash", "scripts/start_hosted_backend.sh"]
