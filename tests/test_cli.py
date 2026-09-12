"""Comprehensive test suite for main.py CLI argument parsing and verification runner.

Tests:
- Argument parser: defaults, custom args, flag toggles, invalid transport
- main() routing: no args (usage help), --test-slack, --listen-slack, --test-mcp
- run_slack_test(): missing token, invalid prefix, auth failure, API exception, successful auth
- run_slack_listener(): normal execution, KeyboardInterrupt graceful stop, runtime exception
- run_mcp_verification(): missing credentials config, nonexistent credentials file,
  missing required tools, tool call exceptions, successful end-to-end pipeline with mock service
- cli() console script entrypoint: calls sys.exit(main())
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

import config
from main import (
    cli,
    create_parser,
    main,
    run_mcp_verification,
    run_slack_listener,
    run_slack_test,
)


# =====================================================================
# 1. Argument Parser Tests
# =====================================================================

class TestCliParser:
    """Tests for create_parser() argument definition and validation."""

    def test_cli_parser_defaults(self):
        """Verify default CLI argument values match configuration defaults."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.test_slack is False
        assert args.listen_slack is False
        assert args.test_mcp is False
        assert args.transport == "inproc"
        assert args.spreadsheet_id == config.GOOGLE_SHEET_ID
        assert args.calendar_id == config.GOOGLE_CALENDAR_ID
        assert args.tab_name == "TestRecords"

    def test_cli_parser_custom_args(self):
        """Verify parsing custom CLI arguments and transport selection."""
        parser = create_parser()
        args = parser.parse_args([
            "--test-mcp",
            "--transport", "stdio",
            "--spreadsheet-id", "custom_sheet_123",
            "--calendar-id", "custom@cal.com",
            "--tab-name", "CustomTab",
        ])

        assert args.test_mcp is True
        assert args.transport == "stdio"
        assert args.spreadsheet_id == "custom_sheet_123"
        assert args.calendar_id == "custom@cal.com"
        assert args.tab_name == "CustomTab"

    def test_cli_parser_slack_flags(self):
        """Verify parsing Slack action flags."""
        parser = create_parser()
        args_test = parser.parse_args(["--test-slack"])
        assert args_test.test_slack is True
        assert args_test.listen_slack is False

        args_listen = parser.parse_args(["--listen-slack"])
        assert args_listen.listen_slack is True
        assert args_listen.test_slack is False

    def test_cli_parser_invalid_transport(self):
        """Verify passing an unsupported transport choice exits with code 2."""
        parser = create_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--transport", "websocket"])
        assert exc_info.value.code == 2

    def test_cli_parser_help_flag(self, capsys):
        """Verify --help flag prints usage and exits with code 0."""
        parser = create_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--help"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "usage: agent-backend" in captured.out


# =====================================================================
# 2. Main Entrypoint Routing Tests
# =====================================================================

class TestCliMainRouting:
    """Tests for main() dispatch routing and exit codes."""

    def test_cli_main_no_args_shows_help(self, capsys):
        """Verify main() without arguments prints help to stdout and returns 0."""
        exit_code = main([])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "usage: agent-backend" in captured.out

    def test_cli_main_test_slack_success(self, monkeypatch):
        """Verify main --test-slack returns 0 when run_slack_test() succeeds."""
        monkeypatch.setattr("main.run_slack_test", lambda: True)
        assert main(["--test-slack"]) == 0

    def test_cli_main_test_slack_failure(self, monkeypatch):
        """Verify main --test-slack returns 1 when run_slack_test() fails."""
        monkeypatch.setattr("main.run_slack_test", lambda: False)
        assert main(["--test-slack"]) == 1

    def test_cli_main_listen_slack_success(self, monkeypatch):
        """Verify main --listen-slack returns 0 when run_slack_listener() succeeds."""
        monkeypatch.setattr("main.run_slack_listener", lambda: True)
        assert main(["--listen-slack"]) == 0

    def test_cli_main_listen_slack_failure(self, monkeypatch):
        """Verify main --listen-slack returns 1 when run_slack_listener() fails."""
        monkeypatch.setattr("main.run_slack_listener", lambda: False)
        assert main(["--listen-slack"]) == 1

    def test_cli_main_test_mcp_success(self, monkeypatch):
        """Verify main --test-mcp returns 0 when run_mcp_verification() succeeds."""
        mock_run = AsyncMock(return_value=True)
        monkeypatch.setattr("main.run_mcp_verification", mock_run)

        exit_code = main(["--test-mcp", "--transport", "stdio"])
        assert exit_code == 0
        mock_run.assert_awaited_once_with(
            transport="stdio",
            spreadsheet_id=config.GOOGLE_SHEET_ID,
            calendar_id=config.GOOGLE_CALENDAR_ID,
            tab_name="TestRecords",
        )

    def test_cli_main_test_mcp_failure(self, monkeypatch):
        """Verify main --test-mcp returns 1 when run_mcp_verification() fails."""
        mock_run = AsyncMock(return_value=False)
        monkeypatch.setattr("main.run_mcp_verification", mock_run)

        exit_code = main(["--test-mcp"])
        assert exit_code == 1
        mock_run.assert_awaited_once()


