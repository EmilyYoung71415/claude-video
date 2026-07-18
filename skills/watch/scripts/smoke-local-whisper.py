#!/usr/bin/env python3
"""Smoke-check local Whisper integration with a fake or real backend."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
WATCH = SCRIPT_DIR / "watch.py"
sys.path.insert(0, str(SCRIPT_DIR))
from config import read_env_file  # noqa: E402


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def _make_clip(path: Path) -> None:
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is required for the smoke check")
    result = _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-t", "1", "-i", "color=c=blue:s=160x120:r=10",
        "-f", "lavfi", "-t", "1", "-i", "sine=frequency=440:sample_rate=16000",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(path),
    ])
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip())


def _make_fake_whisper(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
out_prefix = args[args.index("-of") + 1]
Path(out_prefix + ".json").write_text(json.dumps({
    "transcription": [
        {"offsets": {"from": 0, "to": 1000}, "text": "local smoke transcript"}
    ]
}), encoding="utf-8")
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check /watch --whisper local")
    parser.add_argument(
        "--real",
        action="store_true",
        help="Use configured WATCH_LOCAL_WHISPER_BIN and WATCH_LOCAL_WHISPER_MODEL instead of a fake command.",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="watch-local-smoke-") as tmp:
        work = Path(tmp)
        env = dict(os.environ)
        env["WATCH_LOCAL_WHISPER_CACHE_DIR"] = str(work / "cache")
        env["WATCH_LOCAL_WHISPER_CHUNK_SECONDS"] = "10"

        expected_text = "local smoke transcript"
        if not args.real:
            clip = work / "clip.mp4"
            _make_clip(clip)
            fake = work / "fake_whisper.py"
            model = work / "model.bin"
            _make_fake_whisper(fake)
            model.write_text("model", encoding="utf-8")
            env["WATCH_LOCAL_WHISPER_BIN"] = str(fake)
            env["WATCH_LOCAL_WHISPER_MODEL"] = str(model)
        else:
            file_values = read_env_file()
            for key in (
                "WATCH_LOCAL_WHISPER_BIN",
                "WATCH_LOCAL_WHISPER_MODEL",
                "WATCH_LOCAL_WHISPER_ARGS",
            ):
                if not env.get(key) and file_values.get(key):
                    env[key] = file_values[key]
            env.setdefault("WATCH_LOCAL_WHISPER_ARGS", "-ng")
            sample = Path(env.get("WATCH_LOCAL_WHISPER_BIN", "")).resolve().parents[2] / "samples" / "jfk.wav"
            if not sample.exists():
                raise SystemExit(f"real smoke sample not found: {sample}")
            clip = sample
        if args.real and (not env.get("WATCH_LOCAL_WHISPER_BIN") or not env.get("WATCH_LOCAL_WHISPER_MODEL")):
            raise SystemExit(
                "--real requires WATCH_LOCAL_WHISPER_BIN and WATCH_LOCAL_WHISPER_MODEL"
            )
        if args.real:
            expected_text = ""

        result = _run([
            sys.executable,
            str(WATCH),
            str(clip),
            "--detail",
            "transcript",
            "--whisper",
            "local",
        ], env=env)
        if result.returncode != 0:
            sys.stderr.write(result.stderr)
            return result.returncode
        if "via whisper (local)" not in result.stdout:
            sys.stderr.write(result.stdout)
            raise SystemExit("local transcript source was not reported")
        if expected_text and expected_text not in result.stdout:
            sys.stderr.write(result.stdout)
            raise SystemExit("fake local transcript text was not reported")
        print("local whisper smoke ok")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
