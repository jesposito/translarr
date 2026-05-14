using System;
using System.Collections.Generic;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using MediaBrowser.Controller.Providers;
using MediaBrowser.Controller.Subtitles;
using MediaBrowser.Model.Logging;
using MediaBrowser.Model.Providers;

namespace Translarr.Emby.Subtitles
{
    /// <summary>
    /// Surfaces Translarr inside Emby's player subtitle search modal.
    ///
    /// When a user clicks the subtitles button on an episode/movie and
    /// chooses "Search for subtitles", Emby walks every registered
    /// ISubtitleProvider. We return ONE virtual subtitle per configured
    /// target language; selecting it triggers a synchronous translation
    /// via the Translarr server's /translate/sync endpoint, then returns
    /// the resulting .srt bytes to Emby.
    ///
    /// This is the proper "right-click → translate" UX surface — Emby's
    /// plugin SDK does NOT support generic context menus (per the
    /// v0.1.25 spike), but ISubtitleProvider IS supported, and the
    /// player's subtitle modal is exactly the right surface for our flow.
    /// </summary>
    public class TranslarrSubtitleProvider : ISubtitleProvider, IHasOrder
    {
        /// <summary>
        /// IHasOrder.Order — Emby's subtitle search modal concatenates
        /// provider results in ascending Order. OpenSubtitles is a core
        /// provider at Order=0 by default. Setting ours to int.MinValue
        /// forces our entries to render before any core/community provider.
        /// </summary>
        public int Order => int.MinValue;


        private readonly ILogger _logger;
        private readonly TranslarrHttpClient _client;

        public TranslarrSubtitleProvider(ILogManager logManager)
        {
            _logger = logManager?.GetLogger("Translarr.SubtitleProvider") ?? new NullLogger();
            _client = new TranslarrHttpClient(_logger);
        }

        public string Name => "Translarr";

        public IEnumerable<VideoContentType> SupportedMediaTypes => new[]
        {
            VideoContentType.Episode,
            VideoContentType.Movie,
        };

        /// <summary>
        /// Called when Emby's subtitle modal opens. Returns one virtual
        /// "Translate to &lt;lang&gt;" entry per configured target language.
        /// The Id encodes the source media path + target language so
        /// GetSubtitles can act on it without another lookup.
        /// </summary>
        public Task<IEnumerable<RemoteSubtitleInfo>> Search(SubtitleSearchRequest request, CancellationToken cancellationToken)
        {
            var results = new List<RemoteSubtitleInfo>();
            try
            {
                if (request == null || string.IsNullOrWhiteSpace(request.MediaPath))
                {
                    _logger.Warn("Translarr: subtitle search missing media path");
                    return Task.FromResult<IEnumerable<RemoteSubtitleInfo>>(results);
                }

                var config = Plugin.Instance?.Configuration;
                var defaultLang = !string.IsNullOrWhiteSpace(config?.TargetLanguage)
                    ? config.TargetLanguage
                    : "en";

                // If the user asked for a specific language (via the modal's
                // language picker), honor it. Otherwise default to the
                // plugin's configured target language.
                var lang = !string.IsNullOrWhiteSpace(request.Language)
                    ? NormalizeLang(request.Language)
                    : defaultLang;

                var sourcePath = ApplyPathRemap(request.MediaPath, config?.MediaPathRemap);
                var id = EncodeId(sourcePath, lang);

                results.Add(new RemoteSubtitleInfo
                {
                    Id = id,
                    ProviderName = Name,
                    Name = $"★ Translarr — translate to {lang.ToUpperInvariant()}",
                    Format = "srt",
                    Language = lang,
                    // Emby's subtitle search modal orders entries by
                    // DownloadCount (descending), then CommunityRating.
                    // Set both to high values so the Translarr offering
                    // appears at the top of the list above OpenSubtitles
                    // results (which rank by genuine download stats).
                    DownloadCount = 999_999,
                    CommunityRating = 10.0f,
                });
            }
            catch (Exception ex)
            {
                _logger.ErrorException("Translarr: subtitle search failed", ex);
            }
            return Task.FromResult<IEnumerable<RemoteSubtitleInfo>>(results);
        }

        /// <summary>
        /// Called when the user clicks one of our search results. Triggers
        /// a synchronous translation against the Translarr server, then
        /// returns the .srt bytes for Emby to write next to the media.
        /// </summary>
        public async Task<SubtitleResponse> GetSubtitles(string id, CancellationToken cancellationToken)
        {
            if (!TryDecodeId(id, out var sourcePath, out var targetLang))
            {
                throw new InvalidOperationException("Translarr: malformed subtitle id: " + id);
            }
            _logger.Info("Translarr: sync translation requested for {0} -> {1}", sourcePath, targetLang);

            // Talk to the Translarr server's sync endpoint. Worker pool
            // runs the translation; we block here until done or error.
            var srtBytes = await _client.TranslateSyncAsync(sourcePath, targetLang, cancellationToken).ConfigureAwait(false);

            return new SubtitleResponse
            {
                Format = "srt",
                Language = targetLang,
                IsForced = false,
                Stream = new MemoryStream(srtBytes),
            };
        }

        // === Helpers ==========================================================

        private static string NormalizeLang(string lang)
        {
            if (string.IsNullOrWhiteSpace(lang)) return "en";
            lang = lang.Trim().ToLowerInvariant();
            // Map common 3-letter codes Emby uses to the 2-letter ISO 639-1
            // codes Translarr's pipeline expects.
            switch (lang)
            {
                case "eng": return "en";
                case "spa": return "es";
                case "deu":
                case "ger": return "de";
                case "fre":
                case "fra": return "fr";
                case "jpn": return "ja";
                case "kor": return "ko";
                case "rus": return "ru";
                case "hin": return "hi";
                default: return lang.Length >= 2 ? lang.Substring(0, 2) : lang;
            }
        }

        // Id format: "v1|<base64-of-path>|<lang>". The separator + version
        // byte keep us forward-compatible if the encoding ever changes.
        private static string EncodeId(string mediaPath, string targetLang)
        {
            var b64 = Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(mediaPath));
            return "v1|" + b64 + "|" + targetLang;
        }

        private static bool TryDecodeId(string id, out string mediaPath, out string targetLang)
        {
            mediaPath = null;
            targetLang = null;
            if (string.IsNullOrWhiteSpace(id)) return false;
            var parts = id.Split('|');
            if (parts.Length != 3 || parts[0] != "v1") return false;
            try
            {
                mediaPath = System.Text.Encoding.UTF8.GetString(Convert.FromBase64String(parts[1]));
                targetLang = parts[2];
                return true;
            }
            catch
            {
                return false;
            }
        }

        // Mirrors the same helper in TranslateController. Duplicating to
        // avoid a tight cross-namespace dependency; the body is two lines.
        private static string ApplyPathRemap(string path, string remap)
        {
            if (string.IsNullOrEmpty(path) || string.IsNullOrWhiteSpace(remap)) return path;
            var idx = remap.IndexOf(':');
            if (idx <= 0 || idx >= remap.Length - 1) return path;
            var from = remap.Substring(0, idx);
            var to = remap.Substring(idx + 1);
            if (path.StartsWith(from, StringComparison.Ordinal))
            {
                return to + path.Substring(from.Length);
            }
            return path;
        }
    }
}
