#!/usr/bin/env python3
"""Transcribe a video via Groq or OpenAI Whisper API.

Strategy: extract audio (mono 16kHz mp3, tiny payload), upload to whichever
API has a key. Returns segments in the same shape as transcribe.parse_vtt so
the rest of the pipeline (filter_range, format_transcript) doesn't care where
the transcript came from.

Pure stdlib — no `pip install groq` or `pip install openai` needed.
"""
from __future__ import annotations

import io
import hashlib
import json
import math
import mimetypes
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

from config import CONFIG_FILE, read_env_file


GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "whisper-large-v3"

OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_MODEL = "whisper-1"
LOCAL_BIN_ENV = "WATCH_LOCAL_WHISPER_BIN"
LOCAL_MODEL_ENV = "WATCH_LOCAL_WHISPER_MODEL"
LOCAL_CHUNK_SECONDS_ENV = "WATCH_LOCAL_WHISPER_CHUNK_SECONDS"
LOCAL_CACHE_DIR_ENV = "WATCH_LOCAL_WHISPER_CACHE_DIR"
DEFAULT_LOCAL_CHUNK_SECONDS = 600.0
MIN_LOCAL_CHUNK_SECONDS = 0.25

# Both Groq's free tier and OpenAI whisper-1 cap uploads at 25 MB. We target a
# margin under that so multipart framing overhead never pushes a chunk over.
MAX_UPLOAD_BYTES = 24 * 1024 * 1024


def _config_value(name: str) -> str | None:
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    for path in (CONFIG_FILE, Path.cwd() / ".env"):
        value = read_env_file(path).get(name)
        if value and value.strip():
            return value.strip()
    return None


def _endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/audio/transcriptions"


def endpoint_for_backend(backend: str) -> str:
    """Return the Whisper-compatible transcription endpoint for a backend."""
    if backend == "groq":
        return _endpoint(_config_value("WATCH_GROQ_BASE_URL") or GROQ_BASE_URL)
    if backend == "openai":
        return _endpoint(
            _config_value("WATCH_OPENAI_BASE_URL")
            or _config_value("OPENAI_BASE_URL")
            or OPENAI_BASE_URL
        )
    raise SystemExit(f"Unknown whisper backend: {backend}")


def model_for_backend(backend: str) -> str:
    """Return the transcription model for a backend."""
    if backend == "groq":
        return _config_value("WATCH_GROQ_MODEL") or GROQ_MODEL
    if backend == "openai":
        return _config_value("WATCH_OPENAI_MODEL") or OPENAI_MODEL
    raise SystemExit(f"Unknown whisper backend: {backend}")


def local_whisper_status() -> dict:
    """Return configuration status for the local whisper.cpp backend."""
    bin_value = _config_value(LOCAL_BIN_ENV)
    model_value = _config_value(LOCAL_MODEL_ENV)
    problems: list[str] = []

    resolved_bin = ""
    if not bin_value:
        problems.append(f"{LOCAL_BIN_ENV} is not set")
    else:
        candidate = Path(bin_value).expanduser()
        if candidate.parent != Path(".") or candidate.is_absolute():
            if candidate.exists() and candidate.is_file():
                resolved_bin = str(candidate.resolve())
            else:
                problems.append(f"{LOCAL_BIN_ENV} does not point to a file: {bin_value}")
        else:
            found = shutil.which(bin_value)
            if found:
                resolved_bin = found
            else:
                problems.append(f"{LOCAL_BIN_ENV} is not on PATH: {bin_value}")

    resolved_model = ""
    if not model_value:
        problems.append(f"{LOCAL_MODEL_ENV} is not set")
    else:
        model_path = Path(model_value).expanduser()
        if model_path.exists() and model_path.is_file():
            resolved_model = str(model_path.resolve())
        else:
            problems.append(f"{LOCAL_MODEL_ENV} does not point to a file: {model_value}")

    return {
        "configured": not problems,
        "binary": resolved_bin,
        "model": resolved_model,
        "problems": problems,
    }


def local_whisper_setup_message() -> str | None:
    """Return an actionable setup message, or None when local Whisper is configured."""
    status = local_whisper_status()
    if status["configured"]:
        return None
    problems = "; ".join(status["problems"])
    return (
        f"{problems}. Configure local whisper.cpp in {CONFIG_FILE} or the environment "
        f"with {LOCAL_BIN_ENV}=/path/to/whisper-cli and "
        f"{LOCAL_MODEL_ENV}=/path/to/model.bin."
    )


