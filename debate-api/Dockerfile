FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts
COPY docker-entrypoint.sh ./

RUN pip install . && chmod +x docker-entrypoint.sh

RUN useradd --create-home appuser
USER appuser

# Railway injects PORT; 8000 is the local default.
EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
