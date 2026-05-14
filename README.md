# Translarr

> AI-powered subtitle translation for the arr stack. Translate existing subtitle tracks into any language, preserving timing and cadence.

Translarr is a self-hosted sidecar that plugs directly into Sonarr, Radarr, Emby, and Jellyfin. When a release lands with subtitles in the wrong language (Russian fansubs, embedded Japanese, hardcoded Spanish), Translarr extracts the track and translates it with a context-aware LLM, then drops a clean `.srt` next to the video. Reading rate is adapted so a 1.2-second Japanese line doesn't become an unreadable English wall.

## Why not Bazarr?

Bazarr is great at *finding* existing subtitles. Translarr is for when no good subtitle exists — only a foreign one does. The two solve different halves of the problem. Translarr does not require Bazarr.

## Features (v0.1)

- **Sonarr / Radarr Connect webhooks** — translate automatically on every import
- **Emby / Jellyfin webhooks** — translate retroactively on library scan
- **Pluggable LLM backends** — Claude, OpenAI, Gemini, or local Ollama models
- **Reading-rate adapter** — splits long target lines to stay under per-language CPS limits
- **Style-tag preservation** — keeps ASS / SSA `{\an8}` positioning, fades, colors
- **Glossary lock** — character / show / world terms stay consistent across files
- **Sliding-context translation** — N-line window prevents lost-pronoun mistakes
- **Subgen-compatible `/asr` endpoint** — works with any tool that speaks the Whisper Provider protocol

## Roadmap

- **v0.2** — Emby plugin (C# DLL, context menu integration)
- **v0.3** — Jellyfin plugin (95% shared with Emby plugin)
- **v0.4** — Second-pass critic, audio-grounded correction
- **v0.5** — Auto-translate-on-add per-show flags
- **v0.6** — Library-wide language fill scheduled task

## Quickstart

```bash
git clone https://github.com/jesposito/translarr
cd translarr
cp .env.example .env
# Edit .env: set LLM provider + key, set MEDIA_ROOT, set TARGET_LANG
docker compose up -d
```

Then in Radarr / Sonarr → Settings → Connect → Add → Webhook:

- URL: `http://translarr:9000/webhooks/radarr` (or `/sonarr`)
- Method: POST
- Triggers: `On Import`, `On Upgrade`

In Emby / Jellyfin → Notifications → Add Webhook:

- URL: `http://translarr:9000/webhooks/emby` (or `/jellyfin`)
- Events: `Library New`, `Library Updated`

## Architecture

```
                  ┌──────────────────────────────────────┐
                  │            Translarr Server          │
                  │                                      │
   Radarr ──webhook─►  /webhooks/radarr                  │
   Sonarr ──webhook─►  /webhooks/sonarr                  │
     Emby ──webhook─►  /webhooks/emby                    │
Jellyfin ──webhook─►   /webhooks/jellyfin                │
   Bazarr* ──/asr─►   /asr  (subgen-compat)              │
                  │       │                              │
                  │       ▼                              │
                  │   Sub Pipeline                       │
                  │   ├─ Extract track (ffmpeg)          │
                  │   ├─ Parse (pysubs2)                 │
                  │   ├─ Translate (LLM router)          │
                  │   ├─ Reading-rate adapt              │
                  │   └─ Write .en.translated.srt        │
                  └──────────────────────────────────────┘

                  *Not required and not recommended for
                   stacks that have had Bazarr corrupt
                   their library before.
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for details.

## Configuration

All config via env vars or `.env`. See [`.env.example`](.env.example).

| Variable | Default | Notes |
|----------|---------|-------|
| `LLM_PROVIDER` | `anthropic` | `anthropic`, `openai`, `ollama` |
| `LLM_MODEL` | `claude-sonnet-4-6` | Provider-specific model id |
| `ANTHROPIC_API_KEY` | — | Required for `anthropic` |
| `OPENAI_API_KEY` | — | Required for `openai` |
| `OLLAMA_HOST` | `http://ollama:11434` | Required for `ollama` |
| `MEDIA_ROOT` | `/media` | Where the volume is mounted inside the container |
| `TARGET_LANG` | `en` | ISO 639-1 |
| `READING_RATE_CPS` | `17` | Max chars/sec for target language |
| `MAX_CONCURRENT` | `2` | Parallel translation jobs |
| `WEBHOOK_SECRET` | — | Optional shared secret on webhook calls |
| `LOG_LEVEL` | `INFO` | |

## Status

**Pre-alpha.** v0.1 ships the server brain. Plugin work is queued for v0.2-v0.3. Used in production by exactly one person.

## License

MIT. See [`LICENSE`](LICENSE).
