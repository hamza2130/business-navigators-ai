"""Escalation (FR-2/FR-13) and staff audit trail / dashboard data (FR-12).

Same harness as test_spec.py: real Postgres/Redis/S3, third-party services
stubbed (see conftest.py).
"""
import psycopg

from conftest import (
    OUT,
    AI_REPLY_OVERRIDE,
    TEST_SUPERUSER_URL,
    admin_headers,
    meta_text_payload,
    post_email_webhook,
    post_whatsapp_webhook,
    wa_texts,
)


def _escalating_reply(reason="asked about a legal dispute, outside KB scope"):
    return (
        "That is outside what I can advise on - one of our specialists will follow up shortly.\n"
        '<<<LEAD_DATA>>>{"name": null, "business_type": null, "turnover": null, "industry": null, '
        '"vat_status": null, "service_interest": null, "needs_escalation": true, '
        f'"escalation_reason": "{reason}"}}'
    )


# ==========================================================================
# CONFIDENCE-BASED ESCALATION (FR-2, FR-13)
# ==========================================================================
class TestEscalation:
    def test_low_confidence_pauses_the_ai_for_that_lead(self, client):
        import database
        AI_REPLY_OVERRIDE["value"] = _escalating_reply()
        post_whatsapp_webhook(client, meta_text_payload("971500000070", "my partner is suing me, what do I do?"))
        lead = database.get_lead("971500000070")
        assert lead["human_takeover"] is True, "AI said it wasn't confident but kept the conversation automated"

    def test_client_still_gets_a_helpful_reply_and_never_sees_the_flag(self, client):
        AI_REPLY_OVERRIDE["value"] = _escalating_reply()
        post_whatsapp_webhook(client, meta_text_payload("971500000071", "legal question"))
        sent = wa_texts()
        assert sent and "specialists will follow up" in sent[0]
        assert "needs_escalation" not in sent[0] and "<<<LEAD_DATA>>>" not in sent[0]

    def test_staff_are_notified_with_the_reason(self, client):
        AI_REPLY_OVERRIDE["value"] = _escalating_reply("client is asking for a refund")
        post_whatsapp_webhook(client, meta_text_payload("971500000072", "I want my money back"))
        bodies = " ".join(str(b["json"].get("htmlContent", "")) for b in OUT["brevo"])
        assert "auto-escalated" in bodies and "asking for a refund" in bodies

    def test_later_messages_are_left_for_the_human(self, client):
        AI_REPLY_OVERRIDE["value"] = _escalating_reply()
        post_whatsapp_webhook(client, meta_text_payload("971500000073", "legal thing", "m1"))
        groq_before, wa_before = len(OUT["groq"]), len(OUT["whatsapp"])
        post_whatsapp_webhook(client, meta_text_payload("971500000073", "hello??", "m2"))
        assert len(OUT["groq"]) == groq_before and len(OUT["whatsapp"]) == wa_before, (
            "the AI kept replying after it escalated to a human")

    def test_confident_answers_do_not_escalate(self, client):
        import database
        post_whatsapp_webhook(client, meta_text_payload("971500000074", "what is the VAT rate?"))
        assert database.get_lead("971500000074")["human_takeover"] is False

    def test_email_channel_escalates_too(self, client):
        import database
        AI_REPLY_OVERRIDE["value"] = _escalating_reply()
        post_email_webhook(client, {"sender": {"email": "angry@example.com"}, "subject": "Complaint",
                                    "text": "This is unacceptable, I want to speak to a manager"})
        assert database.get_lead("angry@example.com")["human_takeover"] is True

    def test_staff_can_hand_the_conversation_back(self, client):
        AI_REPLY_OVERRIDE["value"] = _escalating_reply()
        post_whatsapp_webhook(client, meta_text_payload("971500000075", "legal thing", "m1"))
        client.post("/admin/takeover", data={"phone_number": "971500000075", "enable": "false"},
                    headers=admin_headers())
        AI_REPLY_OVERRIDE["value"] = None
        before = len(OUT["groq"])
        post_whatsapp_webhook(client, meta_text_payload("971500000075", "thanks, another question", "m2"))
        assert len(OUT["groq"]) > before, "AI didn't resume after staff handed the conversation back"

    def test_strip_lead_data_escalation_parsing(self):
        from ai_service import strip_lead_data
        _, _, esc = strip_lead_data(_escalating_reply("x"))
        assert esc == {"needed": True, "reason": "x"}
        _, _, esc = strip_lead_data("plain reply, no sentinel")
        assert esc == {"needed": False, "reason": None}


