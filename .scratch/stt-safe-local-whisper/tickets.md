# Tickets: Safe Local Whisper Transcription

Build a safe local whisper.cpp transcription path for `/watch`, based on `.scratch/stt-safe-local-whisper/PRD.md`.

Work the frontier: any ticket whose blockers are all done. For this chain, work top to bottom.

## 接入本地转录开关

**What to build:** `/watch` can explicitly select local transcription and reports clear setup guidance when the local executable or model is missing, without changing the existing external transcript, platform caption, API Whisper, or frames-only behavior.

**Blocked by:** None - can start immediately.

- [x] A user can request local transcription explicitly.
- [x] Existing external transcript and platform caption priority remains unchanged.
- [x] Existing API Whisper fallback remains available.
- [x] `--no-whisper` still disables Whisper fallback.
- [x] Missing local executable or model produces a clear, actionable message.
- [x] The behavior is covered by fast tests that do not require a real local model.

## 建立安全的分段转录流程

**What to build:** Given a local audio or video file, the local backend extracts normalized audio, splits long media into chunks, and transcribes one chunk at a time through a local whisper.cpp-compatible command.

**Blocked by:** 接入本地转录开关.

- [x] Local audio extraction uses a predictable low-resource format.
- [x] Long media is split into ordered chunks.
- [x] Chunk length is configurable with a conservative default.
- [x] Only one local transcription process runs at a time.
- [x] Chunk timestamps are shifted back to source-video time.
- [x] Tests use a fake local transcription command.

## 加入进度保存和失败恢复

**What to build:** Local transcription saves progress after each chunk and can resume an interrupted run without discarding successful chunk output.

**Blocked by:** 建立安全的分段转录流程.

- [x] A manifest records chunk status after each completed chunk.
- [x] A resumed run skips completed chunks.
- [x] Failed chunks are recorded clearly.
- [x] One failed chunk does not erase successful chunk output.
- [x] If every chunk fails, the workflow reports failure clearly.

## 加入缓存复用

**What to build:** Re-running local transcription for the same input and settings reuses completed work instead of redoing expensive transcription.

**Blocked by:** 加入进度保存和失败恢复.

- [ ] Cache identity includes the input and selected transcription settings.
- [ ] Changing model, language, chunk size, or executable configuration avoids stale cache reuse.
- [ ] A repeated run with unchanged settings skips completed work.
- [ ] Cache behavior is visible enough for users to understand what happened.

## 输出稳定的转录成果

**What to build:** Local transcription writes stable raw transcript data, SRT subtitles, and readable Markdown text that later tools can consume.

**Blocked by:** 加入缓存复用.

- [ ] Raw timestamped data is written as the source of truth.
- [ ] SRT output is generated from the raw transcript.
- [ ] Readable Markdown output is generated separately from raw timing data.
- [ ] Artifact names and locations are stable within the working output.
- [ ] Tests verify the artifacts from deterministic fake transcript data.

## 把本地转录完整接入 `/watch` 报告

**What to build:** `/watch` uses local transcription results the same way it uses captions or API Whisper, including focused time ranges and normal transcript reporting.

**Blocked by:** 输出稳定的转录成果.

- [ ] `/watch` reports local transcription as the transcript source.
- [ ] Focused `--start` and `--end` runs filter local transcript segments correctly.
- [ ] Existing frame extraction and report output continue to work.
- [ ] Existing caption and API Whisper paths remain covered by tests.

## 做真实验证和文档补齐

**What to build:** The feature is documented and verified with both fast fake-command tests and one real local whisper.cpp smoke path.

**Blocked by:** 把本地转录完整接入 `/watch` 报告.

- [ ] User-facing setup and usage docs explain local transcription configuration.
- [ ] Fast default tests do not require a real local model.
- [ ] A slower manual or optional verification path is documented.
- [ ] A tiny real local whisper.cpp sample run is performed before the feature is called complete, when the local tool is available.
