# Business Navigators AI 

An intelligent, multi-service backend powered by **FastAPI** that integrates with Meta's **WhatsApp Business Cloud API**. The platform acts as an automated assistant featuring AI-driven conversational logic, document and image text parsing (OCR), automated email notifications, and calendar scheduling integrations.

---

##  Key Features

- **WhatsApp Cloud API Integration:** Full webhook receiver setup handling text messages, media files, dynamic routing, and instant automated replies.
- **AI Service Integration:** Real-time conversational context processing powered by Groq LLM inference.
- **Document Parsing & OCR:** Automated text extraction from uploaded images and documents (PDF, Excel, images).
- **Calendar & Email Automation:** Dynamic calendar booking link generation and automated transaction/notification email delivery via Brevo.
- **SQLite Database Persistence:** Local storage for session tracking, message history, and business logs.

---

##  Project Architecture

```
BusinessNavigatorsAI/
├── main.py                # FastAPI entrypoint & WhatsApp webhook routes
├── config.py               # Environment configuration loader
├── database.py              # SQLite database connections and models
├── ai_service.py            # Groq API integration for conversational AI
├── whatsapp_service.py      # Meta Graph API message dispatch utility
├── ocr_service.py           # Optical Character Recognition engine for media
├── document_parser.py       # Document text extraction logic
├── calendar_service.py      # Appointment booking & calendar link builder
├── email_service.py         # Brevo SMTP email delivery integration
├── scheduler_service.py     # Automated background jobs & task timing
├── kb_service.py             # Knowledge base query processor
├── static/                  # Static web assets & dashboards
├── requirements.txt         # Project dependencies
└── .gitignore                # Excluded secret credentials and environments
```

---

##  Tech Stack

- **Framework:** [FastAPI](https://fastapi.tiangolo.com/) (Python 3.10+)
- **Server:** Uvicorn
- **AI Engine:** Groq API (LLaMA inference models)
- **Messaging:** Meta WhatsApp Cloud API (Graph API v20.0+)
- **Tunneling:** ngrok (for local webhook deployment)
- **Database:** SQLite

---

##  Quickstart Guide

### 1. Prerequisites
- Python 3.10+ installed
- Meta Developer Account with WhatsApp Cloud API enabled
- ngrok installed on your machine

### 2. Installation

Clone the repository and enter the project folder:

```bash
git clone https://github.com/rabia-irshad2/business-navigators-ai.git
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

Create a `.env` file in the root directory and populate it with your API credentials:

```env
# AI Model Configuration
GROQ_API_KEY=your_groq_api_key

# Email Configuration
BREVO_API_KEY=your_brevo_api_key
SENDER_EMAIL=your_email@domain.com

# Calendar Setup
BOOKING_LINK=https://calendar.app.google/...

# Meta WhatsApp Cloud API Credentials
WHATSAPP_TOKEN=your_meta_permanent_or_temporary_access_token
WHATSAPP_PHONE_NUMBER_ID=your_whatsapp_phone_number_id
META_VERIFY_TOKEN=my_secret_token
```

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

- **Callback URL:** `https://your-ngrok-url.ngrok-free.app/webhook/whatsapp`
- **Verify Token:** Use the value matching `META_VERIFY_TOKEN` in your `.env` (e.g., `my_secret_token`)
- Subscribe to the `messages` webhook field.

---

##  Security & Privacy Note

- Secret keys, `.env` files, and local SQLite databases are strictly excluded from version control via `.gitignore`.
- Ensure all API keys are kept secure in deployment environments.

---

##  License

This project is open-source and available under the MIT License.

---

### Step-by-Step Commands to Push `README.md` to GitHub

Run these three commands in your terminal:

```bash
git add README.md
git commit -m "Add detailed README documentation"
git push
```
