using System;
using System.Threading.Tasks;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Logging;
using MediaBrowser.Model.Services;
using Newtonsoft.Json;

namespace Translarr.Emby.Api
{
    // ------------------------------------------------------------------
    // Request DTOs. Emby's IService pattern dispatches on these — one
    // route + one verb per class. The DTO carries every parameter the
    // handler needs; Emby's binder fills route segments, query string,
    // and JSON body into matching properties.
    // ------------------------------------------------------------------

    [Route("/Translarr/Translate", "POST",
        Summary = "Enqueue a Translarr translation job for an Emby item.")]
    public class TranslateItemRequest : IReturn<EnqueueResult>
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
    public class GetJobRequest : IReturn<JobInfo>
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
    public class HealthCheckRequest : IReturn<HealthResponse>
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
                return result;
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
                return await _client.GetJobAsync(request.JobId).ConfigureAwait(false);
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
                return new { status = "cancelled", job_id = request.JobId };
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
                return await _client.HealthAsync().ConfigureAwait(false);
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
        /// Helper that builds a JSON error payload. We deliberately return
        /// an object (not throw) so the plugin never bubbles a raw 500 up
        /// to Emby's host process.
        /// </summary>
        private static object Error(int status, string message)
        {
            return new ErrorPayload { Status = status, Error = message };
        }

        private class ErrorPayload
        {
            [JsonProperty("status")]
            public int Status { get; set; }

            [JsonProperty("error")]
            public string Error { get; set; }
        }
    }
}
