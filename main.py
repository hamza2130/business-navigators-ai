import datetime
from contextlib import asynccontextmanager
import os
import shutil

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ai_service import generate_ai_response
from calendar_service import create_calendar_event
from config import settings
from database import (
    get_db_connection,
    get_lead,
    init_db,
    save_or_update_lead,
    set_human_takeover,
    update_lead_state_and_score,
)
from document_parser import extract_text_from_attachment
from email_service import send_email_to_lead, send_lead_notification
from ocr_service import parse_expiry_date
from scheduler_service import check_expiring_documents
from whatsapp_service import send_whatsapp_message

# Initialize Background Scheduler
scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize DB schema & Start Scheduler
    init_db()

    # Schedule daily document compliance check at 09:00 AM
    scheduler.add_job(check_expiring_documents, "cron", hour=9, minute=0)

    scheduler.start()
    print(
        "[SERVER] APScheduler initialized for document compliance & expiry tracking."
    )

    yield

    # Shutdown
    scheduler.shutdown()
    print("[SERVER] APScheduler shut down.")


app = FastAPI(title="Business Navigators AI Engine", lifespan=lifespan)


# ==========================================
# KNOWLEDGE BASE PYDANTIC MODEL
# ==========================================
class KBItem(BaseModel):
    category: str
    topic: str
    content: str
    is_active: bool = True


@app.get("/")
def read_root():
    return {
        "status": "Active",
        "system": "Business Navigators Milestone 3 Backend Engine",
    }


@app.get("/test-scheduler")
def trigger_scheduler_test():
    """Manual override endpoint to test document compliance alerts immediately."""
    print("[TEST] Manually triggering check_expiring_documents()...")
    check_expiring_documents()
    return {"status": "Triggered compliance check manually."}


# ==========================================
# ADMIN HANDOFF CONTROL ROUTE
# ==========================================
@app.post("/admin/takeover")
def toggle_takeover(phone_number: str = Form(...), enable: bool = Form(...)):
    """Allows a human agent to pause or resume automated AI replies for a specific lead."""
    set_human_takeover(phone_number, enable)
    status_str = "ENABLED (AI Paused)" if enable else "DISABLED (AI Active)"
    return {
        "status": "success",
        "message": f"Human takeover for {phone_number} is now {status_str}.",
    }


# ==========================================
# ADMIN DOCUMENT DIRECT UPLOAD ROUTE (FOR TESTING)
# ==========================================
@app.post("/admin/test-document-upload")
async def test_document_upload(
    phone_number: str = Form("+923159244559"),
    file: UploadFile = File(...)
):
    """
    Directly upload a document image/PDF to test text extraction,
    expiry date parsing, and SQLite database saving without WhatsApp.
    """
    temp_file_path = f"temp_{file.filename}"
    with open(temp_file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    try:
        # Extract text directly using file parser
        extracted_text = extract_text_from_attachment(file_path=temp_file_path)
        
        # Fallback handling if direct file parsing fails
        if not extracted_text:
            return {"status": "failed", "reason": "Text extraction failed on uploaded document."}
            
        expiry_date = parse_expiry_date(extracted_text)
        
        # Save record directly into database
        save_or_update_lead(
            phone_number=phone_number,
            state="DOCUMENT_SUBMITTED",
            score=50,
            document_expiry_date=expiry_date,
            document_status="VERIFIED" if expiry_date else "FAILED"
        )
        
        updated_lead = get_lead(phone_number)
        
        return {
            "status": "success",
            "message": "Document processed and recorded in database.",
            "extracted_text_snippet": extracted_text[:200],
            "parsed_expiry_date": expiry_date,
            "database_record": updated_lead
        }
    finally:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)


