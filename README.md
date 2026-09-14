# Business Navigators AI

An intelligent, multi-service backend powered by **FastAPI** that integrates with Meta's **WhatsApp Business Cloud API**. The platform acts as an automated assistant featuring AI-driven conversational logic with real conversation memory, document and image text parsing (OCR), automated email notifications, and calendar scheduling integrations.

---

## Key Features

- **WhatsApp Cloud API Integration:** Signed webhook receiver handling text messages, media files, and dynamic routing. Inbound messages are enqueued to Redis and handled by a separate `worker.py` process, so Meta's delivery is acknowledged immediately and a message survives an app restart instead of being lost.
- **AI Service Integration:** Conversational context kept across a thread and passed to Groq LLM inference on every reply, which also extracts structured lead-qualification fields (name, industry, VAT status, service interest) from natural conversation.
- **Document Parsing, OCR & Storage:** Automated text extraction from uploaded images and documents (PDF, Word, Excel), with expiry-date extraction that looks for the actual "expiry" label rather than just the first date on the page. The original file is stored privately in S3-compatible object storage, accessible only via short-lived signed URLs, and queued for staff review.
- **Calendar & Email Automation:** Google Calendar booking (Asia/Dubai) and automated transactional/notification email delivery via Brevo.
- **Compliance Reminders:** A daily scheduled job tracks document expiry and sends WhatsApp reminders 30 and 7 days out, resilient to a missed run.
- **Multi-Tenant PostgreSQL:** Every table enforces tenant isolation via Row-Level Security at the database layer, not just application code - a second tenant can be added with zero schema changes.
- **Admin Dashboard:** Key-protected staff UI for managing the knowledge base, scoring rules, lead list, and pausing AI replies per lead.

---

## Project Architecture

```
BusinessNavigatorsAI/
├── main.py                # FastAPI entrypoint, webhook routes, admin routes, auth
├── worker.py               # arq worker process - consumes queued WhatsApp messages
├── queue_utils.py           # Redis settings shared by main.py and worker.py
├── config.py               # Environment configuration loader
├── database.py              # PostgreSQL connection pool, RLS-aware queries
├── schema.sql               # Table definitions, RLS policies, app_user role
├── scoring_service.py       # Lead scoring/tiering against admin-configurable rules
├── document_store.py        # S3-compatible document storage (upload, signed URLs)
├── ai_service.py            # Groq API integration for conversational AI + FR-3 extraction
├── whatsapp_service.py      # Meta Graph API message + template dispatch
├── ocr_service.py           # OCR + expiry-date extraction
├── document_parser.py       # Document text extraction logic (PDF/Word/Excel/images)
├── calendar_service.py      # Google Calendar booking
├── email_service.py         # Brevo email delivery integration
├── scheduler_service.py     # Daily compliance-reminder cron job
├── kb_service.py             # Knowledge base query processor
├── static/                  # Admin dashboard (key-protected)
├── requirements.txt         # Project dependencies
├── docker-compose.yml        # Full local stack: postgres, redis, minio, api, worker
├── .env.example              # Every environment variable this app reads, documented
└── .gitignore                # Excluded secrets, credentials, local databases
```

---

## Tech Stack

