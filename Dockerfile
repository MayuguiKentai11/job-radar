# JobRadar — production image (Render free tier compatible)
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ENV=production

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# run as an unprivileged user; app files are read-only for it
RUN useradd --system --uid 10001 --no-create-home appuser && chmod -R a-w /app
USER appuser

EXPOSE 10000
# single worker: rate limits and concurrency slots are in-memory (one instance on the free plan)
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --workers 1 --no-server-header --timeout-keep-alive 75"]
