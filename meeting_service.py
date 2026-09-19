"""FR-5: consultation booking with a real availability check, plus
reschedule and cancel.

main.py's message pipeline used to hardcode "tomorrow at 10:00" and insert
blindly, so two leads asking on the same day were double-booked and a client
who wanted to change or cancel had no path at all. This module:

- finds the earliest FREE slot on the consultant calendar (Google Calendar
  freebusy) within working hours, skipping weekends;
- remembers which calendar event belongs to which lead (leads.meeting_*), so
  the same lead asking again isn't double-booked and can reschedule/cancel;
- reports what happened as an `outcome` the AI reply is built around, so the
  assistant tells the client the ACTUAL time (previously it was never told).

Failure policy: if the calendar can't be reached we never guess a slot - the
client is handed the fallback booking link instead, exactly like before.
"""
import datetime
import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import calendar_service
import database
from config import settings

DUBAI_TZ = ZoneInfo("Asia/Dubai")
_ISO_FMT = "%Y-%m-%dT%H:%M:%S"

_MEETING_NOUN = r"(meeting|appointment|consultation|booking|call|slot|session)"
_RESCHEDULE_RE = re.compile(
    r"\b(re-?schedul\w*|postpone\w*|"
    r"(change|move|shift|push)\s+(my|the|our)\s+" + _MEETING_NOUN + r"|"
    r"(different|another|other|new)\s+(time|day|date|slot))\b",
    re.IGNORECASE,
)
_CANCEL_RE = re.compile(r"\b(cancel\w*|call\s+off)\b", re.IGNORECASE)
_MEETING_NOUN_RE = re.compile(r"\b" + _MEETING_NOUN + r"\b", re.IGNORECASE)


@dataclass
class Outcome:
    """What the meeting logic did this turn.

    instruction: text appended to the LLM prompt so the reply reflects it.
    new_state:   lead state to move to, or None to leave the state alone.
    staff_note:  set when a human needs to step in (e.g. a cancellation that
                 couldn't be applied to the calendar); main.py notifies staff.
    """
    instruction: str
    new_state: str | None = None
    staff_note: str | None = None


def _now() -> datetime.datetime:
    return datetime.datetime.now(DUBAI_TZ)


def _parse_naive(value: str) -> datetime.datetime:
    return datetime.datetime.strptime(value, _ISO_FMT).replace(tzinfo=DUBAI_TZ)


def has_active_meeting(lead: dict | None) -> bool:
    """True when the lead has a booked meeting that hasn't happened yet - a
    past meeting must not block booking a new one."""
    if not lead or not lead.get("meeting_event_id") or not lead.get("meeting_start"):
        return False
    try:
        return _parse_naive(lead["meeting_start"]) > _now()
    except ValueError:
        return False


def meeting_intent(user_query: str, has_meeting: bool) -> str | None:
    """'reschedule', 'cancel', or None (which leaves the normal booking-
    keyword path in charge).

    Plain keyword matching, deliberately conservative: "cancel" only counts
    when the message also names a meeting or the lead actually has one, so
    "cancel my subscription" isn't misread as a meeting request.
    """
    if _RESCHEDULE_RE.search(user_query):
        return "reschedule"
    if _CANCEL_RE.search(user_query) and (has_meeting or _MEETING_NOUN_RE.search(user_query)):
        return "cancel"
    return None


def _workdays() -> set[int]:
    return {int(d) for d in settings.MEETING_WORKDAYS.split(",") if d.strip().isdigit()}


def find_available_slot(now: datetime.datetime | None = None) -> tuple[str, str] | None:
    """Earliest free slot, as naive Asia/Dubai (start, end) ISO strings, or
    None if the calendar can't be read or nothing is free in the search
    window. Starts from TOMORROW, within working hours on working days.

    Naive local strings on purpose - see calendar_service.create_calendar_event
    for why an offset-bearing string lands the meeting at the wrong hour.
    """
    now = now or _now()
    duration = datetime.timedelta(minutes=settings.MEETING_DURATION_MINUTES)
    workdays = _workdays()

    first_day = (now + datetime.timedelta(days=1)).date()
    window_start = datetime.datetime.combine(first_day, datetime.time(0, 0), tzinfo=DUBAI_TZ)
    window_end = window_start + datetime.timedelta(days=settings.MEETING_SEARCH_DAYS)

    busy = calendar_service.get_busy_periods(window_start.isoformat(), window_end.isoformat())
    if busy is None:
        return None  # can't verify availability - never guess a slot

    for offset in range(settings.MEETING_SEARCH_DAYS):
        day = first_day + datetime.timedelta(days=offset)
        if day.weekday() not in workdays:
            continue
        slot_start = datetime.datetime.combine(
            day, datetime.time(settings.MEETING_START_HOUR, 0), tzinfo=DUBAI_TZ
        )
        day_end = datetime.datetime.combine(
            day, datetime.time(settings.MEETING_END_HOUR, 0), tzinfo=DUBAI_TZ
        )
        while slot_start + duration <= day_end:
            slot_end = slot_start + duration
            if not any(slot_start < b_end and slot_end > b_start for b_start, b_end in busy):
                return slot_start.strftime(_ISO_FMT), slot_end.strftime(_ISO_FMT)
            slot_start += duration
    return None


