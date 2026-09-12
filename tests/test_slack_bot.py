"""Comprehensive offline unit test suite for Slack Socket Mode bot and CLI runner."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from slack_bolt import App
import config
from main import create_parser, main
from slack_bot import SlackSocketListener, init_slack_app, start_slack_bot
import time_utils


@pytest.fixture
def mock_slack_client():
    """Mocked Slack WebClient providing offline deterministic API responses."""
    client = MagicMock()
    client.auth_test.return_value = {
        "ok": True,
        "team": "Test Workspace",
        "team_id": "T12345",
        "user": "smart_summerizer",
        "user_id": "U_BOT_123",
        "bot_id": "B_BOT_123",
    }
    client.users_info.return_value = {
        "ok": True,
        "user": {
            "id": "U_USER_456",
            "name": "alice",
            "real_name": "Alice Smith",
            "profile": {"display_name": "Alice"},
        },
    }
    client.chat_getPermalink.return_value = {
        "ok": True,
        "permalink": "https://test.slack.com/archives/C123/p1726000000000100",
    }
    client.chat_postMessage.return_value = {
        "ok": True,
        "ts": "1726000000.9999",
        "channel": "C123",
    }
    client.reactions_add.return_value = {"ok": True}
    return client


@pytest.fixture
def listener(mock_slack_client, monkeypatch):
    """Fixture providing an initialized SlackSocketListener with mocked client and offline settings."""
    inst = SlackSocketListener(
        bot_token="xoxb-mock-bot-token-12345",
        app_token="xapp-mock-app-token-12345",
        channel_id=None,
        auto_react_emoji="eyes",
        token_verification_enabled=False,
    )
    inst.app._client = mock_slack_client
    inst._bot_user_id = "U_BOT_123"
    return inst


@pytest.fixture
def event_factory():
    """Factory fixture generating realistic Slack message event payloads."""
    def _create(
        text: str = "Test message content",
        user: str = "U_USER_456",
        channel: str = "C123",
        ts: str = "1726000000.0001",
        thread_ts: str | None = None,
        subtype: str | None = None,
        bot_id: str | None = None,
    ) -> dict:
        event = {
            "type": "message",
            "channel": channel,
            "user": user,
            "text": text,
            "ts": ts,
        }
        if thread_ts:
            event["thread_ts"] = thread_ts
        if subtype:
            event["subtype"] = subtype
        if bot_id:
            event["bot_id"] = bot_id
        return event

    return _create


# ==============================================================================
# 1. Token Validation & Configuration Tests
# ==============================================================================

def test_token_validation_invalid_bot_token():
    """Verify ValueError is raised when SLACK_BOT_TOKEN does not start with xoxb-."""
    with pytest.raises(ValueError, match="SLACK_BOT_TOKEN is required"):
        SlackSocketListener(
            bot_token="invalid-bot-token",
            app_token="xapp-valid-app-token",
            token_verification_enabled=False,
        )


def test_token_validation_invalid_app_token():
    """Verify ValueError is raised when SLACK_APP_TOKEN does not start with xapp-."""
    with pytest.raises(ValueError, match="SLACK_APP_TOKEN is required"):
        SlackSocketListener(
            bot_token="xoxb-valid-bot-token",
            app_token="invalid-app-token",
            token_verification_enabled=False,
        )


def test_token_validation_empty_tokens(monkeypatch):
    """Verify ValueError when environment tokens are missing."""
    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "")
    monkeypatch.setattr(config, "SLACK_APP_TOKEN", "")
    with pytest.raises(ValueError, match="SLACK_BOT_TOKEN is required"):
        SlackSocketListener(token_verification_enabled=False)


# ==============================================================================
# 2. Connection Testing & Identity Discovery
# ==============================================================================

def test_auth_test_connection(listener, mock_slack_client):
    """Verify test_connection queries auth_test and caches bot user ID."""
    res = listener.test_connection()
    mock_slack_client.auth_test.assert_called_once()
    assert res["ok"] is True
    assert res["team"] == "Test Workspace"
    assert res["user"] == "smart_summerizer"
    assert res["user_id"] == "U_BOT_123"
    assert listener._bot_user_id == "U_BOT_123"


# ==============================================================================
# 3. User Name Resolution & In-Memory Caching
# ==============================================================================

def test_user_name_resolution_caching(listener, mock_slack_client):
    """Verify users_info is called on first lookup and cached on subsequent queries."""
    name1 = listener._resolve_user_name("U_USER_456")
    assert name1 == "Alice Smith"
    mock_slack_client.users_info.assert_called_once_with(user="U_USER_456")

    # Second lookup should hit cache
    name2 = listener._resolve_user_name("U_USER_456")
    assert name2 == "Alice Smith"
    assert mock_slack_client.users_info.call_count == 1


def test_user_name_resolution_fallback_to_profile_or_id(listener, mock_slack_client):
    """Verify fallback sequence when real_name is empty."""
    mock_slack_client.users_info.return_value = {
        "ok": True,
        "user": {
            "id": "U_USER_789",
            "name": "bob_uname",
            "profile": {"display_name": "Bobby"},
        },
    }
    name = listener._resolve_user_name("U_USER_789")
    assert name == "Bobby"


def test_user_name_resolution_api_error_fallback(listener, mock_slack_client):
    """Verify graceful fallback to raw user_id when users_info fails."""
    mock_slack_client.users_info.side_effect = RuntimeError("Slack API Tier rate limit")
    name = listener._resolve_user_name("U_FAIL_999")
    assert name == "U_FAIL_999"
    # Caches the fallback
    assert listener._user_cache["U_FAIL_999"] == "U_FAIL_999"


def test_user_name_resolution_empty_id(listener):
    """Verify empty or None user_id returns 'Unknown'."""
    assert listener._resolve_user_name("") == "Unknown"
    assert listener._resolve_user_name(None) == "Unknown"


# ==============================================================================
# 4. Message Ingestion Flow (7 Steps)
# ==============================================================================

def test_message_ingestion_full_flow_with_deadline(listener, event_factory, monkeypatch):
    """Verify 7-step ingestion: extraction, session grouping, subteam, link, sheets append, calendar event, reaction."""
    mock_extract = MagicMock(return_value={
        "category": "Trading Project",
        "notes": "Deploy contract",
        "action_items": ["Run audits", "Submit PR"],
        "deadline": "2026-09-25",
    })
    mock_append = MagicMock()
    mock_create_event = MagicMock(return_value="https://calendar.google.com/event?id=123")

    monkeypatch.setattr("ai_extractor.extract", mock_extract)
    monkeypatch.setattr("sheets_service.append_record", mock_append)
    monkeypatch.setattr("calendar_service.create_event", mock_create_event)
    monkeypatch.setattr("config.STAFF_SUBTEAM", {"Alice Smith": "Engineering"})

    event = event_factory(text="Please deploy the contract by 2026-09-25", user="U_USER_456")
    listener.handle_message(event)

    # Step 1: extraction
    mock_extract.assert_called_once_with("Please deploy the contract by 2026-09-25", "Alice Smith")

    # Step 5: sheets append
    mock_append.assert_called_once()
    record = mock_append.call_args[0][0]
    assert record["Message"] == "Please deploy the contract by 2026-09-25"
    assert record["Category"] == "Trading Project"
    assert record["Staff Member"] == "Alice Smith"
    assert record["Subteam"] == "Engineering"
    assert record["Notes"] == "Deploy contract"
    assert record["Action Items"] == "Run audits; Submit PR"
    assert record["Deadline"] == "2026-09-25"
    assert "https://test.slack.com" in record["Link"]

    # Step 6: calendar event creation
    mock_create_event.assert_called_once_with(
        summary="Deploy contract",
        description="From Slack message by Alice Smith: Please deploy the contract by 2026-09-25",
        date_str="2026-09-25",
    )

    # Step 7: reaction added
    listener.app.client.reactions_add.assert_called_once_with(
        channel="C123",
        name="eyes",
        timestamp="1726000000.0001",
    )


def test_message_ingestion_no_deadline_skips_calendar(listener, event_factory, monkeypatch):
    """Verify that when no deadline is extracted, calendar_service.create_event is NOT called."""
    mock_extract = MagicMock(return_value={
        "category": "Outreach",
        "notes": "General update",
        "action_items": [],
        "deadline": None,
    })
    mock_append = MagicMock()
    mock_create_event = MagicMock()

    monkeypatch.setattr("ai_extractor.extract", mock_extract)
    monkeypatch.setattr("sheets_service.append_record", mock_append)
    monkeypatch.setattr("calendar_service.create_event", mock_create_event)

    event = event_factory(text="No deadline mentioned here.")
    listener.handle_message(event)

    mock_append.assert_called_once()
    mock_create_event.assert_not_called()
def test_message_ingestion_followup_prunes_prior_event(listener, event_factory, monkeypatch):
    """Verify that when a follow-up message enriches an event in the same conversation, the prior event is deleted."""
    mock_extract_1 = {
        "category": "Miscellaneous",
        "topic": "Dinner",
        "notes": "Dinner on 29th",
        "action_items": [],
        "deadline": "2026-09-29T19:00:00",
        "location": None,
    }
    mock_extract_2 = {
        "category": "Miscellaneous",
        "topic": "Dinner at Texas Roadhouse",
        "notes": "Dinner at Texas Roadhouse",
        "action_items": [],
        "deadline": "2026-09-29T19:00:00",
        "location": "Texas Roadhouse",
    }
    mock_extract = MagicMock(side_effect=[mock_extract_1, mock_extract_2])
    mock_append = MagicMock(return_value="'ChatRecords'!A2:K2")
    mock_create_event = MagicMock(side_effect=[
        "https://www.google.com/calendar/event?eid=ZXZlbnRfMQ",
        "https://www.google.com/calendar/event?eid=ZXZlbnRfMg",
    ])
    mock_delete_event = MagicMock(return_value=True)
    mock_extract_id = MagicMock(side_effect=["event_1", "event_2"])

    monkeypatch.setattr("ai_extractor.extract", mock_extract)
    monkeypatch.setattr("sheets_service.append_record", mock_append)
    monkeypatch.setattr("sheets_service.update_record_link", MagicMock())
    monkeypatch.setattr("calendar_service.create_event", mock_create_event)
    monkeypatch.setattr("calendar_service.delete_event", mock_delete_event)
    monkeypatch.setattr("calendar_service.extract_event_id_from_link", mock_extract_id)
    monkeypatch.setattr("conversation_tracker.get_or_create_conversation", MagicMock(return_value=("conv_123", "Dinner")))

    # Message 1
    event1 = event_factory(text="lets get dinner on the 29th at 7", ts="1726000001.0001")
    listener.handle_message(event1)
    assert mock_create_event.call_count == 1
    mock_delete_event.assert_not_called()

    # Message 2 (follow-up in same conversation session)
    event2 = event_factory(text="at texas roadhouse", ts="1726000002.0001")
    listener.handle_message(event2)
    assert mock_create_event.call_count == 2
    mock_delete_event.assert_called_once_with("event_1")


def test_message_ingestion_threaded_conversation_id_and_topic(listener, event_factory, monkeypatch):

    """Verify threaded messages use thread_ts for conversation_id and bypass semantic tracker."""
    mock_extract = MagicMock(return_value={"category": "Research", "notes": "", "action_items": [], "deadline": None})
    mock_append = MagicMock()
    mock_gen_topic = MagicMock(return_value="Solidity Auditing")
    mock_tracker = MagicMock()

    monkeypatch.setattr("ai_extractor.extract", mock_extract)
    monkeypatch.setattr("sheets_service.append_record", mock_append)
    monkeypatch.setattr("conversation_tracker._generate_topic_label", mock_gen_topic)
    monkeypatch.setattr("conversation_tracker.get_or_create_conversation", mock_tracker)

    thread_root_ts = "1726000000.0005"
    event1 = event_factory(text="First thread reply", thread_ts=thread_root_ts)
    listener.handle_message(event1)

    record1 = mock_append.call_args[0][0]
    assert record1["Conversation ID"] == f"thread_{thread_root_ts}"
    assert record1["Conversation Topic"] == "Solidity Auditing"
    mock_gen_topic.assert_called_once_with("First thread reply")
    mock_tracker.assert_not_called()

    # Second message in same thread reuses cached topic
    event2 = event_factory(text="Second thread reply", thread_ts=thread_root_ts)
    listener.handle_message(event2)
    record2 = mock_append.call_args[0][0]
    assert record2["Conversation ID"] == f"thread_{thread_root_ts}"
    assert record2["Conversation Topic"] == "Solidity Auditing"
    # _generate_topic_label should still only have been called once
    assert mock_gen_topic.call_count == 1


def test_message_ingestion_unthreaded_semantic_tracker(listener, event_factory, monkeypatch):
    """Verify unthreaded messages delegate to conversation_tracker.get_or_create_conversation."""
    mock_extract = MagicMock(return_value={"category": "General", "notes": "", "action_items": [], "deadline": None})
    mock_append = MagicMock()
    mock_tracker = MagicMock(return_value=("sem_convo_abc123", "Lunch Planning"))

    monkeypatch.setattr("ai_extractor.extract", mock_extract)
    monkeypatch.setattr("sheets_service.append_record", mock_append)
    monkeypatch.setattr("conversation_tracker.get_or_create_conversation", mock_tracker)

    event = event_factory(text="Where should we eat lunch?", thread_ts=None)
    listener.handle_message(event)

    mock_tracker.assert_called_once()
    record = mock_append.call_args[0][0]
    assert record["Conversation ID"] == "sem_convo_abc123"
    assert record["Conversation Topic"] == "Lunch Planning"


def test_subteam_mapping_unassigned_fallback(listener, event_factory, monkeypatch):
    """Verify unlisted staff member defaults to 'Unassigned'."""
    monkeypatch.setattr("ai_extractor.extract", MagicMock(return_value={"category": "General", "notes": "", "action_items": [], "deadline": None}))
    mock_append = MagicMock()
    monkeypatch.setattr("sheets_service.append_record", mock_append)
    monkeypatch.setattr("config.STAFF_SUBTEAM", {})

    event = event_factory(text="Hello world", user="U_USER_456")
    listener.handle_message(event)

    record = mock_append.call_args[0][0]
    assert record["Subteam"] == "Unassigned"


# ==============================================================================
# 5. Event Filtering & Noise Rejection Tests
# ==============================================================================

@pytest.mark.parametrize("subtype", [
    "bot_message",
    "message_changed",
    "message_deleted",
    "channel_join",
    "channel_leave",
])
def test_filtering_drops_subtypes(listener, event_factory, monkeypatch, subtype):
    """Verify messages with any subtype are discarded immediately."""
    mock_extract = MagicMock()
    monkeypatch.setattr("ai_extractor.extract", mock_extract)

    event = event_factory(text="System message", subtype=subtype)
    listener.handle_message(event)

    mock_extract.assert_not_called()
    listener.app.client.reactions_add.assert_not_called()


def test_filtering_drops_bot_id_and_self_messages(listener, event_factory, monkeypatch):
    """Verify messages with bot_id or user == bot_user_id are dropped."""
    mock_extract = MagicMock()
    monkeypatch.setattr("ai_extractor.extract", mock_extract)

    # 1. Message with bot_id
    event_bot = event_factory(text="Automated bot output", bot_id="B_OTHER_BOT")
    listener.handle_message(event_bot)
    mock_extract.assert_not_called()

    # 2. Self message from bot user ID
    event_self = event_factory(text="Self echo", user="U_BOT_123")
    listener.handle_message(event_self)
    mock_extract.assert_not_called()


def test_filtering_drops_empty_and_whitespace_text(listener, event_factory, monkeypatch):
    """Verify empty or whitespace-only messages are discarded."""
    mock_extract = MagicMock()
    monkeypatch.setattr("ai_extractor.extract", mock_extract)

    listener.handle_message(event_factory(text=""))
    listener.handle_message(event_factory(text="   \n\t  "))
    mock_extract.assert_not_called()


def test_filtering_channel_restriction(listener, event_factory, monkeypatch):
    """Verify messages outside watched SLACK_CHANNEL_ID are ignored."""
    listener.channel_id = "C_WATCHED"
    mock_extract = MagicMock()
    monkeypatch.setattr("ai_extractor.extract", mock_extract)

    # Mismatched channel
    listener.handle_message(event_factory(channel="C_OTHER"))
    mock_extract.assert_not_called()

    # Matching channel
    monkeypatch.setattr("sheets_service.append_record", MagicMock())
    listener.handle_message(event_factory(channel="C_WATCHED"))
    mock_extract.assert_called_once()


# ==============================================================================
# 6. Summary Query Handling Tests
# ==============================================================================

def test_summary_query_prefix_today(listener, event_factory, monkeypatch):
    """Verify !summarize routes to sheets_get_records, build_summary, and posts response."""
    mock_get_records = MagicMock(return_value=[{"Message": "Logged item 1"}])
    mock_build_summary = MagicMock(return_value="*Summary for today*: 1 item.")
    mock_append = MagicMock()

    monkeypatch.setattr("sheets_service.get_records_in_range", mock_get_records)
    monkeypatch.setattr("ai_extractor.build_summary", mock_build_summary)
    monkeypatch.setattr("sheets_service.append_record", mock_append)

    event = event_factory(text="!summarize", channel="C123")
    listener.handle_message(event)

    mock_get_records.assert_called_once()
    mock_build_summary.assert_called_once_with([{"Message": "Logged item 1"}], "today")
    listener.app.client.chat_postMessage.assert_called_once_with(
        channel="C123",
        text="*Summary for today*: 1 item.",
    )
    # Crucial: summary command must NOT be logged as a record row
    mock_append.assert_not_called()


@pytest.mark.parametrize("cmd,expected_label", [
    ("!summarize yesterday", "yesterday"),
    ("!summarize week", "the past week"),
    ("!summarize month", "the past month"),
    ("<@U_BOT_123> summarize week", "the past week"),
    ("@bot summarize yesterday", "yesterday"),
    ("<@U_BOT_123|smart_summerizer> summarize month", "the past month"),
])
def test_summary_query_triggers_and_ranges(listener, event_factory, monkeypatch, cmd, expected_label):
    """Verify dual triggers (prefix and mentions) parse temporal scope correctly."""
    mock_get_records = MagicMock(return_value=[])
    mock_build_summary = MagicMock(return_value=f"Nothing logged for {expected_label} yet.")

    monkeypatch.setattr("sheets_service.get_records_in_range", mock_get_records)
    monkeypatch.setattr("ai_extractor.build_summary", mock_build_summary)

    event = event_factory(text=cmd)
    listener.handle_message(event)

    mock_build_summary.assert_called_once_with([], expected_label)
    listener.app.client.chat_postMessage.assert_called_once()


def test_summary_query_in_thread_replies_to_thread(listener, event_factory, monkeypatch):
    """Verify summary requested in thread includes thread_ts in postMessage kwargs."""
    mock_get_records = MagicMock(return_value=[])
    mock_build_summary = MagicMock(return_value="Summary in thread")

    monkeypatch.setattr("sheets_service.get_records_in_range", mock_get_records)
    monkeypatch.setattr("ai_extractor.build_summary", mock_build_summary)

    event = event_factory(text="!summarize today", thread_ts="1726000000.8888")
    listener.handle_message(event)

    listener.app.client.chat_postMessage.assert_called_once_with(
        channel="C123",
        text="Summary in thread",
        thread_ts="1726000000.8888",
    )


def test_summary_query_empty_records_returns_notice(listener, event_factory, monkeypatch):
    """Verify empty record list returns 'Nothing logged for {label} yet.' without failure."""
    monkeypatch.setattr("sheets_service.get_records_in_range", MagicMock(return_value=[]))

    event = event_factory(text="!summarize today")
    listener.handle_message(event)

    listener.app.client.chat_postMessage.assert_called_once()
    args, kwargs = listener.app.client.chat_postMessage.call_args
    assert "Nothing logged for today yet." in kwargs["text"]


def test_summary_query_service_error_containment(listener, event_factory, monkeypatch):
    """Verify upstream failure in summary retrieval posts polite error and doesn't crash."""
    monkeypatch.setattr("sheets_service.get_records_in_range", MagicMock(side_effect=RuntimeError("MCP server down")))

    event = event_factory(text="!summarize week")
    listener.handle_message(event)

    listener.app.client.chat_postMessage.assert_called_once()
    args, kwargs = listener.app.client.chat_postMessage.call_args
    assert "Unable to generate summary at this time" in kwargs["text"]


