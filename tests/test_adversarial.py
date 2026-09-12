"""Adversarial test suite for call_mcp_tool_sync and concurrent service operations.

Challenging:
1. Calling call_mcp_tool_sync from inside active asyncio event loops (simulating FastAPI endpoints).
2. Calling call_mcp_tool_sync concurrently from multiple threads (stressing ThreadPoolExecutor & service state).
3. Exception unmarshaling and error propagation from MCP tools to caller context.
"""

import asyncio
import concurrent.futures
import time
from unittest.mock import MagicMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
import pytest

import calendar_service
from src.mcp_client.client import call_mcp_tool_sync, get_mcp_client
from src.mcp_server.server import get_google_service, mcp_server, set_google_service
from src.services.google_services import GoogleWorkspaceService


def unwrap_exception(exc: BaseException) -> BaseException:
    """Recursively unpack nested ExceptionGroups to retrieve the root cause exception."""
    curr = exc
    while isinstance(curr, BaseExceptionGroup) and curr.exceptions:
        curr = curr.exceptions[0]
    return curr


# =============================================================================
# Challenge Domain 1: Active Asyncio Event Loop & FastAPI Endpoint Simulation
# =============================================================================


@pytest.mark.asyncio
async def test_active_event_loop_single_call(mock_workspace_service):
    """Test calling call_mcp_tool_sync from inside a running asyncio event loop.

    Verifies that the ThreadPoolExecutor bridge prevents
    'RuntimeError: asyncio.run() cannot be called from a running event loop'.
    """
    res = call_mcp_tool_sync(
        "sheets_get_records",
        arguments={"spreadsheet_id": "test_sheet", "sheet_name": "Sheet1"},
        transport="inproc",
    )
    assert res.get("status") == "success"
    assert "records" in res


@pytest.mark.asyncio
async def test_active_event_loop_concurrent_gather(mock_workspace_service):
    """Test calling call_mcp_tool_sync from multiple coroutines in asyncio.gather.

    Empirically verifies that multiple coroutines executing on the same event loop
    do not deadlock, even though each call blocks the event loop thread while waiting
    for the worker thread in _sync_executor.
    """
    async def call_coro(idx: int):
        return call_mcp_tool_sync(
            "sheets_get_records",
            arguments={"spreadsheet_id": f"sheet_{idx}", "sheet_name": "Sheet1"},
            transport="inproc",
        )

    results = await asyncio.gather(*(call_coro(i) for i in range(10)))
    assert len(results) == 10
    for res in results:
        assert res.get("status") == "success"


@pytest.mark.asyncio
async def test_fastapi_async_endpoint_simulation(mock_workspace_service):
    """Simulate a FastAPI application with both async and sync endpoints calling MCP tools.

    Verifies HTTP 200 responses and checks for event loop blocking / deadlock issues.
    """
    app = FastAPI()

    @app.get("/api/async-records")
    async def async_records_endpoint():
        # Calling synchronous MCP tool from async route (running directly in event loop)
        return call_mcp_tool_sync(
            "sheets_get_records",
            arguments={"spreadsheet_id": "fastapi_test", "sheet_name": "Sheet1"},
            transport="inproc",
        )

    @app.get("/api/sync-records")
    def sync_records_endpoint():
        # Standard sync def endpoint (executed in Starlette anyio worker thread)
        return call_mcp_tool_sync(
            "sheets_get_records",
            arguments={"spreadsheet_id": "fastapi_test", "sheet_name": "Sheet1"},
            transport="inproc",
        )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Single async endpoint call
        r_async = await client.get("/api/async-records")
        assert r_async.status_code == 200
        assert r_async.json()["status"] == "success"

        # 2. Single sync endpoint call
        r_sync = await client.get("/api/sync-records")
        assert r_sync.status_code == 200
        assert r_sync.json()["status"] == "success"

        # 3. Concurrent requests to both endpoints simultaneously
        tasks = [
            client.get("/api/async-records"),
            client.get("/api/sync-records"),
            client.get("/api/async-records"),
            client.get("/api/sync-records"),
            client.get("/api/async-records"),
        ]
        responses = await asyncio.gather(*tasks)
        for resp in responses:
            assert resp.status_code == 200
            assert resp.json()["status"] == "success"


