# tests/test_converters_cli.py
"""Tests for the converters_cli CLI entry point (Phase 2).

Contract under test:
    python -m pageindex_mcp.converters_cli <input_pdf_path>

    stdout: exactly one JSON line at exit
      success: {"ok": true, "doc_id": "...", "peak_rss_kib": <int>, "duration_ms": <int>}
      failure: {"ok": false, "error": "<ExceptionClassName>", "message": "..."}
    exit code: 0 on success, 1 on handled exception.

Test strategy:
    - Tests 1, 2, 5 use subprocess.run (real process isolation).
    - Tests 3, 4 use in-process main() call with monkeypatching for speed.
    - Integration test (real Docling) is guarded by DOCLING_INTEGRATION=1.
"""

import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Minimal valid PDF bytes (no text, just structure — enough for file-exists checks).
_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000115 00000 n \n"
    b"trailer<</Size 4/Root 1 0 R>>\n"
    b"startxref\n190\n%%EOF"
)


@pytest.fixture()
def tmp_pdf(tmp_path: Path) -> Path:
    """Write a minimal PDF fixture to a temp file and return its path."""
    p = tmp_path / "fixture.pdf"
    p.write_bytes(_MINIMAL_PDF)
    return p


def _run_cli(*args, env_extra=None, timeout=180):
    """Run the CLI as a subprocess and return the CompletedProcess."""
    import subprocess

    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "pageindex_mcp.converters_cli", *args],
        capture_output=True,
        timeout=timeout,
        env=env,
    )


def _last_stdout_json(proc) -> dict:
    """Return the last non-empty stdout line parsed as JSON."""
    lines = [ln for ln in proc.stdout.decode().splitlines() if ln.strip()]
    assert lines, f"No stdout output. stderr={proc.stderr.decode()!r}"
    return json.loads(lines[-1])


# ---------------------------------------------------------------------------
# Test 1 — fast happy path (monkeypatched index, subprocess)
# ---------------------------------------------------------------------------