# ==========================================
# ADMIN KNOWLEDGE BASE MANAGEMENT ROUTES
# ==========================================
@app.get("/admin/knowledge-base")
def get_all_kb_items():
    """Retrieve all knowledge base items for staff review."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, category, topic, content, is_active, updated_at FROM knowledge_base"
        )
        items = [
            {
                "id": row["id"],
                "category": row["category"],
                "topic": row["topic"],
                "content": row["content"],
                "is_active": bool(row["is_active"]),
                "updated_at": row["updated_at"],
            }
            for row in cursor.fetchall()
        ]
    return {"status": "success", "data": items}


@app.post("/admin/knowledge-base")
def add_kb_item(item: KBItem):
    """Staff endpoint to add a new knowledge base entry."""
    with get_db_connection() as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO knowledge_base (category, topic, content, is_active) VALUES (?, ?, ?, ?)",
                (item.category, item.topic, item.content, 1 if item.is_active else 0),
            )
            item_id = cursor.lastrowid
    return {"status": "success", "message": "Knowledge base item added", "id": item_id}


@app.delete("/admin/knowledge-base/{item_id}")
def delete_kb_item(item_id: int):
    """Staff endpoint to remove or deactivate a knowledge base entry."""
    with get_db_connection() as conn:
        with conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM knowledge_base WHERE id = ?", (item_id,))
    return {"status": "success", "message": f"Item {item_id} deleted"}


def process_message_intent(
    identifier: str,
    user_query: str,
    current_state: str,
    current_score: int,
    lead_data: dict = None,
):
    score_boost = 5
    intent_keywords = [
        "setup",
        "compliance",
        "growth",
        "tax",
        "license",
        "consultation",
        "booking",
        "visa",
        "cost",
        "appointment",
        "schedule",
        "meet",
        "expire",
        "expiry",
        "expiration",
        "document",
    ]
    booking_keywords = [
        "book",
        "booking",
        "appointment",
        "schedule",
        "meet",
        "meeting",
        "call",
    ]

    has_high_intent = any(kw in user_query.lower() for kw in intent_keywords)
    wants_booking = any(kw in user_query.lower() for kw in booking_keywords)

    if has_high_intent:
        score_boost += 15
        new_state = (
            "QUALIFIED" if (current_score + score_boost) >= 50 else "ENGAGED"
        )
    else:
        new_state = "ENGAGED" if current_state == "NEW" else current_state

    # Handle Automated Calendar Booking
    booking_instruction = ""
    if wants_booking:
        new_state = "MEETING_REQUESTED"

        # Calculate dynamic appointment slot (Default: Tomorrow at 10:00 AM GST)
        start_dt = datetime.datetime.now(
            datetime.timezone.utc
        ) + datetime.timedelta(days=1)
        start_dt = start_dt.replace(hour=10, minute=0, second=0, microsecond=0)
        end_dt = start_dt + datetime.timedelta(minutes=30)

        start_iso = start_dt.isoformat()
        end_iso = end_dt.isoformat()

        # Create Google Calendar Event
        event_link = create_calendar_event(
            summary=f"Business Navigators Consultation with {identifier}",
            description=f"Consultation scheduled via WhatsApp/Email Engine for lead {identifier}.",
            start_time_iso=start_iso,
            end_time_iso=end_iso,
        )

        if event_link:
            booking_instruction = (
                f" The consultation meeting has been scheduled! Provide the client with their official meeting details and event link: {event_link}"
            )
        else:
            booking_instruction = (
                f" The client wants to book a meeting. Provide them with our fallback booking link: {settings.BOOKING_LINK}"
            )

    # Extract stored document context if available
    doc_context = ""
    if lead_data and lead_data.get("document_expiry_date"):
        doc_context = (
            f"\nClient Stored Record: Document Expiry Date = {lead_data['document_expiry_date']}. "
            f"If the user asks about document expiration, inform them that our records show their document expires on {lead_data['document_expiry_date']} "
            f"and our automated compliance system will send WhatsApp alerts 30 days and 7 days prior to expiry."
        )

    prompt = (
        f"Lead Context: Identifier = {identifier}, State = '{new_state}', Score = {current_score + score_boost}.{doc_context}\n"
        f"User message: '{user_query}'\n\n"
        f"Respond professionally guiding them on UAE corporate services.{booking_instruction}"
    )

    ai_reply = generate_ai_response(prompt)
    return ai_reply, new_state, score_boost


# ==========================================
# META WHATSAPP INBOUND WEBHOOK
# ==========================================

# 1. GET request for Meta's Webhook Verification
@app.get("/whatsapp/webhook")
async def verify_whatsapp_webhook(request: Request):
    """Handles the Meta Webhook Verification handshake."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    # Fetch configured tokens or fall back directly to 'my_secret_token'
    whatsapp_token = getattr(settings, "WHATSAPP_VERIFY_TOKEN", None)
    meta_token = getattr(settings, "META_VERIFY_TOKEN", None)

    valid_tokens = [t for t in [whatsapp_token, meta_token, "my_secret_token"] if t]

    if mode == "subscribe" and token in valid_tokens:
        print("[WEBHOOK VERIFIED] Handshake successful with Meta!")
        return Response(content=challenge, media_type="text/plain", status_code=200)

    print(f"[WEBHOOK FAILED] Token received: '{token}' | Valid tokens: {valid_tokens}")
    return Response(content="Verification failed", status_code=403)