# ==============================================================================
# 7. Error Containment & Resilience Tests
# ==============================================================================

def test_error_containment_ai_extract_failure(listener, event_factory, monkeypatch):
    """Verify that if ai_extractor.extract fails, fallback record is still logged to sheets."""
    monkeypatch.setattr("ai_extractor.extract", MagicMock(side_effect=RuntimeError("Gemini quota exceeded")))
    mock_append = MagicMock()
    monkeypatch.setattr("sheets_service.append_record", mock_append)

    event = event_factory(text="Important task that failed extraction")
    listener.handle_message(event)

    mock_append.assert_called_once()
    record = mock_append.call_args[0][0]
    assert record["Category"] == "Miscellaneous"
    assert record["Message"] == "Important task that failed extraction"


def test_error_containment_calendar_failure(listener, event_factory, monkeypatch):
    """Verify calendar creation failure does not crash ingestion or prevent reaction."""
    monkeypatch.setattr("ai_extractor.extract", MagicMock(return_value={
        "category": "Trading",
        "notes": "Contract",
        "action_items": [],
        "deadline": "2026-09-30",
    }))
    monkeypatch.setattr("sheets_service.append_record", MagicMock())
    monkeypatch.setattr("calendar_service.create_event", MagicMock(side_effect=RuntimeError("Calendar API error")))

    event = event_factory(text="Deploy contract by 2026-09-30")
    listener.handle_message(event)

    # Reaction should still be added
    listener.app.client.reactions_add.assert_called_once()