# ==========================================================================
# STAFF AUDIT TRAIL + DASHBOARD DATA (FR-12, NFR audit logging)
# ==========================================================================
class TestAuditAndDashboardData:
    def test_admin_actions_are_recorded_with_the_actor(self, client):
        r = client.post("/admin/knowledge-base", headers={**admin_headers(), "X-Actor": "Sara"},
                        json={"category": "Tax", "topic": "VAT", "content": "5%", "is_active": True})
        assert r.status_code == 200
        log = client.get("/admin/audit-log", headers=admin_headers()).json()["data"]
        assert any(e["action"] == "kb.add" and e["actor"] == "Sara" for e in log)

    def test_actor_defaults_when_not_supplied(self, client):
        client.post("/admin/takeover", data={"phone_number": "971500000076", "enable": "true"},
                    headers=admin_headers())
        log = client.get("/admin/audit-log", headers=admin_headers()).json()["data"]
        entry = next(e for e in log if e["action"] == "takeover.enable")
        assert entry["actor"] == "admin" and entry["target"] == "971500000076"

    def test_every_write_route_is_audited(self, client):
        h = admin_headers()
        client.post("/admin/scoring-rules", headers=h, json={"keyword": "zzz", "weight": 3})
        client.post("/admin/booking-keywords", headers=h, json={"keyword": "zzzbook"})
        client.post("/admin/settings", headers=h, json={"key": "hot_threshold", "value": "80"})
        actions = {e["action"] for e in client.get("/admin/audit-log", headers=h).json()["data"]}
        assert {"scoring_rule.save", "booking_keyword.save", "setting.update"} <= actions

    def test_failing_to_write_the_audit_row_never_blocks_the_action(self, client, monkeypatch):
        import main

        def boom(*a, **k):
            raise RuntimeError("db down")

        monkeypatch.setattr(main, "log_audit_event", boom)
        r = client.post("/admin/knowledge-base", headers=admin_headers(),
                        json={"category": "X", "topic": "Y", "content": "Z", "is_active": True})
        assert r.status_code == 200

    def test_audit_log_is_tenant_isolated(self, client):
        import database
        with psycopg.connect(TEST_SUPERUSER_URL, autocommit=True) as conn:
            conn.execute("INSERT INTO tenants (id, name) VALUES ('other_co','Other') ON CONFLICT DO NOTHING")
        try:
            with database.get_conn(tenant_id="other_co") as conn:
                conn.execute("INSERT INTO audit_log (tenant_id, actor, action) VALUES ('other_co','x','secret.action')")
            log = client.get("/admin/audit-log", headers=admin_headers()).json()["data"]
            assert not any(e["action"] == "secret.action" for e in log)
        finally:
            with psycopg.connect(TEST_SUPERUSER_URL, autocommit=True) as conn:
                conn.execute("DELETE FROM audit_log WHERE tenant_id='other_co'")
                conn.execute("DELETE FROM tenants WHERE id='other_co'")

    def test_transcript_returns_the_conversation_in_order(self, client):
        post_whatsapp_webhook(client, meta_text_payload("971500000077", "first question", "m1"))
        post_whatsapp_webhook(client, meta_text_payload("971500000077", "second question", "m2"))
        t = client.get("/admin/leads/971500000077/transcript", headers=admin_headers()).json()["data"]
        assert [m["role"] for m in t] == ["user", "assistant", "user", "assistant"]
        assert t[0]["content"] == "first question" and t[2]["content"] == "second question"
        assert all(m["channel"] == "whatsapp" for m in t)

    def test_kpis_reflect_real_data(self, client):
        post_whatsapp_webhook(client, meta_text_payload("971500000078", "tax compliance setup visa help please", "m1"))
        AI_REPLY_OVERRIDE["value"] = _escalating_reply()
        post_whatsapp_webhook(client, meta_text_payload("971500000079", "legal issue", "m2"))
        k = client.get("/admin/kpis", headers=admin_headers()).json()["data"]
        assert k["total_leads"] == 2
        assert k["awaiting_human"] == 1
        assert k["messages_last_24h"] >= 4
        assert sum(k["leads_by_state"].values()) == 2

    def test_new_dashboard_endpoints_require_auth(self, client):
        for path in ("/admin/kpis", "/admin/audit-log", "/admin/leads/971500000077/transcript"):
            assert client.get(path).status_code == 401, path
