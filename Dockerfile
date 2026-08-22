# TLDR Radio — app image. Multi-arch: builds native amd64 (plexbox) or arm64 (Mac).
FROM python:3.13-slim

WORKDIR /app

# Runtime deps first, for layer caching. lxml ships manylinux wheels → no build tools needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code + static UI (self-hosted fonts bundled in app/static/fonts).
COPY app ./app

ENV APP_PORT=7777 \
    DATA_DIR=/data \
    KOKORO_URL=http://kokoro:8880 \
    PYTHONUNBUFFERED=1

# Run as a non-root user (CWE-250): nothing in here needs root, and the app writes only to /data.
# UID 1000 is not arbitrary — /data is a host bind mount whose ownership the image cannot control,
# and 1000 is the first non-system account on a typical Linux host, so a plain `git clone &&
# make up` already owns ./data. Hosts where it differs override APP_UID/APP_GID in .env instead of
# rebuilding. If the two ever disagree the app says so at startup rather than dying mid-broadcast.
RUN groupadd --gid 1000 appuser \
 && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin appuser \
 && mkdir -p /data \
 && chown appuser:appuser /data
USER appuser

EXPOSE 7777
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7777"]