def test_error_containment_reactions_add_failure(listener, event_factory, monkeypatch):
    """Verify reactions_add failure (e.g. already_reacted) is swallowed silently."""
    monkeypatch.setattr("ai_extractor.extract", MagicMock(return_value={"category": "General", "notes": "", "action_items": [], "deadline": None}))
    monkeypatch.setattr("sheets_service.append_record", MagicMock())
    listener.app.client.reactions_add.side_effect = RuntimeError("already_reacted")

    event = event_factory(text="Hello again")
    # Should complete with no exception
    listener.handle_message(event)


def test_permalink_failure_fallback(listener, event_factory, monkeypatch):
    """Verify fallback synthetic permalink when chat_getPermalink raises error."""
    listener.app.client.chat_getPermalink.side_effect = RuntimeError("Network error")
    mock_append = MagicMock()
    monkeypatch.setattr("sheets_service.append_record", mock_append)
    monkeypatch.setattr("ai_extractor.extract", MagicMock(return_value={"category": "General", "notes": "", "action_items": [], "deadline": None}))

    event = event_factory(channel="C_ABC", ts="1726000000.1234")
    listener.handle_message(event)

    record = mock_append.call_args[0][0]
    assert record["Link"] == "https://slack.com/archives/C_ABC/p17260000001234"


