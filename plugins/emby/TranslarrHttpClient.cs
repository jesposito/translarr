using System;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using MediaBrowser.Model.Logging;
using Newtonsoft.Json;

namespace Translarr.Emby
{
    /// <summary>
    /// Thin HTTP client over the Translarr FastAPI server. Reads
    /// <see cref="Plugin.Instance"/> configuration on every call so a
    /// settings edit takes effect immediately, no restart required.
    ///
    /// Concurrency: instances are cheap; the controller and scheduled
    /// task each construct their own. A single static <see cref="HttpClient"/>
    /// is shared so DNS / socket pools are reused (the netstandard2.0
    /// recommendation per the BCL guidance for long-lived processes).
    /// </summary>
    public class TranslarrHttpClient
    {
        // One static HttpClient for the plugin lifetime. Disposing
        // HttpClient per call leaks sockets in TIME_WAIT on .NET
        // netstandard2.0 — this is the documented BCL guidance.
        // Default 30s for short ops (health, enqueue, get-job).
        private static readonly HttpClient SharedClient = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(30),
        };

        // Separate longer-timeout client for sync translations. A 22-min
        // episode takes ~1-2 min at Sonnet, a 2-hour film up to ~6 min;
        // 10-minute ceiling covers worst-case retries.
        private static readonly HttpClient SyncTranslateClient = new HttpClient
        {
            Timeout = TimeSpan.FromMinutes(10),
        };

        private readonly ILogger _logger;

        public TranslarrHttpClient(ILogger logger)
        {
            _logger = logger;
        }

        /// <summary>
        /// GET /health. Returns the server's version + LLM provider info.
        /// </summary>
        public Task<HealthResponse> HealthAsync()
        {
            return SendAsync<HealthResponse>(HttpMethod.Get, "/health", body: null);
        }

        /// <summary>
        /// POST /translate. Enqueues an async translation job.
        /// </summary>
        public Task<EnqueueResult> EnqueueAsync(TranslateRequest req)
        {
            return SendAsync<EnqueueResult>(HttpMethod.Post, "/translate", req);
        }

        /// <summary>
        /// GET /jobs/{id}. Returns current job state.
        /// </summary>
        public Task<JobInfo> GetJobAsync(string jobId)
        {
            return SendAsync<JobInfo>(HttpMethod.Get, "/jobs/" + Uri.EscapeDataString(jobId), body: null);
        }

        /// <summary>
        /// POST /translate/sync. Blocks until the Translarr server finishes
        /// (or the 10-minute timeout fires). Returns the raw .srt bytes —
        /// not the JSON envelope — by reading the output file from the
        /// JSON response's <c>output_path</c>. NOTE: this won't work
        /// across container boundaries; we ship the bytes embedded in the
        /// response body via a side query to the dedicated sync endpoint.
        ///
        /// Implementation: POST /translate/sync, parse the JSON response,
        /// then read the .srt file bytes through a second GET to a new
        /// endpoint /output/{path} (added to the server alongside).
        /// </summary>
        public async Task<byte[]> TranslateSyncAsync(string mediaPath, string targetLang, CancellationToken cancellationToken)
        {
            var payload = new TranslateRequest
            {
                MediaPath = mediaPath,
                TargetLang = targetLang,
            };
            using (var req = BuildRequest(HttpMethod.Post, "/translate/sync", payload))
            using (var resp = await SyncTranslateClient.SendAsync(req, cancellationToken).ConfigureAwait(false))
            {
                var text = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
                if (!resp.IsSuccessStatusCode)
                {
                    throw new TranslarrHttpException((int)resp.StatusCode, "POST /translate/sync failed: " + Truncate(text, 500));
                }
                // Response shape: { output_path, source_events, output_events, ... }
                // We need to fetch the actual .srt bytes. Add a /output endpoint
                // on the server that returns the file contents.
                TranslateResult result;
                try
                {
                    result = JsonConvert.DeserializeObject<TranslateResult>(text);
                }
                catch (JsonException jex)
                {
                    throw new TranslarrHttpException((int)resp.StatusCode, "Could not parse /translate/sync response: " + jex.Message);
                }
                if (result == null || string.IsNullOrEmpty(result.OutputPath))
                {
                    throw new TranslarrHttpException(500, "/translate/sync returned no output_path");
                }
                return await FetchSrtBytesAsync(result.OutputPath, cancellationToken).ConfigureAwait(false);
            }
        }

