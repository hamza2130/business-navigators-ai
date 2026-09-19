"""FR-5: booking with a real availability check, reschedule and cancel.

The Google Calendar stub in conftest.py keeps a tiny in-memory calendar: an
event that gets inserted shows up as busy in later freebusy queries, so
double-booking is actually observable here rather than assumed away.
"""
import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import (
    CAL,
    OUT,
    calendar_events_for,
    groq_messages,
    meta_text_payload,
    post_whatsapp_webhook,
)

DUBAI = ZoneInfo("Asia/Dubai")


def _first_workday() -> datetime.date:
    day = datetime.datetime.now(DUBAI).date() + datetime.timedelta(days=1)
    while day.weekday() > 4:  # Sat/Sun are the UAE weekend
        day += datetime.timedelta(days=1)
    return day


def _slot_start(event: dict) -> datetime.datetime:
    return datetime.datetime.strptime(event["body"]["start"]["dateTime"], "%Y-%m-%dT%H:%M:%S")


def _block(day: datetime.date, start: str, end: str) -> None:
    """Mark part of the consultant's calendar busy with something that isn't ours."""
    h1, m1 = map(int, start.split(":"))
    h2, m2 = map(int, end.split(":"))
    CAL["extra_busy"].append((
        datetime.datetime.combine(day, datetime.time(h1, m1), tzinfo=DUBAI).isoformat(),
        datetime.datetime.combine(day, datetime.time(h2, m2), tzinfo=DUBAI).isoformat(),
    ))


def _say(client, who, text, msg_id):
    post_whatsapp_webhook(client, meta_text_payload(who, text, msg_id))


def _last_prompt() -> str:
    return [m for m in groq_messages()[-1] if m["role"] == "user"][-1]["content"]


# ==========================================================================
# AVAILABILITY
# ==========================================================================
class TestAvailability:
    def test_books_the_first_free_slot_on_a_working_day(self, client):
        _say(client, "971500000101", "please book a meeting", "m1")
        start = _slot_start(calendar_events_for("971500000101")[0])
        assert start.date() == _first_workday()
        assert (start.hour, start.minute) == (10, 0)
        assert start.weekday() <= 4

    def test_skips_a_slot_that_is_already_busy(self, client):
        _block(_first_workday(), "10:00", "10:30")
        _say(client, "971500000102", "please book a meeting", "m1")
        start = _slot_start(calendar_events_for("971500000102")[0])
        assert (start.date(), start.hour, start.minute) == (_first_workday(), 10, 30)

    def test_a_fully_booked_day_rolls_to_the_next_working_day(self, client):
        _block(_first_workday(), "00:00", "23:59")
        _say(client, "971500000103", "please book a meeting", "m1")
        start = _slot_start(calendar_events_for("971500000103")[0])
        assert start.date() > _first_workday()
        assert start.weekday() <= 4

    def test_two_leads_are_never_double_booked(self, client):
        _say(client, "971500000104", "please book a meeting", "m1")
        _say(client, "971500000105", "please book a meeting", "m2")
        a = _slot_start(calendar_events_for("971500000104")[0])
        b = _slot_start(calendar_events_for("971500000105")[0])
        assert a != b, "two different clients were booked into the same slot"

    def test_weekend_is_skipped(self, app_env):
        import meeting_service
        friday = datetime.datetime(2026, 9, 18, 12, 0, tzinfo=DUBAI)  # a Friday
        start, _ = meeting_service.find_available_slot(now=friday)
        assert datetime.datetime.strptime(start, "%Y-%m-%dT%H:%M:%S").weekday() == 0, (
            "Saturday/Sunday slot offered")

    def test_unreadable_calendar_falls_back_to_the_booking_link(self, client):
        """If availability can't be verified we must not guess a slot."""
        from config import settings
        CAL["freebusy_fail"] = True
        _say(client, "971500000106", "please book a meeting", "m1")
        assert calendar_events_for("971500000106") == []
        assert settings.BOOKING_LINK in _last_prompt()

    def test_client_is_told_the_actual_time(self, client):
        """The LLM used to be given only a link, never the slot itself."""
        _say(client, "971500000107", "please book a meeting", "m1")
        assert "scheduled for" in _last_prompt() and "10:00" in _last_prompt()


# ==========================================================================
# ONE MEETING PER LEAD
# ==========================================================================
class TestNoDuplicates:
    def test_the_meeting_is_remembered_on_the_lead(self, client):
        import database
        _say(client, "971500000110", "please book a meeting", "m1")
        lead = database.get_lead("971500000110")
        assert lead["meeting_event_id"] and lead["meeting_start"]
        assert lead["state"] == "MEETING_REQUESTED"

    def test_asking_again_does_not_create_a_second_event(self, client):
        _say(client, "971500000111", "please book a meeting", "m1")
        _say(client, "971500000111", "can we schedule a call", "m2")
        assert len(calendar_events_for("971500000111")) == 1
        assert "already has a meeting" in _last_prompt()

    def test_a_past_meeting_does_not_block_a_new_booking(self, client):
        import database
        _say(client, "971500000112", "please book a meeting", "m1")
        database.set_meeting("971500000112", "old-event", "2020-01-01T10:00:00", None)
        _say(client, "971500000112", "book another meeting", "m2")
        assert len(calendar_events_for("971500000112")) == 2