def describe_slot(start_iso: str) -> str:
    """e.g. 'Monday 21 September at 10:00 (Dubai time)'."""
    start = _parse_naive(start_iso)
    return f"{start.strftime('%A')} {start.day} {start.strftime('%B')} at {start.strftime('%H:%M')} (Dubai time)"


def _fallback_instruction() -> str:
    return (
        " The client wants to book a meeting. Provide them with our fallback "
        f"booking link: {settings.BOOKING_LINK}"
    )


def _attendee_email(identifier: str, lead: dict | None) -> str | None:
    return identifier if "@" in identifier else (lead or {}).get("email")


def _book(identifier: str, lead: dict | None) -> Outcome:
    slot = find_available_slot()
    if slot is None:
        return Outcome(_fallback_instruction(), new_state="MEETING_REQUESTED")
    start_iso, end_iso = slot
    event = calendar_service.book_event(
        summary=f"Business Navigators Consultation with {identifier}",
        description=f"Consultation scheduled via WhatsApp/Email Engine for lead {identifier}.",
        start_time_iso=start_iso,
        end_time_iso=end_iso,
        attendee_email=_attendee_email(identifier, lead),
    )
    if not event:
        return Outcome(_fallback_instruction(), new_state="MEETING_REQUESTED")
    database.set_meeting(identifier, event["id"], start_iso, event.get("link"))
    return Outcome(
        " The consultation meeting has been scheduled for "
        f"{describe_slot(start_iso)}. Give the client that exact time and their "
        f"official event link: {event.get('link')}. Tell them they can ask to "
        "reschedule or cancel at any time.",
        new_state="MEETING_REQUESTED",
    )


def _reschedule(identifier: str, lead: dict) -> Outcome:
    slot = find_available_slot()
    if slot is None:
        return Outcome(
            " The client wants to reschedule but we could not find or confirm a free "
            "slot automatically. Tell them a member of the team will confirm a new time "
            f"shortly, and give them our booking link: {settings.BOOKING_LINK}",
            staff_note=f"Client asked to reschedule their meeting ({lead.get('meeting_start')}) "
            "but no free slot could be confirmed automatically.",
        )
    start_iso, end_iso = slot
    updated = calendar_service.update_event_time(lead["meeting_event_id"], start_iso, end_iso)
    if updated is None:
        return Outcome(
            " The client wants to reschedule but the calendar update failed. Tell them a "
            "member of the team will confirm the change shortly.",
            staff_note=f"Client asked to reschedule their meeting ({lead.get('meeting_start')}) "
            "but the calendar update failed - please move it manually.",
        )
    link = updated.get("link") or lead.get("meeting_link")
    database.set_meeting(identifier, lead["meeting_event_id"], start_iso, link)
    return Outcome(
        f" Their meeting has been rescheduled to {describe_slot(start_iso)}. Confirm the new "
        f"time to the client. Their event link is unchanged: {link}",
        new_state="MEETING_REQUESTED",
    )


def _cancel(identifier: str, lead: dict) -> Outcome:
    if not calendar_service.delete_event(lead["meeting_event_id"]):
        return Outcome(
            " The client wants to cancel their meeting but the calendar update failed. Tell "
            "them a member of the team will confirm the cancellation shortly.",
            staff_note=f"Client asked to CANCEL their meeting ({lead.get('meeting_start')}) "
            "but the calendar update failed - please cancel it manually.",
        )
    database.clear_meeting(identifier)
    return Outcome(
        " Their meeting has been cancelled. Confirm the cancellation and let them know they "
        "are welcome to book another time whenever they like.",
        new_state="ENGAGED",
    )


def handle(identifier: str, user_query: str, lead: dict | None, wants_booking: bool) -> Outcome | None:
    """Runs whichever meeting action this message asks for; None when it
    isn't about meetings at all (so the caller carries on as normal)."""
    active = has_active_meeting(lead)
    intent = meeting_intent(user_query, active)

    if intent == "cancel":
        if not active:
            return Outcome(
                " The client asked to cancel a meeting but we have no upcoming meeting on "
                "record for them. Tell them so, and offer to book one if they'd like."
            )
        return _cancel(identifier, lead)

    if intent == "reschedule" and active:
        return _reschedule(identifier, lead)

    if intent == "reschedule" or wants_booking:
        if active:
            # Asking again must not create a duplicate event.
            return Outcome(
                f" The client already has a meeting booked for {describe_slot(lead['meeting_start'])}. "
                f"Remind them of it (event link: {lead.get('meeting_link')}) and mention they "
                "can ask to reschedule or cancel.",
                new_state="MEETING_REQUESTED",
            )
        return _book(identifier, lead)

    return None
