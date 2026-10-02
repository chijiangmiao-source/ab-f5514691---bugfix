# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /srv

COPY app/ app/
COPY tests/ tests/
COPY scripts/ scripts/
# Kept in the image so the one-shot verify service can assert the build
# context shipped everything the deployment needs.
COPY Dockerfile docker-compose.yml ./

RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /srv
USER appuser

EXPOSE 8080

CMD ["python", "-m", "app.server"]
