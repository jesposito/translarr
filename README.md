# Translarr

> The arr you reach for when your release has subtitles in the wrong language and nobody has authored English ones yet.

[![CI](https://github.com/jesposito/translarr/actions/workflows/ci.yml/badge.svg)](https://github.com/jesposito/translarr/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)
![Tests](https://img.shields.io/badge/tests-200-green.svg)
![Status](https://img.shields.io/badge/status-alpha-yellow.svg)

Translarr is a self-hosted sidecar that plugs into Sonarr, Radarr, Emby, and Jellyfin. When an import lands with an embedded subtitle track in the wrong language — Russian fansubs on a Japanese anime, hardcoded Spanish on a Korean drama, Polish on an action film — Translarr extracts the track, translates it with a context-aware LLM, redistributes the timing for the target language's reading rate, and drops a clean `.srt` next to the video.

**It validated on a real Demon Slayer Russian-subbed WEB-DL.** 1,649 source events, 1,964 output events after reading-rate split, 5.7 minutes wall clock, $0.30 in API cost.

## Where Translarr fits

```
                    What you have
                          │
            ┌─────────────┴──────────────┐
            ▼                            ▼
  No subtitles anywhere          Subtitles in the wrong language
            │                            │
            ▼                            ▼
       Whisper (subgen)              ┌─────────┐
       Audio → text                  │TRANSLARR│
       (lossy, hallucinates names)   └─────────┘
                                       │
            ┌──────────────────────────┴──────┐
            ▼                                 ▼
  Embedded foreign track          External .srt provider
  (Russian, Japanese, etc.)       (OpenSubtitles, Jimaku — v0.8a+)
            │                                 │
            └──────────────┬──────────────────┘
                           ▼
              LLM translation with sliding context
              + reading-rate adapter
              + ASS/SSA style-tag preservation
                           │
                           ▼
              <basename>.en.translarr.srt next to the video
              + library refresh in Emby/Jellyfin
```

Translarr does NOT compete with Bazarr (which fetches existing subs) or subgen (which transcribes audio). It solves the third case neither of them does well: **the existing subtitle track is in the wrong language and we should translate it.**

## What v0.1 ships today

- **FastAPI server** with async job queue + worker pool (SQLite-backed, survives restart)
- **LLM router** — Anthropic Claude (default), OpenAI, DeepSeek, Google Gemini, Ollama (local, free)
- **Subtitle pipeline** — ffmpeg-extracted track from any container format, parsed by pysubs2
- **Sliding-context translation** — 30-line batches with 10-line prior context, names + pronouns stay consistent
- **Reading-rate adapter** — splits target-language lines that would be unreadable at the original duration; redistributes timing proportionally; preserves the original span
- **Style-tag preservation** — ASS/SSA `{\i1}`, `{\an8}`, `{\fad(...)}`, colors all pass through translation and split
- **Source language auto-detection** — ffprobe reads the track's language tag; no manual `source_lang` needed
- **Preflight cost estimates** — `POST /preflight` shows per-model cost before committing any API spend
- **Translation presets** — Quick & Cheap / Balanced / Best Quality / Local & Free / DeepSeek Budget / Gemini Flash
- **Live-mutable settings** — change model, provider, concurrency, cost caps via the Settings page with zero restarts
- **Sonarr/Radarr/Plex webhooks** — opt-in per movie/series via tags; Plex handles library.new and media.play
- **Emby/Jellyfin webhooks** — retroactive translation on library scan or on-demand via playback.start
- **Emby subtitle picker** — ISubtitleProvider integration; "★ Translarr — translate to EN" in the subtitle modal
- **Item-specific library refresh** — Emby/Jellyfin refresh just the translated item (~2s) instead of full scan
- **Cost guards** — `MAX_COST_CENTS_PER_DAY`, `MAX_COST_CENTS_PER_JOB`, `JOB_TIMEOUT_SECONDS` kill-switches
- **Push notifications** — ntfy.sh integration; know when translations finish without checking the dashboard
- **File browser** — explore your media library from the UI, see which files have translations, coverage stats
- **Glossary persistence** — per-series term dictionaries keep character names consistent across episodes
- **Output-collision policy** — `.translarr.srt` infix never overwrites human/Bazarr/embedded subs
- **Web UI** — dashboard with job history, real-time stats, budget tracking, editable settings page
- **200 tests, all green**

## Quickstart

```bash
git clone https://github.com/jesposito/translarr
cd translarr
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY, adjust MEDIA_ROOT to where your library is mounted
docker compose up -d
curl http://localhost:9100/health   # {"status":"ok","version":"0.1.0",...}
```

Translate a file (async):

```bash
curl -X POST http://localhost:9100/translate \
  -H "Content-Type: application/json" \
  --data '{
    "media_path": "Movies/Demon Slayer (2025)/movie.mkv",
    "source_lang": "ru",
    "target_lang": "en"
  }'
# {"status":"queued","job_id":"abc..."}

curl http://localhost:9100/jobs/abc...
# {"state":"running","attempts":1,...} -> {"state":"done","output_path":"...","cost_cents":54}
```

For one-shot synchronous calls (testing, single-file workflows):

```bash
curl -X POST http://localhost:9100/translate/sync -H "Content-Type: application/json" --data '...'
# Blocks for the duration of the translation, returns the full result.
```

## Wiring into the arr stack

Translarr only acts on items that **opt in via tag**. To enable translation for a movie or series:

1. In Sonarr/Radarr, create a tag named `sonarr_translate` (or `radarr_translate`)
2. Apply the tag to specific items
3. Configure the Connect webhook below
4. Imports of tagged items auto-enqueue a job

**Radarr → Settings → Connect → Add → Webhook:**
- URL: `http://translarr:9000/webhooks/radarr`
- Triggers: `On Import`, `On Upgrade`
- Optional headers: `X-Translarr-Secret: <secret>` (if `WEBHOOK_SECRET` is set)

**Sonarr → Settings → Connect → Add → Webhook:**
- URL: `http://translarr:9000/webhooks/sonarr`
- Same as above, ending in `/sonarr`

**Emby/Jellyfin** — webhook endpoints exist at `/webhooks/emby` and `/webhooks/jellyfin` for retroactive triggers on library events. The Emby plugin also integrates into the player's subtitle search modal — users see "★ Translarr — translate to EN" and can trigger a translation with one click.

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
| `TRANSLARR_DATA_DIR` | `./data` | SQLite DB lives here (jobs + daily_usage tables) |
| `TARGET_LANG` | `en` | ISO 639-1 |
| `READING_RATE_CPS` | `17` | Max chars/sec for target language (15-17 is the industry comfort band for English) |
| `MAX_CONCURRENT` | `2` | Async worker pool size |
| `CONTEXT_WINDOW_LINES` | `10` | Prior translated lines passed to the LLM for context |
| `MAX_COST_CENTS_PER_DAY` | `1000` | Daily LLM spend cap. Exceeded → HTTP 429 on new jobs |
| `MAX_COST_CENTS_PER_JOB` | `500` | Per-job hard cap; aborts mid-batch if estimated cost exceeds |
| `JOB_TIMEOUT_SECONDS` | `1800` | Per-job wall-clock cap |
| `RADARR_TRANSLATE_TAG` | `radarr_translate` | Tag label that opts a movie in for auto-translation |
| `SONARR_TRANSLATE_TAG` | `sonarr_translate` | Same for series |
| `WEBHOOK_SECRET` | — | Optional shared secret on webhook calls |
| `LOG_LEVEL` | `INFO` | |

## Cost estimate (2026 pricing)

Approximate per-film cost for a 1500-event subtitle file at default Sonnet 4.6:

| Model | Per-film cost |
|-------|---------------|
| Claude Haiku 4.5 | ~$0.14 |
| Claude Sonnet 4.6 | ~$0.54 |
| Claude Opus 4.7 | ~$2.70 |
| Ollama qwen3:14b | $0 (local CPU/GPU) |

See `docs/ARCHITECTURE.md` for the token-budget math.

## Roadmap

Translarr is a free, self-hosted tool. The full vision ships before any "launch" push.

| Version | Scope | Status |
|---------|-------|--------|
| v0.1 | Server brain: webhooks, LLM router, sub pipeline, reading-rate, tag-parsing, cost guards | ✅ Shipped |
| v0.1.5 | Persistent queue, async `/translate`, worker pool, cost-tracker | ✅ Shipped |
| v0.1.6 | Source-lang auto-detect, preflight estimates, presets, error cleanup, Unraid template | ✅ Shipped |
| v0.2 | Emby plugin (ISubtitleProvider + scheduled task + settings page) | ✅ Shipped |
| **v0.3** | **DeepSeek/Gemini providers, Plex webhook, item-specific library refresh, GHCR CI, file browser, glossary** | ✅ Shipped |
| v0.4 | Jellyfin plugin (port of v0.2, public repo) | Next |
| v0.5 | Per-series language overrides, UI pages for browse/glossary | Planned |
| v0.7 | Audio-grounded correction (contingent on critic telemetry) | Conditional |
| v0.8a | Direct subtitle provider integrations (OpenSubtitles, Jimaku, Animetosho) | Planned |
| v0.8b | Optional Bazarr-as-fetch-proxy adapter | Planned |
| v0.9 | Whisper-from-audio fallback for no-subs case | Planned |
| v1.0 | Strategy chain endpoint — auto-fallback through embedded → fetch → audio | Planned |

## Architecture

```
                  ┌──────────────────────────────────────┐
                  │          Translarr Server            │
                  │  ┌─────────────────────────────┐     │
   Radarr ──webhook─►/webhooks/radarr ─┐           │     │
   Sonarr ──webhook─►/webhooks/sonarr ─┤  enqueue  │     │
     Emby ──webhook─►/webhooks/emby  ──┤     ▼     │     │
Jellyfin ──webhook─►/webhooks/jellyfin─┘  ┌────────┐     │
   Direct ──POST───►/translate ────────────►SQLite │     │
                  │                       │ queue  │     │
                  │  workers ◄────claim───┤        │     │
                  │     │                 └────────┘     │
                  │     ▼                                │
                  │  Sub pipeline                        │
                  │  ├─ ffmpeg extract OR direct .ass    │
                  │  ├─ pysubs2 parse                    │
                  │  ├─ batch + sliding context          │
                  │  ├─ LLM router (Anthropic/OAI/Ollama)│
                  │  ├─ reading-rate adapt + split       │
                  │  └─ write .translarr.srt + finish    │
                  └──────────────────────────────────────┘
                              │
                              ▼
                    <basename>.en.translarr.srt
                    next to source media
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full file-by-file map.

## Project structure

```
translarr/
├── server/              # Python FastAPI brain
│   ├── main.py
│   ├── config.py
│   ├── cost_tracker.py
│   ├── db.py            # SQLite + migrations
│   ├── llm/             # router + Anthropic / OpenAI / Ollama providers
│   ├── queue/           # base.py + sqlite.py + worker.py
│   ├── subs/            # extract.py + pipeline.py + reading_rate.py
│   └── webhooks/        # radarr.py + sonarr.py + emby.py + jellyfin.py
├── ui/                  # SvelteKit static Web UI (dashboard, jobs, settings)
├── plugins/
│   ├── emby/            # C# Emby plugin (ISubtitleProvider + scheduled task)
│   └── jellyfin/        # v0.3 — C# Jellyfin plugin
├── templates/           # Unraid Community Applications template
├── tests/               # pytest — 178 tests
├── docs/
│   ├── ARCHITECTURE.md
│   ├── INSTALL.md
│   └── COMPETITIVE-ANALYSIS.md
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

## Why "Translarr"?

Sonarr handles TV. Radarr handles movies. Bazarr handles subtitle fetch. Translarr translates subtitles. The `-arr` suffix is a love letter to the whole stack.

## Status

**Alpha.** Working in production, validated on real media. The roadmap is honest, not aspirational. The Docker Hub/GHCR image is the remaining blocker for public adoption — until then, build from source with `docker build`.

## License

MIT. See [`LICENSE`](LICENSE).
