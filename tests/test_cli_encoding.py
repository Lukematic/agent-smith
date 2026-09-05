"""CLI output must not corrupt non-ASCII characters on Windows.

Regression for a real bug: sys.stdout on this platform can default to a
legacy console codepage (observed: cp1252) rather than UTF-8, even when
output is redirected to a file. That codepage cannot encode certain
characters used in CLI advisory text (an em-dash) and silently substitutes
U+FFFD, the mojibake replacement character, instead of raising - so the
corruption is easy to miss unless the raw bytes are inspected.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPLACEMENT_CHAR_UTF8 = b"\xef\xbf\xbd"
EM_DASH_UTF8 = b"\xe2\x80\x94"


def _run_module(project: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    env = os.environ.copy()
    env["SMITH_PROJECT"] = str(project)
    env.pop("VIRTUAL_ENV", None)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return subprocess.run(
        [sys.executable, "-m", "smith.cli", *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=False,  # raw bytes: this test is specifically about encoding
        timeout=30,
        check=False,
    )


def test_watch_advisory_line_contains_a_real_em_dash_not_mojibake(tmp_path: Path) -> None:
    result = _run_module(tmp_path, "watch")

    assert result.returncode == 0, result.stdout + result.stderr
    assert REPLACEMENT_CHAR_UTF8 not in result.stdout, (
        "watch output contains the U+FFFD mojibake replacement character; "
        "an em-dash or other non-ASCII character failed to encode correctly"
    )
    assert EM_DASH_UTF8 in result.stdout, (
        "expected watch's advisory line to contain a real UTF-8 em-dash"
    )


def test_cli_subprocess_writes_utf8_regardless_of_console_codepage(tmp_path: Path) -> None:
    # Directly exercises the fix in cli.py: sys.stdout/stderr are
    # reconfigured to UTF-8 at import time so output is not corrupted by
    # whatever codepage the ambient Windows console defaults to.
    check = subprocess.run(
        [
            sys.executable,
            "-c",
            "import smith.cli; import sys; sys.stdout.write(chr(0x2014))",
        ],
        capture_output=True,
        text=False,
        timeout=15,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert check.stdout == EM_DASH_UTF8
    assert REPLACEMENT_CHAR_UTF8 not in check.stdout
