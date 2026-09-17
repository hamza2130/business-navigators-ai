from datetime import datetime, timedelta

import database
from config import settings
from database import mark_reminder_sent
from whatsapp_service import send_whatsapp_message, send_whatsapp_template_message


def _send_reminder(phone: str, name: str, expiry_date: str, template_name: str, fallback_text: str) -> None:
    """Sends via the configured Message Template when available; otherwise
    falls back to plain text with a loud warning. The fallback is only
    reliable inside Meta's 24h session window and WILL be rejected for a
    client who hasn't messaged recently - it exists for local development,
    not as a production path."""
    if template_name:
        send_whatsapp_template_message(
            phone, template_name, parameters=[name, expiry_date]
        )
    else:
        print(
            "[SCHEDULER WARNING] No WhatsApp template configured for this reminder - "
            "sending plain text, which Meta will reject outside the 24h session window. "
            "Set WHATSAPP_TEMPLATE_30D / WHATSAPP_TEMPLATE_7D once a template is approved."
        )
        send_whatsapp_message(phone, fallback_text)


def check_expiring_documents():
    """Daily job to check document expirations and send WhatsApp reminders.

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
        # 1. 30-day reminders: expiry within 30 days, not yet sent for this
        #    expiry date.
        due_30d = conn.execute(
            """
            SELECT phone_number, name, document_expiry_date
            FROM leads
            WHERE document_expiry_date IS NOT NULL
              AND document_expiry_date <= %s
              AND reminder_30d_sent_at IS NULL
            """,
            (cutoff_30d,),
        ).fetchall()
        for lead in due_30d:
            phone = lead["phone_number"]
            name = lead["name"] or "Valued Client"
            expiry = lead["document_expiry_date"]
            fallback = (
                f"Hello {name},\n\n"
                f"This is a friendly compliance reminder from Business Navigators. "
                f"Your document registered with us is set to expire on *{expiry}*.\n\n"
                f"Please reach out to us or schedule a call to submit your renewed document: {settings.BOOKING_LINK}"
            )
            _send_reminder(phone, name, expiry, settings.WHATSAPP_TEMPLATE_30D, fallback)
            mark_reminder_sent(phone, "30d")
            print(f"[REMINDER SENT] 30-day expiry notification sent to {phone}")

        # 2. 7-day urgent reminders: same pattern, tighter window.
        due_7d = conn.execute(
            """
            SELECT phone_number, name, document_expiry_date
            FROM leads
            WHERE document_expiry_date IS NOT NULL
              AND document_expiry_date <= %s
              AND reminder_7d_sent_at IS NULL
            """,
            (cutoff_7d,),
        ).fetchall()
        for lead in due_7d:
            phone = lead["phone_number"]
            name = lead["name"] or "Valued Client"
            expiry = lead["document_expiry_date"]
            fallback = (
                f"⚠️ *URGENT COMPLIANCE NOTICE*\n\n"
                f"Hello {name},\n"
                f"Your registered document expires on *{expiry}*.\n\n"
                f"To avoid any service interruption or UAE compliance penalties, please reply to this message or book an urgent consultation: {settings.BOOKING_LINK}"
            )
            _send_reminder(phone, name, expiry, settings.WHATSAPP_TEMPLATE_7D, fallback)
            mark_reminder_sent(phone, "7d")
            print(f"[URGENT REMINDER SENT] 7-day expiry notification sent to {phone}")
