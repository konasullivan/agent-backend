"""Adversarial and stress test harness for Slack Socket Mode Bot error containment and resilience.

Specifically challenges:
1. ai_extractor.extract exceptions and timeouts -> fallback record logging and zero bot crash.
2. calendar_service.create_event failures and API errors -> sheet row preservation and reaction addition.
3. Slack reactions_add failures (rate limit, already_reacted, network drops) -> cleanly caught and ignored.
4. Summary queries with zero matching records -> polite clean message posted, zero unhandled exceptions.
5. Upstream sheets failure during summary query -> fallback polite error message.
6. conversation_tracker failure -> fallback conversation ID and topic assignment.
7. Malformed Slack event payloads -> contained within top-level error guard.
8. Concurrent multi-threaded message bursts under failure conditions.
"""

from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest
from slack_sdk.errors import SlackApiError

import config
from slack_bot import SlackSocketListener


@pytest.fixture
def mock_client():
    """Mocked Slack WebClient for offline adversarial testing."""
    client = MagicMock()
    client.auth_test.return_value = {
        "ok": True,
        "team": "Adversarial Test Workspace",
        "team_id": "T99999",
        "user": "smart_summerizer",
        "user_id": "U_BOT_ADV",
        "bot_id": "B_BOT_ADV",
    }
    client.users_info.return_value = {
        "ok": True,
        "user": {
            "id": "U_USER_001",
            "name": "challenger",
            "real_name": "Challenger Agent",
            "profile": {"display_name": "Challenger"},
        },
    }
    client.chat_getPermalink.return_value = {
        "ok": True,
        "permalink": "https://test.slack.com/archives/C_TEST/p1726000000000100",
    }
    client.chat_postMessage.return_value = {
        "ok": True,
        "ts": "1726000000.9999",
        "channel": "C_TEST",
    }
    client.reactions_add.return_value = {"ok": True}
    return client


@pytest.fixture
def adv_listener(mock_client):
    """Initialized SlackSocketListener under test."""
    listener = SlackSocketListener(
        bot_token="xoxb-adv-test-bot-token-12345",
        app_token="xapp-adv-test-app-token-12345",
        channel_id=None,
        auto_react_emoji="eyes",
        token_verification_enabled=False,
    )
    listener.app._client = mock_client
    listener._bot_user_id = "U_BOT_ADV"
    return listener


