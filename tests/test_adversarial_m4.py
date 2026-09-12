"""Adversarial challenge test suite for Milestone M4: Dashboard stress testing.

Evaluates:
1. Malformed records (missing keys, None values, unexpected data types, non-dict rows).
2. XSS payloads in notes, message, category, link, and other fields (Jinja2 auto-escaping vs href JavaScript injection).
3. Empty record sets (0 rows) and large record sets (1,000+ rows, 5,000 rows, 100KB message payloads).
4. Downstream sheets_service exceptions (RuntimeError, ConnectionError, TimeoutError, ValueError returning HTTP 500).
"""

import time
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

import config
from dashboard.app import app
import sheets_service


@pytest.fixture
def client():
    """Create a FastAPI TestClient that captures HTTP 500 errors without raising."""
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def strict_client():
    """Create a FastAPI TestClient that raises server exceptions directly to the caller."""
    return TestClient(app, raise_server_exceptions=True)


# ============================================================================
# 1. Malformed Records Testing
# ============================================================================


class TestDashboardMalformedRecords:
    """Stress testing dashboard endpoints with malformed records."""

    def test_completely_empty_record_dict(self, client):
        """Verify dashboard behavior when record dict has no keys whatsoever."""
        empty_record = {}
        with patch("dashboard.app.get_all_records", return_value=[empty_record]):
            # HTML endpoint
            res_html = client.get("/")
            assert res_html.status_code == 200
            html = res_html.text
            assert "<h1>Business Chat Record</h1>" in html
            # Jinja2 defaults missing keys on dict to Undefined (renders empty string)
            assert "<tr>" in html

            # JSON API endpoint
            res_api = client.get("/api/records")
            assert res_api.status_code == 200
            assert res_api.json() == [{}]

    def test_sparse_and_partial_keys(self, client):
        """Verify behavior when records have only a subset of expected keys."""
        partial_records = [
            {"Message": "Only message"},
            {"Category": "Only category", "Notes": "Some notes"},
            {"Link": "https://example.com/item"},
        ]
        with patch("dashboard.app.get_all_records", return_value=partial_records):
            res_html = client.get("/")
            assert res_html.status_code == 200
            html = res_html.text
            assert "Only message" in html
            assert '<span class="pill">Only category</span>' in html
            assert "Some notes" in html
            assert 'href="https://example.com/item"' in html

            res_api = client.get("/api/records")
            assert res_api.status_code == 200
            assert len(res_api.json()) == 3

    def test_none_values_across_all_fields(self, client):
        """Verify handling when every column key has a None value."""
        none_record = {col: None for col in config.SHEET_HEADERS}
        with patch("dashboard.app.get_all_records", return_value=[none_record]):
            res_html = client.get("/")
            assert res_html.status_code == 200
            html = res_html.text

            # Jinja2 renders None as "None" for {{ r["Message"] }}
            assert "<td>None</td>" in html
            assert '<span class="pill">None</span>' in html
            # But conditional {% if r["Deadline"] %} and {% if r["Link"] %} evaluate to False
            assert '<span class="deadline">' not in html
            assert '<a class="link"' not in html

            res_api = client.get("/api/records")
            assert res_api.status_code == 200
            data = res_api.json()
            assert len(data) == 1
            assert data[0]["Message"] is None
            assert data[0]["Deadline"] is None

    def test_unexpected_data_types(self, client):
        """Verify handling of integers, booleans, floats, lists, and dicts in records."""
        complex_record = {
            "Message": 12345,
            "Category": True,
            "Staff Member": 3.14159,
            "Subteam": False,
            "Date": ["2026", "09", "11"],
            "Notes": {"internal_key": "nested_value"},
            "Deadline": 0,  # 0 is falsy in Python/Jinja2
            "Link": 999,    # 999 is truthy in Python/Jinja2
        }
        with patch("dashboard.app.get_all_records", return_value=[complex_record]):
            res_html = client.get("/")
            assert res_html.status_code == 200
            html = res_html.text

            assert "<td>12345</td>" in html
            assert '<span class="pill">True</span>' in html
            assert "<td>3.14159</td>" in html
            assert "<td>False</td>" in html
            assert "[&#39;2026&#39;, &#39;09&#39;, &#39;11&#39;]" in html
            assert "{&#39;internal_key&#39;: &#39;nested_value&#39;}" in html
            # Deadline 0 is falsy in Jinja2 {% if r["Deadline"] %}
            assert '<span class="deadline">0</span>' not in html
            # Link 999 is truthy in Jinja2 {% if r["Link"] %}
            assert 'href="999"' in html

            res_api = client.get("/api/records")
            assert res_api.status_code == 200
            api_data = res_api.json()
            assert api_data[0]["Message"] == 12345
            assert api_data[0]["Category"] is True
            assert api_data[0]["Date"] == ["2026", "09", "11"]
            assert api_data[0]["Notes"] == {"internal_key": "nested_value"}

    def test_non_dict_row_in_records_handled_gracefully(self, client):
        """Verify that a None item in the records list is rendered as empty values without crashing."""
        with patch("dashboard.app.get_all_records", return_value=[None]):
            res_html = client.get("/")
            assert res_html.status_code == 200
            html = res_html.text
            # Jinja2 treats None['Message'] as Undefined, rendering empty <td></td>
            assert "<td></td>" in html

    def test_non_iterable_records_returns_500(self, client):
        """Verify that non-iterable return from get_all_records (e.g. None or int) returns HTTP 500."""
        with patch("dashboard.app.get_all_records", return_value=None):
            res_html = client.get("/")
            assert res_html.status_code == 500

    def test_sheets_service_get_all_records_malformed_mcp_records(self):
        """Verify sheets_service.get_all_records normalization with various MCP outputs."""
        # 1. MCP returns record with extra unknown fields
        with patch("sheets_service.call_mcp_tool_sync", return_value={
            "status": "success",
            "records": [{"UnknownCol": "val", "Message": "hello"}],
        }):
            recs = sheets_service.get_all_records()
            assert len(recs) == 1
            assert recs[0]["Message"] == "hello"
            assert recs[0]["UnknownCol"] == "val"
            assert recs[0]["Notes"] == ""

        # 2. MCP returns empty records list
        with patch("sheets_service.call_mcp_tool_sync", return_value={"records": []}):
            assert sheets_service.get_all_records() == []

        # 3. MCP returns dict without 'records' key
        with patch("sheets_service.call_mcp_tool_sync", return_value={"status": "error"}):
            assert sheets_service.get_all_records() == []

        # 4. MCP returns records=None -> verifies whether NoneType is trapped or raises
        with patch("sheets_service.call_mcp_tool_sync", return_value={"records": None}):
            # If records is None, result.get("records", []) returns None, causing TypeError
            with pytest.raises(TypeError):
                sheets_service.get_all_records()


