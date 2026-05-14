using System;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Model.Serialization;
using Translarr.Emby.Configuration;

namespace Translarr.Emby
{
    /// <summary>
    /// Entry point for the Translarr Emby plugin.
    ///
    /// Per the v0.1.25 spike, Emby's plugin SDK does NOT support
    /// context-menu items or per-item button injection. User-facing
    /// entry points are therefore:
    ///   1. Emby Dashboard > Scheduled Tasks  (TR-7p7.2.4)
    ///   2. Plugin Settings page              (TR-7p7.2.2)
    ///   3. REST API at /Translarr/Translate  (TR-7p7.2.3)
    ///
    /// This file is the v0.2 bootstrap (TR-7p7.2.1) — it wires the
    /// plugin into Emby's loader but does not yet implement any of
    /// the surfaces above.
    /// </summary>
    public class Plugin : BasePlugin<PluginConfiguration>
    {
        public Plugin(IApplicationPaths applicationPaths, IXmlSerializer xmlSerializer)
            : base(applicationPaths, xmlSerializer)
        {
            Instance = this;
        }

        /// <summary>
        /// Static singleton handle for controllers / scheduled tasks
        /// to read configuration without a DI lookup.
        /// </summary>
        public static Plugin? Instance { get; private set; }

        public override string Name => "Translarr";

        public override string Description =>
            "Thin Emby plugin that calls the Translarr server for subtitle translation. " +
            "Exposes a scheduled task, a settings page, and a REST endpoint. " +
            "Does not translate anything itself.";

        /// <summary>
        /// Stable plugin identifier. Generated 2026-05-14 for v0.2.
        /// Never change this — Emby uses it to persist per-plugin state.
        /// </summary>
        public override Guid Id => new Guid("b2de90e4-a7b2-427f-b562-ca58373e3627");
    }
}
