from groq import Groq
from config import settings
from kb_service import get_active_knowledge_context

client = Groq(api_key=settings.GROQ_API_KEY)


def build_system_prompt() -> str:
    """Pull dynamic knowledge base items live from SQLite database."""
    kb_context = get_active_knowledge_context()

    return f"""
You are the official Business Navigators AI Assistant. Your role is to assist prospective clients with UAE business setup, FTA tax compliance, and visa inquiry services.

CRITICAL INSTRUCTIONS:
- Base your answers accurately on the official Business Navigators Knowledge Base below.
- Do not make up rules or pricing not present in the Knowledge Base.

=== OFFICIAL KNOWLEDGE BASE ===
{kb_context}
================================

Guidelines:
1. Keep responses concise, clear, and structured for WhatsApp (use bullet points or bold text where appropriate).
2. Provide accurate, professional, and helpful responses based on the knowledge base above.
3. Naturally gather key lead details during the conversation (Client Name, Business Activity, Preferred Service Package, Timeline).
4. If a client asks for a meeting or consultation, initiate the meeting booking process.
5. If a required document is needed, prompt the client to upload it via WhatsApp/Email.
"""


def generate_ai_response(
    user_message: str, conversation_history: list = None
) -> str:
    try:
        # Dynamically build system prompt with fresh KB context
        system_prompt = build_system_prompt()

        messages = [{"role": "system", "content": system_prompt}]

        if conversation_history:
            messages.extend(conversation_history)

        messages.append({"role": "user", "content": user_message})

        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            temperature=0.7,
            max_tokens=500,
        )

        return completion.choices[0].message.content

    except Exception as e:
        error_msg = str(e)
        print(
            f"\n================ GROQ API ERROR ================\n{error_msg}\n================================================\n"
        )
        return "Our AI assistant is temporarily busy. Please try again in a moment!"