# ============================================================================
# 2. XSS Payloads & Security Testing
# ============================================================================


class TestDashboardXssSecurity:
    """Stress testing HTML injection and XSS resistance."""

    def test_xss_in_notes_and_message(self, client):
        """Verify that script tags and event handlers in Message and Notes are escaped."""
        xss_payloads = {
            "Message": "<script>alert('XSS_MSG')</script>",
            "Category": "<svg/onload=alert('XSS_CAT')>",
            "Staff Member": "<img src=x onerror=alert('XSS_STAFF')>",
            "Subteam": "<iframe src='evil.html'></iframe>",
            "Date": "<body onload=alert('XSS_DATE')>",
            "Notes": "<b>bold</b><script src='https://evil.com/xss.js'></script>",
            "Deadline": "2026-09-11<script>alert(1)</script>",
            "Link": "",
        }
        with patch("dashboard.app.get_all_records", return_value=[xss_payloads]):
            response = client.get("/")
            assert response.status_code == 200
            html = response.text

            # Raw tags must NOT exist in the rendered output
            assert "<script>alert('XSS_MSG')</script>" not in html
            assert "<svg/onload=alert('XSS_CAT')>" not in html
            assert "<img src=x onerror=alert('XSS_STAFF')>" not in html
            assert "<iframe src='evil.html'></iframe>" not in html
            assert "<body onload=alert('XSS_DATE')>" not in html
            assert "<script src='https://evil.com/xss.js'></script>" not in html

            # Escaped representations must be present
            assert "&lt;script&gt;alert(&#39;XSS_MSG&#39;)&lt;/script&gt;" in html
            assert "&lt;svg/onload=alert(&#39;XSS_CAT&#39;)&gt;" in html
            assert "&lt;img src=x onerror=alert(&#39;XSS_STAFF&#39;)&gt;" in html
            assert "&lt;iframe src=&#39;evil.html&#39;&gt;&lt;/iframe&gt;" in html
            assert "&lt;script src=&#39;https://evil.com/xss.js&#39;&gt;&lt;/script&gt;" in html

    def test_xss_attribute_breakout_in_link(self, client):
        """Verify that quote breakout attempts in the Link href attribute are neutralized."""
        breakout_payload = {
            "Message": "Link breakout test",
            "Category": "Test",
            "Staff Member": "Tester",
            "Subteam": "Security",
            "Date": "2026-09-11",
            "Notes": "Testing quotes",
            "Deadline": "",
            "Link": '" onmouseover="alert(1)" data-x="',
        }
        with patch("dashboard.app.get_all_records", return_value=[breakout_payload]):
            response = client.get("/")
            assert response.status_code == 200
            html = response.text

            # Double quotes should be escaped as &#34; preventing attribute breakout
            assert 'href="&#34; onmouseover=&#34;alert(1)&#34; data-x=&#34;"' in html
            assert 'onmouseover="alert(1)"' not in html

    def test_xss_javascript_pseudo_protocol_in_link(self, client):
        """CRITICAL AUDIT: Check whether javascript: pseudo-protocol in Link is filtered or rendered.

        Jinja2 escapes HTML characters (<, >, &, \", ') but does NOT validate URI schemes.
        A link with href=\"javascript:...\" executes in browser if not filtered.
        """
        js_link_payload = {
            "Message": "Testing javascript link",
            "Category": "Security",
            "Staff Member": "Attacker",
            "Subteam": "RedTeam",
            "Date": "2026-09-11",
            "Notes": "javascript link payload",
            "Deadline": "",
            "Link": "javascript:alert(document.domain)",
        }
        with patch("dashboard.app.get_all_records", return_value=[js_link_payload]):
            response = client.get("/")
            assert response.status_code == 200
            html = response.text

            # Observe empirical behavior: Jinja2 renders href="javascript:alert(document.domain)"
            assert 'href="javascript:alert(document.domain)"' in html

    def test_ssti_expressions_treated_as_literals(self, client):
        """Verify that template syntax like {{ 7 * 7 }} is not evaluated as SSTI."""
        ssti_payload = {
            "Message": "{{ 7 * 7 }}",
            "Category": "{% for x in (1,2,3) %}{{ x }}{% endfor %}",
            "Staff Member": "${7*7}",
            "Subteam": "#{7*7}",
            "Date": "2026-09-11",
            "Notes": "{{ config.items() }}",
            "Deadline": "",
            "Link": "",
        }
        with patch("dashboard.app.get_all_records", return_value=[ssti_payload]):
            response = client.get("/")
            assert response.status_code == 200
            html = response.text

            # Expressions should be rendered as literal text, NOT evaluated
            assert "{{ 7 * 7 }}" in html
            assert "{% for x in (1,2,3) %}{{ x }}{% endfor %}" in html
            assert "{{ config.items() }}" in html
            assert "49" not in html

    def test_api_records_xss_json_content_type(self, client):
        """Verify that GET /api/records returns application/json, preventing HTML interpretation."""
        raw_xss = {
            "Message": "<script>alert(1)</script>",
            "Category": "Test",
            "Staff Member": "User",
            "Subteam": "Team",
            "Date": "2026-09-11",
            "Notes": "<img src=x onerror=alert(2)>",
            "Deadline": "",
            "Link": "",
        }
        with patch("dashboard.app.get_all_records", return_value=[raw_xss]):
            response = client.get("/api/records")
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/json"
            data = response.json()
            assert data[0]["Message"] == "<script>alert(1)</script>"
            assert data[0]["Notes"] == "<img src=x onerror=alert(2)>"


