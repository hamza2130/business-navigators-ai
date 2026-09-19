# config.py
import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

    # Meta WhatsApp Cloud API
    WHATSAPP_TOKEN: str = os.getenv("WHATSAPP_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    META_VERIFY_TOKEN: str = os.getenv("META_VERIFY_TOKEN", "")
    META_API_VERSION: str = os.getenv("META_API_VERSION", "v20.0")
    # App secret from the Meta developer console, used to verify the
    # X-Hub-Signature-256 header on every inbound webhook POST. Without this,
    # anyone who finds the webhook URL can forge messages on your behalf.
    META_APP_SECRET: str = os.getenv("META_APP_SECRET", "")

    BREVO_API_KEY: str = os.getenv("BREVO_API_KEY", "")
    SENDER_EMAIL: str = os.getenv("SENDER_EMAIL", "")
    # Shared secret expected as a query param / header on the Brevo inbound
    # email webhook (Brevo has no built-in HMAC signature for inbound parse).
    EMAIL_WEBHOOK_SECRET: str = os.getenv("EMAIL_WEBHOOK_SECRET", "")

    # Meeting booking link
    BOOKING_LINK: str = os.getenv(
        "BOOKING_LINK", "https://calendly.com/your-business-navigators/consultation"
    )

    # Pre-approved WhatsApp Message Templates for the expiry-reminder cron
    # job. Meta only allows free-form text replies inside the 24h customer
    # service window - a proactive reminder sent days after the client last
    # wrote in is OUTSIDE that window and Meta will reject a plain text
    # send. These must be created and approved in Meta Business Manager
    # first; until WHATSAPP_TEMPLATE_30D/_7D are set, the scheduler falls
    # back to plain text with a loud warning (useful for local dev, NOT
    # sufficient for production).
    WHATSAPP_TEMPLATE_30D: str = os.getenv("WHATSAPP_TEMPLATE_30D", "")
    WHATSAPP_TEMPLATE_7D: str = os.getenv("WHATSAPP_TEMPLATE_7D", "")
    WHATSAPP_TEMPLATE_LANGUAGE: str = os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "en")

    # Google Calendar
    GOOGLE_CALENDAR_ID: str = os.getenv("GOOGLE_CALENDAR_ID", "primary")
    GOOGLE_CREDENTIALS_FILE: str = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")

    # Knowledge-base retrieval (FR-9). While the active KB fits in
    # KB_FULL_CONTEXT_MAX_CHARS the whole thing goes into the prompt (nothing
    # to miss); beyond that only the KB_RETRIEVAL_TOP_K entries most relevant
    # to the client's message are sent - see kb_service.py.
    KB_FULL_CONTEXT_MAX_CHARS: int = int(os.getenv("KB_FULL_CONTEXT_MAX_CHARS", "6000"))
    KB_RETRIEVAL_TOP_K: int = int(os.getenv("KB_RETRIEVAL_TOP_K", "5"))

    # Consultation scheduling (FR-5). Slots are offered inside these Asia/Dubai
    # working hours on these weekdays (0=Mon .. 6=Sun; UAE weekend is Sat/Sun),
    # starting tomorrow, looking MEETING_SEARCH_DAYS ahead for a free slot.
    MEETING_START_HOUR: int = int(os.getenv("MEETING_START_HOUR", "10"))
    MEETING_END_HOUR: int = int(os.getenv("MEETING_END_HOUR", "17"))
    MEETING_DURATION_MINUTES: int = int(os.getenv("MEETING_DURATION_MINUTES", "30"))
    MEETING_WORKDAYS: str = os.getenv("MEETING_WORKDAYS", "0,1,2,3,4")
    MEETING_SEARCH_DAYS: int = int(os.getenv("MEETING_SEARCH_DAYS", "14"))

    # Admin API key - required on every /admin/* route and /test-scheduler.
    # Sent as `X-Admin-Key: <value>`. No default: an empty value means those
    # routes refuse every request until this is actually configured.
    ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "")

    # Durable job queue (see worker.py) - inbound WhatsApp messages are
    # enqueued here rather than processed inline, so a message survives an
    # app-process restart between being acked to Meta and actually being
    # handled (SRS NFR: "No inbound message lost: durable queue with retries").
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6379")


settings = Settings()
