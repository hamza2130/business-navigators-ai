"""FR-9: knowledge-base retrieval.

While the KB fits the prompt budget everything is sent (unchanged
behaviour); once it outgrows it, only the entries relevant to the client's
message are. The budget is shrunk in these tests via monkeypatch rather than
by loading thousands of rows.

This is lexical (PostgreSQL full-text) retrieval - see kb_service.py for what
that does and doesn't cover.
"""
import pytest

from conftest import groq_messages, meta_text_payload, post_whatsapp_webhook, wa_texts

ENTRIES = [
    ("Licensing", "Trade licence renewal", "Renew the trade licence annually before its expiry via the DED portal."),
    ("Licensing", "Free zone licences", "Free zone companies receive a licence from the zone authority."),
    ("Tax", "VAT registration", "Register for VAT when taxable turnover exceeds AED 375,000 per year."),
    ("Tax", "Corporate tax", "Corporate tax of nine percent applies to profits above AED 375,000."),
    ("Visas", "Investor visa", "Company owners can sponsor an investor visa after incorporation."),
    ("Visas", "Employment visa", "Employees are sponsored on an employment visa tied to the company quota."),
    ("Banking", "Corporate bank account", "A corporate bank account requires the trade licence and passport copies."),
    ("Office", "Flexi desk", "A flexi desk package suits home based consultants and freelancers."),
]


def _load_kb(monkeypatch, budget=150, top_k=5):
    import database
    from config import settings
    for category, topic, content in ENTRIES:
        database.add_kb_item(category, topic, content)
    monkeypatch.setattr(settings, "KB_FULL_CONTEXT_MAX_CHARS", budget)
    monkeypatch.setattr(settings, "KB_RETRIEVAL_TOP_K", top_k)


def _system_prompt() -> str:
    return groq_messages()[-1][0]["content"]


class TestTermExtraction:
    def test_terms_are_lowercase_distinct_words(self):
        from kb_service import extract_terms
        assert extract_terms("VAT vat Registration, VAT!") == ["vat", "registration"]

    def test_single_characters_are_dropped(self):
        from kb_service import extract_terms
        assert extract_terms("a b cd") == ["cd"]

    def test_tsquery_syntax_cannot_be_injected(self):
        """Terms are joined into a tsquery, so nothing but word characters may
        get through."""
        from kb_service import extract_terms
        terms = extract_terms("x' | !(y) & <-> :* z'; DROP TABLE leads;--")
        assert all(t.replace("_", "").isalnum() for t in terms)

    def test_term_count_is_capped(self):
        from kb_service import extract_terms
        assert len(extract_terms(" ".join(f"word{i}" for i in range(200)))) == 30


class TestModes:
    def test_small_kb_is_sent_whole(self, app_env):
        import database
        from kb_service import get_knowledge_context
        for category, topic, content in ENTRIES:
            database.add_kb_item(category, topic, content)
        ctx = get_knowledge_context("vat")
        assert ctx.mode == "full"
        assert all(topic in ctx.text for _, topic, _ in ENTRIES)

    def test_large_kb_returns_only_relevant_entries(self, app_env, monkeypatch):
        from kb_service import get_knowledge_context
        _load_kb(monkeypatch)
        ctx = get_knowledge_context("when do I need to register for VAT?")
        assert ctx.mode == "retrieved"
        assert "VAT registration" in ctx.text
        assert "Flexi desk" not in ctx.text and "Investor visa" not in ctx.text

    def test_stemming_matches_word_variants(self, app_env, monkeypatch):
        from kb_service import get_knowledge_context
        _load_kb(monkeypatch)
        assert "Investor visa" in get_knowledge_context("do you sponsor visas?").text

    def test_topic_matches_outrank_content_only_matches(self, app_env, monkeypatch):
        """'licence' is in the topic of the first entry but only in the body
        of the banking one."""
        from database import search_kb
        _load_kb(monkeypatch)
        ranked = [r["topic"] for r in search_kb(["licence"], 10)]
        assert ranked.index("Trade licence renewal") < ranked.index("Corporate bank account")

    def test_result_count_is_capped_at_top_k(self, app_env, monkeypatch):
        from kb_service import get_knowledge_context
        _load_kb(monkeypatch, top_k=2)
        ctx = get_knowledge_context("licence tax visa company")
        assert ctx.text.count("\n- ") == 2

    def test_nothing_relevant_is_reported_not_papered_over(self, app_env, monkeypatch):
        from kb_service import get_knowledge_context
        _load_kb(monkeypatch)
        ctx = get_knowledge_context("what is the airspeed of a swallow")
        assert ctx.mode == "none_matched"
        assert "No Knowledge Base entry matched" in ctx.text

    def test_no_query_on_a_large_kb_still_sends_something_within_budget(self, app_env, monkeypatch):
        from kb_service import get_knowledge_context
        _load_kb(monkeypatch, budget=200)
        ctx = get_knowledge_context(None)
        assert ctx.mode == "full" and ctx.text.count("\n- ") >= 1


class TestInThePipeline:
    def test_only_relevant_entries_reach_the_llm(self, client, monkeypatch):
        _load_kb(monkeypatch)
        post_whatsapp_webhook(client, meta_text_payload("971500000150", "when must I register for VAT?"))
        prompt = _system_prompt()
        assert "VAT registration" in prompt
        assert "Flexi desk" not in prompt

    def test_followup_uses_the_previous_turn_for_retrieval(self, client, monkeypatch):
        """'and how long does that take?' has no searchable subject of its own."""
        _load_kb(monkeypatch)
        post_whatsapp_webhook(client, meta_text_payload("971500000151", "tell me about trade licence renewal", "m1"))
        post_whatsapp_webhook(client, meta_text_payload("971500000151", "and what else is needed?", "m2"))
        assert "Trade licence renewal" in _system_prompt()

    def test_no_match_tells_the_model_to_escalate(self, client, monkeypatch):
        _load_kb(monkeypatch)
        post_whatsapp_webhook(client, meta_text_payload("971500000152", "what is the airspeed of a swallow"))
        prompt = _system_prompt()
        assert "found NO entry relevant" in prompt and "needs_escalation to true" in prompt

    def test_the_no_match_notice_is_absent_when_something_matched(self, client, monkeypatch):
        _load_kb(monkeypatch)
        post_whatsapp_webhook(client, meta_text_payload("971500000153", "how does the investor visa work"))
        assert "found NO entry relevant" not in _system_prompt()

    @pytest.mark.parametrize("message", [
        "what's the cost of A&B | (C) !",
        "'; DROP TABLE knowledge_base; --",
        "<-> :* & !",
    ])
    def test_hostile_or_odd_queries_never_break_a_reply(self, client, monkeypatch, message):
        _load_kb(monkeypatch)
        post_whatsapp_webhook(client, meta_text_payload("971500000154", message))
        assert wa_texts(), f"no reply for {message!r}"

    def test_a_greeting_is_not_pushed_towards_escalation(self, client, monkeypatch):
        """With a large KB a bare 'hi' matches nothing; the no-match notice
        must not tell the model to escalate a greeting."""
        _load_kb(monkeypatch)
        post_whatsapp_webhook(client, meta_text_payload("971500000155", "hi"))
        prompt = _system_prompt()
        assert "only a greeting, thanks, or" in prompt and "do not escalate" in prompt
