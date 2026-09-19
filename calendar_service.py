import datetime
from zoneinfo import ZoneInfo

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from config import settings

SCOPES = ['https://www.googleapis.com/auth/calendar']
DUBAI_TZ = 'Asia/Dubai'


def get_calendar_service():
    creds = Credentials.from_service_account_file(
        settings.GOOGLE_CREDENTIALS_FILE, scopes=SCOPES
    )
    service = build('calendar', 'v3', credentials=creds)
    return service


def _calendar_id(calendar_id: str | None) -> str:
    """calendar_id defaults to settings.GOOGLE_CALENDAR_ID - "primary" is the
    service account's OWN calendar, invisible to any human consultant unless
    explicitly shared, so a real deployment should set GOOGLE_CALENDAR_ID to
    the consultant/shared calendar's id."""
    return calendar_id or settings.GOOGLE_CALENDAR_ID


def _http_status(exc: Exception) -> int | None:
    """HTTP status of a googleapiclient HttpError, without importing the
    library's error type here (keeps this module's imports minimal)."""
    resp = getattr(exc, 'resp', None)
    return getattr(resp, 'status', None)


def book_event(
    summary: str,
    description: str,
    start_time_iso: str,
    end_time_iso: str,
    attendee_email: str = None,
    calendar_id: str = None,
) -> dict | None:
    """Creates a consultation event; returns {"id", "link"} or None on failure.

    start_time_iso / end_time_iso must already represent the intended wall-
    clock time in Asia/Dubai - pass a naive ISO string (no UTC offset) built
    directly in that timezone, not a UTC time relabeled as Dubai. Google
    interprets any offset present in the string literally, so a UTC-offset
    string here previously landed the meeting 4 hours later than intended.
    """
    try:
        service = get_calendar_service()
        event = {
            'summary': summary,
            'description': description,
            'start': {'dateTime': start_time_iso, 'timeZone': DUBAI_TZ},
            'end': {'dateTime': end_time_iso, 'timeZone': DUBAI_TZ},
        }
        if attendee_email:
            event['attendees'] = [{'email': attendee_email}]

        created = (
            service.events()
            .insert(
                calendarId=_calendar_id(calendar_id),
                body=event,
                sendUpdates='all' if attendee_email else 'none',
            )
            .execute()
        )
        return {'id': created.get('id'), 'link': created.get('htmlLink')}
    except Exception as e:
        print(f"[CALENDAR ERROR] {e}")
        return None


def create_calendar_event(*args, **kwargs):
    """Backwards-compatible wrapper around book_event() returning just the
    event's link (or None on failure)."""
    event = book_event(*args, **kwargs)
    return event['link'] if event else None


def get_busy_periods(
    time_min_iso: str, time_max_iso: str, calendar_id: str = None
) -> list[tuple[datetime.datetime, datetime.datetime]] | None:
    """Busy intervals on the consultant calendar between the two (offset-
    bearing) ISO timestamps, as timezone-aware datetimes in Asia/Dubai.

    Returns None - NOT an empty list - if the calendar couldn't be read, so
    callers can't mistake "unknown" for "free" and double-book someone.
    """
    cal_id = _calendar_id(calendar_id)
    try:
        service = get_calendar_service()
        result = (
            service.freebusy()
            .query(body={
                'timeMin': time_min_iso,
                'timeMax': time_max_iso,
                'timeZone': DUBAI_TZ,
                'items': [{'id': cal_id}],
            })
            .execute()
        )
        calendar = result.get('calendars', {}).get(cal_id, {})
        if calendar.get('errors'):
            # Google reports per-calendar failures (e.g. not shared with the
            # service account) inside a 200 response.
            print(f"[CALENDAR ERROR] freebusy: {calendar['errors']}")
            return None
        tz = ZoneInfo(DUBAI_TZ)
        return [
            (
                datetime.datetime.fromisoformat(b['start']).astimezone(tz),
                datetime.datetime.fromisoformat(b['end']).astimezone(tz),
            )
            for b in calendar.get('busy', [])
        ]
    except Exception as e:
        print(f"[CALENDAR ERROR] {e}")
        return None


def update_event_time(
    event_id: str, start_time_iso: str, end_time_iso: str, calendar_id: str = None
) -> dict | None:
    """Moves an existing event (attendees are re-notified). Returns
    {"link": ...} on success, None on failure. Same naive-Dubai-time rule as
    book_event()."""
    try:
        service = get_calendar_service()
        updated = (
            service.events()
            .patch(
                calendarId=_calendar_id(calendar_id),
                eventId=event_id,
                body={
                    'start': {'dateTime': start_time_iso, 'timeZone': DUBAI_TZ},
                    'end': {'dateTime': end_time_iso, 'timeZone': DUBAI_TZ},
                },
                sendUpdates='all',
            )
            .execute()
        )
        return {'link': updated.get('htmlLink')}
    except Exception as e:
        print(f"[CALENDAR ERROR] {e}")
        return None


def delete_event(event_id: str, calendar_id: str = None) -> bool:
    """Cancels an event (attendees are notified). An event that's already
    gone (404/410 - e.g. staff removed it by hand) counts as success: the
    end state the caller wants is reached."""
    try:
        service = get_calendar_service()
        service.events().delete(
            calendarId=_calendar_id(calendar_id), eventId=event_id, sendUpdates='all'
        ).execute()
        return True
    except Exception as e:
        if _http_status(e) in (404, 410):
            return True
        print(f"[CALENDAR ERROR] {e}")
        return False
