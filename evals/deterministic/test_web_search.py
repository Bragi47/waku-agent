"""DETERMINISTIC EVAL — web search backends: parsers must survive a hostile
network, so what is OURS (the parsers and the failure posture) is pinned here
against a canned HTML/JSON stub server. Nothing touches the network.

The contract in one line: **the keyless chain fails OPEN and honestly.** A dead
engine must not silently look like "no results" — the chain names the broken
engine, tries the next one, and points at the keyed upgrade path.
"""

from __future__ import annotations

import http.server
import json
import threading
import urllib.parse

import pytest

import waku.tools.search as search

# Shapes copied from live responses (2026-08): DuckDuckGo lite wraps results in
# //duckduckgo.com/l/?uddg=<urlencoded>&rut=... with class='result-link'
# titles and class='result-snippet' cells; Wikipedia returns query.search with
# <span> search-match marks inside snippets.

LITE_PAGE = """
<html><head><title>новости сегодня at DuckDuckGo</title></head><body>
<table border="0"><tr><td valign="top">1.&nbsp;</td><td>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.mk.ru%2Fnews%2F&amp;rut=abc" class="result-link">Главные новости сегодня в России</a>
</td></tr>
<tr><td class='result-snippet'>Самые последние <b>новости</b> из страны.</td></tr>
<tr><td valign="top">2.&nbsp;</td><td>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Ftoday&amp;rut=def" class='result-link'>another headline</a>
</td></tr></table>
</body></html>
"""

WIKI_JSON = json.dumps({
    "batchcomplete": "",
    "query": {"search": [
        {"title": "Новости (телепередача)",
         "snippet": "«<span class=\"searchmatch\">Новости</span>» — телепередача."},
        {"title": "Технология", "snippet": "Технология — <span class=\"searchmatch\">наука</span>."},
    ]},
})


class _Stub(http.server.BaseHTTPRequestHandler):
    """Serves the two expected shapes by URL prefix: /lite/ and /w/api.php."""

    def do_GET(self):
        if self.path.startswith("/lite/"):
            body = LITE_PAGE.encode()
        else:
            body = WIKI_JSON.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # keep pytest output clean


@pytest.fixture
def stub():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


# ---------- the DuckDuckGo lite parser

def test_lite_parser_unwraps_redirects_and_keeps_order(stub):
    results = search._duckduckgo_lite("новости сегодня", 5, _base=stub)
    assert len(results) == 2
    title, snippet, url = results[0]
    assert title == "Главные новости сегодня в России"
    assert "последние" in snippet.lower()
    assert url == "https://www.mk.ru/news/", "the uddg redirect must unwrap to the real target"
    assert results[1][2] == "https://example.org/today"
    assert results[1][1] == "", "a missing snippet must come back empty, not crash"


def test_lite_parser_respects_max_results(stub):
    assert len(search._duckduckgo_lite("x", 1, _base=stub)) == 1


# ---------- the Wikipedia fallback parser

def test_wikipedia_parser_strips_marks_and_builds_links(stub):
    results = search._wikipedia_search("новости", 2, _base=stub + "/w/api.php")
    assert len(results) == 2
    title, snippet, url = results[0]
    assert "Новости" in title
    assert "<span" not in snippet, "snippet html marks must be stripped"
    assert url.startswith("https://ru.wikipedia.org/wiki/")
    last = urllib.parse.unquote(url.split("/")[-1])
    assert last == "Новости_(телепередача)", "the title must survive the round trip, spaces -> underscores"


def test_wikipedia_falls_back_to_english_when_russian_is_empty(monkeypatch, stub):
    def fake_hits(lang, query, max_results, _base=None):
        return [] if lang == "ru" else [("Water", "H2O", "https://en.wikipedia.org/wiki/Water")]

    monkeypatch.setattr(search, "_wiki_hits", fake_hits)
    results = search._wikipedia_search("water", 2)
    assert results and results[0][0] == "Water"


# ---------- the keyless chain: posture, not luck

def test_chain_falls_back_to_wikipedia_when_ddg_is_dead(monkeypatch):
    monkeypatch.setattr(search, "_duckduckgo_lite", lambda *a, **k: [])
    monkeypatch.setattr(search, "_wikipedia_search",
                        lambda *a, **k: [("Око", "снимок", "https://ru.wikipedia.org/wiki/Око")])
    out = search.make_tool().fn("что такое око")
    assert "via Wikipedia" in out
    assert "Око" in out


def test_chain_names_the_dead_engine_and_points_at_the_key(monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("TLS handshake broke")

    monkeypatch.setattr(search, "_duckduckgo_lite", broken)
    monkeypatch.setattr(search, "_wikipedia_search", lambda *a, **k: [])
    out = search.make_tool().fn("что-нибудь")
    assert "No web results" in out
    assert "DuckDuckGo unreachable" in out, "the dead engine must be named, not hidden"
    assert "TAVILY_API_KEY" in out, "doomed-network message must point at the keyed path"


# ---------- the keyed path keeps its contract

def test_tavily_is_used_when_a_key_is_set(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    calls = []

    def fake_tavily(query, key, max_results):
        calls.append((query, key, max_results))
        return [("T", "S", "U")]

    monkeypatch.setattr(search, "_tavily", fake_tavily)
    out = search.make_tool().fn("курс доллара")
    assert "via Tavily" in out
    assert calls == [("курс доллара", "k", 5)]