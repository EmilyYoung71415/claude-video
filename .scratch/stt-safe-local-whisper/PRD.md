Status: ready-for-agent
Label: ready-for-agent
Feature: stt-safe-local-whisper

# PRD: Safe Local Whisper Transcription

## Problem Statement

Users can already get local speech-to-text from whisper.cpp or desktop tools such as MacWhisper Pro, but those tools are awkward to use from automated content workflows.

The current `/watch` skill can use external transcript files, platform captions, and API Whisper fallback. That works well for many videos, but it does not give users a local, code-callable transcription path when cloud transcription is undesirable, unavailable, expensive, or blocked by privacy constraints.

The user needs a local STT workflow that can be called by `/watch` and by future content tools without manually opening a desktop app, dragging files around, or exporting subtitles by hand.

The user is also worried about resource pressure. Long videos and batch jobs can consume too much memory, CPU, temporary disk space, or model runtime if transcription is run as one large process. A failed run should not require starting over from the beginning.

## Solution

Add a safe local transcription path that wraps a local whisper.cpp-style command-line executable and fits into the existing `/watch` transcript flow.

The first useful version should accept a local audio or video input, extract normalized audio, split long media into manageable chunks, transcribe one chunk at a time, persist progress after every chunk, and merge the result into the transcript shape that `/watch` already understands.

The behavior should prioritize reliability and low memory pressure over maximum speed:

- Do not keep a large model permanently loaded in memory.
- Do not run multiple local transcription jobs in parallel by default.
- Split long media before transcription.
- Cache completed work so repeated runs do not redo expensive transcription.
- Resume from saved chunk progress after interruption.
- Produce stable raw transcript data, subtitle output, and readable text output.

The existing transcript priority should remain intact: external transcript first, platform captions second, configured local Whisper fallback when selected or available, then API Whisper fallback when configured and allowed.

## User Stories

1. As a content creator, I want `/watch` to transcribe a local video without uploading audio, so that private recordings stay on my machine.
2. As a content creator, I want local Whisper to be callable from the command line, so that I can automate transcription instead of using a desktop app by hand.
3. As a content creator, I want `/watch` to accept the same video inputs it accepts today, so that local transcription feels like an added capability rather than a separate workflow.
4. As a content creator, I want the tool to accept both audio and video files, so that I do not need to manually convert formats first.
5. As a content creator, I want audio to be extracted automatically from video, so that MP4, MOV, MKV, and WebM files can be processed directly.
6. As a content creator, I want extracted audio to use a predictable low-resource format, so that local transcription jobs are small and stable.
7. As a content creator, I want long recordings split into smaller chunks, so that my computer does not run out of memory.
8. As a content creator, I want chunk length to be configurable, so that I can choose safer or faster settings for my machine.
9. As a content creator, I want conservative defaults, so that the first run does not overload my computer.
10. As a content creator, I want only one local transcription process to run at a time by default, so that the computer remains usable.
11. As a content creator, I want each local Whisper process to exit after its chunk completes, so that memory is released throughout the job.
12. As a content creator, I want progress saved after each chunk, so that an interruption does not force a full restart.
13. As a content creator, I want completed chunks reused on resume, so that partial work is not wasted.
14. As a content creator, I want failed chunks recorded clearly, so that I can see what needs attention.
15. As a content creator, I want failed chunks rerunnable independently, so that one bad section does not discard the full transcript.
16. As a content creator, I want cached results tied to the input file and transcription settings, so that stale output is not reused accidentally.
17. As a content creator, I want repeated runs with the same input and settings to skip completed transcription, so that reviewing the same video is fast.
18. As a content creator, I want raw timestamped transcript output, so that later tools can connect text back to the original video moment.
19. As a content creator, I want SRT output, so that I can use the result in video editing tools.
20. As a content creator, I want a readable Markdown transcript, so that I can review and rewrite the text as content.
21. As a content creator, I want raw timing data preserved separately from cleaned text, so that editing readability does not destroy alignment.
22. As a content creator, I want `/watch` to use the local transcript as its normal transcript source, so that frame analysis and transcript analysis still work together.
23. As a content creator, I want `/watch --start` and `/watch --end` to work with local transcription, so that focused analysis does not require transcribing irrelevant sections when avoidable.
24. As a content creator, I want a fast draft model option, so that I can quickly judge whether a video is worth deeper processing.
25. As a content creator, I want a more accurate model option, so that final subtitles and quotes are more reliable.
26. As a content creator, I want the selected model and command visible in logs, so that I can confirm what actually ran.
27. As a content creator, I want helpful setup errors when whisper.cpp is missing, so that I know how to install or configure it.
28. As a content creator, I want model path configuration in the existing watch configuration style, so that setup is not tied to one host.
29. As a content creator, I want local transcription disabled unless configured or explicitly requested, so that existing API-based behavior does not change unexpectedly.
30. As a content creator, I want API Whisper fallback to remain available, so that the feature does not remove the current cloud path.
31. As a content creator, I want `--no-whisper` to continue disabling all Whisper fallback, so that I can force frames-only behavior.
32. As a content creator, I want local transcription output to have the same segment shape as captions and API Whisper output, so that downstream `/watch` behavior stays consistent.
33. As a content creator, I want stable filenames for generated transcript artifacts, so that other tools can consume them.
34. As a content creator, I want temporary audio and chunks to be placed under the working directory, so that cleanup is understandable.
35. As a content creator, I want batch processing to be possible later, so that a folder of videos can use the same safe transcription layer.
36. As a content creator, I want future clipping, note-taking, and article-generation tools to reuse this transcript source, so that every workflow reads from the same reliable data.

