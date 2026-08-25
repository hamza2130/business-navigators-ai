import io
import re
from PIL import Image
import pytesseract
import requests

# Set Tesseract executable path for Windows environment
pytesseract.pytesseract.tesseract_cmd = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe"
)


def parse_expiry_date(text: str) -> str | None:
    """Extracts date in YYYY-MM-DD or DD/MM/YYYY format from document text and

    normalizes it to YYYY-MM-DD format.
    """
    if not text:
        return None

    # Pattern for YYYY-MM-DD or YYYY/MM/DD
    match = re.search(
        r"\b(20\d{2})[-/](0[1-9]|1[0-2])[-/](0[1-9]|[12]\d|3[01])\b", text
    )
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

    # Pattern for DD/MM/YYYY or DD-MM-YYYY
    match_alt = re.search(
        r"\b(0[1-9]|[12]\d|3[01])[-/](0[1-9]|1[0-2])[-/](20\d{2})\b", text
    )
    if match_alt:
        return f"{match_alt.group(3)}-{match_alt.group(2)}-{match_alt.group(1)}"

    return None


def validate_document(image_bytes: bytes) -> dict:
    """Validates document byte stream uploaded directly via REST API endpoints

    and extracts compliance expiry date if found.
    """
    try:
        image = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(image).strip()

        if len(text) > 10:
            expiry_date = parse_expiry_date(text)
            return {
                "status": "success",
                "is_legible": True,
                "extracted_text": text,
                "expiry_date": expiry_date,
            }
        else:
            return {
                "status": "warning",
                "is_legible": False,
                "message": "Image text could not be clearly extracted. Please provide a clearer scan.",
                "expiry_date": None,
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Failed to process document: {str(e)}",
            "expiry_date": None,
        }


def extract_text_from_image_url(
    image_url: str, twilio_sid: str = "", twilio_token: str = ""
) -> str:
    """Downloads an image from a Twilio Media URL and extracts text using Tesseract

    OCR.
    """
    try:
        # Authenticate with Twilio credentials to fetch media attachments
        auth = (twilio_sid, twilio_token) if twilio_sid and twilio_token else None
        response = requests.get(image_url, auth=auth)

        if response.status_code == 200:
            image = Image.open(io.BytesIO(response.content))
            extracted_text = pytesseract.image_to_string(image)
            return extracted_text.strip()
        else:
            print(f"Failed to fetch image: HTTP {response.status_code}")
            return ""
    except Exception as e:
        print(f"OCR Exception: {e}")
        return ""