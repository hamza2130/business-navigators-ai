# Test suite — business-navigators-ai

77 specification tests plus 9 multi-tenancy/RLS proofs. Every external
third-party service (Groq, Meta Graph, Brevo, Google Calendar, Tesseract)
is stubbed - no API keys or network calls needed for those. **Postgres,
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
