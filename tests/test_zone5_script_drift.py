"""Zone-5 script-drift tests: CI guard against hardcoded Arabic codepoint ranges outside script.py."""
from __future__ import annotations

import re
from pathlib import Path

SRC_DIR = Path(__file__).parent.parent / "src" / "pageindex_mcp"

_HEX_ARABIC_RE = re.compile(r"0x0[6-8][0-9A-Fa-f]{2}|0xF[BEe][0-9A-Fa-f]{2}")


def test_no_hardcoded_arabic_ranges_outside_script():
    """No file other than script.py should contain raw Arabic hex literals."""
    targets = [SRC_DIR / "helpers.py", SRC_DIR / "converters.py"]
    violations: list[str] = []

    for fpath in targets:
        if not fpath.exists():
            continue
        in_joining_type = False
        for lineno, line in enumerate(fpath.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "from .script" in line:
                continue
            # Track _JOINING_TYPE dict span: it is a lookup table keyed by
            # individual codepoints, not a range definition -- allowed.
            if "_JOINING_TYPE" in line:
                in_joining_type = True
                continue
            if in_joining_type:
                if stripped == "}" or stripped.startswith("}"):
                    in_joining_type = False
                continue
            matches = _HEX_ARABIC_RE.findall(line)
            if matches:
                violations.append(
                    f"{fpath.name}:{lineno}: {matches}  ->  {stripped}"
                )

    assert violations == [], (
        "Hardcoded Arabic codepoint ranges found outside script.py — "
        "import from pageindex_mcp.script instead:\n" + "\n".join(violations)
    )


def test_canonical_ranges_cover_all_blocks():
    """ARABIC_RANGES must cover the 5 standard Unicode Arabic blocks."""
    from pageindex_mcp.script import ARABIC_RANGES

    expected = (
        (0x0600, 0x06FF),
        (0x0750, 0x077F),
        (0x08A0, 0x08FF),
        (0xFB50, 0xFDFF),
        (0xFE70, 0xFEFF),
    )
    assert ARABIC_RANGES == expected, (
        f"ARABIC_RANGES drifted from canonical blocks.\n"
        f"  got:      {ARABIC_RANGES}\n"
        f"  expected: {expected}"
    )
