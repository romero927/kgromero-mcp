"""Load resume data from kgromero.com, with a bundled snapshot as fallback."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date
from importlib import resources
from typing import Any

import httpx

log = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)

SITE_URL = os.environ.get("KGROMERO_SITE_URL", "https://kgromero.com").rstrip("/")
RESUME_JSON_URL = f"{SITE_URL}/api/resume"
RESUME_MD_URL = f"{SITE_URL}/kgromero.md"
SITE_JSON_URL = f"{SITE_URL}/api/site.json"
CACHE_TTL_SECONDS = 3600
OFFLINE = os.environ.get("KGROMERO_OFFLINE", "").lower() in {"1", "true", "yes"}

_cache: dict[str, tuple[float, Any]] = {}


def _snapshot(name: str) -> str:
    return resources.files("kgromero_mcp").joinpath("snapshot", name).read_text("utf-8")


async def _fetch(url: str, snapshot_name: str, parse_json: bool) -> Any:
    """Fetch a URL with an in-memory TTL cache; fall back to the bundled snapshot."""
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]

    value: Any = None
    if not OFFLINE:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "kgromero-mcp"})
                resp.raise_for_status()
                value = resp.json() if parse_json else resp.text
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("Fetching %s failed (%s); using bundled snapshot", url, exc)

    if value is None:
        raw = _snapshot(snapshot_name)
        value = json.loads(raw) if parse_json else raw

    _cache[url] = (time.monotonic(), value)
    return value


async def get_resume() -> dict[str, Any]:
    return await _fetch(RESUME_JSON_URL, "kgromero.json", parse_json=True)


async def get_resume_markdown() -> str:
    return await _fetch(RESUME_MD_URL, "kgromero.md", parse_json=False)


async def get_site() -> dict[str, Any]:
    """Home-page content beyond the resume: projects, skills, docs, travel, etc."""
    return await _fetch(SITE_JSON_URL, "site.json", parse_json=True)


# ---------- formatting helpers ----------

def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def format_range(start: str | None, end: str | None) -> str:
    s, e = _parse_date(start), _parse_date(end)
    fmt = lambda d: d.strftime("%b %Y")  # noqa: E731
    return f"{fmt(s) if s else '?'} – {fmt(e) if e else 'Present'}"


def duration_months(start: str | None, end: str | None) -> int | None:
    s = _parse_date(start)
    if not s:
        return None
    e = _parse_date(end) or date.today()
    return max(0, (e.year - s.year) * 12 + (e.month - s.month))


def split_highlight(text: str) -> tuple[str | None, str]:
    """Split 'Label: body' highlights into (label, body)."""
    m = re.match(r"^([A-Z][\w &/()-]{0,40}):\s+(.*)$", text, re.S)
    return (m.group(1), m.group(2)) if m else (None, text)


def split_tech_stack(text: str) -> list[str]:
    """Split a comma-separated tech list, ignoring commas inside parentheses.

    'AWS (EKS, S3), Docker, Claude.' -> ['AWS (EKS, S3)', 'Docker', 'Claude']
    """
    items, depth, buf = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    items.append("".join(buf))
    return [i.strip().rstrip(".").strip() for i in items if i.strip().rstrip(".").strip()]