# ==============================================================================
# 8. Lifecycle & Factory Function Tests
# ==============================================================================

def test_init_slack_app_factory():
    """Verify init_slack_app creates an initialized App instance."""
    app = init_slack_app(
        token_verification_enabled=False,
        bot_token="xoxb-mock-token",
        app_token="xapp-mock-token",
    )
    assert isinstance(app, App)


def test_graceful_shutdown(listener):
    """Verify close() closes SocketModeHandler safely."""
    mock_handler = MagicMock()
    listener.handler = mock_handler
    listener.close()
    mock_handler.close.assert_called_once()


# ==============================================================================
# 9. CLI Argument Parsing & Runner Tests
# ==============================================================================

def test_cli_parser_defaults():
    """Verify default CLI arguments."""
    parser = create_parser()
    args = parser.parse_args([])

    assert args.test_slack is False
    assert args.listen_slack is False
    assert args.test_mcp is False
    assert args.transport == "inproc"
    assert args.tab_name == "TestRecords"


def test_cli_parser_custom_args():
    """Verify custom argument overrides."""
    parser = create_parser()
    args = parser.parse_args([
        "--test-slack",
        "--transport", "stdio",
        "--spreadsheet-id", "custom_sheet",
        "--calendar-id", "custom@cal.com",
        "--tab-name", "CustomTab",
    ])

    assert args.test_slack is True
    assert args.transport == "stdio"
    assert args.spreadsheet_id == "custom_sheet"
    assert args.calendar_id == "custom@cal.com"
    assert args.tab_name == "CustomTab"


