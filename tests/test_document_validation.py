"""FR-7: document-type detection and validation flags.

This is a heuristic over OCR text, so these tests pin down what it does and,
just as importantly, that it only ever FLAGS (for staff and the client) and
never rejects a document on its own.
"""
import datetime
import io

import pytest

from conftest import (
    OCR_TEXT,
    admin_headers,
    groq_messages,
    meta_media_payload,
    post_whatsapp_webhook,
)
from test_spec import EMIRATES_ID_OCR, PASSPORT_OCR

TRADE_LICENCE_OCR = (
    "GOVERNMENT OF DUBAI\nDepartment of Economy and Tourism\nTRADE LICENSE\n"
    "License No: 123456\nBusiness Activities: General Trading\nExpiry Date: 01/01/2031\n"
)


def _past_date() -> str:
    d = datetime.date.today() - datetime.timedelta(days=30)
    return d.strftime("%d/%m/%Y")


def _upload_via_admin(client, phone):
    return client.post(
        "/admin/test-document-upload",
        headers=admin_headers(),
        data={"phone_number": phone},
        files={"file": ("doc.png", io.BytesIO(b"fake image bytes"), "image/png")},
    )


class TestDetection:
    @pytest.mark.parametrize("text,expected", [
        (PASSPORT_OCR + "P<PAKKHAN<<AYESHA<<<<<<<<<<<<<<<<<<<<<<<<<<<<<", "passport"),
        (EMIRATES_ID_OCR, "emirates_id"),
        (TRADE_LICENCE_OCR, "trade_license"),
        ("Residence Visa\nFile No: 201/2021/1234567\nGDRFA Dubai\nExpiry: 01/01/2030", "residence_visa"),
        ("Tax Registration Certificate\nTax Registration Number (TRN): 100123456700003\nFederal Tax Authority",
         "vat_certificate"),
        ("BLURRY SCAN no dates at all here", "unknown"),
        ("", "unknown"),
    ])
    def test_detect_document_type(self, app_env, text, expected):
        from document_validation import detect_document_type
        assert detect_document_type(text) == expected

    def test_a_weak_single_signal_is_not_enough(self, app_env):
        """The word 'passport' alone in a letter isn't a passport."""
        from document_validation import detect_document_type
        assert detect_document_type("please find my passport details attached") == "unknown"


class TestFlags:
    def test_a_recognised_valid_document_has_no_flag(self, app_env):
        from document_validation import validate_document
        v = validate_document(TRADE_LICENCE_OCR, "2031-01-01")
        assert v.detected_type == "trade_license" and v.ok

    def test_unrecognised_document_is_flagged(self, app_env):
        from document_validation import validate_document
        v = validate_document("some random text", "2031-01-01")
        assert v.detected_type == "unknown" and "Could not recognise" in v.flag

    def test_expired_document_is_flagged(self, app_env):
        from document_validation import validate_document
        v = validate_document(TRADE_LICENCE_OCR, "2020-01-01")
        assert "already expired" in v.flag

    def test_type_outside_the_accepted_list_is_flagged(self, app_env):
        import database
        from document_validation import validate_document
        database.set_app_setting("accepted_document_types", "passport,emirates_id")
        v = validate_document(TRADE_LICENCE_OCR, "2031-01-01")
        assert "not an accepted document type" in v.flag
        assert validate_document(EMIRATES_ID_OCR, "2027-06-04").ok

    def test_empty_accepted_list_means_anything_recognised_is_fine(self, app_env):
        from document_validation import validate_document
        assert validate_document(TRADE_LICENCE_OCR, "2031-01-01").ok


class TestPipeline:
    def test_whatsapp_upload_records_type_and_flag(self, client):
        import database
        OCR_TEXT["value"] = TRADE_LICENCE_OCR.replace("01/01/2031", _past_date())
        post_whatsapp_webhook(client, meta_media_payload("971500000140"))
        doc = database.list_documents_for_lead("971500000140")[0]
        assert doc["detected_type"] == "trade_license"
        assert "already expired" in doc["validation_flag"]

    def test_flagged_document_still_goes_to_the_review_queue(self, client):
        """The heuristic must never reject on its own - staff decide."""
        import database
        OCR_TEXT["value"] = "Date of Expiry: 01/01/2031 and nothing else recognisable"
        post_whatsapp_webhook(client, meta_media_payload("971500000141"))
        pending = [d for d in database.list_pending_documents() if d["lead_phone_number"] == "971500000141"]
        assert pending and pending[0]["validation_flag"]

    def test_client_is_told_what_looked_wrong(self, client):
        OCR_TEXT["value"] = "Date of Expiry: 01/01/2031 and nothing else recognisable"
        post_whatsapp_webhook(client, meta_media_payload("971500000142"))
        prompt = groq_messages()[0][-1]["content"]
        assert "automated check flagged" in prompt and "Could not recognise" in prompt

    def test_a_clean_document_adds_no_warning_to_the_prompt(self, client):
        OCR_TEXT["value"] = EMIRATES_ID_OCR
        post_whatsapp_webhook(client, meta_media_payload("971500000143"))
        assert "automated check flagged" not in groq_messages()[0][-1]["content"]

    def test_staff_are_notified_with_the_flag(self, client):
        from conftest import OUT
        OCR_TEXT["value"] = "Date of Expiry: 01/01/2031 and nothing else recognisable"
        post_whatsapp_webhook(client, meta_media_payload("971500000144"))
        bodies = " ".join(str(b["json"].get("htmlContent", "")) for b in OUT["brevo"])
        assert "FLAG" in bodies

    def test_admin_upload_reports_type_and_flag(self, client):
        OCR_TEXT["value"] = EMIRATES_ID_OCR
        body = _upload_via_admin(client, "971500000145").json()
        assert body["detected_type"] == "emirates_id" and body["validation_flag"] is None

    def test_review_queue_api_exposes_the_flag(self, client):
        OCR_TEXT["value"] = "Date of Expiry: 01/01/2031 and nothing else recognisable"
        _upload_via_admin(client, "971500000146")
        queue = client.get("/admin/documents", headers=admin_headers()).json()["data"]
        assert any(d["validation_flag"] for d in queue)
