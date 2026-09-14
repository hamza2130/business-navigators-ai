"""Test harness for business-navigators-ai.

Stubs every external service (Groq, Meta Graph, Brevo, Google Calendar,
Tesseract) so the REAL application logic runs with zero network calls and
zero API cost. No production-code changes are needed to import the app -
the app imports cleanly on its own now.
"""
import hashlib
import hmac
import json
import os
import pathlib
import sqlite3
import sys
import types

import pytest

WORK = pathlib.Path(__file__).resolve().parent.parent  # project root, tests/ is a subdir of it

# --------------------------------------------------------------------------
# Test-only secrets, set BEFORE `config`/`main` are ever imported anywhere
# --------------------------------------------------------------------------
TEST_APP_SECRET = "test-meta-app-secret"
TEST_EMAIL_SECRET = "test-email-webhook-secret"
TEST_ADMIN_KEY = "test-admin-key"
TEST_CALENDAR_ID = "staff-shared-calendar@group.calendar.google.com"

os.environ.setdefault("GROQ_API_KEY", "test-key")
os.environ.setdefault("WHATSAPP_TOKEN", "test-wa-token")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "123456")
os.environ.setdefault("META_VERIFY_TOKEN", "real_configured_token")
os.environ.setdefault("META_APP_SECRET", TEST_APP_SECRET)
os.environ.setdefault("BREVO_API_KEY", "test-brevo")
os.environ.setdefault("SENDER_EMAIL", "staff@businessnavigators.ae")
os.environ.setdefault("EMAIL_WEBHOOK_SECRET", TEST_EMAIL_SECRET)
os.environ.setdefault("ADMIN_API_KEY", TEST_ADMIN_KEY)
os.environ.setdefault("GOOGLE_CALENDAR_ID", TEST_CALENDAR_ID)

# --------------------------------------------------------------------------
# Recorder: captures everything the app tries to send to the outside world
# --------------------------------------------------------------------------
OUT = {"whatsapp": [], "brevo": [], "groq": [], "calendar": [], "media_get": []}


def reset_recorder():
    for v in OUT.values():
        v.clear()


# --------------------------------------------------------------------------
# Stub: groq
# --------------------------------------------------------------------------
AI_REPLY = "Thank you for contacting Business Navigators. How may I assist?"
GROQ_SHOULD_FAIL = {"value": False}


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Completion:
    def __init__(self, content):
        self.choices = [_Choice(content)]


class _Completions:
    def create(self, **kwargs):
        if GROQ_SHOULD_FAIL["value"]:
            raise RuntimeError("simulated Groq outage")
        OUT["groq"].append(kwargs)
        return _Completion(AI_REPLY)


class _Chat:
    def __init__(self):
        self.completions = _Completions()


class Groq:
    def __init__(self, api_key=None, **kw):
        self.api_key = api_key
        self.chat = _Chat()


_groq = types.ModuleType("groq")
_groq.Groq = Groq
sys.modules["groq"] = _groq

# --------------------------------------------------------------------------
# Stub: google.oauth2.service_account + googleapiclient.discovery
# --------------------------------------------------------------------------
CALENDAR_SHOULD_FAIL = {"value": False}


class _Credentials:
    @staticmethod
    def from_service_account_file(path, scopes=None):
        if CALENDAR_SHOULD_FAIL["value"]:
            raise FileNotFoundError(path)
        return object()


class _Events:
    def insert(self, calendarId=None, body=None, sendUpdates=None):
        OUT["calendar"].append(
            {"calendarId": calendarId, "body": body, "sendUpdates": sendUpdates}
        )

        class _Exec:
            def execute(_self):
                return {"htmlLink": "https://calendar.google.com/event?eid=FAKE123"}

        return _Exec()


class _Service:
    def events(self):
        return _Events()


def _build(serviceName, version, credentials=None):
    return _Service()


for name, mod in [
    ("google", types.ModuleType("google")),
    ("google.oauth2", types.ModuleType("google.oauth2")),
    ("google.oauth2.service_account", types.ModuleType("google.oauth2.service_account")),
    ("googleapiclient", types.ModuleType("googleapiclient")),
    ("googleapiclient.discovery", types.ModuleType("googleapiclient.discovery")),
]:
    mod.__path__ = []
    sys.modules[name] = mod

sys.modules["google.oauth2.service_account"].Credentials = _Credentials
sys.modules["googleapiclient.discovery"].build = _build

# --------------------------------------------------------------------------
# Stub: requests (Meta Graph send/download + Brevo email)
# --------------------------------------------------------------------------
import requests as _requests  # noqa: E402

MEDIA_BYTES = {"content": b"\x89PNG fake", "mime": "image/png"}
WHATSAPP_STATUS = {"code": 200}


class _Resp:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content
        self.text = str(payload)

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=65536):
        data = self.content
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]


