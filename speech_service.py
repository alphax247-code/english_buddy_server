"""
Speech-to-Text and LLM Evaluation Service
Uses OpenAI Whisper for transcription and GPT-3.5-turbo for feedback.
"""
import os
import json
import random
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


CHAT_SYSTEM_PROMPT = """You are a friendly English conversation partner for a Mozambican student doing a 2-minute speaking practice. Keep the conversation flowing naturally and fast.

Rules:
- Reply in 1-2 short sentences MAX — keep it snappy and engaging
- Always end with a question to keep the conversation going
- If the student made a grammar error, silently use the correct form in your reply so they hear it naturally — do NOT lecture them
- Match their energy: if they are short, be short back; if they expand, engage more

Return ONLY this JSON (no markdown, no extra text):
{"reply": "your reply here", "correction": "corrected sentence or null"}"""


def chat_reply(history: list) -> dict:
    """Generate a fast conversational reply."""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set")

    response = requests.post(
        f"{OPENAI_API_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "system", "content": CHAT_SYSTEM_PROMPT}] + history[-10:],
            "temperature": 0.8,
            "max_tokens": 120,
        },
        timeout=20,
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


# ── Level-based random topics ────────────────────────────────────────────────
_TOPICS = {
    "beginner": [
        ("greeting",     "Hello! Tell me your name and how you are feeling today."),
        ("family",       "Can you tell me about your family?"),
        ("daily",        "What do you do every morning before school or work?"),
        ("food",         "What is your favourite food? Why do you like it?"),
        ("colours",      "Can you describe what you are wearing right now?"),
        ("numbers",      "How many people live in your house?"),
    ],
    "intermediate": [
        ("weekend",      "What did you do last weekend? Tell me everything!"),
        ("travel",       "Describe a place in Mozambique you would like to visit."),
        ("future",       "What do you want to do when you finish school?"),
        ("hobbies",      "What do you enjoy doing in your free time and why?"),
        ("city",         "Tell me about the city or town where you live."),
        ("friendship",   "Tell me about your best friend — what are they like?"),
    ],
    "advanced": [
        ("technology",   "How has technology changed the way young people communicate?"),
        ("environment",  "What can ordinary people do to help protect the environment?"),
        ("culture",      "How is Mozambican culture different from Western culture?"),
        ("goals",        "Where do you see yourself in five years, and how will you get there?"),
        ("social_media", "Do you think social media is good or bad for young people? Why?"),
        ("education",    "What changes would you make to improve education in Mozambique?"),
    ],
}

def get_random_topic(level: int) -> dict:
    if level <= 2:
        pool = _TOPICS["beginner"]
    elif level <= 5:
        pool = _TOPICS["intermediate"]
    else:
        pool = _TOPICS["advanced"]
    scenario, question = random.choice(pool)
    return {"scenario": scenario, "question": question}


# ── Session analysis ──────────────────────────────────────────────────────────
_ANALYSIS_PROMPT = """You are an English teacher reviewing a 2-minute spoken practice session by a Mozambican student. Go through every single student message and give detailed feedback.

Return ONLY this JSON (no markdown):
{
  "score": 7,
  "strengths": ["one specific thing they did well", "another strength"],
  "improvements": ["one area to work on", "another"],
  "tip": "The single most important grammar or vocabulary tip from this session, in English",
  "tip_pt": "The same tip in simple Mozambican Portuguese, warm and encouraging",
  "turn_feedback": [
    {
      "turn": 1,
      "original": "exactly what the student said",
      "correction": "the fully corrected sentence, or null if perfect",
      "error_type": "short label e.g. 'Verb tense', 'Missing article', 'Word order', or null",
      "explanation": "1-2 sentences in simple Mozambican Portuguese explaining what was wrong and why — warm teacher tone",
      "suggestion": "a richer, more natural English version they could try — a complete sentence that expands on what they said"
    }
  ]
}
Rules:
- turn_feedback must have exactly one entry per student (user) message, in order
- If the sentence had no errors, set correction and error_type and explanation to null but still provide a suggestion to help them say more
- Score 1-10: grammar accuracy + vocabulary range + sentence complexity. Be honest but kind."""


def analyze_session(history: list) -> dict:
    """GPT analyses the full session and returns a score + feedback."""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not set")
    student_turns = [m for m in history if m.get("role") == "user"]
    if not student_turns:
        return {"score": 0, "strengths": [], "improvements": [], "tip": "", "tip_pt": ""}

    response = requests.post(
        f"{OPENAI_API_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": _ANALYSIS_PROMPT},
                {"role": "user", "content": json.dumps(history, ensure_ascii=False)},
            ],
            "temperature": 0.3,
            "max_tokens": 1200,
        },
        timeout=30,
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
        return {"score": 5, "strengths": [], "improvements": [], "tip": content, "tip_pt": ""}


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
