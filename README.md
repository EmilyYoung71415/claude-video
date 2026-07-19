# /watch

**让 Claude 能够观看并理解视频。**

> [!IMPORTANT]
> 本仓库是基于 [`bradautomates/claude-video`](https://github.com/bradautomates/claude-video) 开发的分支版本（Fork）。本项目包含与上游不同的改动；为确保使用本项目提供的功能，请按下方命令从 `EmilyYoung71415/claude-video` 安装，不要使用上游仓库地址。

Claude Code（推荐，可通过市场更新）：

```
/plugin marketplace add EmilyYoung71415/claude-video
/plugin install watch@claude-video
```

Codex、Cursor、Copilot、Gemini CLI 或其他支持 [Agent Skills](https://agentskills.io) 的宿主：
```bash
npx skills add EmilyYoung71415/claude-video -g
```
`-g` 表示为当前用户全局安装，可供所有项目使用；去掉 `-g` 则只安装到当前项目。

更多安装方式（包括 claude.ai 网页版和从源码手动安装）请参阅[安装](#install)。

Zero config to start — `yt-dlp` and `ffmpeg` install on first run via `brew` on macOS (Linux/Windows print exact commands). Captions cover most public videos for free. Whisper API key is only needed when a video has no captions.

---

Claude can read a webpage, run a script, browse a repo. What it can't do, out of the box, is *watch a video*. You paste a YouTube link and it has to either guess from the title or pull a transcript that's missing 90% of what's on screen.

With Claude Video `/watch` you can paste a URL or a local path, ask a question, and Claude fetches captions first, downloads only what it needs, extracts frames (scene-aware, or fast keyframes at `efficient` detail), pulls a timestamped transcript (free captions when available, Whisper API as fallback), and `Read`s every frame as an image. By the time it answers, it has *seen* the video and *heard* the audio.

```
/watch https://youtu.be/dQw4w9WgXcQ what happens at the 30 second mark?
```

You can also ask naturally in hosts that route Agent Skills automatically:

```
Summarize what this YouTube video is about: https://www.youtube.com/watch?v=xxxx
```

When a message contains a video URL or local video path and asks to summarize, explain, analyze, extract key points, find a timestamp, diagnose a screen recording, or answer questions about the video, the host should route it to `/watch`. The explicit `/watch ...` form is still the most portable invocation across hosts.

## What people actually use it for

**Analyze someone else's content.** `/watch https://youtu.be/<viral-video> what hook did they open with?` Claude looks at the first frames, reads the opening transcript, breaks down the structure. Same for ad creative, competitor launches, podcast intros, anything where the *how* matters as much as the *what*.

**Diagnose a bug from a video.** Someone sends you a screen recording of something broken. `/watch bug-repro.mov what's going wrong?` Claude watches the recording, finds the frame where the issue appears, describes what's on screen, often catches the cause without you ever opening the file.

**Summarize a video.** `/watch https://youtu.be/<long-thing> summarize this` does the obvious thing — pulls the structure, the key moments, what was actually said and shown. Faster than watching at 2x.

**Cut the hype out of an update video.** `/watch https://youtu.be/<launch-video> what's actually new — skip the hype` Strip a "game-changer" feature drop down to the few things that matter, so you get the substance without ten minutes of intro and overselling.

**Turn a playlist into notes.** `/watch https://youtu.be/<video> summarize this to a note` Run it across a series and file a per-video summary, so a channel or course becomes a searchable set of notes instead of hours you have to sit through.

## How it works

1. **You paste a video and a question.** URL (anything yt-dlp supports — YouTube, Loom, TikTok, X, Instagram, plus a few hundred more) or a local path (`.mp4`, `.mov`, `.mkv`, `.webm`).
2. **`yt-dlp` checks captions first.** At `transcript` detail, captioned URLs return without downloading video. Otherwise, or when Whisper needs audio, it downloads only what the run needs.
3. **`ffmpeg` extracts frames at the chosen detail.** `efficient` decodes keyframes only (near-instant); `balanced`/`token-burner` prefer scene-change frames and fall back to the duration-aware uniform sampler when they under-produce. JPEGs are 512px wide by default and clamped to 1998px tall for Claude Read compatibility.
4. **The transcript comes from one of two places.** First try: `yt-dlp` pulls native captions (manual or auto-generated) from the source. Free, instant, accurate-ish. Fallback: extract a mono 16 kHz 64 kbps mp3 audio clip (~480 kB/min) and ship it to Whisper — Groq's `whisper-large-v3` (preferred — cheaper and faster) or OpenAI's `whisper-1`.
5. **Frames + transcript are handed to Claude.** The script prints frame paths with `t=MM:SS` markers and the transcript with timestamps. Claude `Read`s each frame in parallel — JPEGs render directly as images in its context.
6. **Claude answers grounded in what's actually on screen and in the audio.** Not "based on the description" or "according to the title." It saw the frames. It heard the transcript. It answers the way someone who watched the video would.
7. **Cleanup.** The script prints a working directory at the end. If you're not asking follow-ups, Claude removes it.

## Frame budget — why it matters

Token cost is dominated by frames. Every frame is an image; image tokens add up fast. The script's auto-fps logic exists so you don't blow your context budget on a sparse scan of a 30-minute video that would have been better answered by a focused 30-second window.

| Duration | Default frame budget | What you get |
|----------|---------------------|--------------|
| ≤30 s | ~30 frames | Dense — basically every key moment |
| 30 s - 1 min | ~40 frames | Still dense |
| 1 - 3 min | ~60 frames | Comfortable |
| 3 - 10 min | ~80 frames | Sparse but workable |
| > 10 min | 100 frames (capped modes) | "Sparse scan" warning — re-run focused, or `--detail token-burner` for full uncapped coverage |

When the user names a moment ("around 2:30", "the last 30 seconds", "from 0:45 to 1:00"), pass `--start` / `--end`. Focused mode gets denser per-second budgets, capped at 2 fps. Far more useful than a sparse pass over the whole thing.

## Frame deduplication

Frame selection — keyframes (`efficient`), scene-change detection (`balanced`/`token-burner`), or the uniform sampler it falls back to — can still surface near-identical frames: a screen recording that holds one slide for 90 seconds produces a dozen, each billed as a separate image. A dedup pass drops them before frames reach Claude. It runs by default on every frame mode (`--no-dedup` turns it off):

1. One `ffmpeg` call scales each extracted JPEG to a 16×16 grayscale thumbnail. Everything after is pure-stdlib Python — no image libraries.
2. For each frame, compute the **mean absolute difference** against the *last frame that was kept* (average per-pixel brightness change, 0–255 scale).
3. If that difference is at or below the threshold (`2.0`), the frame is a near-duplicate and is dropped. Otherwise it's kept and becomes the new reference.
4. The frame-budget cap applies *after* dedup, so the budget is spent on distinct frames.

Comparing against the last *kept* frame (not the previous one) catches slow fades that never trip a frame-to-frame threshold. The threshold is deliberately low and measures absolute brightness rather than structure, so a one-line code diff, a terminal scrolling a row, or two differently-colored flat slides all survive.

The **Frames** line reports what was collapsed, e.g. `6 selected from 14 candidates (… 8 near-duplicates dropped …)`. On always-moving footage nothing is dropped and you pay what you would have anyway.

## Detail modes — measured

The `--detail` dial trades speed and token cost for visual fidelity. Numbers below are from a real run against a **49:08** YouTube video (1280×720, English auto-captions) — a long, mostly-static screen recording, the case that stresses the caps hardest. Extraction times are local CPU against a pre-downloaded copy; the one-time download was **~37 s** / 76 MB, shared by the three frame modes.

| Mode | Engine | Frames | Cap | Extraction time | Temporal coverage | Est. image tokens |
|------|--------|--------|-----|-----------------|-------------------|-------------------|
| `transcript` | none (captions) | 0 | — | **~4.5 s** (one yt-dlp call, no download) | full (text) | 0 (≈26.6k text tokens) |
| `efficient` | keyframe (`-skip_frame nokey`) | 50 | 50 | **~0.5 s** | 0:00 → 49:04 (full) | **~9.8k** |
| `balanced` | scene-change | 100 | 100 | **~20.9 s** | 0:00 → 48:38 (full) | **~19.7k** |
| `token-burner` | scene-change | 116 | uncapped | **~21.0 s** | 0:00 → 48:38 (full) | **~22.8k** |

- **Image tokens** use Anthropic's `(width × height) / 750` — at the default 512px width these 720p frames are 512×288, **≈197 tokens/frame**; `--resolution 1024` roughly 4×s that. The transcript is surfaced in every captioned mode and on long videos is often the larger cost.
- **One sampling rule across frame modes.** Each detects all candidates across the full range, then even-samples (first + last always kept) down to its cap. The modes differ only in candidate *source* (keyframes vs. scene cuts) and cap, never in how coverage is spread — so the last frame always lands at the end, not partway through.
- **`efficient` is the speed tier** (~0.5 s) — it only reconstructs keyframes, so it's ~40× faster than the scene modes, which decode every frame to find cuts. It can also return *more* frames than `balanced` on low-motion footage (keyframes outnumber scene cuts); "efficient" means fast extraction, not fewer frames.
- **`token-burner` only diverges from `balanced` past the cap.** This clip had 116 cuts, so `balanced` sampled 100 and `token-burner` kept all 116. On high-motion video with hundreds of cuts, `token-burner` keeps everything (and trips the >250-frame token warning) while `balanced` thins to 100.

End-to-end from a cold URL, `transcript` is the cheapest mode by far; the frame modes add the shared ~37 s download on top of the extraction times above.

<a id="install"></a>

## 安装（Install）

以下所有方式都以本 fork 的源码为安装来源。

| 使用环境 | 安装方式 |
|---------|---------|
| **Claude Code** | 先执行 `/plugin marketplace add EmilyYoung71415/claude-video`，再执行 `/plugin install watch@claude-video` |
| **Codex、Cursor、Copilot、Gemini CLI 等** | `npx skills add EmilyYoung71415/claude-video -g` |
| **claude.ai 网页版** | 克隆本仓库并构建 `dist/watch.skill`，然后在 Settings → Capabilities → Skills → `+` 上传 |
| **手动安装 / 开发** | 克隆本仓库，再将 `skills/watch` 链接到宿主的 skills 目录（见下文） |

### Claude Code

```
/plugin marketplace add EmilyYoung71415/claude-video
/plugin install watch@claude-video
```

后续可执行 `/plugin update watch@claude-video` 更新。市场来源保持为本 fork，因此更新也会继续使用本项目源码。

### Codex、Cursor、Copilot、Gemini CLI 及其他宿主

[Agent Skills](https://agentskills.io) 命令行工具会为检测到的智能体宿主安装本技能：

```bash
npx skills add EmilyYoung71415/claude-video -g
```

`-g` 会安装到当前用户的全局目录（如 `~/.codex/skills`、`~/.cursor/skills`）；去掉它则安装到当前项目。常用参数：

- `-a, --agent <names…>`：指定宿主，例如 `-a codex -a cursor`
- `-l, --list`：只列出本仓库中的技能，不执行安装
- `--copy`：复制文件而不是创建符号链接，适用于不支持符号链接的文件系统

命令行工具会从 `skills/watch/SKILL.md` 发现技能，并将整个 `skills/watch` 目录作为一个自包含单元安装，其中包括 `SKILL.md` 和 `scripts/` 运行脚本。`SKILL.md` 会相对于自身安装位置查找脚本，因此各宿主使用的是同一份本项目源码。

后续可执行 `npx skills update watch -g` 更新。

### claude.ai (web)

claude.ai 使用的安装包也应从本 fork 源码构建：

```bash
git clone https://github.com/EmilyYoung71415/claude-video.git
cd claude-video
bash skills/watch/scripts/build-skill.sh
```

构建完成后，在 claude.ai 中进入 Settings → Capabilities → Skills，点击 `+` 并上传 `dist/watch.skill`。

请先在 Capabilities 中启用“Code execution and file creation”。本技能需要运行 `ffmpeg` 和 `yt-dlp`，未启用时无法工作。

### 手动安装（开发者）

克隆本 fork，并将自包含的技能目录链接到宿主的 skills 目录。使用符号链接后，本地源码修改会直接反映到安装结果中：

```bash
git clone https://github.com/EmilyYoung71415/claude-video.git
ln -s "$(pwd)/claude-video/skills/watch" ~/.claude/skills/watch   # or ~/.codex/skills/watch
```

如需用于 claude.ai，请执行 `bash skills/watch/scripts/build-skill.sh` 从源码生成 `dist/watch.skill`。

## First run

On the first `/watch` call, the skill runs `scripts/setup.py --check`. If `ffmpeg` / `yt-dlp` aren't on your PATH, or no Whisper API key is set, it walks you through fixing it:

- **macOS** — auto-runs `brew install ffmpeg yt-dlp`.
- **Linux** — prints the exact `apt` / `dnf` / `pipx` commands.
- **Windows** — prints the `winget` / `pip` commands.
- **API key** — scaffolds `~/.config/watch/.env` (mode `0600`) with commented placeholders for `GROQ_API_KEY` (preferred) and `OPENAI_API_KEY`.

After setup, preflight is silent and `/watch` just works. The check is a sub-100ms lookup, so it doesn't slow you down on subsequent runs.

## Bring your own keys

Captions cover the majority of public videos for free. The Whisper fallback only kicks in when a video genuinely has no caption track — typically local files, TikToks, some Vimeos, and the occasional caption-less YouTube upload.

| Capability | What you need | Cost |
|------------|---------------|------|
| Download + native captions | `yt-dlp` + `ffmpeg` | Free |
| Whisper fallback (preferred) | [Groq API key](https://console.groq.com/keys) — `whisper-large-v3` | Cheap, fast |
| Whisper fallback (alt) | [OpenAI API key](https://platform.openai.com/api-keys) — `whisper-1` | Standard pricing |
| External transcript | `.srt` or `.vtt` file from MacWhisper Pro or another local tool | Free, no audio upload |
| Disable Whisper entirely | `--no-whisper` | Free, frames-only when no captions |

## Usage

```
/watch https://youtu.be/dQw4w9WgXcQ what happens at the 30 second mark?
/watch https://www.tiktok.com/@user/video/123 summarize this
/watch ~/Movies/screen-recording.mp4 when does the UI break?
/watch https://vimeo.com/123 what tools does she mention?
/watch ./video.mp4 --transcript ./video.srt summarize this
/watch https://youtu.be/abc --transcript ./manual.vtt --detail balanced
```

Common transcript-first flows:

```bash
# Use native captions when present; if captions are missing, use local whisper.cpp.
/watch https://www.youtube.com/watch?v=xxxx --detail transcript --whisper local summarize this

# If the URL has no captions, allow the audio download after the user approves it.
/watch https://www.youtube.com/watch?v=xxxx --detail transcript --whisper local --allow-download summarize this

# Use a transcript you already have, without downloading or uploading audio.
/watch https://www.youtube.com/watch?v=xxxx --transcript ./video.srt summarize this
```

Focused on a specific section — denser frame budget, lower token cost:
```
/watch https://youtu.be/abc --start 2:15 --end 2:45
/watch video.mp4 --start 50 --end 60
/watch "$URL" --start 1:12:00            # from 1h12m to end
```

Other knobs (passed to `scripts/watch.py`):

- `--detail transcript|efficient|balanced|token-burner` — fidelity/speed dial. `transcript` skips frames (transcript only); `efficient` uses fast keyframes (cap 50); `balanced` uses scene-aware frames (cap 100); `token-burner` is scene-aware and uncapped.
- `--allow-download` — allow downloading URL audio when captions are missing. For YouTube URLs with no captions, `/watch` always asks for user permission before downloading; re-run with `--allow-download --out-dir download` after approval.
- `--timestamps T1,T2,…` — grab a frame at each absolute timestamp (`SS`/`MM:SS`/`HH:MM:SS`). Claude reads the transcript first, then targets the moments the presenter flags ("look here", "as you can see"). Added on top of the detail frames (reserved against the cap); out-of-window cues are dropped in focus mode; with `--detail transcript` these become the only frames.
- `--max-frames N` — lower the frame cap for a tighter token budget.
- `--resolution W` — bump frame width to 1024 px when Claude needs to read on-screen text (slides, terminals, code).
- `--fps F` — override the auto-fps calculation (still capped at 2 fps).
- `--transcript PATH` — use an external `.srt` or `.vtt` transcript before platform captions or Whisper. External transcripts are useful with MacWhisper Pro or other local transcription tools, and they avoid uploading audio to Groq/OpenAI.
- `--whisper local|groq|openai` — force a specific Whisper backend.
- `--no-whisper` — disable transcription entirely; frames only.
- `--no-dedup` — keep near-duplicate frames. By default a frame-delta pass drops frames that are visually near-identical to the one before them (held slides, static screen recordings, paused video), so the frame budget is spent on distinct content; this flag turns that off.
- `--out-dir DIR` — keep working files somewhere specific (default: auto-generated tmp dir).

## Exposed capabilities

`/watch` exposes these capabilities to an agent:

- **Video Q&A from a URL or local file.** Supports YouTube and anything else `yt-dlp` can read, plus local `.mp4`, `.mov`, `.mkv`, `.webm`, and audio/video-like files.
- **Transcript-first summaries.** `--detail transcript` skips frames when text is enough, which is the cheapest path for long YouTube summaries.
- **Native captions first.** Captions are used before any audio download or Whisper fallback.
- **External subtitle input.** `--transcript ./file.srt` or `--transcript ./file.vtt` lets you bring captions from MacWhisper Pro, YouTube Studio, another transcription tool, or a manually edited file.
- **Whisper API fallback.** Groq and OpenAI compatible endpoints are supported, including custom base URLs for gateways and proxies.
- **Local whisper.cpp fallback.** `--whisper local` runs a local `whisper-cli` binary with a local model, splits long audio into chunks, resumes completed chunks, and writes reusable transcript artifacts.
- **Permissioned URL downloads.** If a YouTube URL has no captions and audio/video must be downloaded, the agent should ask the user first, then re-run with `--allow-download`.

## Routing rules for agents

Route a user request to `/watch` when the request includes a video URL or local media path and asks for any video-grounded work: summarize, explain, list key points, answer questions, inspect visuals, find moments, compare claims, diagnose a screen recording, or produce notes.

Prefer these defaults:

- Use `--detail transcript` for "summarize this YouTube video" unless the user asks about visuals, slides, UI, charts, gestures, or a specific on-screen moment.
- Use native captions first. Do not download URL audio/video when captions are enough.
- If captions are missing and `--whisper local` or an API key is configured, ask for permission before downloading URL audio, then re-run with `--allow-download`.
- If the user provides `.srt` or `.vtt`, pass it with `--transcript` and do not use Whisper unless the transcript is unusable.
- Use `--whisper local` when the user has configured local whisper.cpp or explicitly asks to avoid cloud transcription.

Examples:

```text
User: Summarize https://www.youtube.com/watch?v=xxxx
Route: /watch https://www.youtube.com/watch?v=xxxx --detail transcript summarize this

User: Summarize this caption-less YouTube video with my local Whisper.
Route after permission: /watch <url> --detail transcript --whisper local --allow-download summarize this

User: Use this subtitle file and explain the talk.
Route: /watch <url-or-file> --transcript ./talk.srt explain the talk
```

## Limits

- **Long-video accuracy depends on the detail mode.** On the capped modes (`efficient`, default `balanced`) coverage thins out past ~10 minutes — the frame cap spreads across the whole clip, so the script prints a "sparse scan" warning and you're better off re-running focused with `--start`/`--end`. `token-burner` lifts the cap and keeps *every* scene-change frame across the full video, so it stays complete on longer clips at the cost of more image tokens. The 10-minute mark is guidance for the capped modes, not a hard ceiling.
- **Detail is one dial.** Defaults are balanced: scene-aware frames, 2 fps max, 100-frame cap. Use `--detail efficient` for a fast 50-frame keyframe pass, or `--detail token-burner` for uncapped scene candidates. Set `WATCH_DETAIL` in `~/.config/watch/.env` to change the default.

## Transcription

The script gets a timestamped transcript in one of four ways:

1. **External transcript (free, highest priority).** Pass `--transcript ./video.srt` or `--transcript ./video.vtt` to use a local subtitle file. This takes precedence over platform captions and Whisper, supports `--start` / `--end` filtering, and does not upload audio.
2. **Native captions (free).** yt-dlp pulls manual or auto-generated subtitles from the source platform if available.
3. **Whisper API fallback.** If no external transcript or captions came back (or the source is a local file), the script extracts audio (`ffmpeg -vn -ac 1 -ar 16000 -b:a 64k`, ~0.5 MB/min) and uploads it to whichever Whisper API has a key configured.
4. **Local Whisper fallback.** Pass `--whisper local` to use a local whisper.cpp-compatible command instead of uploading audio. The script extracts mono 16 kHz audio, splits it into conservative chunks, runs one local command at a time, resumes completed chunks from a manifest, and writes `transcript.json`, `transcript.srt`, and `transcript.md` artifacts in the local cache.

API keys and local Whisper settings live in `~/.config/watch/.env`. The script prefers Groq when both API keys are set; override with `--whisper openai` to force OpenAI or `--whisper local` to force local transcription. Use `--no-whisper` to skip the fallback entirely.

Local Whisper configuration:

```text
WATCH_LOCAL_WHISPER_BIN=/path/to/whisper-cli
WATCH_LOCAL_WHISPER_MODEL=/path/to/ggml-model.bin
WATCH_LOCAL_WHISPER_CHUNK_SECONDS=600
WATCH_LOCAL_WHISPER_CACHE_DIR=~/.config/watch/cache/local-whisper
WATCH_LOCAL_WHISPER_ARGS=-ng
```

The local command is invoked with `-m <model> -f <chunk.wav> -of <output-prefix> -oj`, matching whisper.cpp's JSON output mode. `WATCH_LOCAL_WHISPER_CHUNK_SECONDS` defaults to 600 seconds. `WATCH_LOCAL_WHISPER_ARGS` is appended to every local command; `-ng` disables GPU/Metal and is useful on Macs where whisper.cpp's Metal backend cannot allocate memory. The cache key includes the input file identity, local executable, model path, chunk size, and local args, so changing those settings avoids stale reuse.

Smoke verification:

```bash
python3 skills/watch/scripts/smoke-local-whisper.py          # fake local command, fast
python3 skills/watch/scripts/smoke-local-whisper.py --real   # requires WATCH_LOCAL_WHISPER_BIN and WATCH_LOCAL_WHISPER_MODEL
```

Whisper-compatible gateways can be configured in the environment, `~/.config/watch/.env`, or the current directory's `.env`:

```text
WATCH_GROQ_BASE_URL=https://api.groq.com/openai/v1
WATCH_OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_BASE_URL=https://your-gateway.example.com/v1
WATCH_GROQ_MODEL=whisper-large-v3
WATCH_OPENAI_MODEL=whisper-1
```

OpenAI endpoint priority is `WATCH_OPENAI_BASE_URL`, then `OPENAI_BASE_URL`, then the official OpenAI URL. Groq uses `WATCH_GROQ_BASE_URL`, then the official Groq URL. The script appends `/audio/transcriptions` automatically.

## Structure

```
.
├── skills/watch/                 # self-contained skill — copied as a unit by every installer
│   ├── SKILL.md                  # skill contract — the source of truth across all surfaces
│   └── scripts/
│       ├── watch.py              # entry point — orchestrates download → frames → transcript
│       ├── probe.py              # machine-readable YouTube source discovery without media download
│       ├── download.py           # yt-dlp wrapper
│       ├── frames.py             # ffmpeg frame extraction + auto-fps logic
│       ├── transcribe.py         # VTT parsing + dedupe + Whisper orchestration
│       ├── whisper.py            # Groq / OpenAI clients (pure stdlib)
│       ├── config.py             # shared config (~/.config/watch/.env)
│       ├── setup.py              # preflight + installer
│       └── build-skill.sh        # build dist/watch.skill for claude.ai upload (dev-only)
├── hooks/                        # SessionStart status hook (Claude Code only)
├── .claude-plugin/               # plugin.json + marketplace.json (Claude Code)
├── .codex-plugin/                # plugin.json — Codex/agents manifest ("skills": "./skills/")
├── .agents/plugins/              # marketplace.json — Agent Skills marketplace listing
├── AGENTS.md → CLAUDE.md         # generic-agent entry point
├── tests/                        # pytest suite (ffmpeg-synthesized clips, no network)
└── .github/workflows/            # release.yml — auto-builds watch.skill on tag push
```

### Machine-readable YouTube source discovery

Capture workflows can inspect a YouTube channel, playlist, or single-video URL without downloading media:

```bash
python3 skills/watch/scripts/probe.py "https://www.youtube.com/@example/videos"
```

The command prints a versioned JSON contract with source identity, canonical URL, title, video entries, publication times, single-video caption availability, and (when verified) the channel's official feed URL. Errors are returned as JSON with a stable `error.kind`, so downstream code does not need to parse yt-dlp's human-facing text. A non-object JSON result such as `null` is treated as an extractor-format error rather than a process crash.

## Develop

```bash
# Run the test suite (stdlib + pytest; ffmpeg required for frame tests):
python3 -m pytest -q

# Build the claude.ai upload bundle:
bash skills/watch/scripts/build-skill.sh      # → dist/watch.skill
```

Releasing: tag `vX.Y.Z`, push the tag. The workflow builds `dist/watch.skill` and attaches it to the GitHub release. Keep the version in sync across `skills/watch/SKILL.md`, `.claude-plugin/plugin.json`, and `.codex-plugin/plugin.json`.

See [CHANGELOG.md](CHANGELOG.md) for version history.

## 开源说明（Open Source）

本项目使用 MIT 许可证。

项目基于 `yt-dlp`、`ffmpeg` 和 Claude 的多模态 `Read` 工具构建，并通过 [Groq](https://groq.com) 或 [OpenAI](https://openai.com) 提供 Whisper 转写能力。

原项目由 Brad Bonanno 创建，详见[上游仓库](https://github.com/bradautomates/claude-video)。本 fork 在其基础上继续开发；本项目的安装、更新和发布均以 `EmilyYoung71415/claude-video` 中的源码为准。

## Star History

<a href="https://www.star-history.com/?repos=EmilyYoung71415%2Fclaude-video&type=date&legend=top-left">
  <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=EmilyYoung71415/claude-video&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=EmilyYoung71415/claude-video&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=EmilyYoung71415/claude-video&type=date&legend=top-left" />
  </picture>
</a>

---

[本项目源码](https://github.com/EmilyYoung71415/claude-video) · [上游项目](https://github.com/bradautomates/claude-video) · [LICENSE](LICENSE)