def _fake_post(url, **kw):
    if "graph.facebook.com" in url:
        OUT["whatsapp"].append({"url": url, "json": kw.get("json"), "headers": kw.get("headers")})
        return _Resp(WHATSAPP_STATUS["code"], {"messages": [{"id": "wamid.FAKE"}]})
    if "brevo.com" in url:
        OUT["brevo"].append({"url": url, "json": kw.get("json"), "headers": kw.get("headers")})
        return _Resp(201, {"messageId": "brevo-fake"})
    raise AssertionError(f"Unexpected outbound POST to {url}")


def _fake_get(url, **kw):
    OUT["media_get"].append({"url": url, "headers": kw.get("headers")})
    if "graph.facebook.com" in url and "/messages" not in url:
        return _Resp(
            200,
            {
                "url": "https://lookaside.fbsbx.com/signed/FAKE",
                "mime_type": MEDIA_BYTES["mime"],
                "file_size": len(MEDIA_BYTES["content"]),
            },
        )
    if "lookaside" in url:
        return _Resp(200, {}, content=MEDIA_BYTES["content"])
    return _Resp(404, {})


_requests.post = _fake_post
_requests.get = _fake_get

# --------------------------------------------------------------------------
# Stub: pytesseract (no Tesseract binary needed)
# --------------------------------------------------------------------------
import pytesseract as _pt  # noqa: E402

OCR_TEXT = {"value": ""}
OCR_DELAY = {"seconds": 0.0}


def _fake_image_to_string(img, **kw):
    if OCR_DELAY["seconds"]:
        import time

        time.sleep(OCR_DELAY["seconds"])
    return OCR_TEXT["value"]


_pt.image_to_string = _fake_image_to_string


class _FakeImage:
    @staticmethod
    def open(buf):
        return object()


import PIL.Image  # noqa: E402

PIL.Image.open = _FakeImage.open

# --------------------------------------------------------------------------
# App import (must happen AFTER stubs + env vars are set)
# --------------------------------------------------------------------------
os.chdir(WORK)
sys.path.insert(0, str(WORK))


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """Fresh DB per test, recorder cleared, module-global state reset."""
    import database

    db_file = tmp_path / "leads.db"
    monkeypatch.setattr(database, "DB_NAME", str(db_file))
    database.init_db()

    reset_recorder()
    OCR_TEXT["value"] = ""
    OCR_DELAY["seconds"] = 0.0
    CALENDAR_SHOULD_FAIL["value"] = False
    GROQ_SHOULD_FAIL["value"] = False

    import main

    main._recent_hits.clear()  # in-process rate limiter is module-global state

    yield {"db": str(db_file)}


@pytest.fixture()
def client(app_env):
    from fastapi.testclient import TestClient
    import main

    with TestClient(main.app) as c:
        yield c


# --------------------------------------------------------------------------
# Signed-request helpers
# --------------------------------------------------------------------------
def sign_meta_payload(payload: dict) -> tuple[bytes, str]:
    """Raw JSON bytes + the X-Hub-Signature-256 header value Meta would send
    for that exact body, computed with the test META_APP_SECRET."""
    raw = json.dumps(payload).encode("utf-8")
    sig = hmac.new(TEST_APP_SECRET.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return raw, f"sha256={sig}"


def post_whatsapp_webhook(client, payload: dict, *, signed: bool = True):
    """POSTs to /whatsapp/webhook with a valid Meta signature by default."""
    raw, sig = sign_meta_payload(payload)
    headers = {"content-type": "application/json"}
    if signed:
        headers["x-hub-signature-256"] = sig
    return client.post("/whatsapp/webhook", content=raw, headers=headers)


def post_email_webhook(client, payload: dict, *, signed: bool = True):
    headers = {}
    if signed:
        headers["x-email-webhook-secret"] = TEST_EMAIL_SECRET
    return client.post("/webhook/email", json=payload, headers=headers)


def admin_headers():
    return {"X-Admin-Key": TEST_ADMIN_KEY}


def wa_texts():
    """All WhatsApp free-form text message bodies the app tried to send."""
    return [
        w["json"]["text"]["body"]
        for w in OUT["whatsapp"]
        if w.get("json", {}).get("type") == "text"
    ]


def wa_payloads():
    return [w["json"] for w in OUT["whatsapp"]]


def groq_messages():
    """The message arrays passed to the LLM."""
    return [c["messages"] for c in OUT["groq"]]


def meta_text_payload(from_number, body, msg_id="wamid.MSG1"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "123456"},
                            "contacts": [{"profile": {"name": "Test"}, "wa_id": from_number}],
                            "messages": [
                                {
                                    "from": from_number,
                                    "id": msg_id,
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": body},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


def meta_media_payload(from_number, media_type="image", mime="image/jpeg", msg_id="wamid.MEDIA1"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "123456"},
                            "messages": [
                                {
                                    "from": from_number,
                                    "id": msg_id,
                                    "timestamp": "1700000000",
                                    "type": media_type,
                                    media_type: {"id": "MEDIA_ID_1", "mime_type": mime},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
