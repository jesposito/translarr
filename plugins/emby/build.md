# Building the Translarr Emby Plugin

> Bootstrap (TR-7p7.2.1). Subsequent tasks add the settings page UI, REST
> controller, and scheduled task. The build flow below stays the same.

## Prerequisites

The plugin builds with **.NET SDK 8 or 9** on Linux. The Emby plugin SDK
itself targets `netstandard2.0`, so either SDK is fine — netstandard2.0
is what every Emby 4.x plugin ships against, regardless of the SDK the
build host uses.

### Installing the SDK on Linux

The dev box already has .NET SDK 9.0.310 at `~/.dotnet/`. If you are on a
fresh machine, use Microsoft's official installer script (works on
Ubuntu, Debian, and Arch without touching apt repos):

```bash
curl -sSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh
chmod +x /tmp/dotnet-install.sh
/tmp/dotnet-install.sh --channel 8.0          # or --channel 9.0
export PATH="$HOME/.dotnet:$PATH"
dotnet --list-sdks
```

Add the `export PATH` line to your shell rc file to persist it.

The apt package `dotnet-sdk-8.0` works too on Ubuntu 22.04+, but the
installer script is the supported path on Unraid and Arch.

## Build

From this directory:

```bash
cd ~/dev/translarr/plugins/emby
dotnet restore
dotnet build --configuration Release
```

Output lands at:

```
bin/Release/netstandard2.0/Translarr.dll
```

The release build only needs that single DLL — Emby loads it directly,
no `.deps.json` or runtime config required for a netstandard2.0 plugin.

## Why netstandard2.0, not net8.0

The bootstrap task wording asked for `net8.0`, but the actual Emby plugin
SDK (`MediaBrowser.Server.Core` 4.9.x) only publishes a netstandard2.0
surface. Emby Server itself runs on .NET 8, but it loads plugins via the
netstandard2.0 contract so older plugins keep working. Building against
`net8.0` would fail to resolve the SDK package. Every active Emby plugin
on GitHub (SubZ, Jimaku, subbuzz, etc.) targets netstandard2.0 for the
same reason. The Jellyfin plugin in `../jellyfin/` will likely target
`net8.0` instead — Jellyfin's SDK does ship a net8 surface.

## Sideloading into a local Emby for dev testing

1. Stop Emby:
   ```bash
   # On Unraid (TheAnsible)
   docker stop emby
   # Or on a local Emby Server install
   sudo systemctl stop emby-server
   ```
2. Copy the DLL into Emby's plugins directory:
   ```bash
   # Unraid container path (host side)
   cp bin/Release/netstandard2.0/Translarr.dll /mnt/user/appdata/emby/plugins/

   # Local Emby Server on Linux
   cp bin/Release/netstandard2.0/Translarr.dll ~/.config/emby-server/plugins/
   ```
3. Start Emby again. The plugin appears under **Dashboard > Plugins**.
4. Tail the server log for load errors:
   ```bash
   docker logs -f emby 2>&1 | grep -i translarr
   ```

## CI

Not wired yet — TR-7p7.2.5 adds a GitHub Actions workflow that builds
the plugin on every PR and attaches the DLL to GitHub Releases. Until
then, build locally and copy by hand.
