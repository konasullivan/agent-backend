"""
Two jobs for Claude here:
1. extract() -- turn one raw chat message into a structured row
   (category, a short note, action items, deadline).
2. build_summary() -- turn a batch of already-logged rows into a
   readable "here's what happened" reply for the group chat.
"""
import json
from datetime import datetime, timezone

from anthropic import Anthropic

import config

client = Anthropic(api_key=config.ANTHROPIC_API_KEY)

EXTRACT_SYSTEM_PROMPT = """You extract structured data from a single message \
in a company's internal group chat, for a records/action-item log. \
Respond ONLY with a JSON object, no prose, no markdown fences, matching \
exactly this shape:

{
  "category": string,        // short 1-3 word topic label, e.g. "Fundraiser", "Trading Project", "Research Project", "Miscellaneous" -- reuse an existing-sounding label when the topic matches something recurring, otherwise invent a short sensible one
  "notes": string,           // a short (under 8 words) human-readable note/reminder if this message is worth flagging, else ""
  "action_items": [string],  // concrete follow-up tasks mentioned, can be empty
  "deadline": string or null // ISO date (YYYY-MM-DD) if a deadline/date is mentioned, else null
}

Today's date is {today} -- resolve relative dates ("next Friday", "in 3 days") \
against it. If the message is pure small talk with nothing worth logging, \
still return valid JSON with category "Miscellaneous", notes "", \
action_items: [], deadline: null.
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


def _call_claude(system: str, user_content: str, max_tokens: int = 500) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()


def extract(message_text: str, author: str) -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    system = EXTRACT_SYSTEM_PROMPT.format(today=today)
    if config.CATEGORIES:
        allowed = ", ".join(config.CATEGORIES)
        system += f'\nThe "category" value MUST be exactly one of: {allowed}.'

    text = _call_claude(system, f"Message from {author}: {message_text}")
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"category": "Miscellaneous", "notes": "", "action_items": [], "deadline": None}

    data.setdefault("category", "Miscellaneous")
    data.setdefault("notes", "")
    data.setdefault("action_items", [])
    data.setdefault("deadline", None)
    return data


def build_summary(records: list[dict], label: str) -> str:
    if not records:
        return f"Nothing logged for {label} yet."

    payload = json.dumps(records, indent=2, default=str)
    return _call_claude(
        SUMMARY_SYSTEM_PROMPT.format(label=label),
        payload,
        max_tokens=800,
    )
