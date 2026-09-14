import io
import os
from docx import Document
import openpyxl
from pdf2image import convert_from_bytes
from PIL import Image
import pypdf
import pytesseract
import requests

from config import settings

# Hard cap on any document/media we download or accept for parsing. Without
# this, a large or malicious upload can exhaust memory/disk on the server -
# there was previously no limit at all.
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024  # 20 MB


def _download_meta_media(media_id: str) -> tuple:
    """Meta gives you a media_id, not a direct URL. Two-step download:
    1. GET /{media_id} with a Bearer token -> returns a short-lived signed URL
    2. GET that signed URL, also with the Bearer token -> returns the bytes
    """
    headers = {"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"}
    lookup_url = f"https://graph.facebook.com/{settings.META_API_VERSION}/{media_id}"

    lookup_resp = requests.get(lookup_url, headers=headers, timeout=15)
    if lookup_resp.status_code != 200:
        print(f"Failed to resolve media URL: {lookup_resp.status_code} {lookup_resp.text}")
        return b"", ""

    media_info = lookup_resp.json()
    download_url = media_info.get("url")
    mime_type = media_info.get("mime_type", "")
    reported_size = media_info.get("file_size")
    if not download_url:
        return b"", ""
    if reported_size and int(reported_size) > MAX_DOCUMENT_BYTES:
        print(f"Rejected media {media_id}: reported size {reported_size} exceeds cap")
        return b"", ""

    file_resp = requests.get(download_url, headers=headers, timeout=30, stream=True)
    if file_resp.status_code != 200:
        print(f"Failed to download media file: {file_resp.status_code}")
        return b"", ""

    content = bytearray()
    for chunk in file_resp.iter_content(chunk_size=65536):
        content.extend(chunk)
        if len(content) > MAX_DOCUMENT_BYTES:
            print(f"Rejected media {media_id}: exceeded {MAX_DOCUMENT_BYTES} byte cap mid-download")
            return b"", ""

    return bytes(content), mime_type


def _parse_bytes_content(content: bytes, content_type: str) -> str:
    """Core parser engine that extracts text from raw byte buffers."""
    content_type = (content_type or "").lower()

    # 1. Plain Text
    if "text/plain" in content_type or content_type.endswith(".txt"):
        return content.decode("utf-8", errors="ignore").strip()

    # 2. Word Documents (.docx / .doc)
    elif "word" in content_type or "officedocument" in content_type or content_type.endswith(".docx"):
        try:
            doc = Document(io.BytesIO(content))
            full_text = [para.text for para in doc.paragraphs if para.text]
            return "\n".join(full_text).strip()
        except Exception as e:
            print(f"Docx parsing error: {e}")
            return content.decode("utf-8", errors="ignore").strip()

    # 3. Excel Spreadsheets (.xlsx)
    elif "excel" in content_type or "spreadsheetml" in content_type or content_type.endswith(".xlsx"):
        try:
            wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
            lines = []
            for sheet in wb.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    row_vals = [str(cell) for cell in row if cell is not None]
                    if row_vals:
                        lines.append(" ".join(row_vals))
            return "\n".join(lines).strip()
        except Exception as e:
            print(f"Excel parsing error: {e}")
            return ""

    # 4. PDF Files - Native text extraction + scanned OCR fallback
    elif "pdf" in content_type or content_type.endswith(".pdf"):
        extracted_text = ""
        try:
            pdf_reader = pypdf.PdfReader(io.BytesIO(content))
            for page in pdf_reader.pages:
                text = page.extract_text()
                if text:
                    extracted_text += text + "\n"
        except Exception as e:
            print(f"pypdf extraction error: {e}")

        if not extracted_text.strip():
            print("[PDF OCR] No native text found. Running Tesseract OCR on scanned PDF pages...")
            try:
                images = convert_from_bytes(content)
                for img in images:
                    extracted_text += pytesseract.image_to_string(img) + "\n"
            except Exception as ocr_err:
                print(f"PDF OCR conversion error: {ocr_err}")

        return extracted_text.strip()

    # 5. Images (JPG, PNG, JPEG) -> Tesseract OCR
    else:
        try:
            image = Image.open(io.BytesIO(content))
            return pytesseract.image_to_string(image).strip()
        except Exception as e:
            print(f"Image OCR error: {e}")
            return ""


def extract_text_from_attachment(
    media_id: str = None,
    media_content_type: str = "",
    file_path: str = None,
    auth=None
) -> str:
    """Unified entry point: accepts local file paths or Meta WhatsApp media_ids."""
    try:
        # Local File Upload (from /admin/test-document-upload)
        if file_path:
            if not os.path.exists(file_path):
                print(f"[ERROR] File not found: {file_path}")
                return ""

            with open(file_path, "rb") as f:
                content = f.read()

            file_ext = os.path.splitext(file_path)[1].lower()
            return _parse_bytes_content(content, content_type=file_ext)

        # Meta WhatsApp Media via Graph API
        elif media_id:
            content, resolved_mime = _download_meta_media(media_id)
            if not content:
                return ""

            target_mime = media_content_type or resolved_mime
            return _parse_bytes_content(content, content_type=target_mime)

    except Exception as e:
        print(f"Document Parsing Exception: {e}")

    return ""