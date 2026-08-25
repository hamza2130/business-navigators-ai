# config.py
import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

    # Meta WhatsApp Cloud API
    META_WA_TOKEN: str = os.getenv("META_WA_TOKEN", "")
    META_PHONE_NUMBER_ID: str = os.getenv("META_PHONE_NUMBER_ID", "")
    META_VERIFY_TOKEN: str = os.getenv("VERIFY_TOKEN", "my_secret_fastapi_verify_token")
    META_API_VERSION: str = os.getenv("META_API_VERSION", "v20.0")

    BREVO_API_KEY: str = os.getenv("BREVO_API_KEY", "")
    SENDER_EMAIL: str = os.getenv("SENDER_EMAIL", "")
    
    # Milestone 1: Meeting booking link
    BOOKING_LINK: str = os.getenv("BOOKING_LINK", "https://calendly.com/your-business-navigators/consultation")

settings = Settings()