"""search_web — the second real tool, and a great LOOP demo.

"Find the World Cup games left and put them on my calendar" makes the agent
loop across tools: search_web (read the web) → reason over the results →
create_event once per match. Watch the LOOP box cycle on the dashboard.

Zero new dependencies — just stdlib urllib. Backends, in order:

  keyed     Tavily, if TAVILY_API_KEY (or WAKU_SEARCH_API_KEY) is set — an
            agent-friendly API with clean results (free tier)
  keyless   DuckDuckGo "lite" page — html.duckduckgo.com blocks automation
            on most networks (TLS handshake failures, captchas), but the
            lite endpoint still serves plain result tables
  fallback  Wikipedia full-text API (ru first, then en) — the one host that
            answers from behind geo-blocked ISPs; encyclopedic depth

The tool returns plain text the model reads; it never parses HTML for the model.
"""

from __future__ import annotations

import html
import json
import os
import re
import urllib.parse
import urllib.request

from waku.tools.registry import Tool

# lite.duckduckgo.com serves different HTML per User-Agent: a long browser
# UA string trips its "anomaly" challenge (zero result rows), a bare
# "Mozilla/5.0" gets the real result table. Fixed here, tested live 2026-08.
_UA = "Mozilla/5.0"


def _tavily(query: str, key: str, max_results: int) -> list[tuple[str, str, str]]:
    body = json.dumps({"api_key": key, "query": query, "max_results": max_results,
                       "include_answer": False}).encode()
    req = urllib.request.Request("https://api.tavily.com/search", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    return [(r.get("title", ""), (r.get("content", "") or "")[:400], r.get("url", ""))
            for r in data.get("results", [])]


def _strip(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _unwrap(href: str) -> str:
    """DuckDuckGo lite wraps every result in //duckduckgo.com/l/?uddg=<url>&rut=...
    — pull out the real target (and give relative-scheme URLs their https:)."""
    if "uddg=" in href:
        href = urllib.parse.parse_qs(href.split("?", 1)[1]).get("uddg", [""])[0]
    if href.startswith("//"):
        href = "https:" + href
    return href


def _duckduckgo_lite(query: str, max_results: int,
                     _base: str = "https://lite.duckduckgo.com") -> list[tuple[str, str, str]]:
    """The keyless default — lite.duckduckgo.com serves plain server-rendered
    results (class='result-link' anchors + result-snippet cells) that a tiny
    regex understands. `_base` exists for the offline deterministic eval."""
    url = f"{_base}/lite/?q={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        page = resp.read().decode("utf-8", "ignore")
    links = re.findall(r'<a[^>]+href="([^"]+)"[^>]+class=[\'"]result-link[\'"]>(.*?)</a>',
                       page, re.DOTALL)
    snips = re.findall(r'class=[\'"]result-snippet[\'"][^>]*>(.*?)</', page, re.DOTALL)
    out = []
    for i, (href, title) in enumerate(links[:max_results]):
        out.append((_strip(title), _strip(snips[i]) if i < len(snips) else "",
                    _unwrap(href)))
    return out


def _wikipedia_search(query: str, max_results: int,
                      _base: str | None = None) -> list[tuple[str, str, str]]:
    """Full-text search over the Wikipedia API — no key, and wikipedia.org is
    the one host that keeps answering from networks that geo-block the rest.
    Russian first; if it has nothing, try the bigger English index. `_base`
    exists for the offline deterministic eval (a stub api.php server)."""
    langs = ("ru", "en") if _base is None else ("ru",)
    for lang in langs:
        try:
            hits = _wiki_hits(lang, query, max_results, _base=_base)
        except Exception:
            hits = []
        if hits:
            return hits
    return []


def _wiki_hits(lang: str, query: str, max_results: int,
               _base: str | None = None) -> list[tuple[str, str, str]]:
    api = _base or f"https://{lang}.wikipedia.org/w/api.php"
    url = (f"{api}?action=query&list=search"
           f"&srsearch={urllib.parse.quote(query)}&srlimit={max_results}&format=json")
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out = []
    for h in data.get("query", {}).get("search", []) or []:
        title = h.get("title", "")
        snippet = re.sub(r"<[^>]+>", "", h.get("snippet", "") or "")
        link = f"https://{lang}.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
        out.append((_strip(title), _strip(snippet), link))
    return out


def _fmt(engine: str, query: str, results: list[tuple[str, str, str]]) -> str:
    lines = [f"Web results for '{query}' (via {engine}):"]
    for i, (title, snippet, link) in enumerate(results, 1):
        lines.append(f"{i}. {title}\n   {snippet}\n   {link}")
    return "\n".join(lines) if results else ""


def make_tool() -> Tool:
    def search_web(query: str, max_results: int = 5) -> str:
        key = os.getenv("TAVILY_API_KEY") or os.getenv("WAKU_SEARCH_API_KEY")
        if key:
            try:
                results = _tavily(query, key, max_results)
            except Exception as exc:
                return f"Web search failed ({exc}). Answer from what you know, or ask the user."
            if not results:
                return "No results found. Try a more specific query."
            return _fmt("Tavily", query, results)

        # Keyless chain — DuckDuckGo lite first, then Wikipedia as the sturdy
        # fallback. Both may fail on hostile networks; each failure is faced
        # openly instead of masked (the same fail-open rule as the gate).
        problems = []
        results = []
        try:
            results = _duckduckgo_lite(query, max_results)
        except Exception as exc:
            problems.append(f"DuckDuckGo unreachable here ({exc})")
        engine = "DuckDuckGo"
        if not results:
            try:
                results = _wikipedia_search(query, max_results)
                engine = "Wikipedia"
            except Exception as exc:
                problems.append(f"Wikipedia unreachable ({exc})")
        if not results:
            tip = ("; ".join(problems) + ". " if problems else "")
            return (tip + "No web results. The one engine that reliably answers from this "
                    "network is Wikipedia; for full web search set a free TAVILY_API_KEY "
                    "in .env (create it from a network without geo blocks). Meanwhile, tell "
                    "the user you could not search and ask how to proceed.")
        return _fmt(engine, query, results)

    return Tool(
        name="search_web",
        description=(
            "Search the public web and get back the top results (title, snippet, URL). "
            "Use when the user asks about current events, facts, schedules, or anything "
            "you don't already know — then act on what you find (e.g. create calendar events)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "the search query"},
                "max_results": {"type": "integer", "description": "how many results (default 5)"},
            },
            "required": ["query"],
        },
        fn=search_web,
    )