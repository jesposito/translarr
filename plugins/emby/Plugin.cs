using System;
using System.Collections.Generic;
using MediaBrowser.Common.Configuration;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Model.Plugins;
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
    ///   1. Emby Dashboard &gt; Scheduled Tasks  — BatchTranslateTask
    ///      is auto-discovered by Emby's IScheduledTask scanner.
    ///   2. Plugin Settings page                 — wired via
    ///      IHasWebPages.GetPages() below; HTML+JS shipped as embedded
    ///      resources from <c>Web/</c>.
    ///   3. REST API at /Translarr/...           — TranslateController
    ///      is auto-discovered by Emby's IService scanner.
    ///
    /// Discovery rules: Emby scans the plugin assembly for all types
    /// implementing IService and IScheduledTask, so neither needs to
    /// be explicitly registered here. Only IHasWebPages is opt-in.
    /// </summary>
    public class Plugin : BasePlugin<PluginConfiguration>, IHasWebPages
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
        public static Plugin Instance { get; private set; }

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

        /// <summary>
        /// Maps the embedded HTML + JS resources to user-visible plugin
        /// pages inside Emby's dashboard. EmbeddedResourcePath must use
        /// the full default-namespace + path form ("Translarr.Emby.Web.…").
        ///
        /// IsMainConfigPage = true makes the HTML the plugin's primary
        /// settings page (i.e., the "Settings" link on Dashboard &gt;
        /// Plugins routes here). The .js sibling is referenced from the
        /// HTML via a plain &lt;script&gt; tag and served by the same
        /// embedded-resource mechanism.
        /// </summary>
        public IEnumerable<PluginPageInfo> GetPages()
        {
            return new[]
            {
                new PluginPageInfo
                {
                    Name = "translarrconfig",
                    EmbeddedResourcePath = GetType().Namespace + ".Web.translarrconfig.html",
                    IsMainConfigPage = true,
                },
                new PluginPageInfo
                {
                    Name = "translarrconfig.js",
                    EmbeddedResourcePath = GetType().Namespace + ".Web.translarrconfig.js",
                },
            };
        }
    }
}