def test_cli_main_test_slack_mocked(monkeypatch):
    """Verify main --test-slack returns 0 on success."""
    monkeypatch.setattr("main.run_slack_test", lambda: True)
    assert main(["--test-slack"]) == 0

    monkeypatch.setattr("main.run_slack_test", lambda: False)
    assert main(["--test-slack"]) == 1


def test_cli_main_listen_slack_mocked(monkeypatch):
    """Verify main --listen-slack returns 0 on success."""
    monkeypatch.setattr("main.run_slack_listener", lambda: True)
    assert main(["--listen-slack"]) == 0

    monkeypatch.setattr("main.run_slack_listener", lambda: False)
    assert main(["--listen-slack"]) == 1


def test_cli_main_test_mcp_mocked(monkeypatch):
    """Verify main --test-mcp returns 0 on success."""
    mock_run = AsyncMock(return_value=True)
    monkeypatch.setattr("main.run_mcp_verification", mock_run)
    assert main(["--test-mcp"]) == 0

    mock_run = AsyncMock(return_value=False)
    monkeypatch.setattr("main.run_mcp_verification", mock_run)
    assert main(["--test-mcp"]) == 1


def test_cli_main_no_args_prints_help(capsys):
    """Verify running main with no arguments displays help text and exits with 0."""
    assert main([]) == 0
    captured = capsys.readouterr()
    assert "usage: agent-backend" in captured.out