# 2. POST request for Meta's Incoming Messages
@app.post("/whatsapp/webhook")
async def whatsapp_webhook(request: Request):
    """Handles incoming real-time messages from Meta."""
    try:
        data = await request.json()
    except Exception:
        return {"status": "error", "reason": "Invalid JSON body"}

    try:
        # Meta sends a nested JSON object; extract message details
        entry = data.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        if not messages:
            return {"status": "ignored", "reason": "Status update, not a message"}

        message = messages[0]
        phone_number = message.get("from")
        msg_type = message.get("type")

        user_query = ""
        media_id = None
        media_content_type = None

        if msg_type == "text":
            user_query = message.get("text", {}).get("body", "").strip()
        elif msg_type in ["image", "document"]:
            media_id = message.get(msg_type, {}).get("id")
            media_content_type = message.get(msg_type, {}).get("mime_type")

    except IndexError:
        return {"status": "ignored", "reason": "Unrecognized payload"}

    lead = get_lead(phone_number)

    # Check if human agent takeover is active
    if lead and lead.get("human_takeover") == 1:
        print(
            f"[HANDOFF] Suppressing AI response for {phone_number} (Human Takeover Active)."
        )
        return {"status": "paused", "reason": "Human agent takeover active"}

    current_state = lead["state"] if lead else "NEW"
    current_score = lead["score"] if lead else 0

    if media_id:
        extracted_text = extract_text_from_attachment(
            media_id=media_id, media_content_type=media_content_type
        )

        if extracted_text:
            new_state = "DOCUMENT_SUBMITTED"

            # Parse expiry date from extracted document text
            expiry_date = parse_expiry_date(extracted_text)

            # Persist lead state, score (+30 boost), and expiry date in DB
            save_or_update_lead(
                phone_number=phone_number,
                state=new_state,
                score=current_score + 30,
                document_expiry_date=expiry_date,
                document_status="VERIFIED",
            )

            prompt = (
                f"The user uploaded a document file/image. Extracted details:\n\n"
                f"'''\n{extracted_text}\n'''\n\n"
                f"Lead Context: State = '{new_state}'. Extracted Expiry Date = {expiry_date}.\n"
                f"Extract key information (Name, ID/Passport Number, Expiry Date), "
                f"confirm that it has been saved in our system for automated compliance monitoring, "
                f"and ask how Business Navigators can assist them further."
            )
            ai_reply = generate_ai_response(prompt)
            notification_payload = f"[Document Recd | Expiry: {expiry_date}] Snippet: {extracted_text[:100]}..."
        else:
            new_state = current_state
            ai_reply = "I received your file, but was unable to extract legible text from it. Please upload a valid document."
            notification_payload = "[Document Recd] Text extraction failed."
    else:
        # Pass lead dictionary for full context (including expiry dates)
        ai_reply, new_state, score_boost = process_message_intent(
            phone_number, user_query, current_state, current_score, lead_data=lead
        )
        update_lead_state_and_score(
            phone_number=phone_number, state=new_state, score_delta=score_boost
        )
        notification_payload = user_query

    # Send out the message response via WhatsApp service
    send_whatsapp_message(phone_number, ai_reply)

    triggers = [
        "setup",
        "compliance",
        "growth",
        "tax",
        "license",
        "consultation",
        "booking",
        "passport",
        "emirates id",
        "meet",
        "expire",
        "expiry",
    ]
    if any(t in user_query.lower() for t in triggers) or media_id:
        send_lead_notification(
            phone_number=phone_number,
            lead_details=f"State: {new_state} | Payload: {notification_payload}",
        )

    return {"status": "processed", "lead_state": new_state}


# ==========================================
# EMAIL INBOUND WEBHOOK (BREVO)
# ==========================================
@app.post("/webhook/email")
async def email_webhook(request: Request):
    """Handles incoming emails forwarded by Brevo Webhook."""
    try:
        data = await request.json()
    except Exception:
        return {"status": "error", "reason": "Invalid JSON body"}

    # Safe multi-tier lookup for Brevo inbound payload structures
    items = data.get("items", [{}])
    first_item = items[0] if items else {}

    sender_email = (
        first_item.get("From", {}).get("Address")
        or data.get("sender", {}).get("email")
        or data.get("from")
    )
    subject = (
        first_item.get("Subject") or data.get("subject") or "Inquiry"
    )
    body_text = (
        first_item.get("RawTextBody")
        or data.get("extracted_text")
        or data.get("text")
        or ""
    ).strip()

    if not sender_email or not body_text:
        return {"status": "ignored", "reason": "Missing email sender or body text"}

    lead = get_lead(sender_email)

    # Check if human agent takeover is active
    if lead and lead.get("human_takeover") == 1:
        print(
            f"[HANDOFF] Suppressing email reply for {sender_email} (Human Takeover Active)."
        )
        return {"status": "paused", "reason": "Human agent takeover active"}

    current_state = lead["state"] if lead else "NEW"
    current_score = lead["score"] if lead else 0

    ai_reply, new_state, score_boost = process_message_intent(
        sender_email, body_text, current_state, current_score, lead_data=lead
    )
    update_lead_state_and_score(
        phone_number=sender_email, state=new_state, score_delta=score_boost
    )

    send_email_to_lead(
        to_email=sender_email,
        subject=f"Re: {subject}",
        content=ai_reply,
    )

    return {"status": "processed", "lead_state": new_state}


# ==========================================
# STATIC DASHBOARD MOUNT
# ==========================================
app.mount(
    "/dashboard",
    StaticFiles(directory="static", html=True),
    name="static",
)