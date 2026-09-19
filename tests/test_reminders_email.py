"""FR-10: expiry reminders over WhatsApp AND email, plus email capture that
makes the second channel possible for WhatsApp leads."""
import datetime

from conftest import (
    OUT,
    AI_REPLY_OVERRIDE,
    WHATSAPP_STATUS,
    calendar_events_for,
    meta_text_payload,
    post_whatsapp_webhook,
    wa_texts,
)


def _seed(identifier, days_out, name="Ayesha", email=None):
    import database
    expiry = (datetime.date.today() + datetime.timedelta(days=days_out)).isoformat()
    database.save_or_update_lead(
        identifier, name=name, state="DOCUMENT_SUBMITTED", score=50,
        document_expiry_date=expiry, document_status="VERIFIED", email=email,
    )
    return expiry


def _emails_to(address):
    return [b for b in OUT["brevo"] if b["json"]["to"][0]["email"] == address]


def _reply_with_email(value):
    return (
        "Thanks, noted.\n"
        '<<<LEAD_DATA>>>{"name": null, "business_type": null, "turnover": null, "industry": null, '
        '"vat_status": null, "service_interest": null, "email": ' + value + ', '
        '"needs_escalation": false, "escalation_reason": null}'
    )


class TestEmailReminders:
    def test_email_identified_lead_gets_an_email_not_a_whatsapp_message(self, app_env):
        """Previously every reminder went through the WhatsApp API, even for
        a lead whose identifier is an email address."""
        from scheduler_service import check_expiring_documents
        _seed("client@example.com", 30)
        check_expiring_documents()
        assert len(_emails_to("client@example.com")) == 1
        assert OUT["whatsapp"] == [], "an email address was sent to the WhatsApp API"

    def test_whatsapp_lead_with_a_known_email_is_reminded_on_both_channels(self, app_env):
        from scheduler_service import check_expiring_documents
        _seed("971500000090", 30, email="ayesha@example.com")
        check_expiring_documents()
        assert wa_texts(), "no WhatsApp reminder"
        assert len(_emails_to("ayesha@example.com")) == 1, "no email reminder"

    def test_whatsapp_lead_without_an_email_is_reminded_on_whatsapp_only(self, app_env):
        from scheduler_service import check_expiring_documents
        _seed("971500000091", 30)
        check_expiring_documents()
        assert wa_texts()
        assert OUT["brevo"] == []

    def test_urgent_email_is_marked_urgent(self, app_env):
        from scheduler_service import check_expiring_documents
        _seed("urgent@example.com", 5)
        check_expiring_documents()
        subjects = [b["json"]["subject"] for b in _emails_to("urgent@example.com")]
        assert any("URGENT" in s for s in subjects)

    def test_reminder_is_not_marked_sent_when_nothing_was_delivered(self, app_env):
        """If every channel fails, the reminder must be retried next run
        rather than silently recorded as done."""
        from scheduler_service import check_expiring_documents
        _seed("971500000092", 30)
        WHATSAPP_STATUS["code"] = 500
        try:
            check_expiring_documents()
        finally:
            WHATSAPP_STATUS["code"] = 200
        OUT["whatsapp"].clear()
        check_expiring_documents()  # WhatsApp is healthy again
        assert wa_texts(), "the failed reminder was never retried"

    def test_email_reminder_is_not_sent_twice(self, app_env):
        from scheduler_service import check_expiring_documents
        _seed("once@example.com", 30)
        check_expiring_documents()
        check_expiring_documents()
        assert len(_emails_to("once@example.com")) == 1


class TestEmailCapture:
    def test_ai_captured_email_is_saved_on_the_lead(self, client):
        import database
        AI_REPLY_OVERRIDE["value"] = _reply_with_email('"ayesha@example.com"')
        post_whatsapp_webhook(client, meta_text_payload("971500000093", "my email is ayesha@example.com"))
        assert database.get_lead("971500000093")["email"] == "ayesha@example.com"

    def test_a_malformed_email_is_dropped_not_stored(self, client):
        import database
        AI_REPLY_OVERRIDE["value"] = _reply_with_email('"not-an-email"')
        post_whatsapp_webhook(client, meta_text_payload("971500000094", "hello"))
        assert database.get_lead("971500000094")["email"] is None

    def test_a_later_turn_without_an_email_does_not_erase_it(self, client):
        import database
        AI_REPLY_OVERRIDE["value"] = _reply_with_email('"ayesha@example.com"')
        post_whatsapp_webhook(client, meta_text_payload("971500000095", "my email is ayesha@example.com", "m1"))
        AI_REPLY_OVERRIDE["value"] = _reply_with_email("null")
        post_whatsapp_webhook(client, meta_text_payload("971500000095", "another question", "m2"))
        assert database.get_lead("971500000095")["email"] == "ayesha@example.com"

    def test_whatsapp_booking_invites_the_lead_once_their_email_is_known(self, client):
        """Closes the previously documented gap: WhatsApp leads couldn't be
        calendar attendees because we never had an email for them."""
        import database
        database.save_or_update_lead("971500000096", email="ayesha@example.com")
        post_whatsapp_webhook(client, meta_text_payload("971500000096", "please book a meeting"))
        events = calendar_events_for("971500000096")
        assert len(events) == 1
        assert any(a["email"] == "ayesha@example.com" for a in events[0]["body"].get("attendees", []))