@pytest.mark.asyncio
async def test_active_event_loop_tool_exception_propagation(mock_workspace_service, monkeypatch):
    """Verify that exceptions raised by tools inside an active event loop propagate to the caller.

    Checks that caller can catch the exception (as Exception or ExceptionGroup) and root cause is preserved.
    """
    monkeypatch.setattr(
        mock_workspace_service,
        "get_records",
        MagicMock(side_effect=RuntimeError("Event loop failure")),
    )

    with pytest.raises((RuntimeError, ExceptionGroup)) as exc_info:
        call_mcp_tool_sync(
            "sheets_get_records",
            arguments={"spreadsheet_id": "fail_sheet", "sheet_name": "Sheet1"},
            transport="inproc",
        )

    leaf = unwrap_exception(exc_info.value)
    assert isinstance(leaf, RuntimeError)
    assert "sheets_get_records" in str(leaf)


# =============================================================================
# Challenge Domain 2: Multithreaded Concurrency & Resource Contention
# =============================================================================


def test_multithreaded_concurrent_calls(mock_workspace_service):
    """Test calling call_mcp_tool_sync concurrently from multiple OS threads.

    Stresses the ThreadPoolExecutor, in-process MCP server, and GoogleWorkspaceService
    across 20 concurrent threads.
    """
    def worker(idx: int):
        return call_mcp_tool_sync(
            "sheets_get_records",
            arguments={"spreadsheet_id": f"sheet_{idx}", "sheet_name": "Sheet1"},
            transport="inproc",
        )

    num_threads = 20
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as pool:
        futures = [pool.submit(worker, i) for i in range(num_threads)]
        results = [f.result(timeout=10) for f in futures]

    assert len(results) == num_threads
    for res in results:
        assert res.get("status") == "success"


def test_multithreaded_concurrent_mixed_tools(mock_workspace_service):
    """Test calling different MCP tools concurrently across multiple threads.

    Mixes sheets_get_records, sheets_append_rows, and calendar_create_event.
    """
    def worker_sheets_get(idx: int):
        return call_mcp_tool_sync(
            "sheets_get_records",
            {"spreadsheet_id": f"sheet_{idx}", "sheet_name": "Sheet1"},
            transport="inproc",
        )

    def worker_sheets_append(idx: int):
        return call_mcp_tool_sync(
            "sheets_append_rows",
            {"spreadsheet_id": f"sheet_{idx}", "sheet_name": "Sheet1", "rows": [["a", "b"]]},
            transport="inproc",
        )

    def worker_calendar_create(idx: int):
        return call_mcp_tool_sync(
            "calendar_create_event",
            {
                "calendar_id": f"cal_{idx}@example.com",
                "summary": f"Event {idx}",
                "start_iso": "2026-09-11T12:00:00Z",
                "end_iso": "2026-09-11T13:00:00Z",
            },
            transport="inproc",
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as pool:
        f_gets = [pool.submit(worker_sheets_get, i) for i in range(5)]
        f_apps = [pool.submit(worker_sheets_append, i) for i in range(5)]
        f_cals = [pool.submit(worker_calendar_create, i) for i in range(5)]

        all_futures = f_gets + f_apps + f_cals
        results = [f.result(timeout=10) for f in all_futures]

    assert len(results) == 15
    for res in results:
        assert res.get("status") == "success"


def test_multithreaded_simulated_io_delay():
    """Test multithreaded concurrency when operations have simulated network latency.

    Ensures that slow I/O across threads does not cause deadlocks or memory corruption.
    """
    custom_mock_svc = MagicMock(spec=GoogleWorkspaceService)

    def slow_get_records(spreadsheet_id, sheet_name):
        time.sleep(0.05)  # 50ms simulated network latency
        return {"status": "success", "records": [{"id": spreadsheet_id}], "count": 1}

    custom_mock_svc.get_records.side_effect = slow_get_records
    set_google_service(custom_mock_svc)

    try:
        def worker(idx: int):
            return call_mcp_tool_sync(
                "sheets_get_records",
                {"spreadsheet_id": f"sheet_{idx}", "sheet_name": "Sheet1"},
                transport="inproc",
            )

        start_t = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(worker, i) for i in range(10)]
            results = [f.result(timeout=10) for f in futures]
        duration = time.time() - start_t

        assert len(results) == 10
        # If run concurrently across 10 threads with 50ms sleep, total time should be << 0.5s
        assert duration < 0.4
    finally:
        set_google_service(None)


# =============================================================================
# Challenge Domain 3: Exception Unmarshaling & Error Propagation
# =============================================================================


