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


def test_local_whisper_missing_config_is_clear(audio_clip: Path, tmp_path: Path):
    env = dict(os.environ)
    env.pop("WATCH_DETAIL", None)
    env.pop("WATCH_LOCAL_WHISPER_BIN", None)
    env.pop("WATCH_LOCAL_WHISPER_MODEL", None)
    env["HOME"] = str(tmp_path)
    proc = subprocess.run(
        [
            sys.executable,
            str(WATCH),
            str(audio_clip),
            "--detail",
            "transcript",
            "--whisper",
            "local",
        ],
        capture_output=True, text=True, env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert "**Transcript:** none available" in proc.stdout
    assert "local Whisper unavailable" in proc.stderr
    assert "WATCH_LOCAL_WHISPER_BIN" in proc.stderr
    assert "WATCH_LOCAL_WHISPER_MODEL" in proc.stderr


def test_no_whisper_overrides_local_selection(audio_clip: Path, tmp_path: Path):
    env = dict(os.environ)
    env.pop("WATCH_DETAIL", None)
    env.pop("WATCH_LOCAL_WHISPER_BIN", None)
    env.pop("WATCH_LOCAL_WHISPER_MODEL", None)
    env["HOME"] = str(tmp_path)
    proc = subprocess.run(
        [
            sys.executable,
            str(WATCH),
            str(audio_clip),
            "--detail",
            "transcript",
            "--whisper",
            "local",
            "--no-whisper",
        ],
        capture_output=True, text=True, env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert "**Transcript:** none available" in proc.stdout
    assert "local Whisper unavailable" not in proc.stderr


def test_local_whisper_transcript_is_used(audio_clip: Path, tmp_path: Path):
    fake = tmp_path / "fake_whisper.py"
    fake.write_text(
        """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
out_prefix = args[args.index("-of") + 1]
Path(out_prefix + ".json").write_text(json.dumps({
    "transcription": [
        {"offsets": {"from": 0, "to": 1000}, "text": "local transcript works"}
    ]
}), encoding="utf-8")
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    env = dict(os.environ)
    env.pop("WATCH_DETAIL", None)
    env["HOME"] = str(tmp_path)
    env["WATCH_LOCAL_WHISPER_BIN"] = str(fake)
    env["WATCH_LOCAL_WHISPER_MODEL"] = str(model)
    env["WATCH_LOCAL_WHISPER_CHUNK_SECONDS"] = "10"

    proc = subprocess.run(
        [
            sys.executable,
            str(WATCH),
            str(audio_clip),
            "--detail",
            "transcript",
            "--whisper",
            "local",
        ],
        capture_output=True, text=True, env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert "**Transcript:** 1 segments (via whisper (local))" in proc.stdout
    assert "_Source: whisper (local)._" in proc.stdout
    assert "local transcript works" in proc.stdout


def test_local_whisper_transcript_is_filtered_by_range(audio_clip: Path, tmp_path: Path):
    fake = tmp_path / "fake_whisper.py"
    fake.write_text(
        """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
out_prefix = args[args.index("-of") + 1]
Path(out_prefix + ".json").write_text(json.dumps({
    "transcription": [
        {"offsets": {"from": 0, "to": 500}, "text": "outside range"},
        {"offsets": {"from": 700, "to": 1000}, "text": "inside range"}
    ]
}), encoding="utf-8")
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    env = dict(os.environ)
    env.pop("WATCH_DETAIL", None)
    env["HOME"] = str(tmp_path)
    env["WATCH_LOCAL_WHISPER_BIN"] = str(fake)
    env["WATCH_LOCAL_WHISPER_MODEL"] = str(model)
    env["WATCH_LOCAL_WHISPER_CHUNK_SECONDS"] = "10"
    env["WATCH_LOCAL_WHISPER_CACHE_DIR"] = str(tmp_path / "cache")

    proc = subprocess.run(
        [
            sys.executable,
            str(WATCH),
            str(audio_clip),
            "--detail",
            "transcript",
            "--whisper",
            "local",
            "--start",
            "0.6",
            "--end",
            "1.0",
        ],
        capture_output=True, text=True, env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert "**Transcript:** 1 segments in range (via whisper (local))" in proc.stdout
    assert "inside range" in proc.stdout
    assert "outside range" not in proc.stdout


def test_local_whisper_balanced_detail_keeps_frames(audio_clip: Path, tmp_path: Path):
    fake = tmp_path / "fake_whisper.py"
    fake.write_text(
        """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
out_prefix = args[args.index("-of") + 1]
Path(out_prefix + ".json").write_text(json.dumps({
    "transcription": [
        {"offsets": {"from": 0, "to": 1000}, "text": "local with frames"}
    ]
}), encoding="utf-8")
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    model = tmp_path / "model.bin"
    model.write_text("model", encoding="utf-8")
    env = dict(os.environ)
    env.pop("WATCH_DETAIL", None)
    env["HOME"] = str(tmp_path)
    env["WATCH_LOCAL_WHISPER_BIN"] = str(fake)
    env["WATCH_LOCAL_WHISPER_MODEL"] = str(model)
    env["WATCH_LOCAL_WHISPER_CHUNK_SECONDS"] = "10"
    env["WATCH_LOCAL_WHISPER_CACHE_DIR"] = str(tmp_path / "cache")

    proc = subprocess.run(
        [
            sys.executable,
            str(WATCH),
            str(audio_clip),
            "--detail",
            "balanced",
            "--whisper",
            "local",
        ],
        capture_output=True, text=True, env=env,
    )

    assert proc.returncode == 0, proc.stderr
    assert "**Frames:**" in proc.stdout
    assert "Frames live at:" in proc.stdout
    assert "(t=00:00" in proc.stdout
    assert "local with frames" in proc.stdout
