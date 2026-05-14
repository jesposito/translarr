using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Library;
using MediaBrowser.Model.Logging;
using MediaBrowser.Model.Tasks;

namespace Translarr.Emby.ScheduledTasks
{
    /// <summary>
    /// Iterates the library once per scheduled run, picks items tagged
    /// with <see cref="TranslateTag"/>, and enqueues a Translarr job for
    /// each. The plugin never does the actual subtitle work; it just
    /// posts the request and lets the FastAPI server run the pipeline.
    ///
    /// v0.2 uses a hardcoded Emby tag (<c>translarr_translate</c>) as the
    /// per-item opt-in flag. v0.5 will replace this with a query against
    /// the server's <c>series_flags</c> table once that wiring exists.
    /// </summary>
    public class BatchTranslateTask : IScheduledTask
    {
        /// <summary>
        /// Emby tag that flags an item for batch translation. Users add
        /// it from Emby's metadata editor (or via the arr-stack import
        /// flow once that's wired in v0.5). Lowercased for case-stable
        /// comparison since Emby preserves user casing on tags.
        /// </summary>
        public const string TranslateTag = "translarr_translate";

        private readonly ILibraryManager _libraryManager;
        private readonly ILogger _logger;

        public BatchTranslateTask(ILibraryManager libraryManager, ILogManager logManager)
        {
            _libraryManager = libraryManager;
            _logger = logManager?.GetLogger("Translarr.BatchTranslateTask") ?? new NullLogger();
        }

        public string Name => "Translarr: Batch translate library";

        public string Description =>
            "Iterates monitored items and queues translation for each that has " +
            "an enabled translate tag. Calls the Translarr server's REST API; " +
            "does not perform translation in-process.";

        public string Category => "Translarr";

        public string Key => "TranslarrBatchTranslate";

        /// <summary>
        /// Default trigger: every day at 04:00 local time. Users can
        /// override the schedule from Dashboard &gt; Scheduled Tasks.
        /// </summary>
        public IEnumerable<TaskTriggerInfo> GetDefaultTriggers()
        {
            return new[]
            {
                new TaskTriggerInfo
                {
                    Type = TaskTriggerInfo.TriggerDaily,
                    TimeOfDayTicks = TimeSpan.FromHours(4).Ticks,
                },
            };
        }

        public async Task Execute(CancellationToken cancellationToken, IProgress<double> progress)
        {
            progress?.Report(0);

            BaseItem[] candidates;
            try
            {
                candidates = _libraryManager.GetItemList(new InternalItemsQuery
                {
                    IncludeItemTypes = new[] { "Movie", "Episode" },
                    Recursive = true,
                });
            }
            catch (Exception ex)
            {
                _logger.ErrorException("Translarr: failed to query library", ex);
                progress?.Report(100);
                return;
            }

            if (candidates == null || candidates.Length == 0)
            {
                _logger.Info("Translarr: no movies or episodes in library");
                progress?.Report(100);
                return;
            }

            var client = new TranslarrHttpClient(_logger);
            var config = Plugin.Instance?.Configuration;
            var targetLang = config?.TargetLanguage;

            int processed = 0;
            int enqueued = 0;
            int skipped = 0;
            int errored = 0;
            int total = candidates.Length;

            foreach (var item in candidates)
            {
                cancellationToken.ThrowIfCancellationRequested();

                try
                {
                    if (!HasTranslateTag(item))
                    {
                        skipped++;
                    }
                    else if (string.IsNullOrWhiteSpace(item.Path))
                    {
                        _logger.Warn("Translarr: skipping item {0} ({1}) — no file path", item.Id, item.Name);
                        skipped++;
                    }
                    else
                    {
                        var req = new TranslateRequest
                        {
                            MediaPath = item.Path,
                            TargetLang = targetLang,
                            Force = false,
                        };
                        var result = await client.EnqueueAsync(req).ConfigureAwait(false);
                        _logger.Info("Translarr: enqueued job {0} for {1}", result?.JobId, item.Path);
                        enqueued++;
                    }
                }
                catch (OperationCanceledException)
                {
                    throw;
                }
                catch (TranslarrHttpException tex)
                {
                    _logger.Error("Translarr: enqueue failed for {0}: {1}", item.Path, tex.Message);
                    errored++;
                }
                catch (Exception ex)
                {
                    _logger.ErrorException("Translarr: unexpected error processing {0}", ex, item.Path);
                    errored++;
                }

                processed++;
                progress?.Report(processed * 100.0 / total);
            }

            _logger.Info(
                "Translarr: batch run complete. processed={0} enqueued={1} skipped={2} errored={3}",
                processed, enqueued, skipped, errored);
            progress?.Report(100);
        }

        private static bool HasTranslateTag(BaseItem item)
        {
            var tags = item?.Tags;
            if (tags == null || tags.Length == 0) return false;
            foreach (var t in tags)
            {
                if (!string.IsNullOrEmpty(t) && string.Equals(t, TranslateTag, StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }
            return false;
        }
    }
}
