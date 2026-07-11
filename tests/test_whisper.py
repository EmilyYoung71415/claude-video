"""Whisper auto-chunking: plan, split, and timestamp stitching."""
from __future__ import annotations

import math
import json
import subprocess
from pathlib import Path

import pytest

import whisper


MB = 1024 * 1024


class TestPlanChunks:
    def test_under_limit_is_single_chunk(self):
        plan = whisper.plan_chunks(total_seconds=600.0, total_bytes=5 * MB, max_bytes=24 * MB)
        assert plan == [(0.0, 600.0)]

    def test_at_limit_is_single_chunk(self):
        plan = whisper.plan_chunks(total_seconds=600.0, total_bytes=24 * MB, max_bytes=24 * MB)
        assert plan == [(0.0, 600.0)]

    def test_over_limit_splits_into_enough_chunks(self):
        # 71 MB against a 24 MB cap → ceil(71/24) = 3 chunks.
        plan = whisper.plan_chunks(total_seconds=3600.0, total_bytes=71 * MB, max_bytes=24 * MB)
        assert len(plan) == 3

    def test_chunks_are_contiguous_and_cover_full_duration(self):
        total = 3600.0
        plan = whisper.plan_chunks(total_seconds=total, total_bytes=71 * MB, max_bytes=24 * MB)
        # Offsets start at 0 and each picks up where the previous ended.
        assert plan[0][0] == 0.0
        for (off, dur), (next_off, _) in zip(plan, plan[1:]):
            assert math.isclose(off + dur, next_off)
        last_off, last_dur = plan[-1]
        assert math.isclose(last_off + last_dur, total)

    def test_each_chunk_estimated_under_limit(self):
        total_seconds, total_bytes, cap = 3600.0, 71 * MB, 24 * MB
        plan = whisper.plan_chunks(total_seconds, total_bytes, cap)
        bytes_per_second = total_bytes / total_seconds
        for _off, dur in plan:
            assert dur * bytes_per_second <= cap

    def test_zero_duration_is_single_chunk(self):
        plan = whisper.plan_chunks(total_seconds=0.0, total_bytes=0, max_bytes=24 * MB)
        assert plan == [(0.0, 0.0)]


class TestShiftSegments:
    def test_adds_offset_to_start_and_end(self):
        segs = [{"start": 0.0, "end": 2.5, "text": "hi"}, {"start": 2.5, "end": 4.0, "text": "there"}]
        shifted = whisper.shift_segments(segs, 1800.0)
        assert shifted == [
            {"start": 1800.0, "end": 1802.5, "text": "hi"},
            {"start": 1802.5, "end": 1804.0, "text": "there"},
        ]

    def test_zero_offset_is_identity(self):
        segs = [{"start": 1.0, "end": 2.0, "text": "x"}]
        assert whisper.shift_segments(segs, 0.0) == segs

    def test_does_not_mutate_input(self):
        segs = [{"start": 0.0, "end": 1.0, "text": "x"}]
        whisper.shift_segments(segs, 10.0)
        assert segs[0]["start"] == 0.0


def _make_mp3(path: Path, seconds: float) -> None:
    """Synthesize a mono 16k 64k mp3 of a sine tone — mirrors extract_audio's format."""
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-t", str(seconds), "-i", "sine=frequency=440:sample_rate=16000",
            "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1", "-b:a", "64k",
            str(path),
        ],
        check=True,
    )