# ============================================================================
# 3. Empty and Large Record Sets (Stress Testing)
# ============================================================================


class TestDashboardScaleAndPerformance:
    """Stress testing performance with 0, 1,000, and 5,000 records."""

    def test_empty_record_set_0_rows(self, client):
        """Verify clean handling of zero rows on both endpoints."""
        with patch("dashboard.app.get_all_records", return_value=[]):
            res_html = client.get("/")
            assert res_html.status_code == 200
            assert "<tbody>" in res_html.text
            assert "<tr>" not in res_html.text.split("<tbody>")[1].split("</tbody>")[0]

            res_api = client.get("/api/records")
            assert res_api.status_code == 200
            assert res_api.json() == []

    def test_large_record_set_1000_rows(self, client):
        """Verify 1,000 records render quickly (< 500ms) with proper reverse order."""
        records = [
            {
                "Message": f"Message {i}",
                "Category": "Test",
                "Conversation ID": f"cid_{i}",
                "Conversation Topic": f"Topic {i}",
                "Staff Member": f"Staff {i % 10}",
                "Subteam": f"Team {i % 5}",
                "Date": f"2026-09-11 12:{i % 60:02d}:00",
                "Notes": f"Notes for row {i}",
                "Action Items": f"Action {i}",
                "Deadline": f"2026-09-{((i % 28) + 1):02d}",
                "Link": f"https://slack.com/archives/C123/p{i:06d}",
            }
            for i in range(1, 1001)
        ]

        with patch("dashboard.app.get_all_records", return_value=records):
            # Test HTML endpoint
            start_html = time.perf_counter()
            res_html = client.get("/")
            dur_html = time.perf_counter() - start_html

            assert res_html.status_code == 200
            assert dur_html < 0.5  # Sub-500ms execution requirement
            html = res_html.text
            # Verify newest (row 1000) appears before oldest (row 1)
            pos_1000 = html.index("<td>Message 1000</td>")
            pos_1 = html.index("<td>Message 1</td>")
            assert pos_1000 < pos_1

            # Test JSON API endpoint
            start_api = time.perf_counter()
            res_api = client.get("/api/records")
            dur_api = time.perf_counter() - start_api

            assert res_api.status_code == 200
            assert dur_api < 0.3
            data = res_api.json()
            assert len(data) == 1000
            # API preserves original chronological sequence
            assert data[0]["Message"] == "Message 1"
            assert data[-1]["Message"] == "Message 1000"

    def test_large_record_set_5000_rows(self, client):
        """Stress test with 5,000 records ensuring no timeout or crash (< 2.0s)."""
        records = [
            {
                "Message": f"Stress record {i}",
                "Category": "Performance",
                "Staff Member": f"Worker {i}",
                "Subteam": "Stress",
                "Date": "2026-09-11",
                "Notes": "Benchmarking memory and serialization limits",
                "Deadline": "",
                "Link": "",
            }
            for i in range(5000)
        ]

        with patch("dashboard.app.get_all_records", return_value=records):
            start = time.perf_counter()
            res_html = client.get("/")
            dur = time.perf_counter() - start

            assert res_html.status_code == 200
            assert dur < 2.0  # Must finish within 2 seconds
            assert "Stress record 4999" in res_html.text

            res_api = client.get("/api/records")
            assert res_api.status_code == 200
            assert len(res_api.json()) == 5000

    def test_large_message_payload_100kb(self, client):
        """Verify handling of records with 100KB large text payload."""
        large_text = "A" * 100_000
        large_record = {
            "Message": large_text,
            "Category": "Large",
            "Staff Member": "Tester",
            "Subteam": "QA",
            "Date": "2026-09-11",
            "Notes": large_text[:1000],
            "Deadline": "",
            "Link": "",
        }
        with patch("dashboard.app.get_all_records", return_value=[large_record]):
            res_html = client.get("/")
            assert res_html.status_code == 200
            assert large_text in res_html.text

            res_api = client.get("/api/records")
            assert res_api.status_code == 200
            assert res_api.json()[0]["Message"] == large_text


