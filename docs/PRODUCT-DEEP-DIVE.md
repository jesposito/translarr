# Translarr Product Deep Dive

> Honest audit of where we are today, what's missing, and what it would take
> to make Translarr world-class for self-hosted media enthusiasts.
> Date: 2026-05-15 · After v0.1.5 ship + successful E2E test.

---

## 1. What Translarr does today (the good)

The core pipeline **works end-to-end**. We proved it:

- Person of Interest S5E12: 737 Chinese subtitle events → 959 English events
  after reading-rate adaptation, $0.12, ~2.5 minutes, no human intervention.
- Sonarr/Radarr tag-based opt-in webhooks are wired and tested.
- Emby/Jellyfin library refresh fires automatically after translation.
- On-demand playback.start translation (opt-in) is live.
- SQLite-backed persistent queue survives restarts.
- Cost guards (daily cap, per-job cap, timeout) work at batch boundaries.
- ntfy push notifications fire on success/failure/skip.
- 21 live-mutable settings with inline help, auto-save, and revert.
- 165 tests, all green.

The hard technical problem is **solved**. The remaining work is product polish,
integration depth, and the "first 5 minutes" experience.

---

## 2. The First 5 Minutes Problem

Right now a new user must:

1. `git clone` + `docker compose up` (or add to Unraid manually)
2. Edit `.env` with API key, media root path
3. Figure out Docker networking between containers
4. Set up Radarr/Sonarr Connect webhooks by hand (URL, headers, triggers)
5. Tag items one-by-one in Radarr/Sonarr
6. Hope the paths align between containers

**This is 30-45 minutes of friction before the first translation.**
For a tool whose entire value prop is "it just works," this is too much.

### What world-class looks like

**Unraid Community Applications template** (`translarr.xml`):
- One-click install from the Unraid CA app store
- Pre-filled with sensible defaults: `MEDIA_ROOT=/media`, `TRANSLARR_DATA_DIR=/config`
- Volume mappings auto-proposed: `/mnt/user/your-media:/media`
- WebUI button opens `http://[unraid-ip]:9100`

**Setup wizard** (first-run experience):
1. "Choose your LLM" — Anthropic / OpenAI / Ollama. Paste API key. Test connection.
2. "Where's your media?" — Show the detected mount, verify ffprobe can read a file.
3. "Connect your arr stack" — Show the exact Radarr/Sonarr webhook URL to paste.
   Offer to test it (hit the Test button on the Radarr webhook setup page).
4. "Set your budget" — $X/day, $Y/job. Default $10/$5. Show what that buys
   (~18 episodes/day at Sonnet pricing).
5. Done. Show a "Translate something now" box with a file browser.

### What's missing to get there

| Need | Status | Effort |
|------|--------|--------|
| Unraid CA template (`translarr.xml`) | Missing | Small |
| Container icon (256×256 PNG) | Missing | Small |
| Setup wizard (first-run flow) | Missing | Medium |
| Auto-detect media mount + verify read | Missing | Small |
| "Copy webhook URL" clipboard helper in UI | Missing | Small |
| Plex webhook support | Missing | Medium |

---

## 3. Integration Depth: arr Stack

### Sonarr/Radarr — what we have vs what users expect

**Today:**
- Tag-based opt-in works. You tag a movie `radarr_translate`, it auto-translates on import.
- That's the **reactive** path. It only fires on new imports, not on existing library items.

**What's missing:**

1. **Bulk scan endpoint** — "Scan my entire Sonarr library and translate everything
   that doesn't have English subs yet." The Emby plugin has a scheduled task for this,
   but there's no equivalent for Sonarr/Radarr users. This is the #1 ask for any
   subtitle tool. Implementation: a `/scan` endpoint that queries Sonarr/Radarr API
   for all series/episodes, checks for existing `.en.translarr.srt`, and enqueues
   the rest. The user controls scope via the same tag mechanism.

2. **Per-series language override** — An anime fan wants Japanese→English for most
   shows but Spanish→English for telenovelas. Today the only knob is `TARGET_LANG`
   globally. Need a per-series or per-path-prefix override table. This is v0.5
   territory but the data model should support it now.

