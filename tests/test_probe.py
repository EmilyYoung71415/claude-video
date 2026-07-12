from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "watch" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import probe  # noqa: E402


class Result:
    def __init__(self, payload=None, stderr="", returncode=0):
        self.stdout = json.dumps(payload) if payload is not None else ""
        self.stderr = stderr
        self.returncode = returncode


def run(monkeypatch, payload, source, *, stderr="", returncode=0):
    calls = []
    monkeypatch.setattr(probe.shutil, "which", lambda _: "/usr/bin/yt-dlp")
    monkeypatch.setattr(probe.subprocess, "run", lambda command, **kwargs: calls.append(command) or Result(payload, stderr, returncode))
    value = probe.probe(source)
    assert "--skip-download" in calls[0]
    assert "--flat-playlist" in calls[0]
    return value


def test_channel_probe_returns_identity_feed_and_entries(monkeypatch):
    result = run(monkeypatch, {"id": "UC123", "channel_id": "UC123", "title": "Channel", "entries": [
        {"id": "v1", "title": "One", "upload_date": "20260701"},
    ]}, "https://www.youtube.com/@example/videos")
    assert result == {
        "schemaVersion": 1, "status": "ok", "sourceType": "channel", "platformId": "UC123",
        "canonicalUrl": "https://www.youtube.com/channel/UC123", "title": "Channel",
        "officialFeedUrl": "https://www.youtube.com/feeds/videos.xml?channel_id=UC123",
        "entries": [{"id": "v1", "url": "https://www.youtube.com/watch?v=v1", "title": "One",
                     "publishedAt": "2026-07-01T00:00:00Z", "available": True}], "warnings": [],
    }


def test_playlist_supports_empty_and_partially_unavailable_entries(monkeypatch):
    empty = run(monkeypatch, {"id": "PL1", "title": "Empty", "entries": []},
                "https://www.youtube.com/playlist?list=PL1")
    assert empty["sourceType"] == "playlist" and empty["entries"] == []
    partial = run(monkeypatch, {"id": "PL1", "title": "List", "entries": [None, {"id": "v2", "title": "Two"}]},
                  "https://www.youtube.com/playlist?list=PL1")
    assert [item["id"] for item in partial["entries"]] == ["v2"]
    assert partial["warnings"][0]["kind"] == "partial_unavailable"


def test_single_video_reports_caption_availability(monkeypatch):
    result = run(monkeypatch, {"id": "v1", "title": "Video", "webpage_url": "https://youtu.be/v1",
                               "automatic_captions": {}}, "https://www.youtube.com/watch?v=v1")
    assert result["sourceType"] == "video"
    assert result["entries"][0]["captionsAvailable"] is False


@pytest.mark.parametrize(("stderr", "kind"), [
    ("Private video. Sign in", "authentication_required"),
    ("Video unavailable", "not_found"),
    ("not available in your country", "region_restricted"),
    ("HTTP Error 429: Too Many Requests", "rate_limited"),
    ("Unable to extract player data", "format_changed"),
])
def test_structured_failures(monkeypatch, stderr, kind):
    result = run(monkeypatch, None, "https://www.youtube.com/watch?v=v1", stderr=stderr, returncode=1)
    assert result["status"] == "error"
    assert result["error"]["kind"] == kind
