# Translarr

> The arr you reach for when your release has subtitles in the wrong language and nobody has authored English ones yet.

[![CI](https://github.com/jesposito/translarr/actions/workflows/ci.yml/badge.svg)](https://github.com/jesposito/translarr/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)
![Tests](https://img.shields.io/badge/tests-210-green.svg)
![Status](https://img.shields.io/badge/status-alpha-yellow.svg)

Translarr is a self-hosted Docker container that plugs into Sonarr, Radarr, Emby, Jellyfin, and Plex. When an import lands with a subtitle track in the wrong language (Russian fansubs on a Japanese anime, hardcoded Spanish on a Korean drama, etc.), Translarr extracts the track, translates it with an LLM, adjusts the timing for the target language's reading speed, and writes a clean `.srt` next to the video.

Tested on real media: a 1,649-event subtitle file, reading-rate split to 1,964 events, 5.7 min wall clock, $0.30 in API cost.

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

Open `http://your-server:9100` for the Web UI, or verify it's running:

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

The container listens on port **9000** internally. Map it to whatever host port you want.

## Features

- **LLM router**: Anthropic Claude (default), OpenAI, DeepSeek, Google Gemini, Ollama (local, free)
- **6 translation presets**: Quick & Cheap, Balanced, Best Quality, Local & Free, DeepSeek Budget, Gemini Flash
- **Subtitle pipeline**: ffmpeg extraction from any container, pysubs2 parsing, ASS/SSA style-tag pass-through
- **Sliding-context translation**: 30-line batches with 10-line prior context so names and pronouns stay consistent
- **Reading-rate adapter**: auto-splits lines that would be unreadable, redistributes timing to match
- **Source language auto-detection**: ffprobe reads the track's language tag so you don't have to set it manually
- **Cost guards**: daily and per-job spend caps, per-job timeout kill-switch
- **Live-mutable settings**: change model, provider, concurrency, cost caps via the Settings page with no restart
- **Sonarr/Radarr/Plex webhooks**: opt-in per movie/series via tags
- **Emby/Jellyfin webhooks**: retroactive translation on library scan or on-demand via playback events
- **Emby plugin**: shows up in the player's subtitle search modal (see below)
- **Item-specific library refresh**: Emby/Jellyfin refresh just the translated item (~2s) instead of doing a full scan
- **Per-series config**: different source/target language defaults per series, applied automatically by path
- **Glossary**: per-series term dictionaries keep character names consistent across episodes
- **File browser**: browse your media library from the UI, see which files have translations, coverage stats
- **Push notifications**: ntfy.sh integration for translation completion alerts
- **Output-collision policy**: `.translarr.srt` infix means it never overwrites human/Bazarr/embedded subs
- **Web UI**: dashboard, job history, live stats, budget tracking, library browser, glossary editor, settings
- **210 tests, all green**

## Wiring into the arr stack

Translarr only acts on items that **opt in via tag**. To enable translation for a movie or series:

1. In Sonarr/Radarr, create a tag named `sonarr_translate` (or `radarr_translate`)
2. Apply the tag to specific items
3. Configure the Connect webhook below
4. Imports of tagged items auto-enqueue a translation job

**Radarr -> Settings -> Connect -> Add -> Webhook:**
- URL: `http://translarr:9000/webhooks/radarr` (use your Docker host IP if Radarr is on a different network)
- Triggers: `On Import`, `On Upgrade`
- Optional headers: `X-Translarr-Secret: <secret>` (if `WEBHOOK_SECRET` is set)

**Sonarr -> Settings -> Connect -> Add -> Webhook:**
- URL: `http://translarr:9000/webhooks/sonarr`
- Same as above, ending in `/sonarr`

**Emby -> Notifications -> Add -> Webhook:**
- URL: `http://translarr:9000/webhooks/emby`
- Events: `Library New`, `Library Updated`

**Jellyfin** (requires the [Webhook plugin](https://github.com/jellyfin/jellyfin-plugin-webhook)):
- URL: `http://translarr:9000/webhooks/jellyfin`
- Triggers: `ItemAdded`

**Plex** (via Plex settings or Tautulli):
- URL: `http://translarr:9000/webhooks/plex`
- Handles `library.new` and `media.play` events

## Emby Plugin (optional)

The Emby plugin adds a "Translarr: translate to EN" option inside Emby's subtitle search modal. When you open the subtitle picker on a movie or episode, you'll see the Translarr option next to OpenSubtitles and other providers. Clicking it kicks off a translation.

The plugin is **not required**. Webhooks work fine without it. This just adds the in-player UX.

### How it works

1. The plugin registers as an `ISubtitleProvider` inside Emby
2. When you open the subtitle search modal, the plugin shows a "translate to EN" entry
3. Clicking it calls the Translarr server's `/translate` API
4. The server queues the job, runs it through the LLM pipeline, and writes the `.srt`
5. Emby picks up the new subtitle file and shows it in the player

The plugin doesn't do any translation itself. It's just a thin HTTP client that talks to the Translarr container.

### Install

1. Download [`Translarr.dll`](https://github.com/jesposito/translarr/raw/main/plugins/emby/publish/Translarr.dll)
2. Copy it to your Emby plugins directory (`/config/plugins/` inside the Emby container, or `/mnt/user/appdata/emby/plugins/` on Unraid)
3. Restart Emby
4. Go to Emby -> Settings -> Plugins -> Translarr
5. Set the Translarr server URL (e.g. `http://192.168.1.100:9100`) and save

### Requirements

- Emby Server 4.9+
- Translarr container must be running and reachable from Emby
- Both Emby and Translarr must see the same media files (same mount paths)

## Configuration

All config via env vars, `.env`, or the Settings page in the Web UI. Settings changed in the UI take effect right away, no restart needed.

| Variable | Default | Notes |
|----------|---------|-------|
| `LLM_PROVIDER` | `anthropic` | `anthropic`, `openai`, `ollama`, `deepseek`, `gemini` |
| `LLM_MODEL` | `claude-sonnet-4-6` | Model id for your chosen provider |
| `ANTHROPIC_API_KEY` | | Required for `anthropic` |
| `OPENAI_API_KEY` | | Required for `openai` |
| `DEEPSEEK_API_KEY` | | Required for `deepseek` |
| `GEMINI_API_KEY` | | Required for `gemini` |
| `OLLAMA_HOST` | `http://ollama:11434` | Required for `ollama` |
| `MEDIA_ROOT` | `/media` | Where the volume is mounted inside the container |
| `TRANSLARR_DATA_DIR` | `./data` | SQLite DB lives here |
| `TARGET_LANG` | `en` | ISO 639-1 target language |
| `READING_RATE_CPS` | `17` | Max chars/sec (15-17 is standard for English) |
| `MAX_CONCURRENT` | `2` | Parallel translation jobs |
| `CONTEXT_WINDOW_LINES` | `10` | Prior translated lines fed to the LLM for context |
| `MAX_COST_CENTS_PER_DAY` | `1000` | Daily spend cap ($10). Goes over = HTTP 429 |
| `MAX_COST_CENTS_PER_JOB` | `500` | Per-job cap ($5). Kills the job mid-batch if exceeded |
| `JOB_TIMEOUT_SECONDS` | `1800` | Per-job wall-clock timeout (30 min) |
| `RADARR_TRANSLATE_TAG` | `radarr_translate` | Tag that opts a movie in |
| `SONARR_TRANSLATE_TAG` | `sonarr_translate` | Tag that opts a series in |
| `WEBHOOK_SECRET` | | Optional shared secret for webhook calls |
| `EMBY_URL` | | Emby server URL for post-translation refresh |
| `EMBY_API_KEY` | | Emby API key |
| `JELLYFIN_URL` | | Jellyfin server URL for post-translation refresh |
| `JELLYFIN_API_KEY` | | Jellyfin API key |
| `NTFY_URL` | | ntfy.sh endpoint for push notifications. Empty = off |
| `LOG_LEVEL` | `INFO` | |

## Cost estimate (2026 pricing)

Rough per-film cost for a 1500-event subtitle file:

| Model | Per-film cost |
|-------|---------------|
| Claude Haiku 4.5 | ~$0.14 |
| Claude Sonnet 4.6 | ~$0.54 |
| Claude Opus 4.7 | ~$2.70 |
| DeepSeek Chat | ~$0.08 |
| Gemini 2.5 Flash | ~$0.04 |
| Ollama qwen3:14b | $0 (local) |

See `docs/ARCHITECTURE.md` for the token-budget math.

## Where Translarr fits

```
                    What you have
                          |
            +-------------+--------------+
            v                            v
  No subtitles anywhere       Subtitles in the wrong language
            |                            |
            v                            v
       Whisper (subgen)            +-----------+
       Audio -> text               |TRANSLARR  |
       (lossy, guesses names)      +-----------+
                                          |
            +-----------------------------+---------+
            v                                       v
  Embedded foreign track              External .srt provider
  (Russian, Japanese, etc.)           (OpenSubtitles, Jimaku, planned)
            |                                       |
            +------------------+--------------------+
                               v
              LLM translation with sliding context
              + reading-rate adapter
              + ASS/SSA style-tag preservation
                               |
                               v
              <basename>.en.translarr.srt next to the video
              + library refresh in Emby/Jellyfin
```

Bazarr fetches existing subs. Subgen transcribes audio. Translarr translates the subtitle track that's already there. Different jobs.

## Roadmap

Free, self-hosted, no deadline.

| Version | Scope | Status |
|---------|-------|--------|
| v0.1 | Server brain: webhooks, LLM router, sub pipeline, reading-rate, cost guards | Shipped |
| v0.1.5 | Persistent queue, async `/translate`, worker pool, cost tracker | Shipped |
| v0.1.6 | Source-lang auto-detect, preflight estimates, presets, Unraid template | Shipped |
| v0.2 | Emby plugin (ISubtitleProvider + settings page) | Shipped |
| v0.3 | DeepSeek/Gemini providers, Plex webhook, GHCR CI, file browser, glossary, per-series config | Shipped |
| v0.4 | Jellyfin plugin | Next |
| v0.8a | Direct subtitle provider integrations (OpenSubtitles, Jimaku, Animetosho) | Planned |
| v0.9 | Whisper-from-audio fallback | Planned |
| v1.0 | Strategy chain: auto-fallback through embedded, fetch, audio | Planned |

## Architecture

```
                  +--------------------------------------+
                  |          Translarr Server            |
                  |  +-----------------------------+     |
   Radarr --webhook-->/webhooks/radarr -+           |     |
   Sonarr --webhook-->/webhooks/sonarr -+  enqueue  |     |
     Emby --webhook-->/webhooks/emby  --+     |     |     |
  Jellyfin --webhook-->/webhooks/jellyfin-+  v     |     |
     Plex --webhook-->/webhooks/plex -------+----+  |     |
   Direct --POST--->/translate ---------------+  v  |     |
                  |                       +--------+ |     |
                  |  workers <---claim----|SQLite  | |     |
                  |     |                 | queue  | |     |
                  |     v                 +--------+ |     |
                  |  Sub pipeline                    |     |
                  |  +- ffmpeg extract OR direct .ass|     |
                  |  +- pysubs2 parse                |     |
                  |  +- batch + sliding context      |     |
                  |  +- LLM router (5 providers)     |     |
                  |  +- reading-rate adapt + split   |     |
                  |  +- write .translarr.srt + refresh|    |
                  +--------------------------------------+ 
                              |
                              v
                <basename>.en.translarr.srt
                next to source media
```

## Project structure

```
translarr/
+-- server/              # Python FastAPI
|   +-- main.py
|   +-- config.py
|   +-- cost_tracker.py
|   +-- db.py            # SQLite + migrations
|   +-- browse.py        # File browser API
|   +-- glossary.py      # Per-series glossary persistence
|   +-- series_config.py # Per-series language overrides
|   +-- library_refresh.py
|   +-- llm/             # router + 5 providers
|   +-- queue/           # base.py + sqlite.py + worker.py
|   +-- subs/            # extract.py + pipeline.py + reading_rate.py
|   +-- webhooks/        # radarr, sonarr, emby, jellyfin, plex
+-- ui/                  # SvelteKit static Web UI
+-- plugins/
|   +-- emby/            # C# Emby plugin (ISubtitleProvider)
|   +-- jellyfin/        # C# Jellyfin plugin (planned)
+-- templates/           # Unraid Community Applications template
+-- tests/               # pytest: 210 tests
+-- docs/
|   +-- ARCHITECTURE.md
|   +-- INSTALL.md
|   +-- COMPETITIVE-ANALYSIS.md
+-- docker-compose.yml
+-- Dockerfile
+-- pyproject.toml
```

## License

MIT. See [`LICENSE`](LICENSE).