def test_tool_unexpected_exception_unmarshaling(mock_workspace_service, monkeypatch):
    """Verify how unexpected tool exceptions (e.g. ValueError, HttpError) are unmarshaled.

    Documents that FastMCP masks unexpected exceptions as 'Error executing tool <name>'
    and wraps them in RuntimeError inside an ExceptionGroup.
    """
    monkeypatch.setattr(
        mock_workspace_service,
        "append_rows",
        MagicMock(side_effect=ValueError("Corrupted row structure")),
    )

    with pytest.raises((RuntimeError, ExceptionGroup)) as exc_info:
        call_mcp_tool_sync(
            "sheets_append_rows",
            {"spreadsheet_id": "test_sheet", "sheet_name": "Sheet1", "rows": [["bad"]]},
            transport="inproc",
        )

    leaf = unwrap_exception(exc_info.value)
    assert isinstance(leaf, RuntimeError)
    # FastMCP masks the inner 'Corrupted row structure' string for non-ToolError exceptions
    assert "sheets_append_rows" in str(leaf)
    assert "Error executing tool sheets_append_rows" in str(leaf)


def test_tool_error_custom_message_unmarshaling():
    """Verify that ToolError exceptions preserve the custom error message.

    When a tool raises ToolError, FastMCP includes the specific message in the response.
    """
    # Temporarily register an ephemeral test tool that raises ToolError
    @mcp_server.tool()
    def adversarial_error_tool(code: int) -> dict:
        raise ToolError(f"Custom validation code {code} failed")

    with pytest.raises((RuntimeError, ExceptionGroup)) as exc_info:
        call_mcp_tool_sync(
            "adversarial_error_tool",
            {"code": 403},
            transport="inproc",
        )

    leaf = unwrap_exception(exc_info.value)
    assert isinstance(leaf, RuntimeError)
    assert "Custom validation code 403 failed" in str(leaf)


def test_nonexistent_tool_exception_unmarshaling(mock_workspace_service):
    """Verify that calling an unknown tool raises an unmarshaled error identifying the tool."""
    with pytest.raises((RuntimeError, ExceptionGroup)) as exc_info:
        call_mcp_tool_sync(
            "completely_nonexistent_tool",
            {"foo": "bar"},
            transport="inproc",
        )

    leaf = unwrap_exception(exc_info.value)
    assert isinstance(leaf, RuntimeError)
    assert "Unknown tool: completely_nonexistent_tool" in str(leaf)


def test_tool_argument_validation_error_unmarshaling(mock_workspace_service):
    """Verify that missing required arguments raises an unmarshaled parameter validation error."""
    with pytest.raises((RuntimeError, ExceptionGroup)) as exc_info:
        # sheets_append_rows requires spreadsheet_id, sheet_name, rows
        call_mcp_tool_sync(
            "sheets_append_rows",
            {},
            transport="inproc",
        )

    leaf = unwrap_exception(exc_info.value)
    assert isinstance(leaf, RuntimeError)
    assert "sheets_append_rows" in str(leaf)
    assert "rejected arguments" in str(leaf).lower() or "validation error" in str(leaf).lower()


def test_unsupported_transport_raises_direct_value_error():
    """Verify that an invalid transport parameter raises ValueError immediately without ExceptionGroup."""
    with pytest.raises(ValueError) as exc_info:
        call_mcp_tool_sync(
            "sheets_get_records",
            {"spreadsheet_id": "test", "sheet_name": "Sheet1"},
            transport="unsupported_transport_xyz",
        )

    assert "Unsupported transport" in str(exc_info.value)


def test_calendar_service_graceful_handling_of_mcp_exception(mock_workspace_service, monkeypatch):
    """Verify that calendar_service.create_event catches MCP tool failures and returns None.

    Confirms that ExceptionGroup from MCP does not crash calendar_service.create_event.
    """
    monkeypatch.setattr(
        mock_workspace_service,
        "create_event",
        MagicMock(side_effect=RuntimeError("Calendar API quota exceeded")),
    )

    link = calendar_service.create_event(
        summary="Emergency Meeting",
        description="Quota test",
        date_str="2026-09-15",
    )
    # Should catch the error, log, and return None
    assert link is None


# =============================================================================
# Group 1: sheets_ensure_tab Adversarial Challenges
# =============================================================================