# ============================================================================
# 4. Downstream Sheets Service Exceptions (HTTP 500)
# ============================================================================


class TestDashboardDownstreamExceptions:
    """Stress testing failure modes and HTTP 500 responses when sheets_service fails."""

    @pytest.mark.parametrize(
        "exc_type,exc_msg",
        [
            (RuntimeError, "Google Sheets API 503 Service Unavailable"),
            (ConnectionError, "Connection refused to FastMCP stdio server"),
            (TimeoutError, "MCP tool 'sheets_get_records' timed out after 30.0s"),
            (ValueError, "GOOGLE_SHEET_ID is not configured in config.py"),
            (KeyError, "Corrupted MCP response: missing expected key"),
            (OSError, "Broken pipe while communicating with MCP process"),
        ],
    )
    def test_downstream_exceptions_return_http_500(self, client, exc_type, exc_msg):
        """Verify all categories of downstream exceptions yield HTTP 500 on / and /api/records."""
        with patch("dashboard.app.get_all_records", side_effect=exc_type(exc_msg)):
            # HTML endpoint
            res_html = client.get("/")
            assert res_html.status_code == 500

            # JSON API endpoint
            res_api = client.get("/api/records")
            assert res_api.status_code == 500

    @pytest.mark.parametrize(
        "exc_type,exc_msg",
        [
            (RuntimeError, "Backend crash"),
            (ConnectionError, "Socket closed"),
            (TimeoutError, "Timeout occurred"),
            (ValueError, "Invalid config"),
        ],
    )
    def test_strict_client_propagates_exceptions(self, strict_client, exc_type, exc_msg):
        """Verify strict client allows caller to catch the underlying exception type."""
        with patch("dashboard.app.get_all_records", side_effect=exc_type(exc_msg)):
            with pytest.raises(exc_type, match=exc_msg):
                strict_client.get("/")

            with pytest.raises(exc_type, match=exc_msg):
                strict_client.get("/api/records")
