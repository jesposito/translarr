using System;
using System.Net.Http;
using System.Text;
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
        private static readonly HttpClient SharedClient = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(30),
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
