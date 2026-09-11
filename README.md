# AI Interaction Agent (Demo)

Watches a Discord group chat, logs **every** message to a Google Sheet
(stand-in for the ERP) with an AI-assigned category and a short note,
auto-creates a Google Calendar event when a deadline is mentioned, and
only ever speaks up in the chat when someone asks it for a summary.

```
agent_backend/
├── config.py             # env vars + sheet schema + staff->subteam mapping
├── time_utils.py          # detects "!summarize" + parses the time range
├── ai_extractor.py        # Claude calls: per-message extraction + summary writing
├── sheets_service.py      # Google Sheets read/write ("ERP" for the demo)
├── calendar_service.py    # Google Calendar event creation
├── discord_bot.py         # the bot that watches the group chat
├── dashboard/
│   ├── app.py               # FastAPI app serving the dashboard
│   └── templates/index.html
├── requirements.txt
└── .env.example
```

## Behavior

- **Every message in the watched channel gets logged**, no filtering —
  each row gets: Message, Category (AI-guessed topic like "Fundraiser"
  or "Trading Project"), Staff Member (author), Subteam (looked up from
  `config.STAFF_SUBTEAM`, not guessed), Date, Notes (short AI note),
  Action Items, Deadline, Link (jump-to-message link).
- **The bot never replies to a normal message.** The only time it posts
  in the channel is when a message starts with `!summarize`.
  - `!summarize` alone → today's summary + action items.
  - `!summarize week` → past 7 days.
  - `!summarize yesterday` / `!summarize month` also work.
  - Add more phrasing rules in `time_utils.parse_time_range()`.
- Deadlines mentioned in any message still silently create a Calendar
  event — no chat reply for that either.

## 1. Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in real values
```

## 2. What to fill in `.env`

- `DISCORD_BOT_TOKEN` — create an app + bot at
  https://discord.com/developers/applications, enable "Message Content
  Intent" under Bot settings, invite it with `View Channels` +
  `Read Message History` + `Send Messages` permissions.
- `ANTHROPIC_API_KEY` — from console.anthropic.com.
- `GOOGLE_SERVICE_ACCOUNT_FILE` — service-account JSON key (Google
  Cloud Console → IAM → Service Accounts). Share your target Google
  Sheet AND Google Calendar with that service account's email.
- `GOOGLE_SHEET_ID` — from the sheet's URL.
- `GOOGLE_CALENDAR_ID` — usually your Gmail address.
- `DISCORD_CHANNEL_ID` — the channel to watch (enable Developer Mode
  in Discord, then right-click the channel → Copy Channel ID).

Also fill in `config.STAFF_SUBTEAM` with your real staff's Discord
usernames mapped to their subteam — Discord has no concept of "subteam"
on its own, so this has to be a manual lookup table.

## 3. Run it

Terminal 1 — the bot:
```bash
python discord_bot.py
```

Terminal 2 — the dashboard:
```bash
uvicorn dashboard.app:app --reload --port 8000
```
Then open http://localhost:8000

## 4. Later

| Demo piece            | Later becomes                              |
|-------------------------|---------------------------------------------|
| Google Sheets            | Real ERP (via its REST API)                |
| `config.STAFF_SUBTEAM`   | Pulled from the ERP/HR system instead      |
| Single Discord channel   | Multiple channels/servers                  |
| Keyword summary trigger  | Slash command (`/summary week`) if wanted  |

Only `sheets_service.py` needs to change when you swap in a real ERP —
`ai_extractor.py`, `calendar_service.py`, and `discord_bot.py` stay as-is.
