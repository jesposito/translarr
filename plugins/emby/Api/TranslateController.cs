using System;
using System.Threading.Tasks;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Logging;
using MediaBrowser.Model.Services;

namespace Translarr.Emby.Api
{
    // Response POCOs returned from IService handlers. PLAIN PascalCase — no
    // [JsonProperty] attributes — so ServiceStack.Text (Emby's IService
    // serializer) can marshal them cleanly. The DTOs with snake_case
    // [JsonProperty] annotations in Models.cs are used ONLY for
    // (de)serialization to/from the Python Translarr server via Newtonsoft.
    public sealed class HealthResult
    {
        public string Status { get; set; }
        public string Version { get; set; }
        public string LlmProvider { get; set; }
        public string LlmModel { get; set; }
    }

    public sealed class JobResult
    {
        public string Id { get; set; }
        public string State { get; set; }
        public string MediaPath { get; set; }
        public string TargetLang { get; set; }
        public string OutputPath { get; set; }
        public int Attempts { get; set; }
        public int MaxAttempts { get; set; }
        public int CostCents { get; set; }
        public string Error { get; set; }
    }

    public sealed class EnqueueResultDto
    {
        public string Status { get; set; }
        public string JobId { get; set; }
        public string State { get; set; }
    }

    public sealed class CancelResult
    {
        public string Status { get; set; }
        public string JobId { get; set; }
    }

    public sealed class ErrorResult
    {
        public int Status { get; set; }
        public string Error { get; set; }
    }

    // ------------------------------------------------------------------
    // Request DTOs. Emby's IService pattern dispatches on these — one
    // route + one verb per class. The DTO carries every parameter the
    // handler needs; Emby's binder fills route segments, query string,
    // and JSON body into matching properties.
    // ------------------------------------------------------------------

    [Route("/Translarr/Translate", "POST",
        Summary = "Enqueue a Translarr translation job for an Emby item.")]
    public class TranslateItemRequest : IReturn<EnqueueResultDto>
    {
        /// <summary>Emby item id (Guid). The controller resolves this to a file path.</summary>
        public string ItemId { get; set; }

        /// <summary>Optional override; defaults to plugin config's TargetLanguage.</summary>
        public string TargetLang { get; set; }

        /// <summary>If true, the server re-translates even if an output already exists.</summary>
        public bool Force { get; set; }
    }

    [Route("/Translarr/Jobs/{JobId}", "GET",
        Summary = "Proxy GET /jobs/{id} to the Translarr server.")]
    public class GetJobRequest : IReturn<JobResult>
    {
        public string JobId { get; set; }
    }

    [Route("/Translarr/Jobs/{JobId}", "DELETE",
        Summary = "Proxy DELETE /jobs/{id} to the Translarr server.")]
    public class CancelJobRequest : IReturnVoid
    {
        public string JobId { get; set; }
    }

    [Route("/Translarr/Health", "GET",
        Summary = "Proxy GET /health to the Translarr server.")]
    public class HealthCheckRequest : IReturn<HealthResult>
    {
    }

    /// <summary>
    /// Emby in-process REST controller. Plugins register endpoints by
    /// implementing <see cref="IService"/>; Emby's HTTP layer scans
    /// public methods named Get/Post/Delete whose single parameter is
    /// one of the DTOs above and dispatches accordingly.
    ///
    /// All handlers translate Translarr server errors into JSON error
    /// payloads so an offline Translarr never crashes the plugin host.
    /// </summary>
    public class TranslateController : IService
    {
        private readonly ILibraryManager _libraryManager;
        private readonly ILogger _logger;
        private readonly TranslarrHttpClient _client;

        public TranslateController(ILibraryManager libraryManager, ILogManager logManager)
        {
            _libraryManager = libraryManager;
            _logger = logManager?.GetLogger("Translarr") ?? new NullLogger();
            _client = new TranslarrHttpClient(_logger);
        }

