# Translarr Emby Plugin

> Status: **v0.2 milestone** — not yet built.

This directory will hold the C# Emby plugin that adds in-app subtitle translation requests. It will be a thin client that calls the Translarr server (this repo's `server/`) over HTTP.

## Planned features

- **Context menu on shows / seasons / episodes / movies:** "Translate subtitles → English / Spanish / ..."
- **Per-show settings:** "Auto-translate every new episode of this show to English."
- **Scheduled task:** "Ensure every monitored item has subs in [en, es]" — runs overnight using local Ollama.
- **Toast notifications:** "✓ English subs ready for S01E03."
- **Library refresh trigger** after a sub file is written.

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
