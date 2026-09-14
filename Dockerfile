# syntax=docker/dockerfile:1
FROM python:3.11-slim

# System dependencies the Python packages wrap, not install themselves:
#   tesseract-ocr - OCR engine used by pytesseract (ocr_service.py)
#   poppler-utils - gives pdf2image its `pdftoppm`/`pdftocairo` binaries,
#                   used for OCR fallback on scanned PDFs
LABEL org.opencontainers.image.title="business-navigators-ai"
LABEL org.opencontainers.image.description="WhatsApp/Email AI client-acquisition assistant"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first so this layer is cached across code-only changes
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

# SQLite database lives on a volume so it survives container recreation -
# see docker-compose.yml. DB_NAME defaults to "leads.db" (relative to
# WORKDIR) unless overridden; the compose file mounts ./data as /app/data
# and the app is expected to be pointed there in production via a future
# DB_NAME/DATABASE_URL setting (tracked separately - see the SRS gap
# analysis's Phase 2 Postgres migration).

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3)" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
