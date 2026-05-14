# Translarr Emby Plugin

> Status: **v0.2 milestone** — not yet built.

This directory will hold the C# Emby plugin that adds in-app subtitle translation requests. It will be a thin client that calls the Translarr server (this repo's `server/`) over HTTP.

## Important: NO context-menu invocation

The v0.1.25 spike (2026-05-14) confirmed that Emby's plugin SDK does **not** allow plugins to add context-menu items, per-item action buttons, or any other UI injection into the Emby web frontend. Emby team statement: "It's currently not possible for a plugin to do this."

User-facing entry points are therefore:

1. **Emby Dashboard > Scheduled Tasks** — "Translarr: Batch translate library" appears here; user clicks "Run" or schedules nightly
2. **Plugin Settings page** — auto-generated from PluginConfiguration; lists series with per-show "auto-translate" checkboxes; manual "Translate this item by ID" form for one-offs
3. **REST API** — `POST /Translarr/Translate?itemId=X&targetLang=Y` callable from external automation (Sonarr/Radarr Connect webhooks, cron scripts, etc.)

The same constraint applies to v0.3 Jellyfin (Jellyfin inherited the same plugin architecture).

## Planned features

- **Scheduled task** (primary entry point): batch translate all monitored items or a filtered subset
- **Per-show settings:** "Auto-translate every new episode of this show to English" — stored in server-side `series_flags` table
- **Library refresh trigger** after a sub file is written (so new subs appear in player without manual scan)
- **REST controller** at `/Translarr/Translate` for external automation

## Architecture

The plugin contributes:

1. A REST API controller (`/Translarr/Translate?itemId=...&targetLang=en`) inside Emby.
2. JS injection that adds a "Translate" item to the `⋯` menu on every item type.
3. A settings page (`Web/translarrconfig.html`) under Plugin Settings.
4. A scheduled task implementing `IScheduledTask`.

The plugin never translates anything itself — it just collects the request and calls the Translarr server.

## Project layout (TBD)

```
plugins/emby/
├── Translarr.Emby.csproj
├── Plugin.cs
├── Configuration/
│   ├── PluginConfiguration.cs
│   └── configPage.html
├── Api/
│   └── TranslateController.cs
├── ScheduledTasks/
│   └── EnsureLanguagesTask.cs
└── Web/
    ├── contextmenu.js
    └── translarrconfig.html
```

## Building (when implemented)

```bash
cd plugins/emby
dotnet build --configuration Release
```

The resulting DLL ships to `<emby-config>/plugins/Translarr.dll`.

## Why C#?

Emby and Jellyfin plugins must be .NET DLLs that the server loads in-process. No way around it. The Jellyfin plugin (next directory over) shares ~95% of the code via a shared abstraction layer.