def local_chunk_seconds() -> float:
    """Return the local transcription chunk size in seconds."""
    raw = _config_value(LOCAL_CHUNK_SECONDS_ENV)
    if not raw:
        return DEFAULT_LOCAL_CHUNK_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_LOCAL_CHUNK_SECONDS
    if value <= 0:
        return DEFAULT_LOCAL_CHUNK_SECONDS
    return value


def local_cache_root() -> Path:
    """Return the root directory for reusable local transcription cache."""
    raw = _config_value(LOCAL_CACHE_DIR_ENV)
    if raw:
        return Path(raw).expanduser().resolve()
    return (CONFIG_FILE.parent / "cache" / "local-whisper").expanduser().resolve()


def local_cache_key(video_path: str | Path, status: dict, chunk_seconds: float) -> str:
    """Build a cache key from the input file and local transcription settings."""
    path = Path(video_path).expanduser().resolve()
    try:
        stat = path.stat()
        input_id = {
            "path": str(path),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    except OSError:
        input_id = {"path": str(path)}
    payload = {
        "input": input_id,
        "binary": str(status.get("binary") or ""),
        "model": str(status.get("model") or ""),
        "chunk_seconds": chunk_seconds,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def plan_chunks(
    total_seconds: float,
    total_bytes: int,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> list[tuple[float, float]]:
    """Split a duration into contiguous (offset, duration) chunks under max_bytes.

    Size scales linearly with duration (constant-bitrate mono mp3), so an even
    time split yields evenly-sized chunks. Returns a single full-length chunk
    when the audio already fits.
    """
    if total_bytes <= max_bytes or total_seconds <= 0:
        return [(0.0, total_seconds)]

    n = math.ceil(total_bytes / max_bytes)
    chunk = total_seconds / n
    plan: list[tuple[float, float]] = []
    for i in range(n):
        offset = i * chunk
        # The last chunk absorbs any rounding remainder so durations sum exactly.
        duration = (total_seconds - offset) if i == n - 1 else chunk
        plan.append((round(offset, 3), round(duration, 3)))
    return plan


def plan_fixed_chunks(total_seconds: float, chunk_seconds: float) -> list[tuple[float, float]]:
    """Split duration into contiguous fixed-size chunks."""
    if total_seconds <= 0 or chunk_seconds <= 0:
        return [(0.0, max(0.0, total_seconds))]
    plan: list[tuple[float, float]] = []
    offset = 0.0
    while offset < total_seconds:
        duration = min(chunk_seconds, total_seconds - offset)
        if duration < MIN_LOCAL_CHUNK_SECONDS and plan:
            prev_offset, prev_duration = plan[-1]
            plan[-1] = (prev_offset, round(prev_duration + duration, 3))
            break
        plan.append((round(offset, 3), round(duration, 3)))
        offset += duration
    return plan


def load_api_key(preferred: str | None = None) -> tuple[str, str] | tuple[None, None]:
    """Return (backend, api_key). Prefers Groq, falls back to OpenAI.

    If `preferred` is "groq" or "openai", only that backend's key is considered.
    """
    if preferred == "local":
        return None, None

    def _from_env(name: str) -> str | None:
        value = os.environ.get(name)
        return value.strip() if value else None

    def _from_dotenv(path: Path, name: str) -> str | None:
        if not path.exists():
            return None
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() != name:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                    value = value[1:-1]
                return value or None
        except OSError:
            return None
        return None

    dotenv_paths = [
        Path.home() / ".config" / "watch" / ".env",
        Path.cwd() / ".env",
    ]

    candidates = (("GROQ_API_KEY", "groq"), ("OPENAI_API_KEY", "openai"))
    if preferred is not None:
        candidates = tuple(c for c in candidates if c[1] == preferred)

    for key_name, backend in candidates:
        value = _from_env(key_name)
        if not value:
            for candidate in dotenv_paths:
                value = _from_dotenv(candidate, key_name)
                if value:
                    break
        if value:
            return backend, value

    return None, None


def extract_audio(video_path: str, out_path: Path) -> Path:
    """Extract mono 16kHz 64kbps mp3 — ~480 kB/min, fits any Whisper limit."""
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-i", str(Path(video_path).resolve()),
        "-vn",
        "-acodec", "libmp3lame",
        "-ar", "16000",
        "-ac", "1",
        "-b:a", "64k",
        str(out_path.resolve()),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"ffmpeg audio extraction failed: {result.stderr.strip()}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise SystemExit("ffmpeg produced no audio — video may have no audio track")
    return out_path


def audio_duration(audio_path: Path) -> float:
    """Return the duration of an audio file in seconds via ffprobe."""
    if shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe is not installed. Install with: brew install ffmpeg")

    result = subprocess.run(
        [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(audio_path.resolve()),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe failed: {result.stderr.strip()}")
    fmt = json.loads(result.stdout or "{}").get("format", {})
    return float(fmt.get("duration") or 0.0)


def split_audio(
    full_audio: Path,
    work_dir: Path,
    plan: list[tuple[float, float]],
) -> list[tuple[Path, float]]:
    """Slice full_audio into per-plan chunk files, returning (path, offset) pairs.

    Uses stream copy (`-c copy`) so there is no re-encode and no quality loss;
    mp3 frame boundaries are close enough for transcription's purposes.
    """
    if shutil.which("ffmpeg") is None:
        raise SystemExit("ffmpeg is not installed. Install with: brew install ffmpeg")

    work_dir.mkdir(parents=True, exist_ok=True)
    chunks: list[tuple[Path, float]] = []
    for index, (offset, duration) in enumerate(plan):
        out_path = work_dir / f"chunk_{index:03d}.mp3"
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-ss", f"{offset:.3f}",
            "-i", str(full_audio.resolve()),
            "-t", f"{duration:.3f}",
            "-c", "copy",
            str(out_path.resolve()),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            raise SystemExit(
                f"ffmpeg failed to split audio chunk {index + 1}: {result.stderr.strip()}"
            )
        chunks.append((out_path, offset))
    return chunks


def _build_multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    """Assemble a multipart/form-data body the Whisper APIs accept.

    Whisper's multipart upload is small and predictable — doing it by hand
    keeps us on pure stdlib instead of pulling requests/groq/openai SDKs.
    """
    boundary = f"----WatchBoundary{uuid.uuid4().hex}"
    eol = b"\r\n"
    buf = io.BytesIO()

    for name, value in fields.items():
        buf.write(f"--{boundary}".encode()); buf.write(eol)
        buf.write(f'Content-Disposition: form-data; name="{name}"'.encode()); buf.write(eol)
        buf.write(eol)
        buf.write(str(value).encode()); buf.write(eol)

    mimetype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    buf.write(f"--{boundary}".encode()); buf.write(eol)
    buf.write(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"'.encode()
    )
    buf.write(eol)
    buf.write(f"Content-Type: {mimetype}".encode()); buf.write(eol)
    buf.write(eol)
    buf.write(file_path.read_bytes())
    buf.write(eol)
    buf.write(f"--{boundary}--".encode()); buf.write(eol)

    return buf.getvalue(), boundary


MAX_ATTEMPTS = 4       # initial + 3 retries
MAX_429_RETRIES = 2
RETRY_BASE_DELAY = 2.0


def _post_whisper(endpoint: str, api_key: str, model: str, audio_path: Path) -> dict:
    fields = {
        "model": model,
        "response_format": "verbose_json",
        "temperature": "0",
    }
    body, boundary = _build_multipart(fields, audio_path)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        # Groq sits behind Cloudflare — the default `Python-urllib/3.x` UA
        # trips WAF rule 1010 (403) before auth even runs. Any non-default
        # UA clears it; we identify honestly.
        "User-Agent": "watch-skill/1.0 (+claude-code; python-urllib)",
    }

    context = ssl.create_default_context()
    rate_limit_hits = 0
    last_exc: Exception | None = None
    last_detail = ""

    for attempt in range(MAX_ATTEMPTS):
        request = Request(endpoint, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=300, context=context) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = _read_error_body(exc)
            last_exc, last_detail = exc, detail

            # 4xx other than 429 are client errors — no retry will fix them.
            if 400 <= exc.code < 500 and exc.code != 429:
                raise SystemExit(f"Whisper request failed: {exc}{detail}")

            if exc.code == 429:
                rate_limit_hits += 1
                if rate_limit_hits >= MAX_429_RETRIES:
                    raise SystemExit(f"Whisper request failed: {exc}{detail}")
                delay = _retry_after(exc) or RETRY_BASE_DELAY * (2 ** attempt) + 1
            else:
                delay = RETRY_BASE_DELAY * (2 ** attempt)

            if attempt < MAX_ATTEMPTS - 1:
                print(
                    f"[watch] whisper HTTP {exc.code} — retrying in {delay:.1f}s "
                    f"(attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError) as exc:
            last_exc, last_detail = exc, ""
            if attempt < MAX_ATTEMPTS - 1:
                delay = RETRY_BASE_DELAY * (attempt + 1)
                print(
                    f"[watch] whisper network error ({type(exc).__name__}: {exc}) — "
                    f"retrying in {delay:.1f}s (attempt {attempt + 2}/{MAX_ATTEMPTS})",
                    file=sys.stderr,
                )
                time.sleep(delay)
            continue

        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Whisper returned non-JSON response: {exc}: {payload[:200]}")

    raise SystemExit(
        f"Whisper request failed after {MAX_ATTEMPTS} attempts: {last_exc}{last_detail}"
    )


def _read_error_body(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read()
    except Exception:
        return ""
    if not body:
        return ""
    try:
        return f" — {body.decode('utf-8', errors='replace')[:400]}"
    except Exception:
        return ""


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    header = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else None
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def shift_segments(segments: list[dict], offset_seconds: float) -> list[dict]:
    """Return a copy of segments with start/end shifted by offset_seconds.

    Each chunk is transcribed in isolation, so Whisper returns 0-based timestamps
    per chunk; shifting by the chunk's offset stitches them into source time.
    """
    if offset_seconds == 0:
        return segments
    return [
        {
            "start": round(seg["start"] + offset_seconds, 2),
            "end": round(seg["end"] + offset_seconds, 2),
            "text": seg["text"],
        }
        for seg in segments
    ]


def _segments_from_response(data: dict) -> list[dict]:
    """Convert Whisper verbose_json into our {start, end, text} segment format."""
    out: list[dict] = []
    for seg in data.get("segments") or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "start": round(float(seg.get("start") or 0.0), 2),
            "end": round(float(seg.get("end") or 0.0), 2),
            "text": text,
        })

    if not out:
        full = (data.get("text") or "").strip()
        if full:
            out.append({"start": 0.0, "end": 0.0, "text": full})

    return out


def _timestamp_to_seconds(value) -> float:
    """Parse whisper.cpp JSON timestamps in ms numbers or HH:MM:SS.mmm strings."""
    if isinstance(value, (int, float)):
        number = float(value)
        return round(number / 1000.0 if number >= 1000 else number, 2)
    if not isinstance(value, str):
        return 0.0
    raw = value.strip().replace(",", ".")
    parts = raw.split(":")
    try:
        if len(parts) == 3:
            return round(int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]), 2)
        if len(parts) == 2:
            return round(int(parts[0]) * 60 + float(parts[1]), 2)
        return round(float(raw), 2)
    except ValueError:
        return 0.0


def _segment_times(raw: dict) -> tuple[float, float]:
    if isinstance(raw.get("offsets"), dict):
        offsets = raw["offsets"]
        return _timestamp_to_seconds(offsets.get("from")), _timestamp_to_seconds(offsets.get("to"))
    if isinstance(raw.get("timestamps"), dict):
        timestamps = raw["timestamps"]
        return _timestamp_to_seconds(timestamps.get("from")), _timestamp_to_seconds(timestamps.get("to"))
    return _timestamp_to_seconds(raw.get("start")), _timestamp_to_seconds(raw.get("end"))


def parse_local_whisper_json(path: Path) -> list[dict]:
    """Parse whisper.cpp JSON output into the shared segment shape."""
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_segments = data.get("transcription") or data.get("segments") or []
    out: list[dict] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            continue
        text = (raw.get("text") or "").strip()
        if not text:
            continue
        start, end = _segment_times(raw)
        out.append({"start": start, "end": end, "text": text})
    if not out:
        text = (data.get("text") or "").strip()
        if text:
            out.append({"start": 0.0, "end": 0.0, "text": text})
    return out


def _run_local_whisper(audio_path: Path, out_prefix: Path, status: dict) -> list[dict]:
    """Run one local whisper.cpp-compatible transcription command."""
    cmd = [
        str(status["binary"]),
        "-m", str(status["model"]),
        "-f", str(audio_path.resolve()),
        "-of", str(out_prefix.resolve()),
        "-oj",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SystemExit(f"local Whisper command failed: {detail}")
    json_path = out_prefix.with_suffix(".json")
    if not json_path.exists():
        raise SystemExit(f"local Whisper produced no JSON output: {json_path}")
    segments = parse_local_whisper_json(json_path)
    if not segments:
        raise SystemExit(f"local Whisper produced no transcript segments: {json_path}")
    return segments


def _new_local_manifest(
    audio_path: Path,
    chunks: list[tuple[Path, float]],
    plan: list[tuple[float, float]],
    chunk_seconds: float,
) -> dict:
    return {
        "audio": str(audio_path.resolve()),
        "chunk_seconds": chunk_seconds,
        "chunks": [
            {
                "index": index,
                "audio": str(path.resolve()),
                "offset": offset,
                "duration": plan[index][1],
                "status": "pending",
                "segments": [],
                "error": "",
            }
            for index, (path, offset) in enumerate(chunks)
        ],
    }


def _load_or_create_local_manifest(
    manifest_path: Path,
    audio_path: Path,
    chunks: list[tuple[Path, float]],
    plan: list[tuple[float, float]],
    chunk_seconds: float,
) -> dict:
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
        if (
            manifest.get("audio") == str(audio_path.resolve())
            and manifest.get("chunk_seconds") == chunk_seconds
            and len(manifest.get("chunks") or []) == len(chunks)
        ):
            return manifest
    manifest = _new_local_manifest(audio_path, chunks, plan, chunk_seconds)
    _write_local_manifest(manifest_path, manifest)
    return manifest


def _write_local_manifest(manifest_path: Path, manifest: dict) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _transcribe_local_chunks_with_manifest(
    chunks: list[tuple[Path, float]],
    plan: list[tuple[float, float]],
    audio_path: Path,
    audio_out: Path,
    chunk_seconds: float,
    status: dict,
) -> list[dict]:
    manifest_path = audio_out.parent / "local-manifest.json"
    manifest = _load_or_create_local_manifest(
        manifest_path,
        audio_path,
        chunks,
        plan,
        chunk_seconds,
    )
    segments: list[dict] = []
    failures = 0
    for index, (path, offset) in enumerate(chunks):
        entry = manifest["chunks"][index]
        if entry.get("status") == "complete" and entry.get("segments"):
            segments.extend(entry["segments"])
            print(f"[watch] chunk {index + 1}/{len(chunks)} already complete — skipping", file=sys.stderr)
            continue

        out_prefix = audio_out.parent / "local-transcripts" / path.stem
        out_prefix.parent.mkdir(parents=True, exist_ok=True)
        try:
            chunk_segments = _run_local_whisper(path, out_prefix, status)
        except SystemExit as exc:
            failures += 1
            entry["status"] = "failed"
            entry["error"] = str(exc)
            entry["segments"] = []
            _write_local_manifest(manifest_path, manifest)
            print(
                f"[watch] chunk {index + 1}/{len(chunks)} failed — skipping ({exc})",
                file=sys.stderr,
            )
            continue

        shifted = shift_segments(chunk_segments, offset)
        entry["status"] = "complete"
        entry["error"] = ""
        entry["segments"] = shifted
        _write_local_manifest(manifest_path, manifest)
        segments.extend(shifted)
        print(
            f"[watch] chunk {index + 1}/{len(chunks)} → {len(chunk_segments)} segments",
            file=sys.stderr,
        )

    if failures == len(chunks):
        raise SystemExit("local Whisper failed on every audio chunk")
    return segments


def transcribe_chunks(
    chunks: list[tuple[Path, float]],
    transcribe_one,
) -> list[dict]:
    """Transcribe each chunk, shift its segments by the chunk offset, concatenate.

    A chunk that fails after its own retries is logged and skipped so one bad
    slice doesn't discard the whole transcript. Raises only if every chunk fails.
    """
    segments: list[dict] = []
    failures = 0
    for index, (path, offset) in enumerate(chunks):
        try:
            chunk_segments = transcribe_one(path)
        except SystemExit as exc:
            failures += 1
            print(
                f"[watch] chunk {index + 1}/{len(chunks)} failed — skipping ({exc})",
                file=sys.stderr,
            )
            continue
        segments.extend(shift_segments(chunk_segments, offset))
        print(
            f"[watch] chunk {index + 1}/{len(chunks)} → {len(chunk_segments)} segments",
            file=sys.stderr,
        )

    if failures == len(chunks):
        raise SystemExit("Whisper failed on every audio chunk")
    return segments


def _transcribe_file(backend: str, api_key: str, audio_path: Path) -> list[dict]:
    """Upload one audio file and return its 0-based segments."""
    endpoint = endpoint_for_backend(backend)
    model = model_for_backend(backend)
    response = _post_whisper(endpoint, api_key, model, audio_path)
    return _segments_from_response(response)


def transcribe_video(
    video_path: str,
    audio_out: Path,
    backend: str | None = None,
    api_key: str | None = None,
) -> tuple[list[dict], str]:
    """Run the full flow: extract audio → upload → parse segments.

    Returns (segments, backend_used). Raises SystemExit on any failure.
    """
    if backend is None or api_key is None:
        detected_backend, detected_key = load_api_key()
        backend = backend or detected_backend
        api_key = api_key or detected_key

    if not backend or not api_key:
        setup_py = Path(__file__).resolve().parent / "setup.py"
        raise SystemExit(
            "No Whisper API key available. Set GROQ_API_KEY (preferred) or OPENAI_API_KEY "
            "in the environment or in ~/.config/watch/.env. "
            f"Run `python3 {setup_py}` to configure."
        )

    print(f"[watch] extracting audio for Whisper ({backend})…", file=sys.stderr)
    audio_path = extract_audio(video_path, audio_out)
    audio_bytes = audio_path.stat().st_size

    def transcribe_one(path: Path) -> list[dict]:
        return _transcribe_file(backend, api_key, path)

    if audio_bytes <= MAX_UPLOAD_BYTES:
        print(
            f"[watch] audio: {audio_bytes / 1024:.0f} kB — uploading to {backend} Whisper…",
            file=sys.stderr,
        )
        segments = transcribe_one(audio_path)
    else:
        duration = audio_duration(audio_path)
        plan = plan_chunks(duration, audio_bytes, MAX_UPLOAD_BYTES)
        print(
            f"[watch] audio: {audio_bytes / (1024 * 1024):.0f} MB exceeds "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB — splitting into {len(plan)} chunks…",
            file=sys.stderr,
        )
        chunks = split_audio(audio_path, audio_out.parent / "chunks", plan)
        segments = transcribe_chunks(chunks, transcribe_one)

    if not segments:
        raise SystemExit("Whisper returned no transcript segments")

    print(f"[watch] transcribed {len(segments)} segments via {backend}", file=sys.stderr)
    return segments, backend


def transcribe_video_local(
    video_path: str | Path,
    audio_out: Path,
) -> tuple[list[dict], str]:
    """Run local whisper.cpp transcription one chunk at a time."""
    status = local_whisper_status()
    if not status["configured"]:
        raise SystemExit(local_whisper_setup_message() or "local Whisper is not configured")

    print("[watch] extracting audio for local Whisper…", file=sys.stderr)
    chunk_seconds = local_chunk_seconds()
    cache_key = local_cache_key(video_path, status, chunk_seconds)
    cache_dir = local_cache_root() / cache_key
    cache_dir.mkdir(parents=True, exist_ok=True)
    audio_path = cache_dir / "audio.mp3"
    if audio_path.exists() and audio_path.stat().st_size > 0:
        print(f"[watch] using cached local audio: {cache_dir}", file=sys.stderr)
    else:
        audio_path = extract_audio(str(video_path), audio_path)
    duration = audio_duration(audio_path)
    plan = plan_fixed_chunks(duration, chunk_seconds)
    print(
        f"[watch] local audio duration {duration:.1f}s — splitting into {len(plan)} "
        f"chunk(s) of up to {chunk_seconds:.1f}s",
        file=sys.stderr,
    )
    chunks_dir = cache_dir / "chunks"
    expected_chunks = [chunks_dir / f"chunk_{index:03d}.mp3" for index in range(len(plan))]
    if expected_chunks and all(path.exists() and path.stat().st_size > 0 for path in expected_chunks):
        chunks = [(path, plan[index][0]) for index, path in enumerate(expected_chunks)]
        print(f"[watch] using cached local chunks: {cache_dir}", file=sys.stderr)
    else:
        chunks = split_audio(audio_path, chunks_dir, plan)

    segments = _transcribe_local_chunks_with_manifest(
        chunks,
        plan,
        audio_path,
        cache_dir / "audio.mp3",
        chunk_seconds,
        status,
    )
    if not segments:
        raise SystemExit("local Whisper returned no transcript segments")
    print(f"[watch] transcribed {len(segments)} segments via local Whisper", file=sys.stderr)
    return segments, "local"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: whisper.py <video-path> [<audio-out.mp3>] [--backend groq|openai]", file=sys.stderr)
        raise SystemExit(2)

    video = sys.argv[1]
    audio_out = Path(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else Path("audio.mp3")
    backend_override = None
    if "--backend" in sys.argv:
        backend_override = sys.argv[sys.argv.index("--backend") + 1]

    segments, backend = transcribe_video(video, audio_out, backend=backend_override)
    print(json.dumps({"backend": backend, "segments": segments}, indent=2))
