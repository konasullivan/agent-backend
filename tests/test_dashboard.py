"""Comprehensive test suite for FastAPI Interaction Log Dashboard.

Tests:
- GET /: HTMLResponse, 200 status, table header rendering, reverse chronological ordering
- GET /api/records: JSONResponse, 200 status, exact list of record dicts, chronological ordering
- Empty record states: handles 0 records cleanly without error
- Exception propagation & error handling (HTTP 500 / exception bubbling)
- Live/mock MCP client normalization (legacy column remapping and default headers)
- Template formatting, styling, conditional element rendering, and XSS autoescaping
"""

from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

import config
from dashboard.app import app


@pytest.fixture
def client():
    """Create a FastAPI TestClient that captures HTTP errors without raising exceptions."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def strict_client():
    """Create a FastAPI TestClient that raises server exceptions directly to the caller."""
    return TestClient(app, raise_server_exceptions=True)


SAMPLE_RECORDS_11_COLS = [
    {
        "Message": "First interaction (oldest)",
        "Category": "Fundraiser",
        "Conversation ID": "convo_001",
        "Conversation Topic": "Charity Gala",
        "Staff Member": "Alice Smith",
        "Subteam": "Outreach",
        "Date": "2026-09-01 10:00:00",
        "Notes": "Initial setup meeting",
        "Action Items": "Draft proposal",
        "Deadline": "2026-09-10",
        "Link": "https://slack.com/archives/C123/p1001",
    },
    {
        "Message": "Second interaction (middle)",
        "Category": "Trading Project",
        "Conversation ID": "convo_002",
        "Conversation Topic": "Order Book",
        "Staff Member": "Bob Jones",
        "Subteam": "Engineering",
        "Date": "2026-09-05 14:30:00",
        "Notes": "Performance benchmarking",
        "Action Items": "Optimize latency",
        "Deadline": "",
        "Link": "",
    },
    {
        "Message": "Third interaction (newest)",
        "Category": "Customer Service",
        "Conversation ID": "convo_003",
        "Conversation Topic": "Billing Inquiry",
        "Staff Member": "Charlie Brown",
        "Subteam": "Support",
        "Date": "2026-09-10 09:15:00",
        "Notes": "Resolved invoice dispute",
        "Action Items": "Send receipt",
        "Deadline": "2026-09-15",
        "Link": "https://slack.com/archives/C123/p1003",
    },
]


class TestDashboardHtml:
    """Tests for GET / (HTML Dashboard)."""

    def test_dashboard_html_status_and_headers(self, client):
        """Verify GET / returns 200 HTMLResponse and renders table headers."""
        with patch("dashboard.app.get_all_records", return_value=SAMPLE_RECORDS_11_COLS):
            response = client.get("/")
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
            html = response.text

            # Verify document title and heading
            assert "<title>Business Chat Record</title>" in html
            assert "<h1>Business Chat Record</h1>" in html

            # Verify all 9 rendered table headers
            visible_headers = [
                "<th>Message</th>",
                "<th>Category</th>",
                "<th>Topic</th>",
                "<th>Staff Member</th>",
                "<th>Subteam</th>",
                "<th>Date</th>",
                "<th>Notes</th>",
                "<th>Deadline</th>",
                "<th>Link</th>",
            ]
            for header in visible_headers:
                assert header in html

            # Confirm 11-column data model integration: all config.SHEET_HEADERS represented
            for col in config.SHEET_HEADERS:
                assert col in SAMPLE_RECORDS_11_COLS[0]

    def test_dashboard_reverse_chronological_ordering(self, client):
        """Verify that rows are displayed in reverse chronological order (newest first)."""
        with patch("dashboard.app.get_all_records", return_value=SAMPLE_RECORDS_11_COLS):
            response = client.get("/")
            assert response.status_code == 200
            html = response.text

            pos_oldest = html.index("First interaction (oldest)")
            pos_middle = html.index("Second interaction (middle)")
            pos_newest = html.index("Third interaction (newest)")

            # Reverse chronological: newest must appear before middle, and middle before oldest
            assert pos_newest < pos_middle < pos_oldest

    def test_dashboard_column_styling_and_pills(self, client):
        """Verify table cells, category pill badges, topic badges, deadline formatting, and links."""
        with patch("dashboard.app.get_all_records", return_value=[SAMPLE_RECORDS_11_COLS[0]]):
            response = client.get("/")
            assert response.status_code == 200
            html = response.text

            assert "<td>First interaction (oldest)</td>" in html
            assert '<span class="pill">Fundraiser</span>' in html
            assert '<span class="pill pill-topic">Charity Gala</span>' in html
            assert "<td>Alice Smith</td>" in html
            assert "<td>Outreach</td>" in html
            assert "<td>2026-09-01 10:00:00</td>" in html
            assert "<td>Initial setup meeting</td>" in html
            assert '<span class="deadline">2026-09-10</span>' in html
            assert '<a class="link" href="https://slack.com/archives/C123/p1001" target="_blank">Open in Slack</a>' in html

    def test_dashboard_conditional_rendering(self, client):
        """Verify empty deadline and link omit span and anchor tags."""
        with patch("dashboard.app.get_all_records", return_value=[SAMPLE_RECORDS_11_COLS[1]]):
            response = client.get("/")
            assert response.status_code == 200
            html = response.text

            assert "Second interaction (middle)" in html
            assert '<span class="deadline">' not in html
            assert '<a class="link"' not in html

    def test_dashboard_xss_autoescaping(self, client):
        """Verify malicious HTML tags in records are auto-escaped by Jinja2."""
        xss_record = {
            "Message": "<script>alert('xss')</script>",
            "Category": "<b>Danger</b>",
            "Conversation ID": "cid_xss",
            "Conversation Topic": "Topic <hack>",
            "Staff Member": "Mallory & Eve",
            "Subteam": "Red'Team",
            "Date": "2026-09-11",
            "Notes": "<img src=x onerror=alert(1)>",
            "Action Items": "None",
            "Deadline": "",
            "Link": "",
        }
        with patch("dashboard.app.get_all_records", return_value=[xss_record]):
            response = client.get("/")
            assert response.status_code == 200
            html = response.text

            assert "<script>alert" not in html
            assert "&lt;script&gt;alert(&#39;xss&#39;)&lt;/script&gt;" in html
            assert "&lt;b&gt;Danger&lt;/b&gt;" in html
            assert "Mallory &amp; Eve" in html
            assert "&lt;img src=x onerror=alert(1)&gt;" in html

    def test_dashboard_empty_record_state(self, client):
        """Verify GET / renders cleanly when sheet has 0 records."""
        with patch("dashboard.app.get_all_records", return_value=[]):
            response = client.get("/")
            assert response.status_code == 200
            html = response.text

            assert "<h1>Business Chat Record</h1>" in html
            assert "<th>Message</th>" in html
            assert "<tbody>" in html

    def test_dashboard_link_validation_and_schemes(self, client):
        """Verify only http, https, and slack schemes render clickable links; invalid URLs render '-'."""
        test_records = [
            {"Message": "HTTPS link", "Link": "https://slack.com/archives/C123/p1"},
            {"Message": "HTTP link", "Link": "http://slack.com/archives/C123/p2"},
            {"Message": "Slack scheme link", "Link": "slack://channel?id=C123&message=123"},
            {"Message": "ISO date in link", "Link": "2026-09-12T10:00:00Z"},
            {"Message": "Error string in link", "Link": "Error: 404 Not Found"},
            {"Message": "FTP scheme in link", "Link": "ftp://files.example.com"},
            {"Message": "Empty link", "Link": ""},
        ]
        with patch("dashboard.app.get_all_records", return_value=test_records):
            response = client.get("/")
            assert response.status_code == 200
            html = response.text

            # Valid links must render styled 'Open in Slack' anchor tags with target="_blank"
            assert '<a class="link" href="https://slack.com/archives/C123/p1" target="_blank">Open in Slack</a>' in html
            assert '<a class="link" href="http://slack.com/archives/C123/p2" target="_blank">Open in Slack</a>' in html
            assert '<a class="link" href="slack://channel?id=C123&amp;message=123" target="_blank">Open in Slack</a>' in html

            # Invalid or empty links must not render anchor tags
            assert '<a class="link" href="2026-09-12T10:00:00Z"' not in html
            assert '<a class="link" href="Error: 404 Not Found"' not in html
            assert '<a class="link" href="ftp://files.example.com"' not in html

    def test_dashboard_topic_keys_and_badges(self, client):
        """Verify both 'Conversation Topic' and 'Topic' keys render pill badges."""
        records = [
            {"Message": "Full key", "Conversation Topic": "Architecture Review"},
            {"Message": "Short key", "Topic": "Database Tuning"},
            {"Message": "Empty key", "Conversation Topic": ""},
        ]
        with patch("dashboard.app.get_all_records", return_value=records):
            response = client.get("/")
            assert response.status_code == 200
            html = response.text

            assert '<span class="pill pill-topic">Architecture Review</span>' in html
            assert '<span class="pill pill-topic">Database Tuning</span>' in html

    def test_dashboard_date_formatting(self, client):
        """Verify ISO date formatting and raw fallback."""
        records = [
            {"Message": "ISO with Z", "Date": "2026-09-12T14:30:00Z"},
            {"Message": "Raw string", "Date": "Not-A-Date-String"},
        ]
        with patch("dashboard.app.get_all_records", return_value=records):
            response = client.get("/")
            assert response.status_code == 200
            html = response.text

            assert "<td>2026-09-12 14:30:00</td>" in html
            assert "<td>Not-A-Date-String</td>" in html


class TestDashboardApi:
    """Tests for GET /api/records (JSON Endpoint)."""

    def test_api_records_exact_list(self, client):
        """Verify GET /api/records returns 200 JSONResponse and exact record dictionaries."""
        with patch("dashboard.app.get_all_records", return_value=SAMPLE_RECORDS_11_COLS):
            response = client.get("/api/records")
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/json"
            data = response.json()

            assert len(data) == 3
            assert data == SAMPLE_RECORDS_11_COLS
            # Original chronological ordering preserved
            assert data[0]["Message"] == "First interaction (oldest)"
            assert data[1]["Message"] == "Second interaction (middle)"
            assert data[2]["Message"] == "Third interaction (newest)"
            assert data[0]["Conversation ID"] == "convo_001"

    def test_api_records_empty_state(self, client):
        """Verify GET /api/records returns an empty JSON list when 0 records exist."""
        with patch("dashboard.app.get_all_records", return_value=[]):
            response = client.get("/api/records")
            assert response.status_code == 200
            assert response.json() == []


class TestDashboardErrorHandling:
    """Tests for error handling and exception propagation."""

    def test_dashboard_error_returns_500(self, client):
        """Verify HTTP 500 is returned when sheets service raises an exception."""
        with patch("dashboard.app.get_all_records", side_effect=RuntimeError("Google Sheets API 503")):
            res_html = client.get("/")
            assert res_html.status_code == 500

            res_api = client.get("/api/records")
            assert res_api.status_code == 500

    def test_dashboard_exception_propagation(self, strict_client):
        """Verify RuntimeError propagates directly when raise_server_exceptions=True."""
        with patch("dashboard.app.get_all_records", side_effect=RuntimeError("Sheets Service Unavailable")):
            with pytest.raises(RuntimeError, match="Sheets Service Unavailable"):
                strict_client.get("/")

            with pytest.raises(RuntimeError, match="Sheets Service Unavailable"):
                strict_client.get("/api/records")


class TestDashboardMcpNormalization:
    """Tests verifying live/mock MCP client normalization with dashboard endpoints."""

    @patch("sheets_service.call_mcp_tool_sync")
    def test_dashboard_legacy_column_a_normalization(self, mock_call_mcp, client):
        """Verify sheets_service normalizes legacy column 'A' to 'Message' for dashboard."""
        mock_call_mcp.return_value = {
            "status": "success",
            "records": [
                {
                    "A": "Legacy column A message",
                    "Category": "Support",
                    "Staff Member": "Dave",
                    "Subteam": "Sales",
                    "Date": "2026-09-11 15:00:00",
                }
            ],
            "count": 1,
        }

        # HTML endpoint test
        res_html = client.get("/")
        assert res_html.status_code == 200
        assert "Legacy column A message" in res_html.text

        # API endpoint test
        res_api = client.get("/api/records")
        assert res_api.status_code == 200
        records = res_api.json()
        assert records[0]["Message"] == "Legacy column A message"
        # Verify all 11 SHEET_HEADERS were filled with defaults
        for header in config.SHEET_HEADERS:
            assert header in records[0]

    @patch("sheets_service.call_mcp_tool_sync")
    def test_dashboard_missing_columns_default_to_empty_string(self, mock_call_mcp, client):
        """Verify missing columns in MCP response are pre-populated with empty strings."""
        mock_call_mcp.return_value = {
            "status": "success",
            "records": [
                {
                    "Message": "Sparse record",
                    "Category": "General",
                }
            ],
            "count": 1,
        }

        res_api = client.get("/api/records")
        assert res_api.status_code == 200
        sparse_rec = res_api.json()[0]
        assert sparse_rec["Message"] == "Sparse record"
        assert sparse_rec["Deadline"] == ""
        assert sparse_rec["Notes"] == ""
        assert sparse_rec["Action Items"] == ""