def make_event(
    text: str = "Test adversarial message",
    user: str = "U_USER_001",
    channel: str = "C_TEST",
    ts: str = "1726000000.1234",
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


# ==============================================================================
# Challenge 1: ai_extractor Exceptions, Timeouts, and Malformed Output
# ==============================================================================

@pytest.mark.parametrize("exc", [
    RuntimeError("Gemini 429: Resource has been exhausted"),
    TimeoutError("LLM API call timed out after 30s"),
    ValueError("Invalid prompt encoding"),
    Exception("Unanticipated upstream crash"),
])
def test_ai_extractor_exception_fallback_record(adv_listener, monkeypatch, exc):
    """Verify that when ai_extractor raises any exception, a fallback record is logged and the bot does not crash."""
    monkeypatch.setattr("ai_extractor.extract", MagicMock(side_effect=exc))
    mock_append = MagicMock()
    monkeypatch.setattr("sheets_service.append_record", mock_append)

    event = make_event(text="Urgent deploy needed by tomorrow")

    # Should not raise any exception
    adv_listener.handle_message(event)

    # Verify fallback record was appended
    mock_append.assert_called_once()
    record = mock_append.call_args[0][0]
    assert record["Category"] == "Miscellaneous"
    assert record["Notes"] == "Urgent deploy needed by tomorrow"
    assert record["Action Items"] == ""
    assert record["Deadline"] == ""
    assert record["Message"] == "Urgent deploy needed by tomorrow"

    # Verify acknowledgment reaction still occurred
    adv_listener.app.client.reactions_add.assert_called_once_with(
        channel="C_TEST",
        name="eyes",
        timestamp="1726000000.1234",
    )


def test_ai_extractor_long_message_truncated_in_fallback_notes(adv_listener, monkeypatch):
    """Verify fallback notes truncate text to 100 chars when ai_extractor fails."""
    monkeypatch.setattr("ai_extractor.extract", MagicMock(side_effect=RuntimeError("AI Down")))
    mock_append = MagicMock()
    monkeypatch.setattr("sheets_service.append_record", mock_append)

    long_text = "A" * 250
    adv_listener.handle_message(make_event(text=long_text))

    record = mock_append.call_args[0][0]
    assert len(record["Notes"]) == 100
    assert record["Notes"] == "A" * 100


# ==============================================================================
# Challenge 2: calendar_service.create_event Failure & Sheet Preservation
# ==============================================================================

@pytest.mark.parametrize("cal_exc", [
    RuntimeError("Google Calendar API 500: Internal server error"),
    TimeoutError("Calendar API request timed out"),
    Exception("Quota exceeded for calendar events"),
])
def test_calendar_failure_preserves_sheet_row_and_completes(adv_listener, monkeypatch, cal_exc):
    """Verify that when calendar creation fails, the sheet row is already preserved, reaction is added, and no crash occurs."""
    extracted_data = {
        "category": "Trading Project",
        "notes": "Contract review",
        "action_items": ["Check risk"],
        "deadline": "2026-10-15",
    }
    monkeypatch.setattr("ai_extractor.extract", MagicMock(return_value=extracted_data))

    order_of_ops = []

    def mock_append(record):
        order_of_ops.append("sheet_append")

    def mock_create_event(**kwargs):
        order_of_ops.append("calendar_create")
        raise cal_exc

    monkeypatch.setattr("sheets_service.append_record", mock_append)
    monkeypatch.setattr("calendar_service.create_event", mock_create_event)

    event = make_event(text="Review contract by 2026-10-15")

    # Should execute without raising
    adv_listener.handle_message(event)

    # Sheet append must occur BEFORE calendar create
    assert order_of_ops == ["sheet_append", "calendar_create"]

    # Reaction should still be added despite calendar failure
    adv_listener.app.client.reactions_add.assert_called_once_with(
        channel="C_TEST",
        name="eyes",
        timestamp="1726000000.1234",
    )


# ==============================================================================
# Challenge 3: Slack reactions_add Failures Swallowed
# ==============================================================================

@pytest.mark.parametrize("react_err", [
    SlackApiError(message="The server responded with: already_reacted", response={"ok": False, "error": "already_reacted"}),
    SlackApiError(message="The server responded with: ratelimited", response={"ok": False, "error": "ratelimited"}),
    SlackApiError(message="The server responded with: invalid_auth", response={"ok": False, "error": "invalid_auth"}),
    ConnectionError("Network connection reset during reactions_add"),
    TimeoutError("Timeout contacting Slack edge server"),
])
def test_reactions_add_failure_in_message_ingestion_is_ignored(adv_listener, monkeypatch, react_err):
    """Verify that reactions_add failures during message ingestion are caught and ignored."""
    monkeypatch.setattr("ai_extractor.extract", MagicMock(return_value={"category": "General", "notes": "", "action_items": [], "deadline": None}))
    mock_append = MagicMock()
    monkeypatch.setattr("sheets_service.append_record", mock_append)
    adv_listener.app.client.reactions_add.side_effect = react_err

    event = make_event(text="Testing reaction failure resilience")

    # Must complete cleanly with no unhandled exception
    adv_listener.handle_message(event)
    mock_append.assert_called_once()


@pytest.mark.parametrize("react_err", [
    SlackApiError(message="already_reacted", response={"ok": False, "error": "already_reacted"}),
    SlackApiError(message="ratelimited", response={"ok": False, "error": "ratelimited"}),
    RuntimeError("Slack gateway timeout"),
])
def test_reactions_add_failure_in_summary_query_is_ignored(adv_listener, monkeypatch, react_err):
    """Verify that reactions_add failures during summary query handling are caught and ignored."""
    monkeypatch.setattr("sheets_service.get_records_in_range", MagicMock(return_value=[]))
    adv_listener.app.client.reactions_add.side_effect = react_err

    event = make_event(text="!summarize today")

    # Must complete cleanly
    adv_listener.handle_message(event)

    # Post message must still succeed
    adv_listener.app.client.chat_postMessage.assert_called_once()
    assert "Nothing logged for today yet." in adv_listener.app.client.chat_postMessage.call_args[1]["text"]


# ==============================================================================
# Challenge 4: Summary Queries with Zero Matching Records
# ==============================================================================

@pytest.mark.parametrize("trigger,expected_label", [
    ("!summarize", "today"),
    ("!summarize today", "today"),
    ("!summarize yesterday", "yesterday"),
    ("!summarize week", "the past week"),
    ("!summarize month", "the past month"),
    ("<@U_BOT_ADV> summarize week", "the past week"),
    ("@bot summarize month", "the past month"),
])
def test_summary_query_empty_records_clean_message(adv_listener, monkeypatch, trigger, expected_label):
    """Verify summary query with 0 matching records posts clean notification without exception or sheets append."""
    mock_get_records = MagicMock(return_value=[])
    mock_append = MagicMock()
    monkeypatch.setattr("sheets_service.get_records_in_range", mock_get_records)
    monkeypatch.setattr("sheets_service.append_record", mock_append)

    event = make_event(text=trigger, channel="C_SUMMARY", ts="1726000000.5555")
    adv_listener.handle_message(event)

    mock_get_records.assert_called_once()
    # Summary query must NEVER append a row to Google Sheets
    mock_append.assert_not_called()

    # Verify chat_postMessage was called with clean, human-readable notification
    adv_listener.app.client.chat_postMessage.assert_called_once()
    kwargs = adv_listener.app.client.chat_postMessage.call_args[1]
    assert kwargs["channel"] == "C_SUMMARY"
    assert kwargs["text"] == f"Nothing logged for {expected_label} yet."

    # Verify reaction was added to acknowledge summary command
    adv_listener.app.client.reactions_add.assert_called_once_with(
        channel="C_SUMMARY",
        name="eyes",
        timestamp="1726000000.5555",
    )


# ==============================================================================
# Challenge 5: Upstream Failures during Summary Queries
# ==============================================================================

def test_summary_query_sheets_service_failure(adv_listener, monkeypatch):
    """Verify upstream failure in sheets_service.get_records_in_range posts polite error message and does not crash."""
    monkeypatch.setattr(
        "sheets_service.get_records_in_range",
        MagicMock(side_effect=RuntimeError("FastMCP Server connection lost")),
    )

    event = make_event(text="!summarize week", channel="C_TEST")
    adv_listener.handle_message(event)

    adv_listener.app.client.chat_postMessage.assert_called_once()
    posted_text = adv_listener.app.client.chat_postMessage.call_args[1]["text"]
    assert "Unable to generate summary at this time due to an upstream service error." in posted_text


# ==============================================================================
# Challenge 6: conversation_tracker Failure Fallback
# ==============================================================================

def test_conversation_tracker_failure_uses_fallback_convo_id(adv_listener, monkeypatch):
    """Verify conversation_tracker failure falls back to convo_{ts} and General topic."""
    monkeypatch.setattr("ai_extractor.extract", MagicMock(return_value={"category": "Support", "notes": "Ticket", "action_items": [], "deadline": None}))
    monkeypatch.setattr(
        "conversation_tracker.get_or_create_conversation",
        MagicMock(side_effect=RuntimeError("Embeddings API unavailable")),
    )
    mock_append = MagicMock()
    monkeypatch.setattr("sheets_service.append_record", mock_append)

    event = make_event(text="Help with ticket #1234", ts="1726000000.8888")
    adv_listener.handle_message(event)

    mock_append.assert_called_once()
    record = mock_append.call_args[0][0]
    assert record["Conversation ID"] == "convo_1726000000.8888"
    assert record["Conversation Topic"] == "General"


# ==============================================================================
# Challenge 7: Malformed Event Payloads & Edge Cases
# ==============================================================================

@pytest.mark.parametrize("bad_event", [
    {},
    {"type": "message"},
    {"type": "message", "channel": "C123"},
    {"type": "message", "text": "   \n\t  "},
    {"type": "message", "text": "valid", "ts": "not-a-float"},
    {"type": "message", "text": "valid", "ts": None},
    {"type": "message", "text": "valid", "user": None},
])
def test_malformed_event_payloads_do_not_crash_bot(adv_listener, monkeypatch, bad_event):
    """Verify various malformed event payloads are handled gracefully without uncaught exceptions."""
    monkeypatch.setattr("sheets_service.append_record", MagicMock())
    monkeypatch.setattr("ai_extractor.extract", MagicMock(return_value={"category": "General", "notes": "", "action_items": [], "deadline": None}))

    try:
        adv_listener.handle_message(bad_event)
    except Exception as exc:
        pytest.fail(f"handle_message raised uncaught exception on payload {bad_event}: {exc}")


# ==============================================================================
# Challenge 8: High Concurrency Burst Stress Test
# ==============================================================================

def test_concurrent_message_burst_under_failure_conditions(adv_listener, monkeypatch):
    """Stress test 50 concurrent incoming messages with mixed normal and failing conditions.

    Empirically verifies thread safety, no deadlocks, and zero unhandled exceptions.
    """
    call_counter = 0

    def flaky_extract(text, author):
        nonlocal call_counter
        call_counter += 1
        if call_counter % 3 == 0:
            raise RuntimeError("Intermittent AI timeout")
        return {"category": "Testing", "notes": f"Note {call_counter}", "action_items": [], "deadline": None}

    monkeypatch.setattr("ai_extractor.extract", flaky_extract)
    monkeypatch.setattr("sheets_service.append_record", MagicMock())
    monkeypatch.setattr("conversation_tracker.get_or_create_conversation", MagicMock(return_value=("c1", "Topic")))

    events = [
        make_event(text=f"Concurrent message {i}", ts=f"172600000{i:02d}.0000")
        for i in range(50)
    ]

    errors = []

    def dispatch(ev):
        try:
            adv_listener.handle_message(ev)
        except Exception as e:
            errors.append(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(dispatch, ev) for ev in events]
        concurrent.futures.wait(futures)

    assert len(errors) == 0, f"Encountered {len(errors)} unhandled exceptions during concurrency test: {errors}"
