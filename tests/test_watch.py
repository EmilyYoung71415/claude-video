"""End-to-end routing of --detail through watch.py on a local clip."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WATCH = Path(__file__).resolve().parent.parent / "skills" / "watch" / "scripts" / "watch.py"


def _run(clip: Path, *args: str, env_extra: dict | None = None) -> str:
    env = dict(os.environ)
    env.pop("WATCH_DETAIL", None)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(WATCH), str(clip), "--no-whisper", *args],
        capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_efficient_uses_keyframe_engine(cut_clip: Path):
    out = _run(cut_clip, "--detail", "efficient")
    assert "(keyframe" in out
    assert "**Detail:** efficient" in out


def test_balanced_uses_scene_engine(cut_clip: Path):
    out = _run(cut_clip, "--detail", "balanced")
    assert "(scene" in out
    assert "**Detail:** balanced" in out


def test_token_burner_uses_scene_engine(cut_clip: Path):
    out = _run(cut_clip, "--detail", "token-burner")
    assert "(scene" in out


def test_transcript_skips_frames(cut_clip: Path):
    out = _run(cut_clip, "--detail", "transcript")
    assert "skipped" in out
    assert "frame_0000.jpg" not in out


def test_flag_overrides_env(cut_clip: Path):
    out = _run(cut_clip, "--detail", "efficient", env_extra={"WATCH_DETAIL": "balanced"})
    assert "(keyframe" in out


def test_default_is_balanced(cut_clip: Path):
    out = _run(cut_clip)  # no flag, WATCH_DETAIL cleared
    assert "**Detail:** balanced" in out
    assert "(scene" in out


def test_timestamps_add_cue_frames_to_detail(cut_clip: Path):
    out = _run(cut_clip, "--detail", "balanced", "--timestamps", "1,3")
    assert "reason=transcript-cue" in out
    assert "reason=scene-change" in out  # detail frames still present (additive)


def test_timestamps_with_transcript_detail_is_cue_only(cut_clip: Path):
    out = _run(cut_clip, "--detail", "transcript", "--timestamps", "1,3")
    assert "reason=transcript-cue" in out
    assert "reason=scene-change" not in out
    assert "reason=keyframe" not in out


def _frame_lines(out: str) -> int:
    return sum(1 for line in out.splitlines() if "/frames/frame_" in line and "(t=" in line)


def test_dedup_collapses_static_by_default(static_clip: Path):
    out = _run(static_clip)  # solid blue → identical frames collapse to one
    assert "near-duplicate" in out
    assert _frame_lines(out) == 1


def test_no_dedup_preserves_static_frames(static_clip: Path):
    out = _run(static_clip, "--no-dedup")
    assert "near-duplicate" not in out
    assert _frame_lines(out) > 1


def test_transcript_detail_uses_external_srt_without_whisper(cut_clip: Path, tmp_path: Path):
    subtitle = tmp_path / "sample.srt"
    subtitle.write_text(
        """1
00:00:00,000 --> 00:00:02,000
Hello world

2
00:00:02,000 --> 00:00:04,000
This is an external subtitle test.
""",
        encoding="utf-8",
    )

    out = _run(cut_clip, "--detail", "transcript", "--transcript", str(subtitle))

    assert "**Transcript:** 2 segments" in out
    assert "via external transcript (sample.srt)" in out
    assert "_Source: external transcript (sample.srt)._" in out
    assert f"**Title:** {cut_clip.name}" in out
    assert "**Duration:** 00:00 (0.0s)" not in out
    assert "Hello world" in out
    assert "This is an external subtitle test." in out
    assert "skipped (transcript detail)" in out


def test_external_vtt_is_filtered_by_range(cut_clip: Path, tmp_path: Path):
    subtitle = tmp_path / "sample.vtt"
    subtitle.write_text(
        """WEBVTT

00:00:00.000 --> 00:00:01.000
Outside start

00:00:02.000 --> 00:00:04.000
Inside range
""",
        encoding="utf-8",
    )

    out = _run(
        cut_clip,
        "--detail",
        "transcript",
        "--transcript",
        str(subtitle),
        "--start",
        "2",
        "--end",
        "4",
    )

    assert "**Transcript:** 1 segments in range" in out
    assert "Inside range" in out
    assert "Outside start" not in out


def test_external_transcript_missing_file_is_clear(cut_clip: Path, tmp_path: Path):
    proc = subprocess.run(
        [
            sys.executable,
            str(WATCH),
            str(cut_clip),
            "--detail",
            "transcript",
            "--transcript",
            str(tmp_path / "missing.srt"),
        ],
        capture_output=True,
        text=True,
    )

    assert proc.returncode != 0
    assert "Transcript file not found" in proc.stderr
