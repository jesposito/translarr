# Translarr Competitive Analysis & Product Audit

> Research session 2026-05-15. Scoured GitHub (20+ repos) and Reddit
> (r/selfhosted, r/LocalLLaMA, r/JellyfinCommunity) for subtitle
> translation tools, user pain points, and feature gaps.

## The field

| Tool | Stars | What it does | Gap Translarr fills |
|------|-------|-------------|-------------------|
| **Lingarr** | 775 | C#/.NET subtitle translator. 12 LLM/translation providers. Radarr/Sonarr webhooks. Emby/Jellyfin/Plex library refresh. Docker Hub + GHCR images. | Requires MySQL/Postgres. No reading-rate adaptation. No cost estimation. No presets. Webhook-only (no subtitle picker integration). |
| **Sublarr** | 14 | Python/React subtitle *manager* (download, edit, sync, translate). 22 subtitle providers. Waveform editor. Post-processing pipeline. LLM translation is beta. | Sublarr is a *fetcher+editor* that happens to translate. Translarr is a *focused translator*. Sublarr's LLM translation is "experimental, not production-ready." |
| **VideoLingo** | 17K | Netflix-level subtitle cutting + translation + dubbing pipeline. Chinese-focused. Desktop app, not self-hosted. | Not self-hosted, not arr-integrated. One-off video processing, not library-scale. |
| **subtitle-translator-electron** | 1.7K | Desktop Electron app for translating SRT/ASS via ChatGPT. Single-file workflow. | Desktop-only, no automation, no arr integration. |
| **AISubtitle / llm-subtrans / others** | 200-400 each | CLI/desktop tools for single-file subtitle translation. | None are self-hosted, none have arr integration, none have cost control. |

## What users actually want (from Reddit issues + comments)

### P0 — Must-have for launch
1. **"Just works" with zero config** — Lingarr users repeatedly complain about path mapping, DB setup, subtitle detection failures. Translarr's single-binary + SQLite + `.env` is better.
2. **Visible progress during translation** — Lingarr's #1 complaint: "stuck in progress, can't see what's happening." Translarr's dashboard + job detail page solves this.
3. **Cost transparency** — Users are scared of LLM API bills. Preflight estimates + daily cap + per-job cap is unique to Translarr.
4. **Docker Hub image + multi-arch** — Lingarr has this. We don't yet. Users won't build from source.
5. **Sonarr/Radarr tag-based opt-in** — Lingarr requires path mapping + language profile config. Tag-based is simpler.
6. **Emby subtitle picker integration** — The ISubtitleProvider flow (click "Translate to EN" in Emby's subtitle modal) is unique to Translarr. No competitor has this.

### P1 — Strong differentiators
7. **Reading-rate adaptation** — Nobody else does this. Automatically splits fast subtitle lines to be readable. Real quality improvement.
8. **Context-aware batch translation** — Lingarr added this in 0.9.7 but it's confusing to configure. Translarr has it on by default.
9. **Translation presets** — "Quick & Cheap / Balanced / Best Quality / Local & Free" — nobody else has this. Solves the "I don't know which model to pick" problem.
10. **Push notifications (ntfy)** — Know when translations finish without checking the dashboard.
11. **ASS/SSA style preservation** — Lingarr and others lose formatting tags. We pass them through.

### P2 — Nice-to-haves that seal the deal
12. **Web UI dashboard** — Job history, stats, cost tracking. Lingarr has a UI but it's basic.
13. **Item-specific library refresh** — Full library scan is slow. Item-specific is instant. Lingarr does this.
14. **DeepSeek / Gemini providers** — Reddit loves DeepSeek for subtitle translation ($0.35/episode). OpenAI-compatible API means easy to add.
15. **File browser / library coverage view** — See which files have translations, which don't. One-click translate from the UI.
16. **Per-series language overrides** — "Anime → translate Japanese to English. Spanish movies → translate Spanish to English."

## Emby plugin — is it worth pursuing?

**Yes, but deprioritize the plugin DLL distribution.** Here's why:

### Current state
- The C# Emby plugin works (ISubtitleProvider → POST /translate → poll → fetch SRT)
- Users must manually copy the DLL to `/config/plugins/` on the Emby container
- Emby doesn't have a public plugin marketplace like Jellyfin

### Recommended approach
1. **Don't ship the plugin yet.** The webhook-only flow (Emby → webhook → Translarr → translate → library refresh) works without any plugin. Users just enable the Emby webhook notification.
2. **Ship the plugin as a downloadable DLL** on GitHub Releases when it's polished. Add a docs page: "Copy Translarr.dll to your Emby plugin folder, configure Server URL."
3. **Jellyfin plugin** is higher ROI — Jellyfin has a public plugin repository and a straightforward plugin install flow. One PR to jellyfin/jellyfin-plugin-template gets it into the catalog.

## Unraid template — what it needs

The template is already solid. Changes needed:

### Template fixes
- **Port default**: 9100 is fine (avoids conflicting with common ports)
- **WebUI URL**: Should be `http://[IP]:[PORT:9000]/` (not 9100 — the internal port)
- **Docker image**: Must point to a real registry (`ghcr.io/jesposito/translarr:latest` or Docker Hub)
- **Icon**: Needs an actual icon file (SVG or PNG, arr-style)
- **Overview**: Should mention "zero-config presets" and "cost estimator"
- **Category**: `MediaApp:Other` is correct

### What users configure on first install
1. API key (Anthropic or OpenAI)
2. Media library path (volume mapping)
3. Target language (default: en)
4. That's it — presets handle the rest

### What should NOT be in the template (advanced only)
- Webhook secret, Emby/Jellyfin keys, ntfy URL — these go in the Settings UI after install
- Max concurrent, reading rate, context window — presets handle these

## Docker image — the real blocker

**No Docker Hub / GHCR image = no Unraid users.** This is the #1 priority for launch.

Options:
1. **GitHub Actions → GHCR** (free, automatic on tag push) — recommended
2. **GitHub Actions → Docker Hub** (free for public repos)
3. **Both** — Lingarr publishes to both registries

Multi-arch (amd64 + arm64) is table stakes. Unraid is amd64, but Raspberry Pi / ARM NAS users need arm64.

## Icon — arr-style

Translarr needs a clean, recognizable icon that fits the arr family:
- Simple shape on solid background
- Translation theme: speech bubble, language symbol, or subtitle lines
- Colors: pick a distinctive but arr-harmonious palette
- SVG source + 72x72 PNG for Unraid CA template

## Updated priority list

| Priority | Item | Impact | Effort |
|----------|------|--------|--------|
| **P0** | Docker image (GHCR + Docker Hub, multi-arch) | Adoption blocker | Medium (CI pipeline) |
| **P0** | Icon (arr-style SVG + PNG) | Visual identity | Small (design) |
| **P0** | Fix Unraid template (port, image URL, icon) | Adoption | Small |
| **P1** | DeepSeek provider (OpenAI-compatible) | Cost advantage, Reddit demand | Small |
| **P1** | Item-specific Emby/Jellyfin library refresh | Speed, user experience | Medium |
| **P1** | README audit (roadmap, test count, features) | Credibility | Small |
| **P1** | Jellyfin plugin (public repo) | Jellyfin users can discover it | Medium |
| **P2** | File browser + library coverage view | "No-brainer" differentiator | Large |
| **P2** | Per-series language overrides | Power users | Medium |
| **P2** | Plex webhook support | Plex users | Small |
| **P3** | Glossary persistence | Translation quality | Medium |
| **P3** | Emby plugin DLL on GitHub Releases | Emby power users | Small |
