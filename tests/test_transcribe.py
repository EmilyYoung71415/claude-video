"""External transcript parsing for SRT and VTT files."""
from __future__ import annotations

from pathlib import Path

import pytest

import transcribe


def test_parse_srt_returns_timestamped_segments(tmp_path: Path):
    subtitle = tmp_path / "sample.srt"
    subtitle.write_text(
        """1
00:00:00,000 --> 00:00:02,000
Hello world

2
00:00:02,000 --> 00:00:04,500
This is an external subtitle test.
""",
        encoding="utf-8",
    )

    assert transcribe.parse_srt(str(subtitle)) == [
        {"start": 0.0, "end": 2.0, "text": "Hello world"},
        {"start": 2.0, "end": 4.5, "text": "This is an external subtitle test."},
    ]


def test_parse_srt_strips_inline_tags_and_joins_multiline_cues(tmp_path: Path):
    subtitle = tmp_path / "sample.srt"
    subtitle.write_text(
        """1
00:00:01,000 --> 00:00:03,000
<i>Hello</i>
world
""",
        encoding="utf-8",
    )

    assert transcribe.parse_srt(str(subtitle)) == [
        {"start": 1.0, "end": 3.0, "text": "Hello world"},
    ]


def test_parse_transcript_file_accepts_vtt(tmp_path: Path):
    subtitle = tmp_path / "sample.vtt"
    subtitle.write_text(
        """WEBVTT

00:00:00.000 --> 00:00:02.000
Hello from VTT
""",
        encoding="utf-8",
    )

    assert transcribe.parse_transcript_file(str(subtitle)) == [
        {"start": 0.0, "end": 2.0, "text": "Hello from VTT"},
    ]


def test_parse_transcript_file_rejects_missing_file(tmp_path: Path):
    with pytest.raises(SystemExit, match="Transcript file not found"):
        transcribe.parse_transcript_file(str(tmp_path / "missing.srt"))


def test_parse_transcript_file_rejects_unsupported_extension(tmp_path: Path):
    subtitle = tmp_path / "sample.txt"
    subtitle.write_text("Hello", encoding="utf-8")

    with pytest.raises(SystemExit, match="Unsupported transcript format"):
        transcribe.parse_transcript_file(str(subtitle))


def test_parse_transcript_file_rejects_empty_parse(tmp_path: Path):
    subtitle = tmp_path / "empty.srt"
    subtitle.write_text("not a subtitle", encoding="utf-8")

    with pytest.raises(SystemExit, match="Transcript file contains no usable subtitle cues"):
        transcribe.parse_transcript_file(str(subtitle))
