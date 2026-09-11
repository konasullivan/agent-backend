import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")

# Column order in the Google Sheet -- mirrors the "Business Chat Record" table.
# "Action Items" and "Deadline" aren't in the reference table but are kept as
# extra columns so the bot can build summaries and calendar events without
# re-calling Claude on every summary request.
SHEET_HEADERS = [
    "Message",
    "Category",
    "Staff Member",
    "Subteam",
    "Date",
    "Notes",
    "Action Items",
    "Deadline",
    "Link",
]

# Discord doesn't know which "subteam" someone is on -- fill this in with
# your real staff list. Key = str(message.author) (their Discord username).
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
