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
import sys
import types

import psycopg
import pytest

WORK = pathlib.Path(__file__).resolve().parent.parent  # project root, tests/ is a subdir of it

# --------------------------------------------------------------------------
# Test-only secrets, set BEFORE `config`/`main` are ever imported anywhere
# --------------------------------------------------------------------------
TEST_APP_SECRET = "test-meta-app-secret"
TEST_EMAIL_SECRET = "test-email-webhook-secret"
TEST_ADMIN_KEY = "test-admin-key"
TEST_CALENDAR_ID = "staff-shared-calendar@group.calendar.google.com"

# Needs a real reachable PostgreSQL with schema.sql already applied - see
# tests/README.md. Defaults to the local dev Postgres this project's own
# scripts spin up; override with TEST_DATABASE_URL / TEST_SUPERUSER_URL for
# a different instance (CI, another dev machine, a different port).
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://app_user:change_me_in_production@127.0.0.1:5544/business_navigators_test",
)
TEST_SUPERUSER_URL = os.getenv(
    "TEST_SUPERUSER_URL", "postgresql://postgres@127.0.0.1:5544/business_navigators_test"
)

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("TENANT_ID", "default")

# Needs a real reachable S3-compatible endpoint - see tests/README.md.
# moto's server is the reference implementation used in development
# (genuine S3 API semantics, no real AWS account needed); MinIO works the
# same way if you'd rather run that instead.
TEST_S3_ENDPOINT_URL = os.getenv("TEST_S3_ENDPOINT_URL", "http://127.0.0.1:9000")
TEST_S3_BUCKET = os.getenv("TEST_S3_BUCKET", "test-bn-documents")
os.environ["S3_ENDPOINT_URL"] = TEST_S3_ENDPOINT_URL
os.environ["S3_BUCKET"] = TEST_S3_BUCKET
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("S3_REGION", "me-central-1")

# Needs a real reachable Redis - see tests/README.md. Messages are enqueued
# here for real (main.py's webhook handler enqueues a job exactly like it
# would in production); run_queued_jobs() below drains it synchronously so
# test assertions can run right after posting, without a separately
# running worker process during the test run.
TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://127.0.0.1:6390")
os.environ["REDIS_URL"] = TEST_REDIS_URL

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

_DEFAULT_SCORING_RULES = [
    "setup", "compliance", "growth", "tax", "license", "consultation",
    "booking", "visa", "cost", "appointment", "schedule", "meet",
    "expire", "expiry", "expiration", "document",
]
_DEFAULT_BOOKING_KEYWORDS = ["book", "booking", "appointment", "schedule", "meet", "meeting", "call"]


def _reset_test_database():
    """Truncates every business table and re-seeds the same defaults
    schema.sql seeds on a fresh deployment - connects as the superuser so
    RLS (which the app's own app_user role is deliberately subject to)
    doesn't get in the way of a full reset between tests."""
    try:
        with psycopg.connect(TEST_SUPERUSER_URL, autocommit=True) as conn:
            conn.execute(
                "TRUNCATE leads, knowledge_base, messages, scoring_rules, "
                "booking_keywords, app_settings, documents, document_access_log, audit_log "
                "RESTART IDENTITY CASCADE"
            )
            conn.execute(
                "INSERT INTO scoring_rules (tenant_id, keyword, weight) "
                "SELECT 'default', kw, 15 FROM unnest(%s::text[]) AS kw",
                (_DEFAULT_SCORING_RULES,),
            )
            conn.execute(
                "INSERT INTO booking_keywords (tenant_id, keyword) "
                "SELECT 'default', kw FROM unnest(%s::text[]) AS kw",
                (_DEFAULT_BOOKING_KEYWORDS,),
            )
            conn.execute(
                "INSERT INTO app_settings (tenant_id, key, value) VALUES "
                "('default','hot_threshold','70'), ('default','medium_threshold','30'), "
                "('default','base_engagement_boost','5')"
            )
    except psycopg.OperationalError as e:
        pytest.exit(
            f"\nCannot reach the test PostgreSQL database at {TEST_SUPERUSER_URL}.\n"
            f"See tests/README.md to start it. Original error: {e}",
            returncode=1,
        )