- **Framework:** [FastAPI](https://fastapi.tiangolo.com/) (Python 3.10+)
- **Server:** Uvicorn
- **Database:** PostgreSQL with Row-Level Security (multi-tenant from day one)
- **Queue:** Redis + [arq](https://arq-docs.helpmanual.io/) (durable job queue - see `worker.py`)
- **Object storage:** Any S3-compatible endpoint (real AWS S3 in production; MinIO or moto locally)
- **AI Engine:** Groq API (LLaMA inference models)
- **Messaging:** Meta WhatsApp Cloud API (Graph API v20.0+)
- **Tunneling:** ngrok (for local webhook deployment)

---

## Quickstart Guide

The stack now has four moving parts - PostgreSQL, Redis, S3-compatible
storage, and **two** application processes (`api` and `worker`) - so
**Docker is the recommended path**. A fully-native setup is documented
below it for local development without Docker.

### Prerequisites (either path)
- Meta Developer Account with WhatsApp Cloud API enabled
- ngrok installed on your machine (to expose your local server to Meta's webhook)
- Groq / Brevo API keys

### Option A: Docker (recommended)

```bash
git clone https://github.com/hamza2130/business-navigators-ai.git
cd business-navigators-ai
cp .env.example .env   # fill in Groq/Meta/Brevo/admin keys - see .env.example
docker compose up --build
```

This starts the full stack: `postgres` (with `schema.sql` applied
automatically on first startup - every table, RLS policy, and the
`app_user` role), `redis` (the job queue), `minio` (local S3-compatible
document storage - point `S3_ENDPOINT_URL` at nothing and set real AWS
credentials to use real S3 in production instead), `api` (FastAPI on
`http://localhost:8000`), and `worker` (the arq process that actually
handles queued WhatsApp messages - **nothing gets processed without it
running**). Scale workers independently for load:
`docker compose up --scale worker=3`.

**Not yet verified against a real Docker daemon** - written and reviewed
for correctness (images, health checks, service dependencies, environment
wiring), but this environment didn't have Docker available to actually
build and run it. Each underlying piece (schema.sql, the S3 code, the
Redis queue) was verified for real against portable non-Docker installs
of Postgres/Redis/moto - see the commit history - but the Compose file
itself is unverified. Treat it as a strong starting point until someone
runs `docker compose up --build` for real.

### Option B: Native (no Docker)

1. **Install and start PostgreSQL, Redis, and an S3-compatible endpoint**
   (real MinIO, or `pip install "moto[server]" && python -m moto.server -p 9000`
   for a lightweight stand-in), then apply the schema:
   ```bash
   createdb business_navigators
   psql -d business_navigators -f schema.sql
   ```
2. **Install Tesseract OCR and Poppler** and put them on PATH
   (`pytesseract`/`pdf2image` are Python wrappers around these system
   binaries - `pip` does not install them).
3. **Set up the app:**
   ```bash
   python -m venv venv && source venv/bin/activate   # .\venv\Scripts\Activate.ps1 on Windows
   pip install -r requirements.txt
   cp .env.example .env   # fill in DATABASE_URL/REDIS_URL/S3_* plus Groq/Meta/Brevo/admin keys
   ```
4. **Run both processes** (in separate terminals - the app enqueues messages, the worker is what actually handles them):
   ```bash
   uvicorn main:app --reload
   arq worker.WorkerSettings
   ```

### Connecting to Meta (either path)

Expose your local server via ngrok:

```bash
ngrok http 8000
```

In Meta Developer Console under **WhatsApp > Configuration**:

- **Callback URL:** `https://your-ngrok-url.ngrok-free.app/whatsapp/webhook`
- **Verify Token:** the value you set for `META_VERIFY_TOKEN` in your `.env`
- Subscribe to the `messages` webhook field.

The email webhook lives at `https://your-ngrok-url.ngrok-free.app/webhook/email` - configure it as Brevo's inbound-parse destination, with `EMAIL_WEBHOOK_SECRET` set as a custom header or `?secret=` query param on that URL.

### Admin Dashboard

Open `https://your-host/dashboard/?key=<your ADMIN_API_KEY>`. The key is required - the dashboard and every `/admin/*` API route return `401` without it.

---

## Security & Privacy Note

- Secret keys and `.env` files (including `credentials.json`, the Google service account key) are excluded from version control via `.gitignore`.
- Tenant isolation is enforced by PostgreSQL Row-Level Security, not just application-level filtering - every table's policies fail closed (zero rows visible) if a connection's tenant context is ever unset, rather than exposing all tenants' data.
- Documents are stored privately in S3-compatible storage and are only ever accessible via short-lived signed URLs; every URL issuance is logged (`document_access_log`) as an audit trail.
- Every inbound webhook is authenticated (Meta's `X-Hub-Signature-256` for WhatsApp, a shared secret for the email webhook) - requests that don't verify are rejected, not silently accepted.
- Every `/admin/*` route and the dashboard require `ADMIN_API_KEY`; there is no unauthenticated fallback.
- Ensure all API keys are kept secure in deployment environments, and rotate `ADMIN_API_KEY` / `META_APP_SECRET` / `EMAIL_WEBHOOK_SECRET` / the `app_user` Postgres password if they are ever exposed.

---

## License

This project is open-source and available under the [MIT License](LICENSE).
