FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM python:3.13-slim
RUN groupadd --gid 10001 linkvault && useradd --uid 10001 --gid linkvault --create-home linkvault
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" DATA_DIR=/data
COPY --from=builder /app/.venv /app/.venv
COPY --chown=linkvault:linkvault . /app
RUN mkdir -p /data/files /data/tmp /app/staticfiles && chown -R linkvault:linkvault /data /app/staticfiles && chmod 700 /data /data/files /data/tmp && chmod +x /app/docker/entrypoint.sh
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "linkvault.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "300", "--graceful-timeout", "30", "--worker-tmp-dir", "/data/tmp", "--error-logfile", "-"]
