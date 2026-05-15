# Translarr v0.1.6 Work Plan

> Session 2026-05-15-B. Picks the highest-impact items from the product deep dive
> and executes them in dependency order.

## Plan (7 items, ordered by dependency)

### P1. Source language auto-detection from track metadata
**Why:** Every translation today passes `source_lang="auto"` because we never
extract the track's language tag from ffprobe output. The data is already in
`TrackInfo.language` — we just don't use it.
**Scope:** Wire `track.language` through `pick_source_track` return → pipeline
→ LLM call. Show detected language in job metadata.
**Tests:** Unit test for auto-detection path, integration test that the LLM
receives the right source_lang.

### P2. Cost estimate endpoint (preflight)
**Why:** The #1 trust builder. Users need to know what a translation will cost
before committing money. We have all the data — track event count, model pricing.
**Scope:** `POST /preflight` endpoint that runs ffprobe, counts events, estimates
tokens, returns cost per model. No LLM call, no queue — read-only.
**Tests:** Endpoint test with a real media file mock.

### P3. Human-readable error messages
**Why:** Failed jobs show 200 lines of ffmpeg noise. Users see `RuntimeError:
ffmpeg extract failed: <wall of text>` instead of "File not found."
**Scope:** Map known error patterns to short, actionable messages in the worker.
Preserve full error in logs, surface the short version in job.error.
**Tests:** Test that each error class produces the right short message.

### P4. Translation presets
**Why:** 21 settings overwhelm new users. Presets give them a safe starting point
they can tweak from.
**Scope:** Add preset definitions in settings_store, a `POST /config/preset`
endpoint, and a preset selector in the Settings UI.
**Tests:** Test that applying a preset sets all the right fields.

### P5. Unraid Community Applications template
**Why:** Unraid users can't discover or install Translarr without manual Docker.
This is the single biggest adoption barrier.
**Scope:** Create `translarr.xml` CA template with correct volume mappings,
env vars, and WebUI URL. Add container icon.
**Tests:** Manual — validate against Unraid CA template spec.

### P6. Item-specific Emby/Jellyfin library refresh
**Why:** Full library scan is slow. Item-specific refresh is instant (<2s).
We need the Emby item ID. We can look it up via the Emby API after writing
the SRT by searching for the file path.
**Scope:** After writing SRT, query Emby `/Items?Path=` to get item ID,
then `POST /Items/{id}/Refresh`. Fall back to full scan if lookup fails.
**Tests:** Unit test with mocked Emby API responses.

### P7. File browser / library scan foundation
**Why:** Users can't see their library from Translarr. This is the foundation
for the coverage view and one-click translate.
**Scope:** `GET /browse?path=` endpoint that lists directories and media files
within MEDIA_ROOT. Detect subtitle tracks on hover (deferred). Show which
files have `.translarr.srt` already. Add a simple browse UI page.
**Tests:** Endpoint tests for path traversal guard, file listing.

---

## Dependency order

```
P1 (auto-detect) → independent, do first
P2 (preflight)   → independent, do second
P3 (error msg)   → independent, do third
P4 (presets)     → independent, do fourth
P5 (Unraid CA)   → independent, do fifth
P6 (item refresh)→ depends on Emby API key being configured
P7 (file browse) → depends on P2 (preflight shows track info)
```

Items P1-P4 are backend changes with UI follow-up. P5 is a template file.
P6-P7 are new features. All are scoped to ship in this session.