## Implementation Decisions

- Extend the existing transcript layer rather than building a separate product. `/watch` remains the user-facing skill, and local Whisper becomes another transcript source.
- Keep the first version command-line based. Do not build a desktop app or web service for the initial implementation.
- Wrap a local whisper.cpp-compatible executable rather than replacing the transcription engine.
- Keep the existing transcript segment contract: each segment has start time, end time, and text.
- Treat the raw timestamped transcript as the source of truth.
- Generate three artifact layers: raw timestamped data, SRT subtitles, and readable Markdown text.
- Keep cleaned or readable text separate from raw timing data.
- Use the existing audio extraction approach as the starting point: mono, 16 kHz, low-bitrate audio suitable for transcription.
- Split long media before invoking local Whisper. Chunk length should be configurable, with a conservative default such as 5 to 10 minutes.
- Run one local transcription process at a time by default.
- Do not keep the local model loaded in a persistent process for the first version.
- Persist chunk status after each successful chunk.
- Cache based on input identity and selected transcription settings, including model, language when supplied, chunk size, and executable configuration.
- Support resume by reading the saved manifest and skipping completed chunks.
- Record failed chunk information in the manifest.
- Keep API Whisper behavior available as a fallback when local Whisper is not configured or fails, unless the user has disabled Whisper fallback.
- Support an explicit local backend selection so users can force local transcription when they do not want cloud upload.
- Add configuration through the existing watch configuration conventions rather than host-specific environment variables.
- Setup checks should report missing executable or missing model in plain actionable language.
- Avoid speaker diarization, AI rewriting, summarization, automatic clip selection, and batch folder processing in the first version.

## Testing Decisions

The main testing seam is the highest behavior seam:

Given a local audio or video file, the transcription workflow should produce stable transcript artifacts and feed `/watch` the same transcript segment shape it already receives from captions or API Whisper.

Good tests should verify external behavior rather than whisper.cpp's recognition quality. Tests should not assert exact real model wording, because transcription output can vary by model and settings.

Test the following behavior with a fake local Whisper command for fast, deterministic coverage:

- A local media input is accepted.
- Audio is extracted into the expected working area.
- Long audio is split into ordered chunks.
- Chunks are transcribed one at a time.
- Chunk timestamps are shifted back to source-video time.
- Progress is written after each completed chunk.
- A second run skips cached completed chunks.
- A partial run resumes from saved state.
- A failed chunk is recorded without erasing successful chunk output.
- If every chunk fails, the workflow reports failure clearly.
- Raw transcript data, SRT, and Markdown artifacts are written.
- The merged transcript can be formatted by the same transcript presentation path as other transcript sources.

Prior art already exists in the repo:

- Existing transcript parsing tests cover SRT and VTT parsing.
- Existing Whisper tests cover chunk planning, audio splitting, timestamp shifting, and failed chunk behavior.
- Existing `/watch` tests cover user-facing behavior around transcript sources and report output.

Add one slower manual or optional verification path using a real whisper.cpp executable and a tiny synthesized clip before calling the feature finished. This should be separate from the default fast test suite.

## Out of Scope

- Replacing whisper.cpp with a new model.
- Building a desktop application.
- Building a complex web UI.
- Running multiple local transcription jobs in parallel.
- Keeping a large model permanently loaded in memory.
- Speaker diarization.
- AI transcript cleanup or rewriting.
- Automatic video summary generation.
- Automatic short-video clip selection.
- Batch folder processing in the first version.
- Uploading or syncing transcripts to a remote service.
- Guaranteeing perfect transcript accuracy.

## Further Notes

The feature should make local STT a reliable foundation for content workflows, not a speed benchmark.

The minimum bar is:

- callable from `/watch`
- local audio stays local when local backend is selected
- conservative resource usage
- resumable progress
- cache reuse
- stable output artifacts
- existing caption and API flows remain intact

The recommended implementation order is:

1. Add the local backend behind the existing transcript interface.
2. Add manifest-based chunk progress and cache reuse.
3. Add raw, SRT, and Markdown artifact output.
4. Wire the backend into `/watch` with explicit selection and safe defaults.
5. Add fake-command tests, then run one real local verification.

