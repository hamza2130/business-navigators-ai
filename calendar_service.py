import datetime
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/calendar']
SERVICE_ACCOUNT_FILE = 'credentials.json' # Path to your Google Cloud service account key

def get_calendar_service():
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    service = build('calendar', 'v3', credentials=creds)
    return service

def create_calendar_event(summary: str, description: str, start_time_iso: str, end_time_iso: str, calendar_id: str = "primary"):
    """Creates a consultation event directly on Google Calendar."""
    try:
        service = get_calendar_service()
        event = {
            'summary': summary,
            'description': description,
            'start': {'dateTime': start_time_iso, 'timeZone': 'Asia/Dubai'},
            'end': {'dateTime': end_time_iso, 'timeZone': 'Asia/Dubai'},
        }
        created_event = service.events().insert(calendarId=calendar_id, body=event).execute()
        return created_event.get('htmlLink')
    except Exception as e:
        print(f"[CALENDAR ERROR] {e}")
        return None