def test_happy_path_monkeypatched_subprocess(tmp_pdf: Path, tmp_path: Path):
    """Fast happy path: CLI subprocess with monkeypatched client.index via
    env var injection is impractical cross-process; instead we use a real
    subprocess but inject a fake 'index' by patching via a conftest shim.

    Since cross-process monkeypatching isn't feasible, we test the full
    subprocess plumbing by patching at the in-process level in test 4 and
    reserve this test for confirming the subprocess exit/JSON shape when the
    CLI receives a valid file path argument, using a small real-PDF fixture
    and a monkeypatched CustomPageIndexClient baked into a wrapper script.
    """
    # We write a tiny helper script that patches client.index before importing main.
    shim = tmp_path / "shim.py"
    shim.write_text(
        "import sys\n"
        "import asyncio\n"
        "from unittest.mock import AsyncMock, patch\n"
        "sys.argv = ['converters_cli', sys.argv[1]]\n"
        "fake_index = AsyncMock(return_value='abcd1234')\n"
        "with patch('pageindex_mcp.client.CustomPageIndexClient.index', fake_index):\n"
        "    from pageindex_mcp.converters_cli import main\n"
        "    sys.exit(asyncio.run(main()))\n"
    )
    import subprocess
    result = subprocess.run(
        [sys.executable, str(shim), str(tmp_pdf)],
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, f"stderr={result.stderr.decode()!r}"
    payload = _last_stdout_json(result)
    assert payload["ok"] is True
    assert payload["doc_id"] == "abcd1234"
    assert isinstance(payload["peak_rss_kib"], int)
    assert payload["peak_rss_kib"] >= 0
    assert isinstance(payload["duration_ms"], int)
    assert payload["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# Test 2 — missing input path → exit 1
# ---------------------------------------------------------------------------

def test_missing_input_file_exits_1_with_json_error(tmp_path: Path):
    """Missing input → exit code 1, JSON {ok: false, error: FileNotFoundError}."""
    nonexistent = str(tmp_path / "does_not_exist.pdf")
    proc = _run_cli(nonexistent, timeout=30)
    assert proc.returncode == 1, f"Expected exit 1. stderr={proc.stderr.decode()!r}"
    payload = _last_stdout_json(proc)
    assert payload["ok"] is False
    assert payload["error"] == "FileNotFoundError"
    assert "message" in payload


# ---------------------------------------------------------------------------
# Test 3 — RuntimeError from client.index → exit 1 (in-process)
# ---------------------------------------------------------------------------

async def test_runtime_error_from_index_exits_1_in_process(tmp_pdf: Path, monkeypatch):
    """RuntimeError raised by client.index → JSON {ok: false, error: RuntimeError}."""
    import io
    import pageindex_mcp.converters_cli as cli_module
    from pageindex_mcp.converters_cli import main

    monkeypatch.setattr("sys.argv", ["converters_cli", str(tmp_pdf)])

    fake_stdout = io.StringIO()
    monkeypatch.setattr(cli_module, "_stdout", fake_stdout)

    # The CLI now validates/configures the LLM provider (LLM-01) before indexing.
    # This test exercises index() error handling, not provider config, so neutralize
    # the gate to stay independent of ambient OPENAI_API_KEY in the runner env.
    monkeypatch.setattr("pageindex_mcp.client.validate_llm_config", lambda: None)
    monkeypatch.setattr("pageindex_mcp.client.configure_litellm", lambda: None)

    with patch(
        "pageindex_mcp.client.CustomPageIndexClient.index",
        new_callable=AsyncMock,
        side_effect=RuntimeError("empty pdf"),
    ):
        exit_code = await main()

    assert exit_code == 1
    output = fake_stdout.getvalue().strip()
    assert output, "Expected one stdout line"
    payload = json.loads(output.splitlines()[-1])
    assert payload["ok"] is False
    assert payload["error"] == "RuntimeError"
    assert "empty pdf" in payload["message"]


# ---------------------------------------------------------------------------
# Test 4 — JSON shape / structural test (in-process)
# ---------------------------------------------------------------------------

async def test_json_shape_and_types_on_success(tmp_pdf: Path, monkeypatch):
    """Success JSON has exactly the required keys with correct types."""
    import io
    import pageindex_mcp.converters_cli as cli_module
    from pageindex_mcp.converters_cli import main

    monkeypatch.setattr("sys.argv", ["converters_cli", str(tmp_pdf)])

    fake_stdout = io.StringIO()
    monkeypatch.setattr(cli_module, "_stdout", fake_stdout)

    # See note in test_runtime_error_from_index_exits_1_in_process: this is a
    # JSON-shape test, so neutralize the LLM-01 provider gate to stay independent
    # of whether the runner has OPENAI_API_KEY set.
    monkeypatch.setattr("pageindex_mcp.client.validate_llm_config", lambda: None)
    monkeypatch.setattr("pageindex_mcp.client.configure_litellm", lambda: None)

    with patch(
        "pageindex_mcp.client.CustomPageIndexClient.index",
        new_callable=AsyncMock,
        return_value="deadbeef",
    ):
        exit_code = await main()

    assert exit_code == 0
    lines = [ln for ln in fake_stdout.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1, f"Expected exactly 1 stdout line, got: {lines}"
    payload = json.loads(lines[0])

    # Required keys
    assert set(payload.keys()) == {"ok", "doc_id", "peak_rss_kib", "duration_ms"}
    assert payload["ok"] is True
    assert isinstance(payload["doc_id"], str) and len(payload["doc_id"]) > 0
    assert isinstance(payload["peak_rss_kib"], int) and payload["peak_rss_kib"] >= 0
    assert isinstance(payload["duration_ms"], int) and payload["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# Test 5 — No stdout pollution from logging/stray prints (subprocess)
# ---------------------------------------------------------------------------

def test_no_stdout_pollution_from_logs(tmp_pdf: Path, tmp_path: Path):
    """Stdout must be exactly one JSON line even if client.index emits logs and prints."""
    shim = tmp_path / "noisy_shim.py"
    shim.write_text(
        "import sys, logging, asyncio\n"
        "from unittest.mock import AsyncMock, patch\n"
        "sys.argv = ['converters_cli', sys.argv[1]]\n"
        "\n"
        "async def noisy_index(self_or_path, *a, **kw):\n"
        "    print('noisy stdout log')  # stray print — should go to stderr or be suppressed\n"
        "    logging.getLogger().warning('noisy warning')\n"
        "    return 'cafe5678'\n"
        "\n"
        "with patch('pageindex_mcp.client.CustomPageIndexClient.index', noisy_index):\n"
        "    from pageindex_mcp.converters_cli import main\n"
        "    sys.exit(asyncio.run(main()))\n"
    )
    import subprocess

    result = subprocess.run(
        [sys.executable, str(shim), str(tmp_pdf)],
        capture_output=True,
        timeout=60,
    )
    stdout_lines = [ln for ln in result.stdout.decode().splitlines() if ln.strip()]
    assert len(stdout_lines) == 1, (
        f"Expected exactly 1 stdout line, got {len(stdout_lines)}: {stdout_lines!r}. "
        f"stderr={result.stderr.decode()!r}"
    )
    payload = json.loads(stdout_lines[0])
    assert payload["ok"] is True


# ---------------------------------------------------------------------------
# Test 1b — Integration test (real Docling, skipped by default)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_happy_path_real_docling_integration(tmp_pdf: Path):
    """Real Docling conversion — only runs when DOCLING_INTEGRATION=1."""
    if not os.environ.get("DOCLING_INTEGRATION"):
        pytest.skip("Set DOCLING_INTEGRATION=1 to run real Docling integration test")
    proc = _run_cli(str(tmp_pdf), timeout=300)
    assert proc.returncode == 0, f"CLI failed. stderr={proc.stderr.decode()!r}"
    payload = _last_stdout_json(proc)
    assert payload["ok"] is True
    assert isinstance(payload["doc_id"], str) and len(payload["doc_id"]) > 0
    assert isinstance(payload["peak_rss_kib"], int) and payload["peak_rss_kib"] >= 0
    assert isinstance(payload["duration_ms"], int) and payload["duration_ms"] >= 0
