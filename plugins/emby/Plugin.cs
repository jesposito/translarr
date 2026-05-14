using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
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
        // One-shot guard so we don't double-register the resolver if the
        // plugin assembly is loaded twice (defensive — Emby shouldn't do
        // that, but if it ever does we don't want a second handler firing).
        private static bool _resolverRegistered;
        private static readonly object _resolverLock = new object();

        static Plugin()
        {
            RegisterDependencyResolver();
        }

        public Plugin(IApplicationPaths applicationPaths, IXmlSerializer xmlSerializer)
            : base(applicationPaths, xmlSerializer)
        {
            Instance = this;
        }

        /// <summary>
        /// Emby's runtime probes <c>/system/</c> for assemblies; plugin
        /// DLLs and their dependencies live in <c>/config/plugins/</c>
        /// which is NOT on the probe path. Without this resolver, any
        /// reference Translarr.dll makes to a sibling DLL (Newtonsoft.Json,
        /// etc.) fails to load. SubZ and other Emby plugins use the same
        /// pattern.
        /// </summary>
        private static void RegisterDependencyResolver()
        {
            lock (_resolverLock)
            {
                if (_resolverRegistered) return;
                AppDomain.CurrentDomain.AssemblyResolve += ResolvePluginAssembly;
                _resolverRegistered = true;
            }
        }

        private static Assembly ResolvePluginAssembly(object sender, ResolveEventArgs args)
        {
            try
            {
                var name = new AssemblyName(args.Name).Name;
                if (string.IsNullOrEmpty(name)) return null;

                // Try several places the DLL could live. Emby may load plugins
                // from byte[] (Assembly.Location is empty in that case), so we
                // try the obvious typeof().Assembly.Location first, then
                // fall back to a list of well-known plugin directories.
                foreach (var dir in CandidatePluginDirs())
                {
                    if (string.IsNullOrEmpty(dir)) continue;
                    var candidate = Path.Combine(dir, name + ".dll");
                    if (File.Exists(candidate))
                    {
                        return Assembly.LoadFrom(candidate);
                    }
                }
            }
            catch
            {
                // Resolution failure is non-fatal — let the runtime carry on
                // and raise its own FileNotFoundException at the call site.
            }
            return null;
        }

        private static IEnumerable<string> CandidatePluginDirs()
        {
            // 1. The DLL's own location (works when Emby loaded us from disk).
            string ownLocation = null;
            try { ownLocation = typeof(Plugin).Assembly.Location; }
            catch { ownLocation = null; }
            if (!string.IsNullOrEmpty(ownLocation))
            {
                yield return Path.GetDirectoryName(ownLocation);
            }

            // 2. Plugin.Instance's AssemblyFilePath (set after construction).
            string instDir = null;
            try
            {
                var instAsmPath = Instance?.AssemblyFilePath;
                if (!string.IsNullOrEmpty(instAsmPath))
                {
                    instDir = Path.GetDirectoryName(instAsmPath);
                }
            }
            catch { /* ignore */ }
            if (!string.IsNullOrEmpty(instDir)) yield return instDir;

            // 3. Well-known Emby plugin paths (Docker + bare-metal).
            yield return "/config/plugins";
            string localAppDataDir = null;
            try
            {
                localAppDataDir = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                    "emby-server", "plugins");
            }
            catch { /* ignore */ }
            if (!string.IsNullOrEmpty(localAppDataDir)) yield return localAppDataDir;
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
