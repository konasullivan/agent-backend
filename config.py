import os
from dotenv import load_dotenv

load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = os.getenv("SLACK_APP_TOKEN")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or "placeholder-gemini-key"

GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")

# Qdrant Vector Search
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "conversations")

# Column order in the Google Sheet -- mirrors the "Business Chat Record" table.
# "Action Items" and "Deadline" aren't in the reference table but are kept as
# extra columns so the bot can build summaries and calendar events without
# re-calling Claude on every summary request.
SHEET_HEADERS = [
    "Message",
    "Category",
    "Conversation ID",
    "Conversation Topic",
    "Staff Member",
    "Subteam",
    "Date",
    "Notes",
    "Action Items",
    "Deadline",
    "Link",
]

# Slack doesn't know which "subteam" someone is on -- fill this in with
# your real staff list. Key = author display name or username.
# Anyone not listed falls back to "Unassigned".
STAFF_SUBTEAM = {
    # "maria.e": "Open Source",
    # "cory_emil": "Outreach",
    # "adamsilvernail": "Leadership",
}


def get_subteam(author_name: str) -> str:
    return STAFF_SUBTEAM.get(author_name, "Unassigned")


CATEGORIES: list[str] = [
    "Fundraiser",
    "Trading Project",
    "Research Project",
    "Outreach",
    "Customer Service",
    "Miscellaneous",
]
