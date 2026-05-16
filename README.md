# Translarr

> The arr you reach for when your release has subtitles in the wrong language and nobody has authored English ones yet.

[![CI](https://github.com/jesposito/translarr/actions/workflows/ci.yml/badge.svg)](https://github.com/jesposito/translarr/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)
![Tests](https://img.shields.io/badge/tests-210-green.svg)
![Status](https://img.shields.io/badge/status-alpha-yellow.svg)

Translarr is a self-hosted Docker container that plugs into Sonarr, Radarr, Emby, Jellyfin, and Plex. When an import lands with a subtitle track in the wrong language — Russian fansubs on a Japanese anime, hardcoded Spanish on a Korean drama, Polish on an action film — Translarr extracts the track, translates it with a context-aware LLM, adapts the timing for the target language's reading rate, and drops a clean `.srt` next to the video.

**Validated on real media.** A 1,649-event subtitle file translated, reading-rate split to 1,964 events, 5.7 minutes wall clock, $0.30 in API cost.

## Quickstart

```bash
docker run -d \
  --name translarr \
  -p 9100:9000 \
  -v /mnt/user/appdata/translarr:/data \
  -v /mnt/user/media:/media \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  ghcr.io/jesposito/translarr:main
```

Open `http://your-server:9100` for the Web UI. Or verify it's running:

```bash
curl http://localhost:9100/health
# {"status":"ok","version":"0.1.0","llm_provider":"anthropic","llm_model":"claude-sonnet-4-6"}
```

Translate a file:

```bash
curl -X POST http://localhost:9100/translate \
  -H "Content-Type: application/json" \
  --data '{
    "media_path": "Movies/Some Movie (2025)/movie.mkv",
    "source_lang": "ru",
    "target_lang": "en"
  }'
# {"status":"queued","job_id":"abc..."}

curl http://localhost:9100/jobs/abc...
# {"state":"done","output_path":"...","cost_cents":54}
```

The container exposes port **9000** internally. Map it to whatever host port you want (`9100`, `80`, etc.) — Translarr doesn't care about the external port.

## Features

- **LLM router** — Anthropic Claude (default), OpenAI, DeepSeek, Google Gemini, Ollama (local, free)
- **6 translation presets** — Quick & Cheap / Balanced / Best Quality / Local & Free / DeepSeek Budget / Gemini Flash
- **Subtitle pipeline** — ffmpeg-extracted track from any container, parsed by pysubs2, ASS/SSA style-tag preservation
- **Sliding-context translation** — 30-line batches with 10-line prior context for consistent names and pronouns
- **Reading-rate adapter** — auto-splits lines that would be unreadable; redistributes timing proportionally
- **Source language auto-detection** — ffprobe reads the track's language tag; no manual `source_lang` needed
- **Cost guards** — daily and per-job spend caps, per-job timeout kill-switch
- **Live-mutable settings** — change model, provider, concurrency, cost caps via the Settings page; zero restarts
- **Sonarr/Radarr/Plex webhooks** — opt-in per movie/series via tags
- **Emby/Jellyfin webhooks** — retroactive translation on library scan or on-demand via playback events
- **Emby plugin** — integrates into the player's subtitle search modal (see [Emby Plugin](#emby-plugin-optional) below)
- **Item-specific library refresh** — Emby/Jellyfin refresh just the translated item (~2s) instead of full library scan
- **Per-series config** — different source/target language defaults per series, auto-applied by path
- **Glossary** — per-series term dictionaries keep character names consistent across episodes
- **File browser** — explore your media library from the UI, see which files have translations, coverage stats
- **Push notifications** — ntfy.sh integration for translation completion alerts
- **Output-collision policy** — `.translarr.srt` infix never overwrites human/Bazarr/embedded subs
- **Web UI** — dashboard, job history, real-time stats, budget tracking, library browser, glossary editor, settings
- **210 tests, all green**

## Wiring into the arr stack

Translarr only acts on items that **opt in via tag**. To enable translation for a movie or series:

1. In Sonarr/Radarr, create a tag named `sonarr_translate` (or `radarr_translate`)
2. Apply the tag to specific items
3. Configure the Connect webhook below
4. Imports of tagged items auto-enqueue a translation job

**Radarr → Settings → Connect → Add → Webhook:**
- URL: `http://translarr:9000/webhooks/radarr` (use your Docker host IP if Radarr is on a different network)
- Triggers: `On Import`, `On Upgrade`
- Optional headers: `X-Translarr-Secret: <secret>` (if `WEBHOOK_SECRET` is set)

**Sonarr → Settings → Connect → Add → Webhook:**
- URL: `http://translarr:9000/webhooks/sonarr`
- Same as above, ending in `/sonarr`

**Emby → Notifications → Add → Webhook:**
- URL: `http://translarr:9000/webhooks/emby`
- Events: `Library New`, `Library Updated`

**Jellyfin** — requires the [Webhook plugin](https://github.com/jellyfin/jellyfin-plugin-webhook):
- URL: `http://translarr:9000/webhooks/jellyfin`
- Triggers: `ItemAdded`

**Plex** — add a webhook via Plex settings or Tautulli:
- URL: `http://translarr:9000/webhooks/plex`
- Handles `library.new` and `media.play` events

## Emby Plugin (optional)

The Emby plugin adds a "★ Translarr — translate to EN" option inside Emby's player subtitle search modal. When a user opens the subtitle picker on a movie or episode, they see the Translarr option alongside OpenSubtitles and other providers. Clicking it triggers an on-demand translation.

The plugin is **not required** — webhooks work without it. It just adds the in-player UX.

### How it works

1. The plugin registers as an `ISubtitleProvider` inside Emby
2. When a user opens the subtitle search modal, the plugin returns a virtual "translate to EN" entry
3. When the user clicks that entry, the plugin calls the Translarr server's `/translate` API
4. The server queues the translation job, runs it through the LLM pipeline, and writes the `.srt`
5. Emby picks up the new subtitle file and displays it in the player

The plugin never does any translation itself — it's a thin HTTP client that delegates everything to the Translarr container.

### Install

1. Download [`Translarr.dll`](https://github.com/jesposito/translarr/raw/main/plugins/emby/publish/Translarr.dll) from this repo
2. Copy it to your Emby server's plugins directory (typically `/config/plugins/` inside the Emby container, or `/mnt/user/appdata/emby/plugins/` on Unraid)
3. Restart Emby
4. Go to Emby → Settings → Plugins → Translarr
5. Set the Translarr server URL (e.g. `http://192.168.1.100:9100`) and save

### Requirements

- Emby Server 4.9+
- The Translarr Docker container must be running and accessible from Emby's network
- Both Emby and Translarr must see the same media files (same mount paths)

## Configuration

All config via env vars, `.env`, or the Settings page in the Web UI. Settings changed via the UI take effect immediately without a restart.

| Variable | Default | Notes |
|----------|---------|-------|
| `LLM_PROVIDER` | `anthropic` | `anthropic`, `openai`, `ollama`, `deepseek`, `gemini` |
| `LLM_MODEL` | `claude-sonnet-4-6` | Provider-specific model id |
| `ANTHROPIC_API_KEY` | — | Required for `anthropic` |
| `OPENAI_API_KEY` | — | Required for `openai` |
| `DEEPSEEK_API_KEY` | — | Required for `deepseek` |
| `GEMINI_API_KEY` | — | Required for `gemini` |
| `OLLAMA_HOST` | `http://ollama:11434` | Required for `ollama` |
| `MEDIA_ROOT` | `/media` | Where the volume is mounted inside the container |
| `TRANSLARR_DATA_DIR` | `./data` | SQLite DB lives here |
| `TARGET_LANG` | `en` | ISO 639-1 target language |
| `READING_RATE_CPS` | `17` | Max chars/sec (15–17 is the industry comfort band for English) |
| `MAX_CONCURRENT` | `2` | Parallel translation jobs |
| `CONTEXT_WINDOW_LINES` | `10` | Prior translated lines passed to the LLM for context |
| `MAX_COST_CENTS_PER_DAY` | `1000` | Daily LLM spend cap ($10). Exceeded → HTTP 429 on new jobs |
| `MAX_COST_CENTS_PER_JOB` | `500` | Per-job cap ($5); aborts mid-batch if exceeded |
| `JOB_TIMEOUT_SECONDS` | `1800` | Per-job wall-clock cap (30 min) |
| `RADARR_TRANSLATE_TAG` | `radarr_translate` | Tag that opts a movie in for auto-translation |
| `SONARR_TRANSLATE_TAG` | `sonarr_translate` | Tag that opts a series in for auto-translation |
| `WEBHOOK_SECRET` | — | Optional shared secret on webhook calls |
| `EMBY_URL` | — | Emby server URL for post-translation library refresh |
| `EMBY_API_KEY` | — | Emby API key |
| `JELLYFIN_URL` | — | Jellyfin server URL for post-translation library refresh |
| `JELLYFIN_API_KEY` | — | Jellyfin API key |
| `NTFY_URL` | — | ntfy.sh push notification endpoint. Empty = off |
| `LOG_LEVEL` | `INFO` | |

## Cost estimate (2026 pricing)

Approximate per-film cost for a 1500-event subtitle file:

| Model | Per-film cost |
|-------|---------------|
| Claude Haiku 4.5 | ~$0.14 |
| Claude Sonnet 4.6 | ~$0.54 |
| Claude Opus 4.7 | ~$2.70 |
| DeepSeek Chat | ~$0.08 |
| Gemini 2.5 Flash | ~$0.04 |
| Ollama qwen3:14b | $0 (local CPU/GPU) |

See `docs/ARCHITECTURE.md` for the token-budget math.

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

Translarr does NOT compete with Bazarr (which fetches existing subs) or subgen (which transcribes audio). It solves the third case neither of them covers: **the existing subtitle track is in the wrong language and we should translate it.**

## Roadmap

Translarr is a free, self-hosted tool. The full vision ships before any "launch" push.

| Version | Scope | Status |
|---------|-------|--------|
| v0.1 | Server brain: webhooks, LLM router, sub pipeline, reading-rate, tag-parsing, cost guards | ✅ Shipped |
| v0.1.5 | Persistent queue, async `/translate`, worker pool, cost-tracker | ✅ Shipped |
| v0.1.6 | Source-lang auto-detect, preflight estimates, presets, error cleanup, Unraid template | ✅ Shipped |
| v0.2 | Emby plugin (ISubtitleProvider + scheduled task + settings page) | ✅ Shipped |
| v0.3 | DeepSeek/Gemini providers, Plex webhook, item-specific library refresh, GHCR CI, file browser, glossary, per-series config | ✅ Shipped |
| v0.4 | Jellyfin plugin (port of v0.2, public repo) | Next |
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
  Jellyfin ──webhook─►/webhooks/jellyfin─┘  ┌────────┐   │
     Plex ──webhook─►/webhooks/plex ────────►SQLite │   │
   Direct ──POST───►/translate ────────────►│ queue  │   │
                  │                       └────────┘   │
                  │  workers ◄────claim───┤            │
                  │     │                 └────────┘   │
                  │     ▼                              │
                  │  Sub pipeline                      │
                  │  ├─ ffmpeg extract OR direct .ass  │
                  │  ├─ pysubs2 parse                  │
                  │  ├─ batch + sliding context        │
                  │  ├─ LLM router (5 providers)       │
                  │  ├─ reading-rate adapt + split     │
                  │  └─ write .translarr.srt + refresh │
                  └──────────────────────────────────────┘
                              │
                              ▼
                <basename>.en.translarr.srt
                next to source media
```

## Project structure

```
translarr/
├── server/              # Python FastAPI brain
│   ├── main.py
│   ├── config.py
│   ├── cost_tracker.py
│   ├── db.py            # SQLite + migrations
│   ├── browse.py        # File browser API
│   ├── glossary.py      # Per-series glossary persistence
│   ├── series_config.py # Per-series language overrides
│   ├── library_refresh.py
│   ├── llm/             # router + 5 providers
│   ├── queue/           # base.py + sqlite.py + worker.py
│   ├── subs/            # extract.py + pipeline.py + reading_rate.py
│   └── webhooks/        # radarr, sonarr, emby, jellyfin, plex
├── ui/                  # SvelteKit static Web UI
├── plugins/
│   ├── emby/            # C# Emby plugin (ISubtitleProvider)
│   └── jellyfin/        # C# Jellyfin plugin (planned)
├── templates/           # Unraid Community Applications template
├── tests/               # pytest — 210 tests
├── docs/
│   ├── ARCHITECTURE.md
│   ├── INSTALL.md
│   └── COMPETITIVE-ANALYSIS.md
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

## License

MIT. See [`LICENSE`](LICENSE).
