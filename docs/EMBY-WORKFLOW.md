# Emby Workflow

> How to drive Translarr from inside Emby. v0.2 plugin surfaces, the tag-based opt-in mechanism, the one-off form, and the honest answer to "why no right-click menu?"

For the C# build details, see [`plugins/emby/README.md`](../plugins/emby/README.md). For the server install, see [`INSTALL.md`](INSTALL.md).

## Quick start

1. Install the plugin (`Translarr.dll` into `<emby-config>/plugins/`, restart Emby).
2. Tag the item(s) you want translated with `translarr_translate`.
3. Dashboard > Scheduled Tasks > "Translarr: Batch translate library" > Run Now.

That's it. The new `.srt` lands next to the source media within a few minutes per item.

## Install the plugin

1. Download `Translarr.dll` from the [GitHub releases page](https://github.com/jesposito/translarr/releases) (live after the first v0.2 release).
2. Place it in `<emby-config>/plugins/` — on most installs that's `/var/lib/emby/plugins/` or wherever your Emby `programData` lives.
3. Restart the Emby server.
4. Open the web UI: Dashboard > Plugins > Translarr. If you see it in the list, you're good.

The plugin is a thin client. It does no translation itself — it calls the Translarr Python server over HTTP. You need that server running first. See [`INSTALL.md`](INSTALL.md) for the Docker compose setup.

## Configure the plugin

![Plugin settings page](screenshots/plugin-settings.png)

Dashboard > Plugins > Translarr opens the settings page. Fill in:

- **Server URL** — where the Translarr server is reachable from Emby.
  - Both in the same Docker network: `http://translarr:9000`
  - Emby bare-metal, Translarr in Docker: `http://<host>:9100`
- **Target language** — ISO 639-1 (`en`, `es`, `de`, `ja`, `fr`, ...). Defaults to `en`.
- **Webhook secret** — must match the server's `WEBHOOK_SECRET` env var. Leave blank if the server isn't using one.

Click **Test Connection** to confirm the plugin can reach the server. It hits `/health` and reports the version it sees.

## Tag-based opt-in (recommended)

Translarr never auto-translates everything in your library. You opt items in by tagging them.

Add an Emby tag named `translarr_translate`:

- **Single movie:** open the movie > Edit Metadata > Tags > add `translarr_translate` > Save.
- **Whole series (every episode):** tag the series itself. The scheduled task walks down to episodes automatically.
- **Stop translating:** remove the tag. Already-translated `.srt` files stay where they are — removing the tag just stops future runs from queueing more work.

Then either:

- Let the scheduled task run on its default schedule (4 AM local, configurable in Dashboard > Scheduled Tasks), or
- Trigger it manually: Dashboard > Scheduled Tasks > **Translarr: Batch translate library** > Run Now.

The task walks every tagged item, skips ones that already have a `.<lang>.translarr.srt` next to them, and enqueues the rest.

## One-off translate (skip the tag)

For a single item with no tagging:

1. Open the item in Emby and copy its **Item ID** from the URL bar — the GUID-looking string after `/item/`.
2. Dashboard > Plugins > Translarr > **Translate by Item ID** form.
3. Paste the ID, pick a target language, click **Translate**.
4. A job ID is shown immediately. The translation runs on the Translarr server; the new `.srt` lands next to the source file within ~5 minutes (faster for short videos).

You can check job state via the server's `/jobs/{id}` endpoint or just watch the file appear in Emby once the library refresh fires.

## Why no right-click context menu?

Honest answer: Emby's plugin SDK doesn't allow it.

From Luke (Emby Team) on the official forums:

> "It's currently not possible for a plugin to do this."

Emby plugins are server-side data manipulation only. There's no extension point for injecting buttons, context-menu items, or per-item action UI into the web frontend. The v0.1.25 spike (2026-05-14) confirmed this directly with the Emby team before locking in the v0.2 design.

This is why Translarr exposes three explicit surfaces (scheduled task, settings page, REST controller) instead of a slick right-click.

**v0.6.5** ships a polished standalone Translarr web UI at `:9100/` with rich per-item controls, search, coverage matrix, and live job progress. That UI is free from Emby's plugin constraints because it isn't a plugin.

## Where do translations land?

Translated subtitle files land next to the source media:

```
/movies/Movie (2025)/Movie (2025).mkv
/movies/Movie (2025)/Movie (2025).en.translarr.srt   ← new
```

The `.translarr.srt` infix is deliberate: it guarantees Translarr never overwrites a Bazarr fetch, a human-authored subtitle, or an embedded-extraction sub that might use the bare `.<lang>.srt` pattern. If you later download an English fansub, both files coexist and Emby shows both subtitle tracks in the player.

Once the file is written, the plugin triggers a library refresh on that specific Emby item. The new subtitle track appears in the player within seconds — no full library scan required.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Plugin settings page shows "Server unreachable" | Wrong URL or network gap | Verify Server URL. If Emby is in Docker, use the container name (`http://translarr:9000`), not `localhost`. |
| Scheduled task runs but no items translated | No items have the tag | Tag at least one movie or series with `translarr_translate`. |
| Job state stuck in `queued` | Worker pool not running on the server | Check the translarr container logs for `lifespan_startup workers=N`. If N is 0 or the line is missing, the server didn't start its workers. |
| Translation finishes but Emby doesn't show the new subtitle | Library refresh on the item didn't fire | Trigger a full library scan, or restart playback on the item. |
| Error: `no_source_subtitles` | The media file has no embedded subtitle tracks | Not supported in v0.2. v0.8a adds provider fetch; v0.9 adds Whisper-from-audio. |
| Error: `cost_cap_exceeded` | Daily or per-job spend cap hit | Raise `MAX_COST_CENTS_PER_DAY` / `MAX_COST_CENTS_PER_JOB` on the server, or wait for the daily reset. |

Server-side logs are the source of truth. The plugin only shows what the server told it.

## REST API for automation

If you want to drive Translarr from outside Emby — cron scripts, Sonarr/Radarr Connect webhooks, ad-hoc shell loops — talk to the Python server directly. It's simpler than going through the plugin.

```bash
curl -X POST http://<translarr-host>:9100/translate \
  -H "Content-Type: application/json" \
  -d '{"media_path":"Movies/X/Y.mkv","target_lang":"en"}'
```

See [the main README](../README.md#wiring-into-the-arr-stack) for the full Sonarr/Radarr Connect tag-based wiring.

The Emby plugin's REST controller at `POST /Translarr/Translate` (body `{"itemId":"X","targetLang":"en"}`) is a convenience layer that resolves Emby item IDs to file paths — useful when you have an item ID handy but not the path. For everything else, the Python `/translate` endpoint is more direct.

## What v0.2 doesn't do (yet)

- **No per-item right-click menu.** Emby SDK constraint — see the "Why no right-click context menu?" section above.
- **No real-time progress in the Emby UI.** The scheduled task page shows batch progress; one-off translates via the settings form return immediately with a job ID. Poll the server's `/jobs/{id}` endpoint for live state.
- **No per-show flag editing in the plugin.** v0.2's only opt-in mechanism is the Emby tag. The per-show settings UI lives in v0.4+ once the server-side `series_flags` table is wired.
- **No bulk re-translate of already-translated items.** The scheduled task skips items that already have a matching `.translarr.srt`. Delete the file (or pass `force=true` via REST) to retry.

For these gaps and the wider plan, see the roadmap in the main [README](../README.md#roadmap).
