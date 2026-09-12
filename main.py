"""Agent Backend CLI entry point and verification runner."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any, Literal

import config


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="agent-backend",
        description="Agent Backend: FastMCP Workspace Automation & Slack Socket Mode Ingestion",
    )
    parser.add_argument(
        "--test-slack",
        action="store_true",
        help="Verify Slack bot authentication and workspace connection via Slack API",
    )
    parser.add_argument(
        "--listen-slack",
        action="store_true",
        help="Launch the live Slack Socket Mode listener to stream channel messages",
    )
    parser.add_argument(
        "--test-mcp",
        action="store_true",
        help="Execute FastMCP client-server verification pipeline against Sheet and Calendar",
    )
    parser.add_argument(
        "--transport",
        choices=["inproc", "stdio"],
        default="inproc",
        help="MCP client transport mechanism: 'inproc' (default) or 'stdio'",
    )
    parser.add_argument(
        "--spreadsheet-id",
        default=config.GOOGLE_SHEET_ID,
        help=f"Target Google Spreadsheet ID (default: {config.GOOGLE_SHEET_ID})",
    )
    parser.add_argument(
        "--calendar-id",
        default=config.GOOGLE_CALENDAR_ID,
        help=f"Target Google Calendar ID (default: {config.GOOGLE_CALENDAR_ID})",
    )
    parser.add_argument(
        "--tab-name",
        default="TestRecords",
        help="Worksheet tab name for MCP verification tests (default: TestRecords)",
    )
    return parser


def run_slack_test() -> bool:
    """Verify Slack bot authentication and identity via Slack API."""
    from slack_sdk import WebClient

    print("\n" + "=" * 65)
    print("  💬 AGENT BACKEND: SLACK AUTH & CONNECTIVITY TEST")
    print("=" * 65 + "\n")

    if not config.SLACK_BOT_TOKEN:
        print("❌ [Pre-Flight Error] Missing SLACK_BOT_TOKEN in environment / .env.")
        print("   Required: SLACK_BOT_TOKEN (starts with xoxb-)")
        return False

    if not config.SLACK_BOT_TOKEN.startswith("xoxb-"):
        print("❌ [Pre-Flight Error] Invalid SLACK_BOT_TOKEN. Must start with 'xoxb-'.")
        return False

    try:
        client = WebClient(token=config.SLACK_BOT_TOKEN)
        auth_res = client.auth_test()
        if not auth_res.get("ok"):
            print(f"❌ [Slack Connection Failed] auth_test returned: {auth_res}")
            return False

        team_name = auth_res.get("team")
        team_id = auth_res.get("team_id")
        user_name = auth_res.get("user")
        user_id = auth_res.get("user_id")
        bot_id = auth_res.get("bot_id")

        print("  ✓ Slack Bot Authentication Successful!")
        print(f"    • Team Name  : {team_name} ({team_id})")
        print(f"    • Bot Name   : {user_name}")
        print(f"    • Bot User ID: {user_id}")
        print(f"    • Bot App ID : {bot_id}")
        print("\n  ✓ Socket Mode Ready: You can now run `uv run python main.py --listen-slack`")
        print("=" * 65 + "\n")
        return True
    except Exception as exc:
        print(f"❌ [Slack Connection Failed] {exc}")
        return False


def run_slack_listener() -> bool:
    """Launch the live Slack Socket Mode listener."""
    from slack_bot import start_slack_bot

    print("\n" + "=" * 65)
    print("  🎧 AGENT BACKEND: LIVE SLACK SOCKET MODE LISTENER")
    print("=" * 65 + "\n")
    try:
        start_slack_bot()
        return True
    except KeyboardInterrupt:
        print("\n[*] Slack listener stopped by user.")
        return True
    except Exception as exc:
        print(f"❌ [Slack Listener Error] {exc}", file=sys.stderr)
        return False


async def run_mcp_verification(
    transport: Literal["inproc", "stdio"] = "inproc",
    spreadsheet_id: str | None = None,
    calendar_id: str | None = None,
    tab_name: str | None = None,
) -> bool:
    """Execute FastMCP client-server verification pipeline against Sheet and Calendar."""
    from src.mcp_client.client import call_mcp_tool, get_mcp_client

    target_spreadsheet = spreadsheet_id or config.GOOGLE_SHEET_ID
    target_calendar = calendar_id or config.GOOGLE_CALENDAR_ID
    target_tab = tab_name or "TestRecords"

    print("\n" + "=" * 65)
    print("  🚀 AGENT BACKEND: MCP VERIFICATION RUNNER")
    print(f"  Transport       : {transport.upper()}")
    print(f"  Spreadsheet ID  : {target_spreadsheet}")
    print(f"  Target Tab      : {target_tab}")
    print(f"  Calendar ID     : {target_calendar}")
    print("=" * 65 + "\n")

    if not config.GOOGLE_SERVICE_ACCOUNT_FILE:
        print("❌ [Pre-Flight Error] GOOGLE_SERVICE_ACCOUNT_FILE not configured.")
        return False

    sa_path = Path(config.GOOGLE_SERVICE_ACCOUNT_FILE)
    if not sa_path.exists():
        print(f"❌ [Pre-Flight Error] Credentials file not found at: {sa_path}")
        return False

    try:
        async with get_mcp_client(transport=transport) as client:
            # 1. Discover Tools
            print("[1/4] Discovering MCP Tools from server...")
            tools_resp = await client.list_tools()
            discovered = {t.name for t in tools_resp.tools}
            print(f"      Discovered {len(discovered)} tool(s): {', '.join(sorted(discovered))}")

            required = {"sheets_ensure_tab", "sheets_append_rows", "calendar_create_event"}
            missing = required - discovered
            if missing:
                print(f"❌ Missing required MCP tools: {missing}")
                return False
            print("      ✓ Required tools present.\n")

            # 2. Ensure Tab
            print(f"[2/4] Ensuring worksheet tab '{target_tab}'...")
            tab_res = await call_mcp_tool(
                client=client,
                name="sheets_ensure_tab",
                arguments={
                    "spreadsheet_id": target_spreadsheet,
                    "sheet_name": target_tab,
                    "headers": config.SHEET_HEADERS,
                },
            )
            print(f"      ✓ Tab ensured (Sheet ID: {tab_res.get('sheet_id')})\n")

            # 3. Append 2 test rows
            print(f"[3/4] Appending 2 test rows to '{target_tab}'...")
            now_iso = datetime.now(timezone.utc).isoformat()
            row1 = [
                "Test message 1 from CLI verification",
                "Miscellaneous",
                "test_convo_1",
                "CLI Verification",
                "tester",
                "Engineering",
                now_iso,
                "Note 1",
                "Task 1",
                "2026-09-30",
                "https://slack.com",
            ]
            row2 = [
                "Test message 2 from CLI verification",
                "Outreach",
                "test_convo_1",
                "CLI Verification",
                "tester",
                "Engineering",
                now_iso,
                "Note 2",
                "Task 2",
                "",
                "https://slack.com",
            ]
            append_res = await call_mcp_tool(
                client=client,
                name="sheets_append_rows",
                arguments={
                    "spreadsheet_id": target_spreadsheet,
                    "sheet_name": target_tab,
                    "rows": [row1, row2],
                },
            )
            print(f"      ✓ Rows appended: {append_res.get('rows_appended', 2)}\n")

            # 4. Insert calendar event
            print(f"[4/4] Inserting Calendar Event on '{target_calendar}'...")
            now = datetime.now(timezone.utc)
            start_iso = (now + timedelta(hours=2)).isoformat()
            end_iso = (now + timedelta(hours=3)).isoformat()
            cal_res = await call_mcp_tool(
                client=client,
                name="calendar_create_event",
                arguments={
                    "calendar_id": target_calendar,
                    "summary": f"[CLI Verification] MCP Event ({transport})",
                    "start_iso": start_iso,
                    "end_iso": end_iso,
                    "description": f"Automated test event created at {now_iso}",
                    "location": "Virtual",
                },
            )
            print(f"      ✓ Calendar event created: {cal_res.get('event_id')}\n")

        print("=" * 65)
        print("  🎉 ALL MCP VERIFICATION STEPS PASSED!")
        print("=" * 65 + "\n")
        return True
    except Exception as exc:
        print(f"❌ [MCP Verification Failed] {exc}")
        import traceback
        traceback.print_exc()
        return False


def main(argv: list[str] | None = None) -> int:
    """Main CLI entrypoint."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if args.test_slack:
        return 0 if run_slack_test() else 1

    if args.listen_slack:
        return 0 if run_slack_listener() else 1

    if args.test_mcp:
        success = asyncio.run(
            run_mcp_verification(
                transport=args.transport,
                spreadsheet_id=args.spreadsheet_id,
                calendar_id=args.calendar_id,
                tab_name=args.tab_name,
            )
        )
        return 0 if success else 1

    parser.print_help()
    return 0


def cli() -> None:
    """Console script entrypoint for pyproject.toml scripts."""
    sys.exit(main())


if __name__ == "__main__":
    cli()
