# Test suite — business-navigators-ai

Specification tests (`test_spec.py`, including the multi-tenancy/RLS proofs)
plus one module per later feature: `test_escalation_audit.py` (escalation,
audit log, dashboard data), `test_reminders_email.py` (email reminders and
email capture) and `test_meetings.py` (availability-checked booking,
reschedule, cancel). Every external third-party service (Groq, Meta Graph,
Brevo, Google Calendar, Tesseract) is stubbed - no API keys or network calls
needed for those. The Google Calendar stub is a small in-memory calendar (see
`CAL` in `conftest.py`): events the app inserts show up as busy in later
free/busy queries, so double-booking is actually observable. **Postgres,
Redis, and an S3-compatible endpoint are real dependencies**, not stubbed,
because the whole point of Phase 2 was proving multi-tenancy (RLS),
document storage, and the job queue actually work, not just that the code
compiles against a mock.

## Running it

You need three things reachable before running `pytest`:

### 1. PostgreSQL with schema.sql applied

```bash
createdb business_navigators_test
psql -d business_navigators_test -f ../schema.sql
```

Point the suite at it via `TEST_DATABASE_URL` / `TEST_SUPERUSER_URL` (see
`conftest.py` for defaults - they assume a local instance on port 5544;
override if yours is elsewhere, e.g. the standard 5432).

If you run these services from a scratch/temp directory, keep the data
somewhere that isn't age-pruned by your OS or tooling - a Postgres data
directory that gets partly deleted mid-session fails in confusing ways
(this repo's `.gitignore` reserves `.devservices/` for exactly that).

### 2. An S3-compatible endpoint

Either MinIO or `moto`'s server work identically (both implement the real
S3 API):

```bash
pip install "moto[server]"
python -m moto.server -p 9000
```

Override with `TEST_S3_ENDPOINT_URL` / `TEST_S3_BUCKET` if needed.

### 3. Redis

```bash
redis-server --port 6390
```

Override with `TEST_REDIS_URL` if needed.

### Then

```bash
pip install -r ../requirements.txt pytest httpx
pytest tests/ -v
```

## A known flakiness, documented rather than hidden

On Windows, `run_queued_jobs()` (in `conftest.py`) occasionally needs more
than one retry pass to see a job that was just enqueued - roughly 1 run in
5-7 shows one test failing on the first attempt, always a different test,
always because the queue looked empty a moment too soon. It's mitigated
with a short retry loop, which resolves it the large majority of the time
but not 100%.

This is a **test-harness artifact, not a production bug**: it comes from
repeatedly creating and tearing down asyncio event loops and Redis
connections in a tight loop within one Windows process (`run_queued_jobs`
calls `asyncio.run()` per test, on top of TestClient's own separate event
loop) - something the real deployment never does, since `worker.py` runs
as its own long-lived OS process with one persistent connection. The real,
separate-process path was verified manually end-to-end (a live `uvicorn`
server + a live `arq worker.WorkerSettings` process, both against real
Postgres/Redis/S3) and processed messages correctly and deterministically.

If you hit this, just re-run. If it's affecting CI, running the suite
under WSL/Linux instead of native Windows or increasing the retry budget
in `run_queued_jobs()` are both reasonable next steps - this wasn't
chased further because the actual production code path is unaffected.

## Multi-tenancy / RLS

`TestMultiTenancy` in `test_spec.py` is worth reading on its own - it
proves Row-Level Security actually isolates two tenants' data from each
other, and that a connection with no tenant context set sees nothing
(fails closed, not open), by exercising `database.py`'s real connection
pool against the real test database. This was also verified manually with
raw `psql` as the `app_user` role during development.