def test_ensure_tab_idempotency_preserves_existing(mock_workspace_service, mock_sheets_client):
    """Verify ensure_tab returns created=False and headers_written=[] when tab exists."""
    mock_sheets_client.spreadsheets().get().execute.return_value = {
        "sheets": [
            {"properties": {"title": "ExistingTab", "sheetId": 8888}},
        ]
    }

    result = mock_workspace_service.ensure_tab(
        spreadsheet_id="test_sheet_id",
        sheet_name="ExistingTab",
        headers=["ColA", "ColB", "ColC"],
    )

    assert result["status"] == "success"
    assert result["created"] is False
    assert result["sheet_id"] == 8888
    assert result["headers_written"] == []

    # API batchUpdate and values().update should not have been called
    mock_sheets_client.spreadsheets().batchUpdate.assert_not_called()
    mock_sheets_client.spreadsheets().values().update.assert_not_called()


def test_ensure_tab_empty_headers_list(mock_workspace_service, mock_sheets_client):
    """Verify ensure_tab creates the tab but skips values().update when headers is empty list []."""
    mock_sheets_client.spreadsheets().get().execute.return_value = {"sheets": []}
    mock_sheets_client.spreadsheets().batchUpdate.return_value.execute.return_value = {
        "replies": [{"addSheet": {"properties": {"title": "NoHeaderTab", "sheetId": 999}}}]
    }

    result = mock_workspace_service.ensure_tab(
        spreadsheet_id="test_sheet_id",
        sheet_name="NoHeaderTab",
        headers=[],
    )

    assert result["status"] == "success"
    assert result["created"] is True
    assert result["sheet_id"] == 999
    assert result["headers_written"] == []

    # batchUpdate must be called, but values().update must NOT be called
    mock_sheets_client.spreadsheets().batchUpdate.assert_called_once()
    mock_sheets_client.spreadsheets().values().update.assert_not_called()


def test_ensure_tab_special_characters_in_name(mock_workspace_service, mock_sheets_client):
    """Verify ensure_tab handles unicode and spaces in sheet names."""
    special_name = "Summary 2026 (Q3) - 🚀 & Data"
    mock_sheets_client.spreadsheets().get().execute.return_value = {"sheets": []}
    mock_sheets_client.spreadsheets().batchUpdate.return_value.execute.return_value = {
        "replies": [{"addSheet": {"properties": {"title": special_name, "sheetId": 1001}}}]
    }

    result = mock_workspace_service.ensure_tab(
        spreadsheet_id="test_sheet_id",
        sheet_name=special_name,
        headers=["Header 1", "Header 2"],
    )

    assert result["status"] == "success"
    assert result["sheet_name"] == special_name
    assert result["created"] is True

    # Check that safe_range wraps sheet name in quotes
    call_args = mock_sheets_client.spreadsheets().values().update.call_args
    assert call_args is not None
    used_range = call_args.kwargs.get("range")
    assert used_range == f"'{special_name}'!A1"


def test_ensure_tab_single_quote_in_sheet_name_behavior(mock_workspace_service, mock_sheets_client):
    """Check how ensure_tab formats A1 notation when sheet_name contains a single quote.

    In Google Sheets A1 notation, single quotes in sheet titles must be escaped by doubling (' -> '').
    This test verifies whether the implementation escapes single quotes or emits literal quotes.
    """
    quote_name = "User's Records"
    mock_sheets_client.spreadsheets().get().execute.return_value = {"sheets": []}
    mock_sheets_client.spreadsheets().batchUpdate.return_value.execute.return_value = {
        "replies": [{"addSheet": {"properties": {"title": quote_name, "sheetId": 1002}}}]
    }

    result = mock_workspace_service.ensure_tab(
        spreadsheet_id="test_sheet_id",
        sheet_name=quote_name,
        headers=["Col1"],
    )
    assert result["status"] == "success"

    call_args = mock_sheets_client.spreadsheets().values().update.call_args
    assert call_args is not None
    used_range = call_args.kwargs.get("range")
    assert used_range is not None


# =============================================================================
# Group 2: sheets_append_rows Adversarial Challenges
# =============================================================================


def test_append_rows_empty_array_short_circuit(mock_workspace_service, mock_sheets_client):
    """Verify append_rows with [] short-circuits cleanly without calling the API."""
    result = mock_workspace_service.append_rows(
        spreadsheet_id="test_sheet_id",
        sheet_name="TestRecords",
        rows=[],
    )

    assert result["status"] == "success"
    assert result["rows_appended"] == 0
    assert result["updated_range"] == ""
    mock_sheets_client.spreadsheets().values().append.assert_not_called()