        private async Task<byte[]> FetchSrtBytesAsync(string outputPath, CancellationToken cancellationToken)
        {
            // GET /output?path=<path> on the server returns the file bytes.
            var encoded = Uri.EscapeDataString(outputPath);
            using (var req = BuildRequest(HttpMethod.Get, "/output?path=" + encoded, body: null))
            using (var resp = await SharedClient.SendAsync(req, cancellationToken).ConfigureAwait(false))
            {
                if (!resp.IsSuccessStatusCode)
                {
                    var text = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
                    throw new TranslarrHttpException((int)resp.StatusCode, "GET /output failed: " + Truncate(text, 500));
                }
                return await resp.Content.ReadAsByteArrayAsync().ConfigureAwait(false);
            }
        }

        /// <summary>
        /// DELETE /jobs/{id}. Best-effort cancellation; the server's
        /// response shape varies, so we just confirm 2xx and return.
        /// </summary>
        public async Task CancelJobAsync(string jobId)
        {
            using (var req = BuildRequest(HttpMethod.Delete, "/jobs/" + Uri.EscapeDataString(jobId), body: null))
            using (var resp = await SharedClient.SendAsync(req).ConfigureAwait(false))
            {
                if (!resp.IsSuccessStatusCode)
                {
                    var bodyText = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
                    throw new TranslarrHttpException(
                        (int)resp.StatusCode,
                        "DELETE /jobs/" + jobId + " failed: " + bodyText);
                }
            }
        }

        private async Task<T> SendAsync<T>(HttpMethod method, string path, object body)
        {
            using (var req = BuildRequest(method, path, body))
            using (var resp = await SharedClient.SendAsync(req).ConfigureAwait(false))
            {
                var text = await resp.Content.ReadAsStringAsync().ConfigureAwait(false);
                if (!resp.IsSuccessStatusCode)
                {
                    var msg = method.Method + " " + path + " -> " + (int)resp.StatusCode + ": " + Truncate(text, 500);
                    _logger?.Error("Translarr HTTP error: {0}", msg);
                    throw new TranslarrHttpException((int)resp.StatusCode, msg);
                }

                try
                {
                    return JsonConvert.DeserializeObject<T>(text);
                }
                catch (JsonException jex)
                {
                    _logger?.ErrorException("Translarr JSON decode failed for {0} {1}", jex, method.Method, path);
                    throw new TranslarrHttpException(
                        (int)resp.StatusCode,
                        "Could not parse Translarr response: " + jex.Message);
                }
            }
        }

        private HttpRequestMessage BuildRequest(HttpMethod method, string path, object body)
        {
            var config = Plugin.Instance?.Configuration;
            var baseUrl = (config?.ServerUrl ?? string.Empty).TrimEnd('/');
            if (string.IsNullOrWhiteSpace(baseUrl))
            {
                throw new TranslarrHttpException(0, "Translarr ServerUrl is not configured. Set it on the plugin settings page.");
            }

            var msg = new HttpRequestMessage(method, baseUrl + path);

            var secret = config?.WebhookSecret;
            if (!string.IsNullOrEmpty(secret))
            {
                msg.Headers.Add("X-Translarr-Secret", secret);
            }

            if (body != null)
            {
                var json = JsonConvert.SerializeObject(body, JsonSettings);
                msg.Content = new StringContent(json, Encoding.UTF8, "application/json");
            }

            return msg;
        }

        private static readonly JsonSerializerSettings JsonSettings = new JsonSerializerSettings
        {
            // Honour [JsonProperty] attributes; let nulls drop where annotated.
            NullValueHandling = NullValueHandling.Include,
        };

        private static string Truncate(string s, int max)
        {
            if (string.IsNullOrEmpty(s)) return s;
            return s.Length <= max ? s : s.Substring(0, max) + "...";
        }
    }

    /// <summary>
    /// Wraps any non-2xx response or transport failure from the
    /// Translarr server with the upstream status code for callers.
    /// </summary>
    public class TranslarrHttpException : Exception
    {
        public int StatusCode { get; }

        public TranslarrHttpException(int statusCode, string message) : base(message)
        {
            StatusCode = statusCode;
        }
    }
}
