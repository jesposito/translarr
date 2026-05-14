# Translarr — Project Instructions

> AI-powered subtitle translator for the arr stack. Drops translated `.srt` next to media on import.

## Stance — read before doing anything

**Free self-hosted tool. No launch deadline. Build the full vision.**

- Sequence work by dependency, not by "ship something soon."
- Never cut scope to hit an MVP date.
- Quality over speed. One well-tested feature beats three half-built ones.
- Mirrors the stance for the user's other long-horizon project at `~/dev/quillr`.

## Hard constraints

1. **No Bazarr integration.** Bazarr previously corrupted the user's Emby library. We expose a subgen-compatible `/asr` endpoint for opt-in compatibility with other tools, but Bazarr is not a supported consumer.
2. **No "MVP shortcuts."** Reorder around the dependency graph; never drop a planned feature to ship sooner.
3. **Deployment target is TheAnsible Unraid box.** Container runs on `ansiblenet` alongside Radarr/Sonarr.
4. **Secrets via env + Infisical, never hardcoded.**

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->


## Repository layout

```
translarr/
├── server/                # Python FastAPI brain (v0.1, shipping)
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   ├── llm/               # anthropic, openai, ollama providers + router
│   ├── subs/              # extract, parse, reading-rate adapter, pipeline
│   └── webhooks/          # radarr, sonarr, emby, jellyfin, security, queue
├── plugins/
│   ├── emby/              # v0.2 — C# Emby plugin (README only today)
│   └── jellyfin/          # v0.3 — C# Jellyfin plugin (README only today)
├── tests/                 # pytest
├── docs/                  # ARCHITECTURE.md, INSTALL.md
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

## Milestones

| Version | Scope | Status |
|---------|-------|--------|
| v0.1 | Server brain: webhooks, LLM router, sub pipeline, reading-rate, tests | Scaffolded 2026-05-14 |
| v0.2 | Emby plugin (C#): scheduled task + REST controller + settings page (NO context menu — Emby SDK constraint per v0.1.25 spike) | Next |
| v0.3 | Jellyfin plugin (C#): port of v0.2 | After v0.2 |
| v0.4 | Critic pass + audio-grounded correction + glossary persistence (SQLite) | After v0.3 |
| v0.5 | Persistent queue, per-show auto-translate flags | After v0.4 |
| v0.6 | Library-wide language fill scheduled task, cost dashboard | After v0.5 |
| v0.7 (contingent) | Audio-grounded correction (only if v0.4 critic telemetry justifies) | Conditional |
| v0.8a | Provider-fetch fallback: direct integrations OpenSubtitles/Jimaku/Animetosho (zero Bazarr) | After v0.6 |
| v0.8b | Optional Bazarr-as-fetch-proxy adapter (opt-in, for sandboxed Bazarr setups only) | After v0.8a |
| v0.9 | Whisper-from-audio fallback for truly-no-subs case | After v0.8 |
| v1.0 | Strategy chain endpoint — runs all fallbacks in priority order | After v0.8 + v0.9 |

## Build & Test

```bash
# Install
pip install -e ".[dev]"

# Run locally
uvicorn server.main:app --reload --port 9000

# Test + lint
pytest -v
ruff check server tests

# Docker
docker compose up -d
```

## Architecture Overview

Translarr is a single Python service that listens for events from the arr stack and produces translated subtitle files. The C# Emby / Jellyfin plugins (v0.2+) are thin clients that send the same kind of event the webhooks already accept.

Pipeline: webhook fires → queue dedup → ffprobe lists sub tracks → pick non-target-lang track → ffmpeg extract → pysubs2 parse → batch with sliding context → LLM router → reading-rate adapt → write `.srt` next to source media.

See `docs/ARCHITECTURE.md` for full details.

## LLM provider routing

Three providers via a single `Protocol`:

- **Anthropic** (default, `claude-sonnet-4-6`) — best translation quality
- **OpenAI** — alternate
- **Ollama** (e.g. `qwen3:14b`) — local, free, lower quality

Adding a new provider: implement `LLMProvider` protocol in `server/llm/<name>_provider.py`, register in `server/llm/router.py`.

## Subtitle pipeline invariants

- Output line count must equal input line count. Pad or truncate if the LLM returns mismatched output.
- ASS/SSA style tags pass through untranslated.
- Reading-rate split must redistribute duration to preserve the original span.
- Output filename pattern: `<basename>.<target_lang>.translated.srt` next to the source media.

## Conventions & Patterns

- Python 3.12+, type hints required, ruff for lint.
- All subprocess calls use `asyncio.create_subprocess_exec` with arg lists, never `shell=True`.
- Webhook payloads are typed via Pydantic where stable; raw dict for the parts that vary across consumer versions.
- Tests under `tests/`, mirror the `server/` package layout.
