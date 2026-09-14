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


def create_calendar_event(
    summary: str,
    description: str,
    start_time_iso: str,
    end_time_iso: str,
    attendee_email: str = None,
    calendar_id: str = None,
):
    """Creates a consultation event on Google Calendar.

    start_time_iso / end_time_iso must already represent the intended wall-
    clock time in Asia/Dubai - pass a naive ISO string (no UTC offset) built
    directly in that timezone, not a UTC time relabeled as Dubai. Google
    interprets any offset present in the string literally, so a UTC-offset
    string here previously landed the meeting 4 hours later than intended.

    calendar_id defaults to settings.GOOGLE_CALENDAR_ID - "primary" is the
    service account's OWN calendar, invisible to any human consultant unless
    explicitly shared, so a real deployment should set GOOGLE_CALENDAR_ID to
    the consultant/shared calendar's id.
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

        created_event = (
            service.events()
            .insert(
                calendarId=calendar_id or settings.GOOGLE_CALENDAR_ID,
                body=event,
                sendUpdates='all' if attendee_email else 'none',
            )
            .execute()
        )
        return created_event.get('htmlLink')
    except Exception as e:
        print(f"[CALENDAR ERROR] {e}")
        return None
