# email_service.py
import html

import requests
from config import settings


def send_email_to_lead(to_email: str, subject: str, content: str) -> bool:
    """Sends an automated email response to a lead using Brevo API."""
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": settings.BREVO_API_KEY,
        "content-type": "application/json",
    }
    safe_body = html.escape(content).replace("\n", "<br>")
    payload = {
        "sender": {"name": "Business Navigators AI", "email": settings.SENDER_EMAIL},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": "<p>" + safe_body + "</p>",
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=15)
        return response.status_code in (200, 201)
    except Exception as e:
        print(f"Error sending email: {e}")
        return False


def send_lead_notification(phone_number: str, lead_details: str):
    """Sends an internal email notification to staff when high-intent leads arrive."""
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": settings.BREVO_API_KEY,
        "content-type": "application/json",
    }
    safe_phone = html.escape(phone_number)
    safe_details = html.escape(lead_details)
    payload = {
        "sender": {"name": "Business Navigators Bot", "email": settings.SENDER_EMAIL},
        "to": [{"email": settings.SENDER_EMAIL}],
        "subject": f"High-Intent Lead Alert: {phone_number}",
        "htmlContent": (
            "<h3>New High-Intent Lead Action</h3>"
            f"<p><b>Phone:</b> {safe_phone}</p>"
            f"<p><b>Details:</b> {safe_details}</p>"
        ),
    }
    try:
        requests.post(url, json=payload, headers=headers, timeout=15)
    except Exception as e:
        print(f"Failed to send staff alert: {e}")
