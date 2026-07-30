#!/usr/bin/env python3
"""Pre-process markdown links for Confluence sync.

mark (kovetskiy/mark) cannot resolve relative cross-file links when files are
processed one at a time (see confluence_sync.sh). This script rewrites:

  - Relative links to synced .agents/ and audit/ files → Confluence display URLs
  - Relative links to non-synced files (CLAUDE.md, etc.) → GitHub blob URLs
  - Same-page #anchors → preserved as-is

Writes pre-processed copies to a temp directory, preserving the directory
structure relative to the repo root. The caller passes the temp dir copies
to mark instead of the originals.

Usage:
    python3 scripts/confluence_preprocess.py <tmpdir> <file1> [file2 ...]

Env:
    CONFLUENCE_URL  — e.g. https://inheaden.atlassian.net/wiki
    GITHUB_REPO_URL — e.g. https://github.com/trehansalil/pageindex (auto-detected from git remote)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT_DIR = Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT_DIR / ".agents"
AUDIT_DIR = ROOT_DIR / "audit"
SPACE = "CITRA"

TITLE_RE = re.compile(r"<!--\s*Title:\s*(.+?)\s*-->", re.IGNORECASE)
SPACE_RE = re.compile(r"<!--\s*Space:", re.IGNORECASE)
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")


def _detect_github_url() -> str:
    try:
        url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=ROOT_DIR,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "https://github.com/trehansalil/pageindex"
    url = url.removesuffix(".git")
    if url.startswith("git@github.com:"):
        url = "https://github.com/" + url[len("git@github.com:") :]
    return url


def _get_default_branch() -> str:
    try:
        ref = subprocess.check_output(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=ROOT_DIR,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return ref.split("/")[-1]
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "master"


def build_title_map() -> dict[Path, str]:
    """Scan synced dirs for files with mark headers → {abs_path: page_title}."""
    title_map: dict[Path, str] = {}
    dirs = [AGENTS_DIR / "rfcs", AGENTS_DIR / "designs", AGENTS_DIR / "tasks", AUDIT_DIR]
    for d in dirs:
        if not d.is_dir():
            continue
        for f in d.glob("*.md"):
            if f.name.endswith("-metadata.md"):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not SPACE_RE.search(text):
                continue
            m = TITLE_RE.search(text)
            if m:
                title_map[f.resolve()] = m.group(1).strip()
    return title_map


def confluence_display_url(base_url: str, space: str, title: str) -> str:
    encoded_title = quote(title, safe="")
    return f"{base_url}/display/{space}/{encoded_title}"


def github_blob_url(github_url: str, branch: str, repo_rel_path: str, fragment: str) -> str:
    url = f"{github_url}/blob/{branch}/{repo_rel_path}"
    if fragment:
        url += f"#{fragment}"
    return url


def rewrite_links(
    text: str,
    source_file: Path,
    title_map: dict[Path, str],
    confluence_base: str,
    github_url: str,
    branch: str,
) -> str:
    source_dir = source_file.resolve().parent

    def _replace(m: re.Match) -> str:
        link_text = m.group(1)
        target = m.group(2)

        if target.startswith(("#", "http://", "https://", "mailto:")):
            return m.group(0)

        path_part, _, fragment = target.partition("#")

        if not path_part:
            return m.group(0)

        try:
            resolved = (source_dir / path_part).resolve()
        except (OSError, ValueError):
            return m.group(0)

        if resolved in title_map:
            page_title = title_map[resolved]
            url = confluence_display_url(confluence_base, SPACE, page_title)
            return f"[{link_text}]({url})"

        try:
            repo_rel = resolved.relative_to(ROOT_DIR)
        except ValueError:
            return m.group(0)

        if resolved.is_file():
            url = github_blob_url(github_url, branch, str(repo_rel), fragment)
            return f"[{link_text}]({url})"

        return m.group(0)

    return MD_LINK_RE.sub(_replace, text)


def main() -> None:
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <tmpdir> <file1> [file2 ...]", file=sys.stderr)
        sys.exit(1)

    tmpdir = Path(sys.argv[1])
    files = [Path(f) for f in sys.argv[2:]]

    confluence_base = os.environ.get("CONFLUENCE_URL", "https://inheaden.atlassian.net/wiki")
    confluence_base = confluence_base.rstrip("/")
    github_url = os.environ.get("GITHUB_REPO_URL", _detect_github_url())
    branch = _get_default_branch()

    title_map = build_title_map()

    for src in files:
        try:
            text = src.read_text(encoding="utf-8")
        except OSError as e:
            print(f"WARN: cannot read {src}: {e}", file=sys.stderr)
            continue

        rewritten = rewrite_links(text, src, title_map, confluence_base, github_url, branch)

        try:
            rel = src.resolve().relative_to(ROOT_DIR)
        except ValueError:
            rel = Path(src.name)

        dst = tmpdir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(rewritten, encoding="utf-8")

    print(f"[preprocess] rewrote {len(files)} file(s) into {tmpdir}", file=sys.stderr)


if __name__ == "__main__":
    main()