# =====================================================================
# 3. Slack Auth Verification Tests (run_slack_test)
# =====================================================================

class TestRunSlackTest:
    """Tests for run_slack_test() pre-flight and execution logic."""

    def test_run_slack_test_missing_token(self, monkeypatch):
        """Verify run_slack_test returns False when SLACK_BOT_TOKEN is empty."""
        monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "")
        assert run_slack_test() is False

    def test_run_slack_test_none_token(self, monkeypatch):
        """Verify run_slack_test returns False when SLACK_BOT_TOKEN is None."""
        monkeypatch.setattr(config, "SLACK_BOT_TOKEN", None)
        assert run_slack_test() is False

    def test_run_slack_test_invalid_prefix(self, monkeypatch):
        """Verify run_slack_test returns False when SLACK_BOT_TOKEN lacks xoxb- prefix."""
        monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "xoxp-user-token-not-bot")
        assert run_slack_test() is False

    def test_run_slack_test_auth_failure_response(self, monkeypatch):
        """Verify run_slack_test returns False when auth_test() returns ok=False."""
        monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "xoxb-valid-prefix-token")
        mock_client = MagicMock()
        mock_client.auth_test.return_value = {"ok": False, "error": "invalid_auth"}

        with patch("slack_sdk.WebClient", return_value=mock_client):
            assert run_slack_test() is False

    def test_run_slack_test_api_exception(self, monkeypatch):
        """Verify run_slack_test returns False when WebClient raises an exception."""
        monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "xoxb-valid-prefix-token")
        mock_client = MagicMock()
        mock_client.auth_test.side_effect = ConnectionError("Slack API endpoint unreachable")

        with patch("slack_sdk.WebClient", return_value=mock_client):
            assert run_slack_test() is False

    def test_run_slack_test_success(self, monkeypatch, capsys):
        """Verify run_slack_test returns True and prints identity info on success."""
        monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "xoxb-valid-test-token")
        mock_client = MagicMock()
        mock_client.auth_test.return_value = {
            "ok": True,
            "team": "Test Workspace",
            "team_id": "T12345",
            "user": "test_bot_user",
            "user_id": "U12345",
            "bot_id": "B12345",
        }

        with patch("slack_sdk.WebClient", return_value=mock_client):
            assert run_slack_test() is True

        captured = capsys.readouterr()
        assert "Slack Bot Authentication Successful!" in captured.out
        assert "Test Workspace" in captured.out
        assert "test_bot_user" in captured.out
        assert "U12345" in captured.out
        assert "B12345" in captured.out


# =====================================================================
# 4. Slack Listener Tests (run_slack_listener)
# =====================================================================

class TestRunSlackListener:
    """Tests for run_slack_listener() execution and signal trapping."""

    def test_run_slack_listener_success(self, monkeypatch):
        """Verify run_slack_listener returns True when start_slack_bot() exits cleanly."""
        monkeypatch.setattr("slack_bot.start_slack_bot", lambda: None)
        assert run_slack_listener() is True

    def test_run_slack_listener_keyboard_interrupt(self, monkeypatch, capsys):
        """Verify run_slack_listener traps KeyboardInterrupt and returns True."""
        def _raise_sigint():
            raise KeyboardInterrupt()

        monkeypatch.setattr("slack_bot.start_slack_bot", _raise_sigint)
        assert run_slack_listener() is True
        captured = capsys.readouterr()
        assert "Slack listener stopped by user" in captured.out

    def test_run_slack_listener_exception(self, monkeypatch, capsys):
        """Verify run_slack_listener traps runtime exception, logs to stderr, and returns False."""
        def _raise_error():
            raise RuntimeError("Fatal SocketMode connection failure")

        monkeypatch.setattr("slack_bot.start_slack_bot", _raise_error)
        assert run_slack_listener() is False
        captured = capsys.readouterr()
        assert "Fatal SocketMode connection failure" in captured.err