# ==============================================================================
# 10. Milestone 3 Remediation Regression Tests
# ==============================================================================

def test_discord_bot_decommissioned_from_git_and_disk():
    """Verify discord_bot.py is completely absent from filesystem and untracked in git index."""
    import subprocess
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    discord_path = repo_root / "discord_bot.py"
    assert not discord_path.exists(), f"discord_bot.py still exists on disk at {discord_path}!"

    res = subprocess.run(
        ["git", "ls-files", "discord_bot.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert res.stdout.strip() == "", f"discord_bot.py is still tracked in git index: {res.stdout}"

    res_stage = subprocess.run(
        ["git", "ls-files", "--stage", "discord_bot.py"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert res_stage.returncode == 0
    assert res_stage.stdout.strip() == "", f"discord_bot.py has staged entries in git: {res_stage.stdout}"


def test_summary_query_colon_mention_not_logged_as_record(listener, event_factory, monkeypatch):
    """Verify `<@BOT>: summarize today` is recognized as summary request and never logged to Sheets."""
    mock_get_records = MagicMock(return_value=[{"Message": "Prior task"}])
    mock_build_summary = MagicMock(return_value="*Summary for today*: Prior task.")
    mock_append = MagicMock()

    monkeypatch.setattr("sheets_service.get_records_in_range", mock_get_records)
    monkeypatch.setattr("ai_extractor.build_summary", mock_build_summary)
    monkeypatch.setattr("sheets_service.append_record", mock_append)

    assert time_utils.is_summary_request("<@U_BOT_123>: summarize today") is True
    assert time_utils.extract_range_text("<@U_BOT_123>: summarize today") == "today"

    event = event_factory(text="<@U_BOT_123>: summarize today", channel="C123")
    listener.handle_message(event)

    mock_get_records.assert_called_once()
    mock_build_summary.assert_called_once_with([{"Message": "Prior task"}], "today")
    listener.app.client.chat_postMessage.assert_called_once()
    mock_append.assert_not_called()


@pytest.mark.parametrize("query,expected_days,expected_label", [
    ("!summarize last 7 days", 7, "the past week"),
    ("!summarize past 7 days", 7, "the past week"),
    ("!summarize 7 days", 7, "the past week"),
    ("!summarize past 30 days", 30, "the past month"),
    ("!summarize 30 days", 30, "the past month"),
    ("!summarize last 14 days", 14, "the past 14 days"),
    ("!summarize past 3 days", 3, "the past 3 days"),
    ("!summarize 1 day", 1, "the past day"),
])
def test_summary_range_parsing_numeric_days(query, expected_days, expected_label):
    """Verify numeric day queries parse accurately and alias canonical labels."""
    assert time_utils.is_summary_request(query) is True
    range_text = time_utils.extract_range_text(query)
    now = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    start, end, label = time_utils.parse_time_range(range_text, now=now)
    assert label == expected_label
    assert start == today_start - timedelta(days=expected_days)
    assert end == now


def test_payload_none_text_no_exception(listener, monkeypatch, caplog):
    """Verify that event with {'text': None} does not raise AttributeError or emit error logs."""
    mock_append = MagicMock()
    monkeypatch.setattr("sheets_service.append_record", mock_append)

    event = {
        "type": "message",
        "channel": "C123",
        "user": "U_USER_456",
        "text": None,
        "ts": "1726000000.0001",
    }
    with caplog.at_level("ERROR", logger="slack_bot"):
        listener.handle_message(event)

    mock_append.assert_not_called()
    assert "Error processing message event" not in caplog.text
    assert "AttributeError" not in caplog.text

