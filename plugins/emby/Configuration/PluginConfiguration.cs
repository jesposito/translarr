using MediaBrowser.Model.Plugins;

namespace Translarr.Emby.Configuration
{
    /// <summary>
    /// Persisted plugin settings. Emby serializes this to XML under
    /// &lt;emby-config&gt;/plugins/configurations/Translarr.xml on every change.
    ///
    /// TR-7p7.2.1 (bootstrap): defines the config class only. The settings
    /// page UI that lets users edit these values is TR-7p7.2.2.
    /// </summary>
    public class PluginConfiguration : BasePluginConfiguration
    {
        public PluginConfiguration()
        {
            // Default to a sibling container on ansiblenet, the deploy
            // target documented in CLAUDE.md. Users will override this
            // from the settings page once TR-7p7.2.2 ships.
            ServerUrl = "http://translarr:9000";

            // English is the project's default target language across
            // the server (server.config) and the arr-stack tag parser.
            TargetLanguage = "en";

            // Empty by default. If the Translarr server is configured
            // with TRANSLARR_WEBHOOK_SECRET, the user must paste the
            // same value here so the plugin can sign its requests.
            WebhookSecret = string.Empty;

            // Emby's container mounts media at /mnt; Translarr's at /media.
            // When the plugin sends an Emby item path to Translarr, it
            // applies this remap (simple prefix substitution). Format:
            //   <emby-prefix>:<translarr-prefix>
            // Empty/missing = no remap (pass the Emby path through unchanged).
            // Default reflects the TheAnsible Unraid deploy documented in CLAUDE.md.
            MediaPathRemap = "/mnt/:/media/";
        }

        /// <summary>
        /// Base URL of the Translarr FastAPI server, no trailing slash.
        /// Example: http://translarr:9000
        /// </summary>
        public string ServerUrl { get; set; }

        /// <summary>
        /// ISO 639-1 code of the language Translarr should translate
        /// into. The server's LLM router uses this as the target.
        /// </summary>
        public string TargetLanguage { get; set; }

        /// <summary>
        /// Shared secret sent in the X-Translarr-Secret header on every
        /// outbound request. Must match TRANSLARR_WEBHOOK_SECRET on the
        /// server side, or be empty on both sides.
        /// </summary>
        public string WebhookSecret { get; set; }

        /// <summary>
        /// Single-prefix path remap applied to Emby item paths before
        /// shipping them to Translarr. Format: <c>from:to</c>. Used because
        /// Emby's container exposes media under <c>/mnt</c> but Translarr's
        /// under <c>/media</c>. Empty or malformed = no remap.
        /// </summary>
        public string MediaPathRemap { get; set; }
    }
}
