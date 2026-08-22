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

EXPOSE 7777
USER 1000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7777"]