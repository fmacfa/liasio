FROM python:3.13-slim@sha256:57b29a7c050c4e679e4bea572e14b3965cf4b5839de2da49d55e49ffc4024f39 AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9@sha256:538e0b39736e7feae937a65983e49d2ab75e1559d35041f9878b7b7e51de91e4 /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM python:3.13-slim@sha256:57b29a7c050c4e679e4bea572e14b3965cf4b5839de2da49d55e49ffc4024f39
RUN groupadd --gid 10001 linkvault && useradd --uid 10001 --gid linkvault --create-home linkvault
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH" DATA_DIR=/data
COPY --from=builder /app/.venv /app/.venv
COPY --chown=linkvault:linkvault manage.py pyproject.toml uv.lock LICENSE /app/
COPY --chown=linkvault:linkvault linkvault/ /app/linkvault/
COPY --chown=linkvault:linkvault vault/ /app/vault/
COPY --chown=linkvault:linkvault templates/ /app/templates/
COPY --chown=linkvault:linkvault static/ /app/static/
COPY --chown=linkvault:linkvault locale/ /app/locale/
COPY --chown=linkvault:linkvault scripts/ /app/scripts/
COPY --chown=linkvault:linkvault docs/ /app/docs/
COPY --chown=linkvault:linkvault examples/ /app/examples/
COPY --chown=linkvault:linkvault docker/entrypoint.sh /app/docker/entrypoint.sh
RUN mkdir -p /data/files /data/tmp /data/uploads /app/staticfiles /run/linkvault-preview && chown -R linkvault:linkvault /data /app/staticfiles /run/linkvault-preview && chmod 700 /data /data/files /data/tmp /data/uploads /run/linkvault-preview && chmod +x /app/docker/entrypoint.sh
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "linkvault.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "300", "--graceful-timeout", "30", "--worker-tmp-dir", "/data/tmp", "--error-logfile", "-"]