# =====================================================================
# 5. MCP Verification Pipeline Tests (run_mcp_verification)
# =====================================================================

class TestRunMcpVerification:
    """Tests for run_mcp_verification() pre-flight and execution pipeline."""

    @pytest.mark.anyio
    async def test_mcp_verification_missing_credentials_config(self, monkeypatch):
        """Verify run_mcp_verification fails when GOOGLE_SERVICE_ACCOUNT_FILE is empty."""
        monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "")
        assert await run_mcp_verification(transport="inproc") is False

    @pytest.mark.anyio
    async def test_mcp_verification_nonexistent_credentials_file(self, monkeypatch):
        """Verify run_mcp_verification fails when credentials file does not exist on disk."""
        monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", "/nonexistent/path/creds.json")
        assert await run_mcp_verification(transport="inproc") is False

    @pytest.mark.anyio
    async def test_mcp_verification_missing_tools(self, tmp_path, monkeypatch):
        """Verify run_mcp_verification fails when required MCP tools are not registered."""
        fake_key = tmp_path / "fake_key.json"
        fake_key.write_text("{}")
        monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", str(fake_key))

        mock_client = MagicMock()
        tool_mock = MagicMock()
        tool_mock.name = "sheets_ensure_tab"
        # Only 1 tool provided, missing sheets_append_rows and calendar_create_event
        tool_resp = MagicMock()
        tool_resp.tools = [tool_mock]
        mock_client.list_tools = AsyncMock(return_value=tool_resp)

        class MockCM:
            async def __aenter__(self):
                return mock_client
            async def __aexit__(self, *args):
                pass

        monkeypatch.setattr("src.mcp_client.client.get_mcp_client", lambda transport: MockCM())
        assert await run_mcp_verification(transport="inproc") is False

    @pytest.mark.anyio
    async def test_mcp_verification_tool_execution_error(self, tmp_path, monkeypatch):
        """Verify run_mcp_verification handles tool execution exceptions gracefully."""
        fake_key = tmp_path / "fake_key.json"
        fake_key.write_text("{}")
        monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", str(fake_key))

        mock_client = MagicMock()
        tools = []
        for name in ["sheets_ensure_tab", "sheets_append_rows", "calendar_create_event"]:
            t = MagicMock()
            t.name = name
            tools.append(t)
        tool_resp = MagicMock()
        tool_resp.tools = tools
        mock_client.list_tools = AsyncMock(return_value=tool_resp)

        class MockCM:
            async def __aenter__(self):
                return mock_client
            async def __aexit__(self, *args):
                pass

        monkeypatch.setattr("src.mcp_client.client.get_mcp_client", lambda transport: MockCM())
        monkeypatch.setattr(
            "src.mcp_client.client.call_mcp_tool",
            AsyncMock(side_effect=RuntimeError("Google Sheets API 500 internal error")),
        )

        assert await run_mcp_verification(transport="inproc") is False

    @pytest.mark.anyio
    async def test_mcp_verification_inproc_mocked_success(self, mock_workspace_service, tmp_path, monkeypatch):
        """Verify run_mcp_verification end-to-end pipeline succeeds with mocked service."""
        fake_key = tmp_path / "fake_key.json"
        fake_key.write_text("{}")
        monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_FILE", str(fake_key))

        success = await run_mcp_verification(
            transport="inproc",
            spreadsheet_id="mock_sheet_id",
            calendar_id="mock_cal_id",
            tab_name="TestRecords",
        )
        assert success is True


# =====================================================================
# 6. Console Script Entrypoint Tests (cli)
# =====================================================================

class TestCliEntrypoint:
    """Tests for cli() packaging entrypoint."""

    def test_cli_entrypoint_calls_main_and_exits(self, monkeypatch):
        """Verify cli() calls main() and terminates via sys.exit with returned status code."""
        monkeypatch.setattr("main.main", lambda argv=None: 42)
        with pytest.raises(SystemExit) as exc_info:
            cli()
        assert exc_info.value.code == 42

    def test_cli_entrypoint_success_exit(self, monkeypatch):
        """Verify cli() exits with code 0 when main returns 0."""
        monkeypatch.setattr("main.main", lambda argv=None: 0)
        with pytest.raises(SystemExit) as exc_info:
            cli()
        assert exc_info.value.code == 0
