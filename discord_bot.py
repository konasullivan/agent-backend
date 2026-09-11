"""
Watches one Discord channel.
- Every human message gets logged to the sheet (Message, Category,
  Staff Member, Subteam, Date, Notes, Action Items, Deadline, Link).
- If a deadline is mentioned, a Calendar event is created -- silently,
  no reply in chat.
- The bot only ever POSTS in the channel when someone asks for a
  "summary" -- unspecified period defaults to today; "week"/"month"/
  "yesterday" pull that range instead.
"""
from datetime import datetime, timezone

import discord

import config
from ai_extractor import extract, build_summary
from sheets_service import append_record, get_records_in_range
from calendar_service import create_event
from time_utils import is_summary_request, parse_time_range

intents = discord.Intents.default()
intents.message_content = True  # must also be enabled in the Dev Portal

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"Logged in as {client.user}. Watching channel {config.DISCORD_CHANNEL_ID}.")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.id != config.DISCORD_CHANNEL_ID:
        return
    if not message.content.strip():
        return

    author_name = str(message.author)
    data = extract(message.content, author_name)

    record = {
        "Message": message.content,
        "Category": data.get("category", "Miscellaneous"),
        "Staff Member": author_name,
        "Subteam": config.get_subteam(author_name),
        "Date": message.created_at.astimezone(timezone.utc).isoformat(),
        "Notes": data.get("notes", ""),
        "Action Items": "; ".join(data.get("action_items", [])),
        "Deadline": data.get("deadline") or "",
        "Link": message.jump_url,
    }
    append_record(record)  # every message gets logged, no exceptions

    if record["Deadline"]:
        create_event(
            summary=data.get("notes") or record["Category"],
            description=f"From Discord message by {author_name}: {message.content}",
            date_str=record["Deadline"],
        )

    # The bot only ever speaks up when asked for a summary.
    if is_summary_request(message.content):
        start, end, label = parse_time_range(message.content)
        records = get_records_in_range(start, end)
        summary_text = build_summary(records, label)
        await message.channel.send(summary_text)


if __name__ == "__main__":
    client.run(config.DISCORD_BOT_TOKEN)
