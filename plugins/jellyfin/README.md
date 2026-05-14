# Translarr Jellyfin Plugin

> Status: **v0.3 milestone** — not yet built.

Companion to the Emby plugin one directory over. Jellyfin's plugin API diverged from Emby's around 2019 — a 1:1 port doesn't work, but the bulk of the C# logic (REST controllers, scheduled tasks, settings models) ports cleanly. JS for the UI is mostly portable too.

## Planned features

Same as the Emby plugin: context-menu translation requests, per-show auto-translate flags, scheduled "ensure languages" task, toast notifications.

## Build (when implemented)

```bash
cd plugins/jellyfin
dotnet build --configuration Release
```

Drop the DLL into `<jellyfin-config>/plugins/Translarr/`.

## Submission to Jellyfin Plugin Catalog

The plan is to submit to the official catalog once v1.0 ships. The catalog requires:

- Plugin manifest in `manifest.json`
- Signed GitHub release with the `.zip` and a `meta.json`
- PR to `jellyfin/jellyfin-plugin-manifest` adding the manifest URL
