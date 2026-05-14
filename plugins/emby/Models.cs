using Newtonsoft.Json;

namespace Translarr.Emby
{
    /// <summary>
    /// DTO that matches the body of <c>POST /translate</c> on the
    /// Translarr FastAPI server. Field names are wired to snake_case to
    /// match <c>server/models.py::TranslateRequest</c>; do not rename
    /// without also updating the Python side.
    /// </summary>
    public class TranslateRequest
    {
        [JsonProperty("media_path")]
        public string MediaPath { get; set; }

        [JsonProperty("source_track_index", NullValueHandling = NullValueHandling.Ignore)]
        public int? SourceTrackIndex { get; set; }

        [JsonProperty("source_lang", NullValueHandling = NullValueHandling.Ignore)]
        public string SourceLang { get; set; }

        [JsonProperty("target_lang", NullValueHandling = NullValueHandling.Ignore)]
        public string TargetLang { get; set; }

        [JsonProperty("glossary_id", NullValueHandling = NullValueHandling.Ignore)]
        public string GlossaryId { get; set; }

        [JsonProperty("force")]
        public bool Force { get; set; }
    }

    /// <summary>
    /// Response body from <c>POST /translate</c> (async enqueue path).
    /// The server returns at minimum {status, job_id}; <c>state</c> is
    /// optional and surfaces when the job is already terminal.
    /// </summary>
    public class EnqueueResult
    {
        [JsonProperty("status")]
        public string Status { get; set; }

        [JsonProperty("job_id")]
        public string JobId { get; set; }

        [JsonProperty("state", NullValueHandling = NullValueHandling.Ignore)]
        public string State { get; set; }
    }

    /// <summary>
    /// Response body from <c>GET /jobs/{id}</c>. Only the fields the
    /// plugin currently consumes are mapped; the server may add more
    /// without breaking the plugin (Newtonsoft ignores unknown JSON).
    /// </summary>
    public class JobInfo
    {
        [JsonProperty("id")]
        public string Id { get; set; }

        [JsonProperty("state")]
        public string State { get; set; }

        [JsonProperty("media_path")]
        public string MediaPath { get; set; }

        [JsonProperty("target_lang")]
        public string TargetLang { get; set; }

        [JsonProperty("output_path", NullValueHandling = NullValueHandling.Ignore)]
        public string OutputPath { get; set; }

        [JsonProperty("attempts")]
        public int Attempts { get; set; }

        [JsonProperty("max_attempts")]
        public int MaxAttempts { get; set; }

        [JsonProperty("cost_cents")]
        public int CostCents { get; set; }

        [JsonProperty("error", NullValueHandling = NullValueHandling.Ignore)]
        public string Error { get; set; }
    }

    /// <summary>
    /// Response body from <c>GET /health</c>. Used by the settings page
    /// "Test Connection" button to surface server version and provider.
    /// </summary>
    public class HealthResponse
    {
        [JsonProperty("status")]
        public string Status { get; set; }

        [JsonProperty("version")]
        public string Version { get; set; }

        [JsonProperty("llm_provider")]
        public string LlmProvider { get; set; }

        [JsonProperty("llm_model")]
        public string LlmModel { get; set; }
    }
}
