#!/usr/bin/env python3
"""One-off migration script to create 'ChatRecords' tab and migrate rows from 'Sheet1'.

- Creates 'ChatRecords' worksheet tab with canonical 11-column headers:
  ['Message', 'Category', 'Conversation ID', 'Conversation Topic', 'Staff Member', 'Subteam', 'Date', 'Notes', 'Action Items', 'Deadline', 'Link']
- Does NOT delete or alter 'Sheet1'.
- Resolves Slack user names using users_info (now that users:read is active).
- Re-aligns legacy 7-column rows and preserves 11-column test rows.
- Appends all migrated rows to 'ChatRecords'.
"""

import os
import sys
from pathlib import Path

# Ensure root directory is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import config
from src.services.google_services import GoogleWorkspaceService
from slack_sdk import WebClient


def resolve_slack_user_name(client: WebClient, user_id: str) -> str:
    """Attempt to resolve Slack user ID to real or display name."""
    if not user_id or not user_id.startswith("U"):
        return user_id
    try:
        res = client.users_info(user=user_id)
        if res.get("ok"):
            user = res.get("user", {})
            profile = user.get("profile", {})
            name = (
                profile.get("display_name")
                or user.get("real_name")
                or profile.get("real_name")
                or user.get("name")
                or user_id
            )
            return name
    except Exception as exc:
        print(f"[-] Could not resolve user {user_id} via Slack API: {exc}")
    return user_id


def main():
    print("[*] Initializing Google Workspace Service...")
    svc = GoogleWorkspaceService()
    sheets_client = svc.sheets_service.spreadsheets()
    spreadsheet_id = config.GOOGLE_SHEET_ID
    target_tab = "ChatRecords"

    # Initialize Slack client for user resolution
    slack_client = None
    if config.SLACK_BOT_TOKEN:
        slack_client = WebClient(token=config.SLACK_BOT_TOKEN)

    # 1. Inspect existing tabs
    spreadsheet = sheets_client.get(spreadsheetId=spreadsheet_id).execute()
    existing_tabs = [
        s.get("properties", {}).get("title")
        for s in spreadsheet.get("sheets", [])
    ]
    print(f"[*] Found existing tabs: {existing_tabs}")

    # 2. Create 'ChatRecords' if not present
    if target_tab not in existing_tabs:
        print(f"[*] Creating new tab '{target_tab}'...")
        add_req = {
            "requests": [
                {
                    "addSheet": {
                        "properties": {
                            "title": target_tab,
                        }
                    }
                }
            ]
        }
        sheets_client.batchUpdate(spreadsheetId=spreadsheet_id, body=add_req).execute()
        print(f"[+] Tab '{target_tab}' created successfully.")
    else:
        print(f"[*] Tab '{target_tab}' already exists.")

    # 3. Write row 1 headers
    headers = list(config.SHEET_HEADERS)
    print(f"[*] Writing 11 canonical headers to '{target_tab}!A1': {headers}")
    sheets_client.values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{target_tab}'!A1:K1",
        valueInputOption="USER_ENTERED",
        body={"values": [headers]},
    ).execute()

    # 4. Fetch existing data from 'Sheet1'
    print("[*] Reading rows from 'Sheet1'...")
    res = sheets_client.values().get(
        spreadsheetId=spreadsheet_id,
        range="Sheet1!A1:Z100",
    ).execute()
    sheet1_values = res.get("values", [])
    print(f"[*] Read {len(sheet1_values)} total rows from 'Sheet1'.")

    if len(sheet1_values) <= 1:
        print("[!] No data rows found in 'Sheet1' to migrate.")
        return

    # 5. Transform and realign rows
    migrated_rows = []
    user_cache = {}

    for idx, row in enumerate(sheet1_values[1:], start=2):
        if not row or not any(str(cell).strip() for cell in row):
            continue

        # Check if row is 11-column (test rows 17-21) or legacy 7-column (rows 2-16)
        if len(row) > 7:
            # 11-column format:
            # [Message, Category, Conversation ID, Conversation Topic, Staff Member, Subteam, Date, Notes, Action Items, Deadline, Link]
            msg = row[0] if len(row) > 0 else ""
            cat = row[1] if len(row) > 1 else ""
            convo_id = row[2] if len(row) > 2 else ""
            convo_topic = row[3] if len(row) > 3 else ""
            staff = row[4] if len(row) > 4 else ""
            subteam = row[5] if len(row) > 5 else ""
            date_val = row[6] if len(row) > 6 else ""
            notes = row[7] if len(row) > 7 else ""
            action_items = row[8] if len(row) > 8 else ""
            deadline = row[9] if len(row) > 9 else ""
            link = row[10] if len(row) > 10 else ""

            # Attempt to resolve Slack user ID to real name
            if staff and staff.startswith("U"):
                if staff not in user_cache and slack_client:
                    user_cache[staff] = resolve_slack_user_name(slack_client, staff)
                resolved_name = user_cache.get(staff, staff)
                if resolved_name != staff:
                    print(f"    [+] Resolved staff '{staff}' -> '{resolved_name}'")
                    staff = resolved_name

            # Subteam resolution if unassigned
            if (not subteam or subteam == "Unassigned") and staff:
                subteam = config.get_subteam(staff)

            migrated_rows.append([
                msg, cat, convo_id, convo_topic, staff, subteam,
                date_val, notes, action_items, deadline, link
            ])
        else:
            # Legacy 7-column format:
            # [Message, Category, Staff Member, Subteam, Date, Notes, (optional Link)]
            msg = row[0] if len(row) > 0 else ""
            cat = row[1] if len(row) > 1 else ""
            staff = row[2] if len(row) > 2 else ""
            subteam = row[3] if len(row) > 3 else ""
            date_val = row[4] if len(row) > 4 else ""
            notes = row[5] if len(row) > 5 else ""
            link = row[6] if len(row) > 6 else ""

            if staff and staff.startswith("U"):
                if staff not in user_cache and slack_client:
                    user_cache[staff] = resolve_slack_user_name(slack_client, staff)
                resolved_name = user_cache.get(staff, staff)
                if resolved_name != staff:
                    print(f"    [+] Resolved staff '{staff}' -> '{resolved_name}'")
                    staff = resolved_name

            migrated_rows.append([
                msg, cat, "", "", staff, subteam,
                date_val, notes, "", "", link
            ])

    print(f"[*] Prepared {len(migrated_rows)} migrated rows for '{target_tab}'.")

    # 6. Clear any existing rows in ChatRecords below row 1, then append migrated rows
    sheets_client.values().clear(
        spreadsheetId=spreadsheet_id,
        range=f"'{target_tab}'!A2:K1000",
    ).execute()

    if migrated_rows:
        append_res = sheets_client.values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{target_tab}'!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": migrated_rows},
        ).execute()
        updated_rows = append_res.get("updates", {}).get("updatedRows", len(migrated_rows))
        print(f"[+] Successfully migrated and appended {updated_rows} rows to '{target_tab}'.")

    print("[*] Migration completed successfully!")


if __name__ == "__main__":
    main()
