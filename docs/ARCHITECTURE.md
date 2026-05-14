# Architecture

## High level

Translarr is a single Python service that listens for events from the arr stack and produces translated subtitle files. The C# Emby / Jellyfin plugins (v0.2+) are thin clients that send the same kind of event the webhooks already accept.

## Components

### Server (Python, FastAPI)

- **Webhooks** (`server/webhooks/`): four event-shaped endpoints, one per upstream (Radarr, Sonarr, Emby, Jellyfin). They normalize the incoming payload to a single internal job: "translate the subs of media at path X."
- **Queue** (`server/webhooks/queue.py`): in-memory dedup queue + semaphore. v0.1 is single-process; v0.4 should move to Redis or a SQLite-backed queue so jobs survive restarts.
- **Pipeline** (`server/subs/pipeline.py`): the core. Lists tracks, picks a source, extracts to disk, parses with `pysubs2`, batches with sliding context, sends to LLM, adapts reading rate, writes output.
- **LLM router** (`server/llm/`): one `Protocol` interface, three implementations (Anthropic, OpenAI, Ollama). Retries via `tenacity`.
- **Reading-rate adapter** (`server/subs/reading_rate.py`): split-on-sentences-or-whitespace to keep target lines under CPS limit, redistribute duration.

### Plugins (C#, v0.2+)

Emby and Jellyfin both load .NET assemblies. The plugins contribute:

1. A REST controller (`POST /Translarr/Translate`) inside the media server.
2. JS injection that adds context-menu items.
3. A settings page.
4. A scheduled task for batch / library-wide runs.

Plugins never translate. They call `POST http://translarr:9000/translate` and let the server do the work.

## Data flow

```
1. Radarr completes a movie import
2. Radarr fires Connect webhook -> POST /webhooks/radarr
3. Webhook handler extracts media path, calls queue.enqueue(path)
4. Queue task acquires semaphore, calls pipeline.translate_media(path)
5. Pipeline lists subtitle tracks via ffprobe
6. Pipeline picks the best foreign-language source track
7. Pipeline extracts that track to a temp .srt/.ass with ffmpeg
8. Pipeline parses events, batches into context windows
9. Each batch goes to LLM router -> provider -> model
10. Translated events are merged back, reading-rate adapted, written next to the video
11. (v0.4+) Pipeline calls Emby/Jellyfin /Library/Refresh to surface the new subs
```

## Why subgen-compatible `/asr`?

Tools like Bazarr can call any Whisper-Provider-shaped HTTP service. By exposing the same shape, Translarr drops into existing pipelines without code changes on the consumer side. **We do not require Bazarr** — this is opt-in compatibility, not a dependency.

## Future work

- **Audio-grounded correction** (v0.4): for high-stakes lines, feed the matching audio chunk through Whisper and use the transcript to sanity-check the translation candidate.
- **Glossary cache** (v0.4): SQLite-backed per-series glossary auto-extracted from first-pass translations.
- **Critic pass** (v0.4): cheap second LLM call rates each line for naturalness; top 5% awkward lines get regenerated.
- **Persistent queue** (v0.5): replace in-memory dedup with Redis or SQLite so restarts don't drop work.
- **Cost dashboard** (v0.5): per-import tokens / dollars, exposed on `/stats`.
