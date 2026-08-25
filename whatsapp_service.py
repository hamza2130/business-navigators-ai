import requests
from config import settings

GRAPH_URL = f"https://graph.facebook.com/{settings.META_API_VERSION}/{settings.META_PHONE_NUMBER_ID}/messages"


def send_whatsapp_message(to_number: str, message_body: str):
    """Sends a WhatsApp text message via the Meta Cloud API.

    to_number must be the raw E.164 number Meta gave you in the inbound
    webhook (e.g. '9715XXXXXXXX'), no 'whatsapp:' prefix.
    """
    headers = {
        "Authorization": f"Bearer {settings.META_WA_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": message_body},
    }

    try:
        response = requests.post(GRAPH_URL, headers=headers, json=payload, timeout=15)
        if response.status_code != 200:
            print(f"Failed to send WhatsApp message: {response.status_code} {response.text}")
            return None

        message_id = response.json().get("messages", [{}])[0].get("id")
        print(f"WhatsApp message sent successfully! Message ID: {message_id}")
        return message_id
    except Exception as e:
        print(f"Failed to send WhatsApp message: {e}")
        return None