# ==========================================================================
# RESCHEDULE
# ==========================================================================
class TestReschedule:
    def test_reschedule_moves_the_existing_event(self, client):
        import database
        _say(client, "971500000120", "please book a meeting", "m1")
        before = database.get_lead("971500000120")
        _say(client, "971500000120", "sorry, I need to reschedule my meeting", "m2")
        after = database.get_lead("971500000120")

        assert len(OUT["calendar_patch"]) == 1
        assert OUT["calendar_patch"][0]["eventId"] == before["meeting_event_id"]
        assert len(calendar_events_for("971500000120")) == 1, "reschedule created a second event"
        assert after["meeting_event_id"] == before["meeting_event_id"]
        assert after["meeting_start"] != before["meeting_start"]
        assert after["state"] == "MEETING_REQUESTED"

    def test_reschedule_tells_the_client_the_new_time(self, client):
        import database
        _say(client, "971500000121", "please book a meeting", "m1")
        _say(client, "971500000121", "can we change my meeting to another time", "m2")
        new_start = database.get_lead("971500000121")["meeting_start"]
        assert "rescheduled to" in _last_prompt()
        assert new_start.split("T")[1][:5] in _last_prompt()

    def test_reschedule_without_a_meeting_just_books_one(self, client):
        _say(client, "971500000122", "I'd like to reschedule", "m1")
        assert len(calendar_events_for("971500000122")) == 1
        assert OUT["calendar_patch"] == []

    def test_failed_calendar_update_notifies_staff_and_keeps_the_meeting(self, client):
        import database
        _say(client, "971500000123", "please book a meeting", "m1")
        before = database.get_lead("971500000123")
        CAL["patch_fail"] = True
        _say(client, "971500000123", "please reschedule my meeting", "m2")
        after = database.get_lead("971500000123")
        assert after["meeting_start"] == before["meeting_start"]
        bodies = " ".join(str(b["json"].get("htmlContent", "")) for b in OUT["brevo"])
        assert "reschedule" in bodies.lower() and "manually" in bodies.lower()


# ==========================================================================
# CANCEL
# ==========================================================================
class TestCancel:
    def test_cancel_deletes_the_event_and_clears_the_lead(self, client):
        import database
        _say(client, "971500000130", "please book a meeting", "m1")
        event_id = database.get_lead("971500000130")["meeting_event_id"]
        _say(client, "971500000130", "please cancel my meeting", "m2")

        assert [d["eventId"] for d in OUT["calendar_delete"]] == [event_id]
        lead = database.get_lead("971500000130")
        assert lead["meeting_event_id"] is None and lead["meeting_start"] is None
        assert lead["state"] == "ENGAGED"
        assert "cancelled" in _last_prompt()

    def test_cancelling_frees_the_slot_for_someone_else(self, client):
        _say(client, "971500000131", "please book a meeting", "m1")
        first = _slot_start(calendar_events_for("971500000131")[0])
        _say(client, "971500000131", "cancel my meeting", "m2")
        _say(client, "971500000132", "please book a meeting", "m3")
        assert _slot_start(calendar_events_for("971500000132")[0]) == first

    def test_cancel_without_a_meeting_touches_nothing(self, client):
        _say(client, "971500000133", "cancel my meeting", "m1")
        assert OUT["calendar_delete"] == [] and calendar_events_for("971500000133") == []
        assert "no upcoming meeting" in _last_prompt()

    def test_cancel_is_not_mistaken_for_a_booking(self, client):
        """'meeting' is a booking keyword, so this used to book a NEW meeting."""
        _say(client, "971500000134", "please book a meeting", "m1")
        _say(client, "971500000134", "cancel the meeting", "m2")
        assert len(calendar_events_for("971500000134")) == 1

    def test_failed_calendar_update_notifies_staff_and_keeps_the_meeting(self, client):
        import database
        _say(client, "971500000135", "please book a meeting", "m1")
        CAL["delete_fail"] = True
        _say(client, "971500000135", "cancel my meeting", "m2")
        assert database.get_lead("971500000135")["meeting_event_id"], "meeting forgotten though it still exists"
        bodies = " ".join(str(b["json"].get("htmlContent", "")) for b in OUT["brevo"])
        assert "CANCEL" in bodies and "manually" in bodies.lower()

    def test_an_already_deleted_event_counts_as_cancelled(self, client):
        import database
        _say(client, "971500000136", "please book a meeting", "m1")
        CAL["events"].clear()  # staff already removed it from the calendar
        CAL["delete_gone"] = True
        _say(client, "971500000136", "cancel my meeting", "m2")
        assert database.get_lead("971500000136")["meeting_event_id"] is None


# ==========================================================================
# INTENT DETECTION
# ==========================================================================
class TestIntent:
    @pytest.mark.parametrize("text,has_meeting,expected", [
        ("I need to reschedule", False, "reschedule"),
        ("can we move my meeting to another day?", True, "reschedule"),
        ("please postpone the call", True, "reschedule"),
        ("cancel my meeting", True, "cancel"),
        ("please cancel the appointment", False, "cancel"),
        ("cancel", True, "cancel"),
        ("I want to cancel my subscription", False, None),
        ("can we book a meeting", False, None),
        ("what documents do I need?", True, None),
    ])
    def test_meeting_intent(self, text, has_meeting, expected):
        import meeting_service
        assert meeting_service.meeting_intent(text, has_meeting) == expected
