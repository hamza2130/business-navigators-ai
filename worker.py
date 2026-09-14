"""arq worker process for the WhatsApp message pipeline.

Run with:  arq worker.WorkerSettings

Separate from main.py's ASGI process - the queue is what makes inbound
message processing durable (SRS NFR: "No inbound message lost: durable
queue with retries"). Before this, deferring work to a FastAPI
BackgroundTask still lost the message if the process crashed or restarted
between acking Meta and the task actually completing; a job sitting in
Redis survives that.

Importing main.py here only pulls in function definitions - it does NOT
start the FastAPI app, APScheduler, or the DB pool (those all happen
inside main.py's `lifespan`, which only runs under an actual ASGI
server). This process opens its own DB pool and S3 bucket check via
on_startup below.
"""
import os

from dotenv import load_dotenv

from queue_utils import redis_settings_from_url

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379")


async def process_whatsapp_message_job(ctx, phone_number: str, msg_type: str, message: dict, external_id: str | None):
    """Job body - delegates to the exact same pipeline main.py used to run
    inline via BackgroundTasks, so behaviour is unchanged, only WHERE and
    HOW durably it runs."""
    from main import _process_whatsapp_message

    _process_whatsapp_message(phone_number, msg_type, message, external_id)


async def on_startup(ctx):
    import database
    import document_store

    database.init_db()
    document_store.ensure_bucket()
    print("[WORKER] Started - DB pool open, S3 bucket ready.")


async def on_shutdown(ctx):
    import database

    database.close_db()
    print("[WORKER] Shut down.")


class WorkerSettings:
    functions = [process_whatsapp_message_job]
    redis_settings = redis_settings_from_url(REDIS_URL)
    on_startup = on_startup
    on_shutdown = on_shutdown
    # Meta's own retry behaviour already covers transient failures on the
    # webhook side; a modest in-queue retry count handles a job that fails
    # due to a transient DB/S3/LLM blip rather than a real bug.
    max_tries = 3
