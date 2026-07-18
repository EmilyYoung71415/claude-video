#!/usr/bin/env python3
"""Machine-readable YouTube source discovery using yt-dlp without media download."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from typing import Any
from urllib.parse import parse_qs, urlparse


def probe(source: str) -> dict[str, Any]:
    if shutil.which("yt-dlp") is None:
        return failure("unavailable", "yt-dlp is not installed")
    if not is_youtube_url(source):
        return failure("format_changed", "source is not a YouTube URL")
    command = [
        "yt-dlp", "--dump-single-json", "--flat-playlist", "--skip-download",
        "--ignore-errors", "--no-warnings", "--", source,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if not result.stdout.strip():
        return failure(classify_error(result.stderr), clean_message(result.stderr, result.returncode))
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        return failure("format_changed", "yt-dlp returned invalid JSON")
    if not isinstance(raw, dict):
        return failure("format_changed", "yt-dlp returned an unexpected JSON value")
    normalized = normalize(raw, source)
    if result.returncode != 0:
        normalized["warnings"].append({"kind": classify_error(result.stderr), "message": clean_message(result.stderr, result.returncode)})
    return normalized


def normalize(raw: dict[str, Any], source: str) -> dict[str, Any]:
    entries_value = raw.get("entries")
    is_collection = isinstance(entries_value, list)
    source_type = identify_type(raw, source, is_collection)
    platform_id = stable_id(raw, source_type)
    canonical_url = raw.get("webpage_url") or canonical_url_for(source_type, platform_id, source)
    entries: list[dict[str, Any]] = []
    unavailable = 0
    if is_collection:
        for entry in entries_value:
            if not isinstance(entry, dict) or not entry.get("id"):
                unavailable += 1
                continue
            video_id = str(entry["id"])
            availability = entry.get("availability")
            available = availability is None or availability in {"public", "unlisted"}
            if not available:
                unavailable += 1
            entries.append({
                "id": video_id,
                "url": entry_url(entry, video_id),
                "title": entry.get("title") or "(unavailable title)",
                "publishedAt": upload_date(entry),
                "available": available,
            })
    elif platform_id:
        entries.append({
            "id": platform_id, "url": canonical_url, "title": raw.get("title") or "(untitled)",
            "publishedAt": upload_date(raw), "available": True,
            "captionsAvailable": bool(raw.get("subtitles") or raw.get("automatic_captions")),
        })
    warnings = []
    if unavailable:
        warnings.append({"kind": "partial_unavailable", "message": f"{unavailable} entries were unavailable"})
    channel_id = raw.get("channel_id") or (platform_id if source_type == "channel" and str(platform_id).startswith("UC") else None)
    return {
        "schemaVersion": 1, "status": "ok", "sourceType": source_type, "platformId": platform_id,
        "canonicalUrl": canonical_url, "title": raw.get("title") or raw.get("channel") or raw.get("uploader") or "(untitled)",
        "officialFeedUrl": f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}" if channel_id else None,
        "entries": entries, "warnings": warnings,
    }


def identify_type(raw: dict[str, Any], source: str, is_collection: bool) -> str:
    parsed = urlparse(source)
    query = parse_qs(parsed.query)
    if "list" in query or "/playlist" in parsed.path:
        return "playlist"
    if parsed.hostname == "youtu.be" or parsed.path == "/watch" or parsed.path.startswith("/shorts/"):
        return "video"
    if any(parsed.path.startswith(prefix) for prefix in ("/@", "/channel/", "/c/", "/user/")):
        return "channel"
    if not is_collection:
        raw_id = str(raw.get("id") or "")
        if raw_id.startswith("PL"):
            return "playlist"
        if raw_id.startswith("UC"):
            return "channel"
        return "video"
    return "channel"


def stable_id(raw: dict[str, Any], source_type: str) -> str | None:
    if source_type == "channel":
        return raw.get("channel_id") or raw.get("uploader_id") or raw.get("id")
    return raw.get("playlist_id") or raw.get("id")


def canonical_url_for(source_type: str, platform_id: str | None, fallback: str) -> str:
    if not platform_id:
        return fallback
    if source_type == "video":
        return f"https://www.youtube.com/watch?v={platform_id}"
    if source_type == "playlist":
        return f"https://www.youtube.com/playlist?list={platform_id}"
    if str(platform_id).startswith("UC"):
        return f"https://www.youtube.com/channel/{platform_id}"
    return fallback


def upload_date(value: dict[str, Any]) -> str | None:
    raw = value.get("upload_date") or value.get("release_date")
    if isinstance(raw, str) and len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}T00:00:00Z"
    timestamp = value.get("timestamp") or value.get("release_timestamp")
    if isinstance(timestamp, (int, float)):
        from datetime import datetime, timezone
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")
    return None


def entry_url(entry: dict[str, Any], video_id: str) -> str:
    webpage_url = entry.get("webpage_url")
    if isinstance(webpage_url, str) and webpage_url.startswith("http"):
        return webpage_url
    url = entry.get("url")
    if isinstance(url, str) and url.startswith("http"):
        return url
    return f"https://www.youtube.com/watch?v={video_id}"


def classify_error(stderr: str) -> str:
    text = stderr.lower()
    if any(term in text for term in ("too many requests", "http error 429", "rate limit")):
        return "rate_limited"
    if any(term in text for term in ("private video", "sign in", "login", "cookies")):
        return "authentication_required"
    if any(term in text for term in ("not available in your country", "geo-restricted", "region")):
        return "region_restricted"
    if any(term in text for term in ("video unavailable", "has been removed", "deleted")):
        return "not_found"
    if any(term in text for term in ("unsupported url", "unable to extract", "extractor")):
        return "format_changed"
    return "unavailable"


def failure(kind: str, message: str) -> dict[str, Any]:
    return {"schemaVersion": 1, "status": "error", "error": {"kind": kind, "message": message}}


def clean_message(stderr: str, returncode: int) -> str:
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    return lines[-1] if lines else f"yt-dlp failed with exit code {returncode}"


def is_youtube_url(source: str) -> bool:
    parsed = urlparse(source)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and (host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe a YouTube source as JSON without downloading media")
    parser.add_argument("source")
    args = parser.parse_args()
    result = probe(args.source)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
