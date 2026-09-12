"""
Two jobs for Gemini here:
1. extract() -- turn one raw chat message into a structured row
   (category, a short note, action items, deadline).
2. build_summary() -- turn a batch of already-logged rows into a
   readable "here's what happened" reply for the group chat.
"""
import json
from datetime import datetime, timezone

from google import genai
from google.genai import types

import config

client = genai.Client(api_key=config.GEMINI_API_KEY)

MODEL = "gemini-3.7-flash"

EXTRACT_SYSTEM_PROMPT = """You extract structured business data, events, and action items from messages in a company's internal chat for logging and calendar scheduling.
Respond ONLY with a valid JSON object (no markdown fences, no prose) matching exactly:

{{
  "category": string,        // short 1-3 word topic label, e.g. "Fundraiser", "Trading Project", "Research Project", "Customer Service", "Outreach", "Miscellaneous"
  "topic": string,           // concise 2-5 word label of the discussion/meeting/event (e.g. "Dinner at Becker Dining Hall", "Quarterly Audit")
  "notes": string,           // a short human-readable summary of the plan, decision, or reminder (under 12 words)
  "action_items": [string],  // concrete follow-up tasks mentioned (can be empty)
  "deadline": string or null,// ISO 8601 datetime (YYYY-MM-DDTHH:MM:SS) or ISO date (YYYY-MM-DD) if an event, meeting, dinner, lunch, appointment, or deadline is scheduled or agreed upon. If a specific time is mentioned (e.g. 7pm -> 19:00:00, 8pm -> 20:00:00), include the time in the ISO format!
  "location": string or null // location/venue mentioned for the event/meeting (e.g. "Becker Dining Hall", "Texas Roadhouse", "Zoom"), else null
}}

Current reference local timestamp: {now_iso}
Current local date: {today}
Target timezone: {timezone}

CRITICAL CONVERSATIONAL CONTEXT RULES:
- All meeting, lunch, and dinner times stated in chat (e.g. '7pm' -> 19:00:00, '8pm' -> 20:00:00) without an explicit timezone MUST be interpreted in the target local timezone ({timezone}).
- Output the 'deadline' as a standard ISO 8601 string (YYYY-MM-DDTHH:MM:SS), representing the exact scheduled time in {timezone}.
- You will be given RECENT CONTEXT MESSAGES (from the past 24 hours in the channel) followed by the CURRENT MESSAGE.
- Use the recent context to resolve conversational references in the current message (e.g., if a previous message said "lets get dinner on the 29th at 7" and the current message says "at texas roadhouse", synthesize the complete plan: dinner on September 29th at 7:00 PM at Texas Roadhouse).
- If the messages describe an agreed meeting, dinner, lunch, sync, or deadline, resolve the exact date/time relative to the current timestamp and set "deadline" to the ISO 8601 string (e.g. "2026-09-29T19:00:00").
- If the current message is standalone casual small talk with no scheduled events or action items, return category "Miscellaneous", topic "General", notes "", action_items: [], deadline: null, location: null.
"""

SUMMARY_SYSTEM_PROMPT = """You are summarizing a team's internal group-chat \
activity for {label}, based on the JSON log records below (each has Message, \
Category, Staff Member, Subteam, Notes, Action Items, Deadline, Date). \
Write a reply suitable for posting directly in the group chat:
- 2-4 sentence plain-language summary of what happened
- Then a bullet list of open/outstanding action items (dedupe similar ones, \
  mention who owns each if clear)
- If there are no records, say plainly that there's nothing to report for that period.
Keep the whole thing under 1500 characters. Plain text only, no markdown \
code fences, no JSON.
"""


def _call_gemini(system: str, user_content: str, max_output_tokens: int = 500) -> str:
    response = client.models.generate_content(
        model=MODEL,
        contents=user_content,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_output_tokens,
        ),
    )
    return (response.text or "").strip()


def extract(
    message_text: str,
    author: str,
    context_messages: list[dict[str, str]] | None = None,
) -> dict:
    from zoneinfo import ZoneInfo
    tz_str = getattr(config, "TIMEZONE", None) or "America/New_York"
    try:
        tz = ZoneInfo(tz_str)
    except Exception:
        tz = timezone.utc
        tz_str = "UTC"

    now_local = datetime.now(tz)
    today = now_local.strftime("%Y-%m-%d")
    now_iso = now_local.strftime("%Y-%m-%d %H:%M:%S %Z")

    system = EXTRACT_SYSTEM_PROMPT.format(today=today, now_iso=now_iso, timezone=tz_str)
    if config.CATEGORIES:
        allowed = ", ".join(config.CATEGORIES)
        system += f'\nThe "category" value MUST be exactly one of: {allowed}.'

    content_parts: list[str] = []
    if context_messages:
        content_parts.append("--- RECENT CONTEXT MESSAGES (Past 24h) ---")
        for ctx in context_messages:
            c_author = ctx.get("author") or ctx.get("user") or "Staff"
            c_text = ctx.get("text") or ctx.get("message") or ""
            c_time = ctx.get("date") or ctx.get("time") or ""
            content_parts.append(f"[{c_time}] {c_author}: {c_text}")
        content_parts.append("--- END OF CONTEXT ---\n")

    content_parts.append(f"CURRENT MESSAGE TO LOG:\nAuthor: {author}\nMessage: {message_text}")
    user_content = "\n".join(content_parts)

    text = _call_gemini(system, user_content)
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {
            "category": "Miscellaneous",
            "topic": "General",
            "notes": "",
            "action_items": [],
            "deadline": None,
            "location": None,
        }

    data.setdefault("category", "Miscellaneous")
    data.setdefault("topic", "General")
    data.setdefault("notes", "")
    data.setdefault("action_items", [])
    data.setdefault("deadline", None)
    data.setdefault("location", None)
    return data


def build_summary(records: list[dict], label: str) -> str:
    if not records:
        return f"Nothing logged for {label} yet."

    payload = json.dumps(records, indent=2, default=str)
    return _call_gemini(
        SUMMARY_SYSTEM_PROMPT.format(label=label),
        payload,
        max_output_tokens=800,
    )