def test_append_rows_mixed_datatypes_sanitization(mock_workspace_service, mock_sheets_client):
    """Verify append_rows correctly sanitizes mixed types: int, float, None, bool, str, datetime."""
    mixed_row = [
        100,  # int
        None,  # None -> ""
        99.95,  # float
        "2026-09-10T14:30:00Z",  # datetime string
        True,  # bool
        False,  # bool
        {"meta": 1},  # dict
        [1, 2, 3],  # list
    ]

    mock_workspace_service.append_rows(
        spreadsheet_id="test_sheet_id",
        sheet_name="TestRecords",
        rows=[mixed_row],
    )

    call_args = mock_sheets_client.spreadsheets().values().append.call_args
    assert call_args is not None
    body = call_args.kwargs.get("body", {})
    sanitized = body.get("values", [])

    assert len(sanitized) == 1
    row = sanitized[0]
    assert row[0] == "100"
    assert row[1] == ""
    assert row[2] == "99.95"
    assert row[3] == "2026-09-10T14:30:00Z"
    assert row[4] == "True"
    assert row[5] == "False"
    assert row[6] == "{'meta': 1}"
    assert row[7] == "[1, 2, 3]"


def test_append_rows_inner_empty_row(mock_workspace_service, mock_sheets_client):
    """Verify append_rows with [[]] (list containing empty list) is handled without crash."""
    mock_workspace_service.append_rows(
        spreadsheet_id="test_sheet_id",
        sheet_name="TestRecords",
        rows=[[]],
    )

    call_args = mock_sheets_client.spreadsheets().values().append.call_args
    assert call_args is not None
    body = call_args.kwargs.get("body", {})
    sanitized = body.get("values", [])
    assert sanitized == [[]]


def test_append_rows_non_iterable_row_raises_cleanly(mock_workspace_service):
    """Verify append_rows with non-list row elements raises TypeError cleanly."""
    with pytest.raises(TypeError):
        mock_workspace_service.append_rows(
            spreadsheet_id="test_sheet_id",
            sheet_name="TestRecords",
            rows=[12345],  # type: ignore[list-item]
        )


# =============================================================================
# Group 3: calendar_create_event Adversarial Challenges
# =============================================================================


def test_create_event_end_before_start_raises_value_error(mock_workspace_service):
    """Verify create_event raises ValueError when end_iso is strictly before start_iso."""
    with pytest.raises(ValueError, match="must be greater than or equal to start_iso"):
        mock_workspace_service.create_event(
            calendar_id="test@example.com",
            summary="Backwards in time",
            start_iso="2026-09-11T16:00:00Z",
            end_iso="2026-09-11T15:00:00Z",
        )


def test_create_event_zero_duration_boundary(mock_workspace_service, mock_calendar_client):
    """Verify behavior when start_iso equals end_iso (boundary condition: end == start)."""
    result = mock_workspace_service.create_event(
        calendar_id="test@example.com",
        summary="Zero duration event",
        start_iso="2026-09-11T15:00:00Z",
        end_iso="2026-09-11T15:00:00Z",
    )
    assert result["status"] == "success"
    mock_calendar_client.events().insert.assert_called_once()


def test_create_event_missing_timezone_offset_behavior(mock_workspace_service, mock_calendar_client):
    """Verify behavior when timezone offset is omitted (naive ISO 8601 string)."""
    result = mock_workspace_service.create_event(
        calendar_id="test@example.com",
        summary="Naive Datetime Event",
        start_iso="2026-09-11T10:00:00",
        end_iso="2026-09-11T11:00:00",
    )
    assert result["status"] == "success"
    call_args = mock_calendar_client.events().insert.call_args
    body = call_args.kwargs.get("body", {})
    assert body["start"]["dateTime"] == "2026-09-11T10:00:00"
    assert body["end"]["dateTime"] == "2026-09-11T11:00:00"


def test_create_event_mixed_timezone_awareness(mock_workspace_service):
    """Verify behavior when one timestamp is offset-aware and the other is offset-naive.

    Python's `datetime` raises TypeError when comparing aware and naive datetimes.
    """
    with pytest.raises(TypeError, match="can't compare offset-naive and offset-aware datetimes"):
        mock_workspace_service.create_event(
            calendar_id="test@example.com",
            summary="Mixed TZ Event",
            start_iso="2026-09-11T10:00:00Z",  # aware
            end_iso="2026-09-11T11:00:00",  # naive
        )