def _reset_test_bucket():
    """Empties the test S3 bucket between tests (creating it first time
    round). A separate boto3 client here, not document_store's cached one -
    this runs before the app's own client would otherwise lazily create it.

    Bucket creation delegates to document_store.ensure_bucket() rather than
    calling client.create_bucket() directly here: a bare create_bucket()
    needs an explicit CreateBucketConfiguration/LocationConstraint for any
    region other than us-east-1 (moto enforces this correctly, and
    ensure_bucket() already handles it) - a previous version of this
    function called create_bucket() directly and hung/errored on a fresh
    moto instance with no bucket yet, since that path was never actually
    exercised until the bucket didn't already exist from a prior run.
    """
    import boto3
    from botocore.exceptions import ClientError

    import document_store

    client = boto3.client(
        "s3",
        endpoint_url=TEST_S3_ENDPOINT_URL,
        region_name="me-central-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    try:
        client.head_bucket(Bucket=TEST_S3_BUCKET)
    except ClientError:
        try:
            document_store.ensure_bucket()
        except Exception as e:
            pytest.exit(
                f"\nCannot reach the test S3 endpoint at {TEST_S3_ENDPOINT_URL}.\n"
                f"See tests/README.md to start it. Original error: {e}",
                returncode=1,
            )
        return
    objects = client.list_objects_v2(Bucket=TEST_S3_BUCKET).get("Contents", [])
    if objects:
        client.delete_objects(
            Bucket=TEST_S3_BUCKET,
            Delete={"Objects": [{"Key": o["Key"]} for o in objects]},
        )


def _reset_test_redis():
    """Flushes the test Redis DB so no job left over from a prior test
    (e.g. one posted with run_jobs=False, or one that errored mid-test)
    leaks into the next."""
    import redis as redis_sync
    from urllib.parse import urlparse

    parsed = urlparse(TEST_REDIS_URL)
    try:
        r = redis_sync.Redis(host=parsed.hostname or "127.0.0.1", port=parsed.port or 6379)
        r.flushdb()
        r.close()
    except redis_sync.exceptions.ConnectionError as e:
        pytest.exit(
            f"\nCannot reach the test Redis at {TEST_REDIS_URL}.\n"
            f"See tests/README.md to start it. Original error: {e}",
            returncode=1,
        )


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
# Override for tests that need the stub to emit a specific reply (e.g. one
# carrying a <<<LEAD_DATA>>> block to exercise FR-3 extraction). None means
# "use AI_REPLY as-is".
AI_REPLY_OVERRIDE = {"value": None}


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
        reply = AI_REPLY_OVERRIDE["value"] if AI_REPLY_OVERRIDE["value"] is not None else AI_REPLY
        return _Completion(reply)


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

# Saved before patching, for the fallback below and for tests that need a
# genuine outbound call (e.g. fetching a real presigned S3 URL from the
# local moto server) rather than the Meta Graph/Brevo interception.
REAL_REQUESTS_GET = _requests.get
REAL_REQUESTS_POST = _requests.post


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
    # Anything else (e.g. a real presigned S3 URL against the local moto
    # server) is a genuine outbound call this stub doesn't know about -
    # pass it through instead of faking a 404, so tests that legitimately
    # need real network access (verifying a signed URL actually works)
    # aren't silently broken by this stub's global patch of requests.get.
    return REAL_REQUESTS_GET(url, **kw)


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
def app_env():
    """Fresh DB state per test (real PostgreSQL, truncated + reseeded -
    see _reset_test_database), recorder cleared, module-global state reset."""
    _reset_test_database()
    _reset_test_bucket()
    _reset_test_redis()

    import database

    database.init_db()  # idempotent - opens the pool once, no-ops after

    reset_recorder()
    OCR_TEXT["value"] = ""
    OCR_DELAY["seconds"] = 0.0
    CALENDAR_SHOULD_FAIL["value"] = False
    GROQ_SHOULD_FAIL["value"] = False
    AI_REPLY_OVERRIDE["value"] = None

    import main

    main._recent_hits.clear()  # in-process rate limiter is module-global state

    yield {}


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


def run_queued_jobs():
    """Drains the real Redis queue synchronously by running an arq worker
    in burst mode (process everything currently queued, then return) - the
    test equivalent of `arq worker.WorkerSettings` running continuously in
    production. Called automatically by post_whatsapp_webhook so ordinary
    tests can assert on side effects right after posting, same as before
    the queue existed.

    Retries a couple of times if a pass finds nothing: enqueue_job() is
    awaited to completion inside the FastAPI handler before client.post()
    returns, but that write happens on a different event loop/connection
    (TestClient's portal) than this burst worker's own fresh one
    (asyncio.run() creates a new loop each call) - on Windows' default
    Proactor loop, rapidly creating/tearing down loops and Redis
    connections like this occasionally shows the just-written key a beat
    late. A brief re-check is cheap and correct either way: a genuinely
    empty queue just no-ops again.
    """
    import asyncio
    import time as _time

    from arq.worker import Worker

    from worker import WorkerSettings

    async def _burst():
        # Deliberately NOT passing on_startup/on_shutdown: in production
        # the worker is its own OS process with its own DB pool, so
        # closing it on worker shutdown is correct there. Here the burst
        # worker runs IN-PROCESS with the test/app (for test convenience,
        # not mirroring the real deployment topology), sharing database.py's
        # module-level pool - calling on_shutdown's close_db() would kill
        # the same pool the test's own assertions need right after this
        # returns. The pool is already open via app_env's database.init_db().
        worker = Worker(
            functions=WorkerSettings.functions,
            redis_settings=WorkerSettings.redis_settings,
            burst=True,
            poll_delay=0.02,
        )
        await worker.async_run()
        completed = worker.jobs_complete
        await worker.close()
        return completed

    total = 0
    max_attempts = 6
    for attempt in range(max_attempts):
        total += asyncio.run(_burst())
        if total > 0 or attempt == max_attempts - 1:
            break
        _time.sleep(0.2)


def post_whatsapp_webhook(client, payload: dict, *, signed: bool = True, run_jobs: bool = True):
    """POSTs to /whatsapp/webhook with a valid Meta signature by default,
    then drains the queue so the message is actually processed before this
    returns - pass run_jobs=False for tests specifically checking the
    handler's own immediate response (e.g. that it enqueues without doing
    the work inline)."""
    raw, sig = sign_meta_payload(payload)
    headers = {"content-type": "application/json"}
    if signed:
        headers["x-hub-signature-256"] = sig
    response = client.post("/whatsapp/webhook", content=raw, headers=headers)
    if run_jobs and response.status_code == 200 and response.json().get("status") == "queued":
        run_queued_jobs()
    return response


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


def calendar_events_for(identifier: str) -> list[dict]:
    """Calendar events booked for a specific identifier. Every event's
    summary includes it (see main.py's process_message_intent) - filtering
    on it, rather than assuming OUT["calendar"][0] is exactly this test's
    one event, keeps these tests correct even if the queue's async drain
    timing ever interleaves with another test's leftover activity."""
    return [c for c in OUT["calendar"] if identifier in c["body"].get("summary", "")]


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
