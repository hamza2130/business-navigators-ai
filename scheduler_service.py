from datetime import datetime, timedelta
import sqlite3
from config import settings
from database import DB_NAME
from whatsapp_service import send_whatsapp_message


def check_expiring_documents():
    """Daily job to check document expirations and send WhatsApp reminders."""
    print("[SCHEDULER] Running daily document expiry compliance check...")

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    today = datetime.now().date()
    target_30d = (today + timedelta(days=30)).strftime("%Y-%m-%d")
    target_7d = (today + timedelta(days=7)).strftime("%Y-%m-%d")

    # 1. Check for 30-Day Expiry Reminders
    cursor.execute(
        """
        SELECT phone_number, name, document_expiry_date 
        FROM leads 
        WHERE document_expiry_date = ? 
        """,
        (target_30d,),
    )
    leads_30d = cursor.fetchall()
    for lead in leads_30d:
        phone = lead["phone_number"]
        name = lead["name"] or "Valued Client"
        msg = (
            f"Hello {name},\n\n"
            f"This is a friendly compliance reminder from Business Navigators. "
            f"Your document registered with us is set to expire on *{lead['document_expiry_date']}* (in 30 days).\n\n"
            f"Please reach out to us or schedule a call to submit your renewed document: {settings.BOOKING_LINK}"
        )
        send_whatsapp_message(phone, msg)
        print(f"[REMINDER SENT] 30-day expiry notification sent to {phone}")

    # 2. Check for 7-Day Urgent Expiry Reminders
    cursor.execute(
        """
        SELECT phone_number, name, document_expiry_date 
        FROM leads 
        WHERE document_expiry_date = ?
        """,
        (target_7d,),
    )
    leads_7d = cursor.fetchall()
    for lead in leads_7d:
        phone = lead["phone_number"]
        name = lead["name"] or "Valued Client"
        msg = (
            f"⚠️ *URGENT COMPLIANCE NOTICE*\n\n"
            f"Hello {name},\n"
            f"Your registered document expires in *7 days* ({lead['document_expiry_date']}).\n\n"
            f"To avoid any service interruption or UAE compliance penalties, please reply to this message or book an urgent consultation: {settings.BOOKING_LINK}"
        )
        send_whatsapp_message(phone, msg)
        print(f"[URGENT REMINDER SENT] 7-day expiry notification sent to {phone}")

    conn.close()