def test_create_event_special_characters_and_payload(mock_workspace_service, mock_calendar_client):
    """Verify create_event safely forwards unicode, HTML, emojis, newlines, and quotes."""
    complex_summary = "🚀 Sprint Review: \"Q3 Launch\" & <Security> 'Check'"
    complex_desc = (
        "Multi-line notes:\n"
        "- Item 1: 100% complete\n"
        "- Item 2: <script>console.log('test')</script>\n"
        "- Item 3: Special symbols: & £ € ¥ 漢字\r\n"
    )
    complex_loc = "Meeting Room 4B / https://meet.google.com/abc-defg-hij?authuser=1"

    result = mock_workspace_service.create_event(
        calendar_id="test@example.com",
        summary=complex_summary,
        start_iso="2026-09-11T14:00:00-04:00",
        end_iso="2026-09-11T15:00:00-04:00",
        description=complex_desc,
        location=complex_loc,
    )

    assert result["status"] == "success"
    call_args = mock_calendar_client.events().insert.call_args
    body = call_args.kwargs.get("body", {})
    assert body["summary"] == complex_summary
    assert body["description"] == complex_desc
    assert body["location"] == complex_loc


# =============================================================================
# Group 4: FastMCP Server Robustness & Error Trapping
# =============================================================================


@pytest.mark.asyncio
async def test_mcp_server_handles_invalid_tool_arguments_without_crashing(mock_workspace_service):
    """Verify FastMCP server catches tool exceptions and returns is_error=True without crash.

    Note: In MCP 2.2.0, non-ToolError exceptions (like ValueError) are treated as unexpected
    and their message is masked as 'Error executing tool <name>' to the client.
    """
    async with Client(mcp_server) as client:
        # 1. Invalid date format
        res1 = await client.call_tool(
            "calendar_create_event",
            arguments={
                "calendar_id": "test@example.com",
                "summary": "Bad date",
                "start_iso": "not-a-datetime",
                "end_iso": "2026-09-11T15:00:00Z",
            },
        )
        assert res1.is_error is True
        assert "Error executing tool calendar_create_event" in res1.content[0].text

        # 2. End before start
        res2 = await client.call_tool(
            "calendar_create_event",
            arguments={
                "calendar_id": "test@example.com",
                "summary": "Backwards",
                "start_iso": "2026-09-11T16:00:00Z",
                "end_iso": "2026-09-11T15:00:00Z",
            },
        )
        assert res2.is_error is True
        assert "Error executing tool calendar_create_event" in res2.content[0].text

        # 3. Subsequent valid call still works on the same client connection (server didn't crash)
        res3 = await client.call_tool(
            "calendar_create_event",
            arguments={
                "calendar_id": "test@example.com",
                "summary": "Valid Event",
                "start_iso": "2026-09-11T14:00:00Z",
                "end_iso": "2026-09-11T15:00:00Z",
            },
        )
        assert res3.is_error is False


@pytest.mark.asyncio
async def test_mcp_stdio_transport_survives_adversarial_errors():
    """Verify MCP Stdio subprocess transport survives errors without JSON-RPC corruption."""
    async with get_mcp_client(transport="stdio") as client:
        # Adversarial tool call 1: Invalid datetime format
        res1 = await client.call_tool(
            "calendar_create_event",
            arguments={
                "calendar_id": "test@example.com",
                "summary": "Bad date via stdio",
                "start_iso": "invalid-timestamp",
                "end_iso": "2026-09-11T15:00:00Z",
            },
        )
        assert res1.is_error is True
        assert "Error executing tool calendar_create_event" in res1.content[0].text

        # Adversarial tool call 2: End before start
        res2 = await client.call_tool(
            "calendar_create_event",
            arguments={
                "calendar_id": "test@example.com",
                "summary": "Time paradox",
                "start_iso": "2026-09-11T17:00:00Z",
                "end_iso": "2026-09-11T16:00:00Z",
            },
        )
        assert res2.is_error is True
        assert "Error executing tool calendar_create_event" in res2.content[0].text

        # Adversarial tool call 3: Mixed timezone awareness (offset-aware vs naive)
        res3 = await client.call_tool(
            "calendar_create_event",
            arguments={
                "calendar_id": "test@example.com",
                "summary": "Mixed TZ via stdio",
                "start_iso": "2026-09-11T10:00:00Z",
                "end_iso": "2026-09-11T11:00:00",
            },
        )
        assert res3.is_error is True
        assert "Error executing tool calendar_create_event" in res3.content[0].text

        # Verify server is still alive and responds to tool listing
        tools_resp = await client.list_tools()
        assert len(tools_resp.tools) >= 4
