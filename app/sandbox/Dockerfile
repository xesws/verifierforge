FROM python:3.11-slim

RUN python -m pip install --no-cache-dir "sqlparse>=0.5,<1"

WORKDIR /tmp
USER 65534:65534

ENTRYPOINT ["python"]
