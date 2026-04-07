"""
Speech-to-Text and LLM Evaluation Service
Uses OpenAI Whisper for transcription and GPT-3.5-turbo for feedback.
"""
import os
import json
import requests
from typing import Optional

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_BASE = "https://api.openai.com/v1"

PROMPT_TEMPLATE = """You are a friendly English teacher helping a Mozambican student.

IMPORTANT:
- Use very simple English
- Be encouraging
- Do not be too strict
- Focus on clarity

TASK:
1. Correct the sentence
2. Explain grammar simply
3. Give pronunciation tips (simple sounds)
4. Give 2 similar example sentences
5. Give a grammar_score (0-10) and pronunciation_score (0-10)

User sentence:
"{user_input}"

Return JSON ONLY (no extra text):

{{
  "corrected": "",
  "grammar": "",
  "pronunciation": "",
  "examples": ["", ""],
  "grammar_score": 0,
  "pronunciation_score": 0
}}"""


def transcribe_audio(audio_bytes: bytes, filename: str = "audio.m4a") -> str:
    """Send audio to OpenAI Whisper and return transcript."""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set")

    response = requests.post(
        f"{OPENAI_API_BASE}/audio/transcriptions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        files={"file": (filename, audio_bytes, "audio/m4a")},
        data={"model": "whisper-1", "language": "en"},
        timeout=30
    )

    if response.status_code != 200:
        raise ValueError(f"Whisper error: {response.text}")

    return response.json().get("text", "")


def evaluate_text(user_input: str) -> dict:
    """Send transcript to GPT-3.5-turbo and return structured feedback."""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set")

    prompt = PROMPT_TEMPLATE.format(user_input=user_input)

    response = requests.post(
        f"{OPENAI_API_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 300
        },
        timeout=30
    )

    if response.status_code != 200:
        raise ValueError(f"OpenAI error: {response.text}")

    content = response.json()["choices"][0]["message"]["content"].strip()

    # Strip markdown code blocks if present
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Fallback if JSON is malformed
        return {
            "corrected": user_input,
            "grammar": "Could not evaluate. Please try again.",
            "pronunciation": "",
            "examples": [],
            "grammar_score": 5,
            "pronunciation_score": 5
        }


CHAT_SYSTEM_PROMPT = """You are an English conversation coach for a Mozambican student. Have a natural conversation in English AND help them improve each turn.

After each student message return ONLY this JSON (no markdown):
{
  "reply": "1-2 sentence English reply that continues the conversation naturally and asks a follow-up question",
  "correction": "the student's sentence corrected, or null if there are no errors",
  "tip": "one short English challenge — suggest a better word or more natural phrase they could use (e.g. 'Try: I am doing well, thank you!')",
  "explanation": "2-3 sentences in simple Mozambican Portuguese explaining the grammar or vocabulary point. Be encouraging. Example: 'Em inglês usamos o verbo TO BE (am/is/are) para descrever estados. Diz I AM fine, não I IS fine. Continue assim!'"
}"""


def chat_reply(history: list) -> dict:
    """Generate a conversational reply with optional grammar correction."""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set")

    response = requests.post(
        f"{OPENAI_API_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "system", "content": CHAT_SYSTEM_PROMPT}] + history,
            "temperature": 0.7,
            "max_tokens": 150,
        },
        timeout=25,
    )

    if response.status_code != 200:
        raise ValueError(f"OpenAI error: {response.text}")

    content = response.json()["choices"][0]["message"]["content"].strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"reply": content, "correction": None}


CONVERSATIONS = [
    {"id": 1, "scenario": "greeting",     "question": "Hello! How are you today?"},
    {"id": 2, "scenario": "food",         "question": "What did you eat today?"},
    {"id": 3, "scenario": "location",     "question": "Where do you live?"},
    {"id": 4, "scenario": "work",         "question": "What do you do?"},
    {"id": 5, "scenario": "likes",        "question": "Do you like music?"},
    {"id": 6, "scenario": "friends",      "question": "Who is your best friend?"},
    {"id": 7, "scenario": "daily_action", "question": "What do you do every day?"},
    {"id": 8, "scenario": "past_action",  "question": "What did you do yesterday?"},
]
