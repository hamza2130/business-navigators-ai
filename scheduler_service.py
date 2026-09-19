from datetime import datetime, timedelta

import database
from config import settings
from database import mark_reminder_sent
from email_service import send_email_to_lead
from whatsapp_service import send_whatsapp_message, send_whatsapp_template_message


def _split_channels(lead: dict) -> tuple[str | None, str | None]:
    """(whatsapp_number, email) this lead can be reached on.

    A lead's primary identifier is whichever channel they first came in on:
    a phone number for WhatsApp leads, an email address for email leads. An
    email-identified lead must NOT be sent through the WhatsApp API (it
    isn't a phone number), and a WhatsApp lead may also have shared an email
    that the AI captured (leads.email) - so reminders can go out on both.
    """
    identifier = lead["phone_number"]
    email = lead.get("email")
    if "@" in identifier:
        return None, email or identifier
    return identifier, email


def _send_whatsapp(phone: str, name: str, expiry_date: str, template_name: str, fallback_text: str):
    """Sends via the configured Message Template when available; otherwise
    falls back to plain text with a loud warning. The fallback is only
    reliable inside Meta's 24h session window and WILL be rejected for a
    client who hasn't messaged recently - it exists for local development,
    not as a production path."""
    if template_name:
        return send_whatsapp_template_message(phone, template_name, parameters=[name, expiry_date])
    print(
        "[SCHEDULER WARNING] No WhatsApp template configured for this reminder - "
        "sending plain text, which Meta will reject outside the 24h session window. "
        "Set WHATSAPP_TEMPLATE_30D / WHATSAPP_TEMPLATE_7D once a template is approved."
    )
    return send_whatsapp_message(phone, fallback_text)


def _deliver_reminder(
    lead: dict,
    template_name: str,
    whatsapp_text: str,
    email_subject: str,
    email_text: str,
) -> bool:
    """Sends the reminder on every channel we have for this lead (FR-10:
    WhatsApp AND email). True if at least one channel accepted it - only
    then is the reminder marked sent, so a total delivery failure is retried
    on the next run instead of being silently recorded as done."""
    phone, email = _split_channels(lead)
    name = lead["name"] or "Valued Client"
    expiry = lead["document_expiry_date"]
    delivered = False

    if phone:
        if _send_whatsapp(phone, name, expiry, template_name, whatsapp_text):
            delivered = True
    if email:
        if send_email_to_lead(to_email=email, subject=email_subject, content=email_text):
            delivered = True
    return delivered


def check_expiring_documents():
    """Daily job to check document expirations and send reminders over
    WhatsApp and email.

    Uses a "days remaining <= threshold, not yet sent" range check rather
    than exact-date equality, guarded by reminder_30d_sent_at/
    reminder_7d_sent_at: a missed day of scheduler downtime no longer skips
    a client's reminder forever, and the guard column stops the same
    reminder being re-sent on every later run. Re-uploading a document
    resets both guards (see save_or_update_lead), so a new expiry date is
    eligible for reminders again.
    """
    print("[SCHEDULER] Running daily document expiry compliance check...")

    today = datetime.now().date()
    cutoff_30d = (today + timedelta(days=30)).strftime("%Y-%m-%d")
    cutoff_7d = (today + timedelta(days=7)).strftime("%Y-%m-%d")

    with database.get_conn() as conn:
        due_30d = conn.execute(
            """
            SELECT phone_number, name, document_expiry_date, email
            FROM leads
            WHERE document_expiry_date IS NOT NULL
              AND document_expiry_date <= %s
              AND reminder_30d_sent_at IS NULL
            """,
            (cutoff_30d,),
        ).fetchall()
        due_7d = conn.execute(
            """
            SELECT phone_number, name, document_expiry_date, email
            FROM leads
            WHERE document_expiry_date IS NOT NULL
              AND document_expiry_date <= %s
              AND reminder_7d_sent_at IS NULL
            """,
            (cutoff_7d,),
        ).fetchall()

    for lead in due_30d:
        name = lead["name"] or "Valued Client"
        expiry = lead["document_expiry_date"]
        text = (
            f"Hello {name},\n\n"
            f"This is a friendly compliance reminder from Business Navigators. "
            f"Your document registered with us is set to expire on {expiry}.\n\n"
            f"Please reach out to us or schedule a call to submit your renewed document: {settings.BOOKING_LINK}"
        )
        if _deliver_reminder(
            lead,
            settings.WHATSAPP_TEMPLATE_30D,
            text.replace(expiry, f"*{expiry}*"),
            "Reminder: your document expires soon",
            text,
        ):
            mark_reminder_sent(lead["phone_number"], "30d")
            print(f"[REMINDER SENT] 30-day expiry notification sent to {lead['phone_number']}")
        else:
            print(f"[REMINDER FAILED] 30-day reminder to {lead['phone_number']} not delivered on any channel - will retry next run")

    for lead in due_7d:
        name = lead["name"] or "Valued Client"
        expiry = lead["document_expiry_date"]
        text = (
            f"URGENT COMPLIANCE NOTICE\n\n"
            f"Hello {name},\n"
            f"Your registered document expires on {expiry}.\n\n"
            f"To avoid any service interruption or UAE compliance penalties, please reply to this message or book an urgent consultation: {settings.BOOKING_LINK}"
        )
        if _deliver_reminder(
            lead,
            settings.WHATSAPP_TEMPLATE_7D,
            "⚠️ *URGENT COMPLIANCE NOTICE*" + text[len("URGENT COMPLIANCE NOTICE"):].replace(expiry, f"*{expiry}*"),
            "URGENT: your document expires within 7 days",
            text,
        ):
            mark_reminder_sent(lead["phone_number"], "7d")
            print(f"[URGENT REMINDER SENT] 7-day expiry notification sent to {lead['phone_number']}")
        else:
            print(f"[REMINDER FAILED] 7-day reminder to {lead['phone_number']} not delivered on any channel - will retry next run")