class TestSplitAudio:
    def test_creates_one_file_per_plan_entry(self, tmp_path: Path):
        full = tmp_path / "audio.mp3"
        _make_mp3(full, 6.0)
        plan = [(0.0, 3.0), (3.0, 3.0)]

        chunks = whisper.split_audio(full, tmp_path, plan)

        assert len(chunks) == 2
        for chunk_path, _offset in chunks:
            assert chunk_path.exists() and chunk_path.stat().st_size > 0

    def test_returns_plan_offsets(self, tmp_path: Path):
        full = tmp_path / "audio.mp3"
        _make_mp3(full, 6.0)
        plan = [(0.0, 3.0), (3.0, 3.0)]

        chunks = whisper.split_audio(full, tmp_path, plan)

        assert [offset for _path, offset in chunks] == [0.0, 3.0]

    def test_chunks_are_smaller_than_full(self, tmp_path: Path):
        full = tmp_path / "audio.mp3"
        _make_mp3(full, 6.0)
        plan = [(0.0, 3.0), (3.0, 3.0)]

        chunks = whisper.split_audio(full, tmp_path, plan)

        full_size = full.stat().st_size
        for chunk_path, _offset in chunks:
            assert chunk_path.stat().st_size < full_size


class TestAudioDuration:
    def test_reads_duration_of_synthesized_clip(self, tmp_path: Path):
        audio = tmp_path / "audio.mp3"
        _make_mp3(audio, 5.0)
        assert whisper.audio_duration(audio) == pytest.approx(5.0, abs=0.5)