        public async Task<object> Post(TranslateItemRequest request)
        {
            if (string.IsNullOrWhiteSpace(request?.ItemId))
            {
                return Error(400, "ItemId is required.");
            }

            BaseItem item;
            try
            {
                if (!Guid.TryParse(request.ItemId, out var guid))
                {
                    return Error(400, "ItemId is not a valid GUID: " + request.ItemId);
                }
                item = _libraryManager.GetItemById(guid);
            }
            catch (Exception ex)
            {
                _logger.ErrorException("Translarr: ILibraryManager.GetItemById failed", ex);
                return Error(500, "Failed to resolve Emby item: " + ex.Message);
            }

            if (item == null)
            {
                return Error(404, "No Emby item with id " + request.ItemId);
            }
            if (string.IsNullOrWhiteSpace(item.Path))
            {
                return Error(409, "Emby item " + request.ItemId + " has no file path on disk.");
            }

            var config = Plugin.Instance?.Configuration;
            var targetLang = !string.IsNullOrWhiteSpace(request.TargetLang)
                ? request.TargetLang
                : config?.TargetLanguage;

            var payload = new TranslateRequest
            {
                MediaPath = item.Path,
                TargetLang = targetLang,
                Force = request.Force,
            };

            try
            {
                var result = await _client.EnqueueAsync(payload).ConfigureAwait(false);
                _logger.Info("Translarr: enqueued job {0} for item {1} ({2})", result?.JobId, item.Id, item.Path);
                return new EnqueueResultDto
                {
                    Status = result?.Status,
                    JobId = result?.JobId,
                    State = result?.State,
                };
            }
            catch (TranslarrHttpException tex)
            {
                return Error(tex.StatusCode > 0 ? tex.StatusCode : 502, tex.Message);
            }
            catch (Exception ex)
            {
                _logger.ErrorException("Translarr: enqueue failed", ex);
                return Error(502, "Translarr server unreachable: " + ex.Message);
            }
        }

        public async Task<object> Get(GetJobRequest request)
        {
            if (string.IsNullOrWhiteSpace(request?.JobId))
            {
                return Error(400, "JobId is required.");
            }
            try
            {
                var j = await _client.GetJobAsync(request.JobId).ConfigureAwait(false);
                if (j == null) return Error(404, "Job not found.");
                return new JobResult
                {
                    Id = j.Id, State = j.State, MediaPath = j.MediaPath,
                    TargetLang = j.TargetLang, OutputPath = j.OutputPath,
                    Attempts = j.Attempts, MaxAttempts = j.MaxAttempts,
                    CostCents = j.CostCents, Error = j.Error,
                };
            }
            catch (TranslarrHttpException tex)
            {
                return Error(tex.StatusCode > 0 ? tex.StatusCode : 502, tex.Message);
            }
            catch (Exception ex)
            {
                _logger.ErrorException("Translarr: GetJob failed", ex);
                return Error(502, "Translarr server unreachable: " + ex.Message);
            }
        }

        public async Task<object> Delete(CancelJobRequest request)
        {
            if (string.IsNullOrWhiteSpace(request?.JobId))
            {
                return Error(400, "JobId is required.");
            }
            try
            {
                await _client.CancelJobAsync(request.JobId).ConfigureAwait(false);
                return new CancelResult { Status = "cancelled", JobId = request.JobId };
            }
            catch (TranslarrHttpException tex)
            {
                return Error(tex.StatusCode > 0 ? tex.StatusCode : 502, tex.Message);
            }
            catch (Exception ex)
            {
                _logger.ErrorException("Translarr: CancelJob failed", ex);
                return Error(502, "Translarr server unreachable: " + ex.Message);
            }
        }

        public async Task<object> Get(HealthCheckRequest request)
        {
            try
            {
                var h = await _client.HealthAsync().ConfigureAwait(false);
                if (h == null) return Error(502, "Translarr server returned no body.");
                return new HealthResult
                {
                    Status = h.Status, Version = h.Version,
                    LlmProvider = h.LlmProvider, LlmModel = h.LlmModel,
                };
            }
            catch (TranslarrHttpException tex)
            {
                return Error(tex.StatusCode > 0 ? tex.StatusCode : 502, tex.Message);
            }
            catch (Exception ex)
            {
                _logger.ErrorException("Translarr: Health failed", ex);
                return Error(502, "Translarr server unreachable: " + ex.Message);
            }
        }

        /// <summary>
        /// Helper that builds an error payload. Returns object (not throws)
        /// so the plugin never bubbles a raw 500 up to Emby's host process.
        /// Plain POCO — no JsonProperty attributes — for ServiceStack.Text.
        /// </summary>
        private static object Error(int status, string message)
        {
            return new ErrorResult { Status = status, Error = message };
        }
    }
}