3. **Radarr/Sonarr API integration** (not just webhooks) — Today we're passive;
   we wait for the webhook to tell us a file landed. We could also be active:
   - Query Radarr for all movies with a specific tag that lack English subs
   - Pull the file path directly from Radarr's API
   - This lets us do "translate my tagged library" without a full scan

4. **Import gracefully** — When Sonarr upgrades a file (better quality), the old
   `.en.translarr.srt` becomes orphaned. We should detect this and either re-translate
   or warn. The Upgrade event is already handled by the webhook, but we don't clean up
   the old SRT.

5. **Tag sync** — Show in the Translarr UI which items are tagged for translation
   in Radarr/Sonarr. Let users add/remove tags from the Translarr UI. This turns
   Translarr into the control plane, not just a passive listener.

### What "perfect" looks like

The user tags one series in Sonarr. That's it. Translarr:
- Translates every new episode as it imports (webhook — works today)
- Backfills all existing episodes (scan — missing)
- Re-translates on quality upgrade (webhook — works today, but doesn't clean old SRT)
- Shows coverage: "12/13 episodes translated, 1 queued" (missing)
- Lets the user set per-series overrides: target lang, reading rate, model (missing)

---

## 4. Integration Depth: Media Servers

### Emby

**Today:** Library refresh after translation (full scan). On-demand playback.start.
**Missing:**

1. **Item-specific refresh** — We trigger a full library scan. Emby supports
   `POST /emby/Items/{id}/Refresh` which is instant. We need the Emby item ID
   alongside the file path. Options:
   - After writing the SRT, search Emby's API by file path to get the item ID
   - Have the Emby plugin pass the item ID with the webhook
   - Item-specific refresh means the new subtitle appears in <2 seconds, not
     after a full scan (which can take minutes on large libraries)

2. **Subtitle provider plugin** (the `ISubtitleProvider` interface) — The Emby plugin
   has a scaffold for this (`TranslarrSubtitleProvider.cs`). When Emby's player
   requests subtitle tracks for an item, this provider can check if Translarr
   has a translation and return it as a virtual subtitle track — even before
   the SRT file lands on disk. This is the "seamless" experience: user presses
   play, subtitle track is already there.

3. **Emby user settings** — Per-user language preference. The plugin should read
   the Emby user's subtitle language preference and use that as the target lang
   for on-demand translations.

### Jellyfin

**Today:** Basic webhook. Missing everything Emby has, plus:
- Jellyfin plugin (v0.3 on roadmap)
- Same item-specific refresh gap
- Same subtitle provider concept

### Plex (not even started)

Plex is ~40% of the self-hosted media server market. Ignoring it is leaving
users on the table. The integration surface is:
- Plex webhook (similar shape to Emby/Jellyfin)
- Plex Metadata API for library scan
- No plugin system comparable to Emby/Jellyfin, so all interaction is API-only

**Minimum viable Plex support:**
- Webhook endpoint at `/webhooks/plex` (Plex sends different payload shapes)
- The user configures a Plex webhook pointing at Translarr
- After translation, call Plex's `/library/sections/{id}/refresh?path=...` to
  scan just the affected directory

---

## 5. The Translation Quality Gap

### What we do well
- Sliding context window (10 prior lines) keeps pronouns consistent
- Reading-rate adaptation prevents unreadable subtitles
- ASS/SSA style tag passthrough preserves formatting
- Output count reconciliation handles LLM over/undercount

### What's missing

1. **Source language detection** — Today the user must specify `source_lang`
   or we pass "auto" to the LLM. We should auto-detect from the ffprobe
   track metadata (which already reports language tags). This is low-hanging
   fruit — the track's language tag is already in `TrackInfo.language`.

2. **Glossary support** — The `glossary_id` field exists on `TranslateRequest`
   but nothing reads or writes it. For anime, this is critical: character names,
   attack names, honorifics. Without it, the LLM will transliterate differently
   in each batch and the viewer sees "Tanjiro" in one scene and "Kamado" in the next.
   
   **Minimum viable:** Let the user upload a simple `name_eng = name_source` mapping
   per series. The LLM prompt already has a `glossary` parameter. Wire it.

3. **Multi-track language awareness** — Person of Interest had 3 Chinese tracks
   (Simplified, Traditional, mixed). We blindly pick the first non-target one.
   We should prefer: SubRip > ASS, track with most events, track with a language
   tag that's NOT "mixed." Show the user which track was picked and let them
   override via the UI.

4. **Translation quality indicator** — After a job completes, show a rough
   quality metric: "737 source lines → 959 output lines (30% split rate).
   2 lines padded (LLM undercount). $0.12." This gives the user confidence
   without requiring the full critic pass (v0.4).

5. **Batch translation quality** — For a 44-minute TV episode with 737 lines,
   we send ~25 batches. A name introduced in batch 1 might be translated
   differently in batch 20 because the 10-line context window doesn't reach
   back far enough. Solutions:
   - Extract a name list from the full subtitle before batching, pass it as
     a glossary to every batch
   - Increase context window (costs more tokens but improves consistency)
   - Both are cheap to implement and would meaningfully improve quality

---

## 6. Cost Controls: What's Missing

### Today
- Daily cap ($X/day, resets UTC midnight)
- Per-job cap ($X/job, kills mid-batch)
- Job timeout (wall-clock seconds)
- Token cost estimation table

### Missing

1. **Cost estimate before translation** — Show the user "This file has 737 subtitle
   events. Estimated cost: $0.12 at Sonnet 4.6, $0.03 at Haiku 4.5, $0 at Ollama."
   Before the first dollar is spent. This is the single biggest trust builder.

2. **Monthly budget** — Daily cap resets every day, which means a runaway could
   spend $10/day × 30 days = $300/month. Add a monthly budget that's harder to
   blow past. Show it on the dashboard: "$3.24 of $50.00 monthly budget used."

3. **Cost alerting** — ntfy on success/failure is nice, but "You've spent 80% of
   your daily budget" is more actionable. Threshold alerts: 50%, 80%, 100%.

4. **Per-model cost visibility** — The user switches from Sonnet to Haiku to save
   money. Show them the actual savings: "Haiku: $0.03/job avg vs Sonnet: $0.12/job avg.
   You've saved $2.16 this week by using Haiku."

5. **Queue cost preview** — When 5 jobs are queued, show "Estimated queue cost:
   $0.60 (5 jobs × $0.12 avg)." Let the user decide if they want to proceed.

---

## 7. The Unraid Experience

Unraid users are the primary audience. They expect:

1. **Community Applications template** — One-click install, not `docker compose`.
   The template specifies container name, ports, volumes, env vars, and a WebUI URL.
   This is table stakes for any Unraid app.

2. **Appdata config directory** — Unraid convention is `/mnt/user/appdata/<appname>/`.
   The SQLite DB, overrides, and any persistent state should live here.
   `TRANSLARR_DATA_DIR=/config` where `/config` maps to the appdata dir.

3. **Clean Docker log output** — Unraid shows container logs in the web UI.
   Our structured log output is fine for developers but noisy for users.
   Consider a human-readable log mode for production.

4. **No manual Docker commands** — The user should never need to `docker exec`
   or `docker build`. Everything should be configurable through the web UI
   (which we have now — the editable Settings page).

5. **GPU passthrough for Ollama** — Unraid users with Nvidia GPUs want to run
   Ollama with CUDA acceleration. The compose file should include an Ollama
   service with GPU passthrough as a commented option.

### What's missing specifically

| Item | Impact |
|------|--------|
| Unraid CA template XML | Users can't discover/install without manual Docker |
| Container icon | Looks unpolished in Unraid Docker list |
| `UNRAID_WEBUI` env var in template | WebUI button in Unraid doesn't work |
| `HOST_CONTAINERFORMAT` support | Unraid-specific integration niceties |
| Pre-built Docker Hub image | Users must build from source today |
| GPU Ollama compose option | Free translation needs GPU for acceptable speed |

---

## 8. Settings: Setting People Up for Success

### The problem with 21 settings

A new user opens Settings and sees 21 fields across 6 sections. Most are
self-explanatory with the descriptions we added, but several are confusing
without context:

- **Reading rate CPS** — "17 chars/sec" means nothing to most people. Should
  show: "How fast subtitles appear on screen. 17 is comfortable for English.
  Chinese/Japanese: 8-10. German: 20."
- **Context window lines** — "10 lines of prior context" → "How many previously
  translated lines the AI sees for consistency. Higher = better names/pronouns,
  more tokens spent."
- **Max concurrent** — "Number of translation jobs to run in parallel" →
  "How many files to translate at once. 2 is safe. Increase if your API can
  handle it and you want faster batch processing."

### Recommended presets

Instead of exposing every knob, offer **presets** the user can start from:

| Preset | Target | Reading Rate | Context | Concurrency | Model |
|--------|--------|-------------|---------|-------------|-------|
| **Quick & Cheap** | en | 17 | 5 | 1 | Haiku 4.5 |
| **Balanced** | en | 17 | 10 | 2 | Sonnet 4.6 |
| **Best Quality** | en | 15 | 20 | 2 | Sonnet 4.6 |
| **Local & Free** | en | 17 | 10 | 1 | Ollama qwen3:14b |
| **Anime (JP→EN)** | en | 12 | 15 | 1 | Sonnet 4.6 |

One click loads the preset, user can tweak from there.

### Settings UX improvements

1. **Group by use case, not by implementation** — Instead of "LLM Provider",
   "Translation Defaults", "Cost Guards", group by:
   - "Getting Started" (LLM choice, API key, media root)
   - "What to Translate" (target lang, source lang, arr tags)
   - "Translation Quality" (reading rate, context window, model)
   - "Budget" (daily cap, per-job cap, timeout)
   - "Notifications" (ntfy, push categories)
   - "Advanced" (everything else — concurrency, webhook secret, etc.)

2. **Cost preview inline** — When the user picks a model, show the estimated
   cost per episode/movie right there: "~$0.12/episode, ~$0.30/movie at Sonnet 4.6"

3. **Validation feedback** — When the user types an API key, immediately test
   it and show "✓ Connected to Anthropic" or "✗ Invalid key". Don't make them
   save and try a translation to find out.

---

## 9. What Would Delight Users (Ordered by Impact)

### Tier 1: "How did I live without this?"

1. **File browser + one-click translate** — Browse your library from the Translarr
   UI. See which files have foreign subs, which are missing English subs, and
   translate with one click. No need to copy/paste paths or use curl.

2. **Library coverage view** — A matrix showing: "TV Shows: 87% covered with
   English subs. Movies: 45% covered. 23 items need translation."
   Click any gap to translate. This is the killer feature for people with
   large libraries.

3. **Translation preview** — Before committing $0.12, show a 10-line preview
   of what the translation would look like. "Here's what you'll get. Translate
   the full file?" Builds trust, especially for new users.

### Tier 2: "This is really well thought out"

4. **Auto-detect and suggest** — On startup, scan the media root. Find files
   with foreign subs and no English subs. Show a notification: "Found 47 items
   that could be translated. Review them?"

5. **Smart defaults by media type** — Anime gets lower reading rate (Japanese
   names are longer), documentaries get higher context window, movies get
   higher per-job cap. Auto-detect media type from path/Emby metadata.

6. **Post-translation quality check** — After translating, extract a random
   5-line sample and show it to the user. "Looks good?" / "Re-translate with
   different settings?" This is a lighter version of the v0.4 critic pass.

### Tier 3: "Polish that shows care"

7. **Keyboard shortcuts** — `T` to translate selected item, `J`/`K` to navigate
   jobs, `R` to retry a failed job.

8. **Dark/light auto-switch** — Respects system preference (we have the CSS
   variables, need to wire the detection).

9. **Translation history per file** — "This file was translated 3 times.
   First on May 10 (Haiku, $0.03), re-translated May 12 (Sonnet, $0.12).
   View diff." Let users see how their settings affect quality.

10. **Batch operations** — Select 10 items → "Translate all" or "Re-translate
    all with new settings." Queue them all at once.

---

## 10. The Missing "Control Plane" Vision

Right now Translarr is a **passive worker** — it waits for webhooks and does
what it's told. The next evolution is making it the **active control plane**
for subtitle translation in your media stack.

### The Translarr Dashboard should answer:

- "What's the subtitle coverage of my library?" (per-show, per-language)
- "What's my translation spend this week/month?" (per-show, per-model)
- "Which items need attention?" (failed translations, low-quality results)
- "What's the queue doing right now?" (live progress, ETA, cost)

### The API should support:

- `GET /library/coverage` — Returns coverage stats per directory/show
- `GET /library/items?filter=needs_translation` — Items missing target subs
- `POST /library/scan` — Trigger a scan of the media root
- `GET /library/items/{path}/tracks` — Show all subtitle tracks for an item
- `POST /library/items/{path}/translate` — Translate a specific item
- `DELETE /library/items/{path}/translation` — Remove a translation

This turns Translarr from "a webhook listener" into "the place you go to
understand and manage your subtitle situation."

---

## 11. Technical Debt & Risk

| Area | Issue | Risk |
|------|-------|------|
| Path mapping | Container paths vs host paths vs Emby paths — no normalization | Users confused about which path to use where |
| Error messages | Raw Python tracebacks in job.error | Users see ffmpeg noise, not actionable info |
| Library refresh | Full scan instead of item-specific | Slow on large libraries, may not pick up new file |
| No Docker Hub image | Must build from source | Friction for Unraid users, can't pin versions |
| No versioned releases | No git tags, no GitHub releases | Users can't upgrade safely, can't roll back |
| No backup/restore | SQLite DB is a single file, no export | Settings overrides lost if DB corrupted |
| No auth on the API | Any LAN user can translate/modify settings | Fine for home LAN, dangerous if exposed publicly |
| No rate limiting | A misconfigured webhook could flood the queue | Queue fills up, costs spike |

---

## 12. Priority Roadmap (What to build next)

Ordered by **user impact × effort**:

### Immediate (next 2 sessions)

1. **Unraid CA template** + Docker Hub image — Removes the biggest adoption barrier
2. **Cost estimate before translation** — Trust builder, prevents surprises
3. **Source language auto-detection from track metadata** — One less thing to configure
4. **Error message cleanup** — Show "File not found" not 200 lines of ffmpeg output
5. **Pre-flight check endpoint** — `/preflight?path=...` returns track info + cost estimate

### Short-term (next 2 weeks)

6. **File browser UI** — Browse media, see tracks, one-click translate
7. **Library coverage view** — What % of your library has English subs
8. **Sonarr/Radarr bulk scan** — "Translate all tagged items" button
9. **Translation presets** — Quick & Cheap / Balanced / Best Quality / Local
10. **Item-specific Emby refresh** — Fast library update after translation

### Medium-term (next month)

11. **Plex webhook support** — 40% of the market
12. **Per-series language overrides** — Japanese→English for anime, Spanish→English for dramas
13. **Name extraction + glossary** — Better consistency across batches
14. **Monthly budget + cost alerting** — Financial safety net
15. **Translation preview** — 10-line sample before committing

### Long-term (v0.4+)

16. **Critic pass** — Second LLM call rates quality, re-generates bottom 5%
17. **Glossary editor UI** — Per-series name tables
18. **Subtitle provider fetch** — OpenSubtitles/Jimaku for the no-subs case
19. **Whisper fallback** — Audio→text when no subs exist at all
20. **Strategy chain** — Embedded → fetch → audio, automatic fallback

---

## 13. The One-Line Pitch

**Today:** "Translarr translates subtitle files in the wrong language to English
using AI, and drops the result next to your video."

**World-class:** "Translarr is the subtitle control plane for your media stack.
It knows what's in your library, what needs translation, translates it with
best-in-class quality and cost transparency, and makes the result instantly
available in your media player — all from a single dashboard."

The distance between those two sentences is about 6-8 weeks of focused work.
Every item in the priority roadmap above is a concrete step toward closing it.