class TestWhisperBackendConfig:
    @pytest.fixture(autouse=True)
    def _clean_config_files(self, monkeypatch, tmp_path):
        monkeypatch.setattr(whisper, "CONFIG_FILE", tmp_path / "missing.env")
        monkeypatch.chdir(tmp_path)

    def test_openai_default_endpoint_and_model(self, monkeypatch):
        monkeypatch.delenv("WATCH_OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("WATCH_OPENAI_MODEL", raising=False)

        assert whisper.endpoint_for_backend("openai") == "https://api.openai.com/v1/audio/transcriptions"
        assert whisper.model_for_backend("openai") == "whisper-1"

    def test_watch_openai_base_url_overrides_default(self, monkeypatch):
        monkeypatch.setenv("WATCH_OPENAI_BASE_URL", "https://gateway.example.com/v1/")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)

        assert whisper.endpoint_for_backend("openai") == "https://gateway.example.com/v1/audio/transcriptions"

    def test_openai_base_url_is_fallback(self, monkeypatch):
        monkeypatch.delenv("WATCH_OPENAI_BASE_URL", raising=False)
        monkeypatch.setenv("OPENAI_BASE_URL", "https://fallback.example.com/v1")

        assert whisper.endpoint_for_backend("openai") == "https://fallback.example.com/v1/audio/transcriptions"

    def test_watch_openai_base_url_wins_over_openai_base_url(self, monkeypatch):
        monkeypatch.setenv("WATCH_OPENAI_BASE_URL", "https://watch.example.com/v1")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://fallback.example.com/v1")

        assert whisper.endpoint_for_backend("openai") == "https://watch.example.com/v1/audio/transcriptions"

    def test_groq_default_endpoint_and_model(self, monkeypatch):
        monkeypatch.delenv("WATCH_GROQ_BASE_URL", raising=False)
        monkeypatch.delenv("WATCH_GROQ_MODEL", raising=False)

        assert whisper.endpoint_for_backend("groq") == "https://api.groq.com/openai/v1/audio/transcriptions"
        assert whisper.model_for_backend("groq") == "whisper-large-v3"

    def test_groq_base_url_overrides_default_without_double_slash(self, monkeypatch):
        monkeypatch.setenv("WATCH_GROQ_BASE_URL", "https://groq-gateway.example.com/openai/v1/")

        assert whisper.endpoint_for_backend("groq") == "https://groq-gateway.example.com/openai/v1/audio/transcriptions"

    def test_backend_models_are_configurable(self, monkeypatch):
        monkeypatch.setenv("WATCH_GROQ_MODEL", "custom-groq")
        monkeypatch.setenv("WATCH_OPENAI_MODEL", "custom-openai")

        assert whisper.model_for_backend("groq") == "custom-groq"
        assert whisper.model_for_backend("openai") == "custom-openai"

    def test_base_url_can_come_from_watch_config_file(self, monkeypatch, tmp_path):
        config_file = tmp_path / ".env"
        config_file.write_text(
            "WATCH_OPENAI_BASE_URL=https://config.example.com/v1\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("WATCH_OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.setattr(whisper, "CONFIG_FILE", config_file)

        assert whisper.endpoint_for_backend("openai") == "https://config.example.com/v1/audio/transcriptions"


class TestLocalWhisperConfig:
    @pytest.fixture(autouse=True)
    def _clean_config(self, monkeypatch, tmp_path):
        monkeypatch.setattr(whisper, "CONFIG_FILE", tmp_path / "missing.env")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("WATCH_LOCAL_WHISPER_BIN", raising=False)
        monkeypatch.delenv("WATCH_LOCAL_WHISPER_MODEL", raising=False)

    def test_missing_local_config_reports_both_required_values(self):
        status = whisper.local_whisper_status()

        assert status["configured"] is False
        assert "WATCH_LOCAL_WHISPER_BIN is not set" in status["problems"]
        assert "WATCH_LOCAL_WHISPER_MODEL is not set" in status["problems"]

    def test_local_config_accepts_executable_on_path_and_model_file(self, monkeypatch, tmp_path):
        fake_bin_dir = tmp_path / "bin"
        fake_bin_dir.mkdir()
        fake_bin = fake_bin_dir / "whisper-cli"
        fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
        fake_bin.chmod(0o755)
        model = tmp_path / "ggml-base.bin"
        model.write_text("model", encoding="utf-8")
        monkeypatch.setenv("PATH", f"{fake_bin_dir}")
        monkeypatch.setenv("WATCH_LOCAL_WHISPER_BIN", "whisper-cli")
        monkeypatch.setenv("WATCH_LOCAL_WHISPER_MODEL", str(model))

        status = whisper.local_whisper_status()

        assert status["configured"] is True
        assert status["binary"] == str(fake_bin)
        assert status["model"] == str(model)
        assert status["problems"] == []

    def test_local_setup_message_names_config_file_and_env_keys(self):
        msg = whisper.local_whisper_setup_message()

        assert msg is not None
        assert "WATCH_LOCAL_WHISPER_BIN" in msg
        assert "WATCH_LOCAL_WHISPER_MODEL" in msg
        assert str(whisper.CONFIG_FILE) in msg


class TestLocalWhisperTranscription:
    @pytest.fixture(autouse=True)
    def _clean_config(self, monkeypatch, tmp_path):
        monkeypatch.setattr(whisper, "CONFIG_FILE", tmp_path / "missing.env")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("WATCH_LOCAL_WHISPER_BIN", raising=False)
        monkeypatch.delenv("WATCH_LOCAL_WHISPER_MODEL", raising=False)

    def test_local_chunk_seconds_uses_env_or_default(self, monkeypatch):
        monkeypatch.delenv("WATCH_LOCAL_WHISPER_CHUNK_SECONDS", raising=False)
        assert whisper.local_chunk_seconds() == 600.0

        monkeypatch.setenv("WATCH_LOCAL_WHISPER_CHUNK_SECONDS", "3.5")
        assert whisper.local_chunk_seconds() == 3.5

        monkeypatch.setenv("WATCH_LOCAL_WHISPER_CHUNK_SECONDS", "0")
        assert whisper.local_chunk_seconds() == 600.0

    def test_plan_fixed_chunks_is_contiguous(self):
        plan = whisper.plan_fixed_chunks(total_seconds=7.0, chunk_seconds=3.0)

        assert plan == [(0.0, 3.0), (3.0, 3.0), (6.0, 1.0)]

    def test_parse_local_whisper_json_accepts_segments_shape(self, tmp_path: Path):
        payload = tmp_path / "out.json"
        payload.write_text(
            json.dumps({
                "transcription": [
                    {"offsets": {"from": 1000, "to": 2500}, "text": "hello"},
                    {"timestamps": {"from": "00:00:02,500", "to": "00:00:04,000"}, "text": "world"},
                ]
            }),
            encoding="utf-8",
        )

        assert whisper.parse_local_whisper_json(payload) == [
            {"start": 1.0, "end": 2.5, "text": "hello"},
            {"start": 2.5, "end": 4.0, "text": "world"},
        ]

    def test_local_transcription_extracts_splits_and_runs_chunks_in_order(
        self,
        monkeypatch,
        tmp_path: Path,
    ):
        video = tmp_path / "clip.mp4"
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-t", "3", "-i", "color=c=blue:s=160x120:r=10",
                "-f", "lavfi", "-t", "3", "-i", "sine=frequency=440:sample_rate=16000",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest",
                str(video),
            ],
            check=True,
        )
        fake = tmp_path / "fake_whisper.py"
        calls = tmp_path / "calls.jsonl"
        fake.write_text(
            """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
audio = args[args.index("-f") + 1]
out_prefix = args[args.index("-of") + 1]
calls = Path(__file__).with_name("calls.jsonl")
with calls.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"audio": Path(audio).name, "args": args}) + "\\n")
index = int(Path(audio).stem.split("_")[-1])
Path(out_prefix + ".json").write_text(json.dumps({
    "transcription": [
        {"offsets": {"from": 0, "to": 1000}, "text": f"chunk {index}"}
    ]
}), encoding="utf-8")
""",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        model = tmp_path / "model.bin"
        model.write_text("model", encoding="utf-8")
        monkeypatch.setenv("WATCH_LOCAL_WHISPER_BIN", str(fake))
        monkeypatch.setenv("WATCH_LOCAL_WHISPER_MODEL", str(model))
        monkeypatch.setenv("WATCH_LOCAL_WHISPER_CHUNK_SECONDS", "1")

        segments, backend = whisper.transcribe_video_local(video, tmp_path / "audio.mp3")

        assert backend == "local"
        assert [seg["text"] for seg in segments] == ["chunk 0", "chunk 1", "chunk 2"]
        assert [seg["start"] for seg in segments] == pytest.approx([0.0, 1.0, 2.0], abs=0.25)
        call_lines = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
        assert [call["audio"] for call in call_lines] == ["chunk_000.mp3", "chunk_001.mp3", "chunk_002.mp3"]
        for call in call_lines:
            assert "-m" in call["args"]
            assert str(model) in call["args"]


class TestTranscribeChunks:
    def test_shifts_and_concatenates_each_chunk(self):
        chunks = [(Path("a.mp3"), 0.0), (Path("b.mp3"), 100.0)]

        def fake_transcribe(path: Path) -> list[dict]:
            return [{"start": 0.0, "end": 2.0, "text": path.stem}]

        out = whisper.transcribe_chunks(chunks, fake_transcribe)

        assert out == [
            {"start": 0.0, "end": 2.0, "text": "a"},
            {"start": 100.0, "end": 102.0, "text": "b"},
        ]

    def test_keeps_successful_chunks_when_one_fails(self):
        chunks = [(Path("a.mp3"), 0.0), (Path("b.mp3"), 100.0)]

        def flaky(path: Path) -> list[dict]:
            if path.stem == "b":
                raise SystemExit("chunk b failed")
            return [{"start": 1.0, "end": 2.0, "text": "a"}]

        out = whisper.transcribe_chunks(chunks, flaky)

        assert out == [{"start": 1.0, "end": 2.0, "text": "a"}]

    def test_raises_when_every_chunk_fails(self):
        chunks = [(Path("a.mp3"), 0.0), (Path("b.mp3"), 100.0)]

        def always_fail(path: Path) -> list[dict]:
            raise SystemExit("boom")

        with pytest.raises(SystemExit):
            whisper.transcribe_chunks(chunks, always_fail)
