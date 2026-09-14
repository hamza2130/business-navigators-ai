# Business Navigators AI

An intelligent, multi-service backend powered by **FastAPI** that integrates with Meta's **WhatsApp Business Cloud API**. The platform acts as an automated assistant featuring AI-driven conversational logic with real conversation memory, document and image text parsing (OCR), automated email notifications, and calendar scheduling integrations.

---

## Key Features

- **WhatsApp Cloud API Integration:** Signed webhook receiver handling text messages, media files, dynamic routing, and instant automated replies, processed in the background so Meta's delivery is acknowledged immediately.
- **AI Service Integration:** Conversational context kept across a thread and passed to Groq LLM inference on every reply.
- **Document Parsing & OCR:** Automated text extraction from uploaded images and documents (PDF, Word, Excel), with expiry-date extraction that looks for the actual "expiry" label rather than just the first date on the page.
- **Calendar & Email Automation:** Google Calendar booking (Asia/Dubai) and automated transactional/notification email delivery via Brevo.
- **Compliance Reminders:** A daily scheduled job tracks document expiry and sends WhatsApp reminders 30 and 7 days out, resilient to a missed run.
- **SQLite Database Persistence:** Local storage for lead records, conversation history, and the knowledge base.
- **Admin Dashboard:** Key-protected staff UI for managing the knowledge base and pausing AI replies per lead.

---

## Project Architecture

```
BusinessNavigatorsAI/
├── main.py                # FastAPI entrypoint, webhook routes, admin routes, auth
├── config.py               # Environment configuration loader
├── database.py              # SQLite connections, schema migrations, lead/message models
├── ai_service.py            # Groq API integration for conversational AI
├── whatsapp_service.py      # Meta Graph API message + template dispatch
├── ocr_service.py           # OCR + expiry-date extraction
├── document_parser.py       # Document text extraction logic (PDF/Word/Excel/images)
├── calendar_service.py      # Google Calendar booking
├── email_service.py         # Brevo email delivery integration
├── scheduler_service.py     # Daily compliance-reminder cron job
├── kb_service.py             # Knowledge base query processor
├── static/                  # Admin dashboard (key-protected)
├── requirements.txt         # Project dependencies
├── .env.example              # Every environment variable this app reads, documented
└── .gitignore                # Excluded secrets, credentials, local databases
```

---

## Tech Stack

- **Framework:** [FastAPI](https://fastapi.tiangolo.com/) (Python 3.10+)
- **Server:** Uvicorn
- **AI Engine:** Groq API (LLaMA inference models)
- **Messaging:** Meta WhatsApp Cloud API (Graph API v20.0+)
- **Tunneling:** ngrok (for local webhook deployment)
- **Database:** SQLite

---

## Quickstart Guide

### 1. Prerequisites
- Python 3.10+ installed
- Meta Developer Account with WhatsApp Cloud API enabled
- ngrok installed on your machine
- **Tesseract OCR** and **Poppler** installed and on PATH (`pdf2image`/`pytesseract` are Python wrappers around these system binaries - they are not installed by `pip`)

### 2. Installation

Clone the repository and enter the project folder:

```bash
git clone https://github.com/hamza2130/business-navigators-ai.git
cd business-navigators-ai
```

Set up a virtual environment:

```bash
# On Windows
python -m venv venv
.\venv\Scripts\Activate.ps1

# On macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

Install the dependencies:

```bash
pip install -r requirements.txt
```

### 3. Environment Setup

Copy `.env.example` to `.env` and fill in real values - every variable the app reads is documented there, including which ones are required (the app refuses requests rather than silently running unauthenticated if these are left blank):

```bash
cp .env.example .env
```

At minimum you'll need:

```env
GROQ_API_KEY=your_groq_api_key

WHATSAPP_TOKEN=your_meta_permanent_or_temporary_access_token
WHATSAPP_PHONE_NUMBER_ID=your_whatsapp_phone_number_id
META_VERIFY_TOKEN=choose_a_long_random_string
META_APP_SECRET=your_meta_app_secret          # required - verifies inbound webhooks are really from Meta

BREVO_API_KEY=your_brevo_api_key
SENDER_EMAIL=your_email@domain.com
EMAIL_WEBHOOK_SECRET=choose_a_long_random_string   # required - verifies inbound email webhook calls

ADMIN_API_KEY=choose_a_long_random_string      # required - gates every /admin/* route and the dashboard

BOOKING_LINK=https://calendar.app.google/...
```

See `.env.example` for the full list, including optional WhatsApp Message Template names for the expiry-reminder job.

### 4. Running the Server & Webhook

Start the FastAPI application:

```bash
uvicorn main:app --reload
```

Expose your local server via ngrok (in a separate terminal):

```bash
ngrok http 8000
```

Configure Meta Webhook:

In Meta Developer Console under **WhatsApp > Configuration**:

- **Callback URL:** `https://your-ngrok-url.ngrok-free.app/whatsapp/webhook`
- **Verify Token:** the value you set for `META_VERIFY_TOKEN` in your `.env`
- Subscribe to the `messages` webhook field.

The email webhook lives at `https://your-ngrok-url.ngrok-free.app/webhook/email` - configure it as Brevo's inbound-parse destination, with `EMAIL_WEBHOOK_SECRET` set as a custom header or `?secret=` query param on that URL.

### Alternative: Docker

```bash
cp .env.example .env   # fill in real values first
docker compose up --build
```

This builds the image (Python 3.11 + Tesseract OCR + Poppler, the two
system binaries `pytesseract`/`pdf2image` depend on but don't install
themselves) and runs it on `http://localhost:8000`, with the SQLite
database persisted to `./data/leads.db` so it survives a rebuild. Set
`DB_NAME` yourself if you're running outside Docker and want the same
override.

**Not yet verified against a real Docker daemon** - it was written and
reviewed for correctness (base image, system packages, healthcheck) but
this environment didn't have Docker available to actually build and run
it. Treat it as a strong starting point, not a confirmed-working image,
until someone runs `docker compose up --build` for real.

### 5. Admin Dashboard

Open `https://your-host/dashboard/?key=<your ADMIN_API_KEY>`. The key is required - the dashboard and every `/admin/*` API route return `401` without it.

---

## Security & Privacy Note

- Secret keys, `.env` files, `credentials.json` (Google service account), and local SQLite databases are excluded from version control via `.gitignore`.
- Every inbound webhook is authenticated (Meta's `X-Hub-Signature-256` for WhatsApp, a shared secret for the email webhook) - requests that don't verify are rejected, not silently accepted.
- Every `/admin/*` route and the dashboard require `ADMIN_API_KEY`; there is no unauthenticated fallback.
- Ensure all API keys are kept secure in deployment environments, and rotate `ADMIN_API_KEY` / `META_APP_SECRET` / `EMAIL_WEBHOOK_SECRET` if they are ever exposed.

---

## License

This project is open-source and available under the [MIT License](LICENSE).
