import requests
from config import settings

GRAPH_URL = f"https://graph.facebook.com/{settings.META_API_VERSION}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"


def _post(payload: dict):
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
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


def send_whatsapp_message(to_number: str, message_body: str):
    """Sends a free-form WhatsApp text message via the Meta Cloud API.

    Only deliverable inside Meta's 24h customer service window (i.e. the
    client messaged in the last 24h) - Meta rejects free-form text outside
    that window. For anything proactive/scheduled (reminders), use
    send_whatsapp_template_message() instead.

    to_number must be the raw E.164 number Meta gave you in the inbound
    webhook (e.g. '9715XXXXXXXX'), no 'whatsapp:' prefix.
    """
    return _post(
        {
            "messaging_product": "whatsapp",
            "to": to_number,
            "type": "text",
            "text": {"body": message_body},
        }
    )


def send_whatsapp_template_message(
    to_number: str,
    template_name: str,
    parameters: list[str] = None,
    language_code: str = None,
):
    """Sends a pre-approved WhatsApp Message Template - the only way to
    reach a client OUTSIDE the 24h session window (e.g. a scheduled
    document-expiry reminder). template_name must already be created and
    approved in Meta Business Manager; `parameters` fills the template's
    numbered {{1}}, {{2}}, ... placeholders in order, as body-component text.
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code or settings.WHATSAPP_TEMPLATE_LANGUAGE},
        },
    }
    if parameters:
        payload["template"]["components"] = [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in parameters],
            }
        ]
    return _post(payload)
