<script lang="ts">
  import { onMount, onDestroy } from 'svelte';

  type JobState = 'queued' | 'running' | 'retrying' | 'done' | 'failed' | 'cancelled';

  interface Job {
    id: string;
    state: JobState;
    media_path: string;
    target_lang: string;
    output_path: string | null;
    attempts: number;
    max_attempts?: number;
    cost_cents: number;
    error: string | null;
    created_at: number;
    updated_at: number;
    finished_at: number | null;
  }

  interface Health {
    status: string;
    version: string;
    llm_provider: string;
    llm_model: string;
  }

  interface Stats {
    today: {
      date: string;
      cost_cents: number;
      jobs_count: number;
      jobs_done: number;
      jobs_failed: number;
      jobs_in_flight: number;
    };
    all_time: { cost_cents: number; jobs_count: number };
    queue: { queued: number; running: number; retrying: number };
    budget: {
      daily_cap_cents: number;
      spent_cents: number;
      remaining_cents: number;
      pct_used: number;
    };
  }

  let health = $state<Health | null>(null);
  let stats = $state<Stats | null>(null);
  let liveJobs = $state<Job[] | null>(null);
  let doneJobs = $state<Job[] | null>(null);
  let failedJobs = $state<Job[] | null>(null);
  let failuresOpen = $state(false);
  // "Hide trivial jobs" filter — when ON, the Recent Completed table
  // hides $0 skips (no-source-subs / already-done / bitmap-only). Lets
  // users focus on real translation work without losing the audit trail
  // (the rows still exist server-side, just not rendered here).
  let hideTrivialJobs = $state(true);
  const trivialFilter = (j: Job) =>
    !hideTrivialJobs || j.cost_cents > 0 || (j.output_path && j.output_path.length > 0);
  // A11y M2: announce filter state changes so SR users hear when the
  // table collapses to empty (or expands) after a toggle.
  let filterAnnouncement = $state('');

  function onFilterToggle(): void {
    queueMicrotask(() => {
      if (!doneJobs) return;
      const shown = doneJobs.filter(trivialFilter).length;
      const hidden = doneJobs.length - shown;
      if (hideTrivialJobs) {
        filterAnnouncement = shown === 0
          ? `All ${hidden} completed jobs hidden — they were all $0 skips.`
          : `Showing ${shown} paid jobs. ${hidden} $0 skip${hidden === 1 ? '' : 's'} hidden.`;
      } else {
        filterAnnouncement = `Showing all ${doneJobs.length} completed jobs.`;
      }
    });
  }
  let loadError = $state<string | null>(null);
  let lastLoadError = $state<string | null>(null);  // dedupe alert re-announce
  // Transition-only live announcement string. SR users hear about state
  // changes (queued -> running -> done), not the full live-jobs list on
  // every 3s repoll.
  let liveAnnouncement = $state('');
  let prevLiveStates = $state<Map<string, JobState>>(new Map());

  let pollTimer: ReturnType<typeof setInterval> | null = null;

  function basename(p: string): string {
    if (!p) return '';
    const parts = p.split('/');
    return parts[parts.length - 1] || p;
  }

  function dollars(cents: number): string {
    return `$${(cents / 100).toFixed(2)}`;
  }

  function relTime(epoch: number | null): string {
    // Mo4 (a11y audit): "Not finished" reads better than an em-dash for
    // screen readers that pronounce dash characters literally.
    if (!epoch) return 'Not finished';
    const now = Date.now() / 1000;
    const diff = Math.max(0, now - epoch);
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} hr ago`;
    return `${Math.floor(diff / 86400)} days ago`;
  }

  function isoTime(epoch: number | null): string {
    if (!epoch) return '';
    return new Date(epoch * 1000).toISOString();
  }

  function elapsed(start: number): string {
    const diff = Math.max(0, Date.now() / 1000 - start);
    if (diff < 60) return `${Math.floor(diff)}s`;
    return `${Math.floor(diff / 60)}m ${Math.floor(diff % 60)}s`;
  }

  async function fetchJSON<T>(url: string): Promise<T> {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${url}: ${r.status}`);
    return (await r.json()) as T;
  }

  async function loadAll() {
    try {
      const [h, s, live, done, fail] = await Promise.all([
        fetchJSON<Health>('/health').catch(() => null),
        fetchJSON<Stats>('/stats'),
        fetchJSON<{ jobs: Job[] }>('/jobs?state=running&limit=20'),
        fetchJSON<{ jobs: Job[] }>('/jobs?state=done&limit=20'),
        fetchJSON<{ jobs: Job[] }>('/jobs?state=failed&limit=5')
      ]);
      health = h;
      stats = s;
      // Combine running + retrying for the live stream view.
      const retrying = await fetchJSON<{ jobs: Job[] }>(
        '/jobs?state=retrying&limit=20'
      ).catch(() => ({ jobs: [] as Job[] }));
      const newLive = [...live.jobs, ...retrying.jobs].sort(
        (a, b) => b.updated_at - a.updated_at
      );

      // Build a transition-only announcement: include only jobs whose
      // state CHANGED since the last poll, plus jobs that entered the
      // done/failed lists (i.e. left "live"). Cap to first 3 to keep
      // announcement short.
      const transitions: string[] = [];
      const nextStates = new Map<string, JobState>();
      for (const j of newLive) nextStates.set(j.id, j.state);
      for (const [id, prev] of prevLiveStates) {
        const next = nextStates.get(id);
        if (!next) {
          // Job left the live list — find it in done/failed for context.
          const completed = done.jobs.find((j) => j.id === id);
          const failed = fail.jobs.find((j) => j.id === id);
          if (completed) transitions.push(`Job ${id.slice(0, 8)} completed.`);
          else if (failed) transitions.push(`Job ${id.slice(0, 8)} failed.`);
        } else if (next !== prev) {
          transitions.push(`Job ${id.slice(0, 8)} is now ${next}.`);
        }
      }
      prevLiveStates = nextStates;
      liveAnnouncement = transitions.slice(0, 3).join(' ');

      liveJobs = newLive;
      doneJobs = done.jobs;
      failedJobs = fail.jobs;
      loadError = null;
      lastLoadError = null;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      // Only re-trigger role="alert" when error text actually changes.
      if (msg !== lastLoadError) {
        loadError = msg;
        lastLoadError = msg;
      }
    }
  }

  onMount(() => {
    loadAll();
    pollTimer = setInterval(loadAll, 3000);
  });

  onDestroy(() => {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  });
</script>

<svelte:head>
  <title>Translarr — Dashboard</title>
</svelte:head>

<header class="page-head">
  <div class="title-row">
    <h1>Translarr</h1>
    {#if health}
      <span class="version-pill" aria-label="Server version {health.version}">
        v{health.version}
      </span>
    {:else}
      <span class="version-pill skeleton" aria-hidden="true" style="width: 48px; height: 20px;"></span>
    {/if}
  </div>
  {#if health}
    <p class="sub">
      Provider <span class="mono">{health.llm_provider}</span>
      · Model <span class="mono">{health.llm_model}</span>
    </p>
  {/if}
</header>

{#if loadError}
  <div class="error-banner" role="alert">
    Couldn't reach the server: {loadError}
  </div>
{/if}

<section aria-labelledby="stats-heading">
  <h2 id="stats-heading" class="sr-only">Summary stats</h2>
  <div class="stat-row" aria-busy={stats === null}>
    {#if stats}
      <div class="stat">
        <span class="value" aria-label="Today's spend {dollars(stats.today.cost_cents)}">
          {dollars(stats.today.cost_cents)}
        </span>
        <span class="label">Today's cost</span>
        <span
          class="cap-bar"
          aria-hidden="true"
          class:warn={stats.budget.pct_used >= 70}
          class:hot={stats.budget.pct_used >= 90}
        >
          <span class="cap-fill" style="width: {Math.min(100, stats.budget.pct_used)}%"></span>
        </span>
        <span class="cap-label">
          {stats.budget.pct_used.toFixed(0)}% of {dollars(stats.budget.daily_cap_cents)} cap
          <!-- M4 (a11y audit): non-color severity indicator. WCAG 1.4.1
               forbids relying on the cap-bar color alone. -->
          {#if stats.budget.pct_used >= 90}
            <span class="warn-tag" data-level="hot"> — near limit</span>
          {:else if stats.budget.pct_used >= 70}
            <span class="warn-tag" data-level="warn"> — over 70%</span>
          {/if}
        </span>
      </div>
      <div class="stat">
        <span class="value">{stats.today.jobs_count}</span>
        <span class="label">Today's jobs</span>
      </div>
      <div class="stat">
        <span class="value">
          {stats.queue.queued + stats.queue.running + stats.queue.retrying}
        </span>
        <span class="label">Queue depth</span>
      </div>
      <div class="stat">
        <span class="value">{dollars(stats.all_time.cost_cents)}</span>
        <span class="label">All-time cost</span>
      </div>
    {:else}
      {#each Array(4) as _}
        <div class="stat" aria-hidden="true">
          <span class="skeleton" style="width: 80%; height: 32px;"></span>
          <span class="skeleton" style="width: 60%; height: 12px; margin-top: 8px;"></span>
        </div>
      {/each}
    {/if}
  </div>
</section>

<section aria-labelledby="live-heading">
  <h2 id="live-heading">Live jobs</h2>
  <!-- Transition-only SR announcement (M1 from a11y audit). -->
  <span class="sr-only" aria-live="polite" aria-atomic="true">{liveAnnouncement}</span>
  <!-- M3 (a11y audit): aria-live="off" is explicit — the visible list
       is NOT the announcement target, the sr-only span above is. Some
       screen readers otherwise crawl the live list mid-poll. -->
  <div class="live-region" aria-busy={liveJobs === null} aria-live="off">
    {#if liveJobs === null}
      <div class="card" aria-hidden="true">
        <div class="skeleton" style="width: 60%; height: 16px; margin-bottom: 12px;"></div>
        <div class="skeleton" style="width: 40%; height: 12px;"></div>
      </div>
    {:else if liveJobs.length === 0}
      <div class="card empty">
        <p>No jobs running.</p>
        <p class="muted">
          <a href="/translate">Queue something<span aria-hidden="true"> →</span></a>
        </p>
      </div>
    {:else}
      <ul class="live-list">
        {#each liveJobs as job (job.id)}
          <li class="card live-row">
            <a href={`/jobs/${job.id}`} class="live-link">
              <span class="pill" data-state={job.state}>
                {job.state}
              </span>
              <span class="live-name" title={job.media_path}>
                {basename(job.media_path)}
              </span>
              <span class="live-lang mono"><span aria-hidden="true">→ </span>{job.target_lang}</span>
              <span class="live-progress muted">
                attempt {job.attempts}{job.max_attempts ? `/${job.max_attempts}` : ''}
              </span>
              <span class="live-time muted">{elapsed(job.updated_at)}</span>
            </a>
          </li>
        {/each}
      </ul>
    {/if}
  </div>
</section>

<section aria-labelledby="done-heading">
  <div class="section-head">
    <h2 id="done-heading">Recent completed</h2>
    <label class="filter-toggle">
      <input type="checkbox" bind:checked={hideTrivialJobs} onchange={onFilterToggle} />
      <span>Hide $0 skips</span>
    </label>
  </div>
  <!-- A11y M2: SR-only live region announces filter state changes
       (especially the "table collapsed to empty" case). -->
  <div class="sr-only" role="status" aria-live="polite" aria-atomic="true">
    {filterAnnouncement}
  </div>
  {#if doneJobs === null}
    <div class="card" aria-hidden="true">
      {#each Array(3) as _}
        <div class="skeleton" style="width: 100%; height: 16px; margin-bottom: 10px;"></div>
      {/each}
    </div>
  {:else if doneJobs.length === 0}
    <div class="card empty"><p>No completed jobs yet.</p></div>
  {:else if doneJobs.filter(trivialFilter).length === 0}
    <div class="card empty">
      <p>No paid translations in the recent window.</p>
      <p class="muted small">
        {doneJobs.length} $0 skip{doneJobs.length === 1 ? '' : 's'} hidden.
        Uncheck "Hide $0 skips" to show them.
      </p>
    </div>
  {:else}
    <div class="card no-pad">
      <table>
        <caption class="sr-only">Recent completed translation jobs</caption>
        <thead>
          <tr>
            <th scope="col">State</th>
            <th scope="col">Media</th>
            <th scope="col">Lang</th>
            <th scope="col" class="num">Cost</th>
            <th scope="col">Finished</th>
          </tr>
        </thead>
        <tbody>
          {#each doneJobs.filter(trivialFilter) as job (job.id)}
            <tr>
              <td>
                <span class="pill" data-state={job.state}>
                  {job.state}
                </span>
              </td>
              <td class="trunc">
                <a href={`/jobs/${job.id}`} title={job.media_path}>
                  {basename(job.media_path)}
                </a>
              </td>
              <td class="mono">{job.target_lang}</td>
              <td class="num">{dollars(job.cost_cents)}</td>
              <td>
                <time datetime={isoTime(job.finished_at)} title={isoTime(job.finished_at)}>
                  {relTime(job.finished_at)}
                </time>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</section>

<section aria-labelledby="fail-heading">
  <details bind:open={failuresOpen}>
    <summary>
      <h2 id="fail-heading">
        Recent failures
        {#if failedJobs}<span class="count">({failedJobs.length})</span>{/if}
      </h2>
    </summary>
    <div class="fail-body">
      {#if failedJobs === null}
        <div class="card skeleton" aria-hidden="true" style="height: 80px;"></div>
      {:else if failedJobs.length === 0}
        <div class="card empty"><p>No recent failures. Clean run.</p></div>
      {:else}
        <ul class="fail-list">
          {#each failedJobs as job (job.id)}
            <li class="card fail-row">
              <a href={`/jobs/${job.id}`} class="fail-link">
                <div class="fail-head">
                  <span class="pill" data-state="failed">failed</span>
                  <span class="trunc mono" title={job.media_path}>
                    {basename(job.media_path)}
                  </span>
                  <time class="muted" datetime={isoTime(job.finished_at)}>
                    {relTime(job.finished_at)}
                  </time>
                </div>
                {#if job.error}
                  <p class="fail-err">{job.error}</p>
                {/if}
              </a>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  </details>
</section>

<style>
  .page-head {
    margin-bottom: var(--space-6);
  }
  .title-row {
    display: flex;
    align-items: baseline;
    gap: var(--space-3);
  }
  .version-pill {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--text-muted);
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    padding: 2px 8px;
    border-radius: var(--radius-pill);
  }
  .sub {
    margin: var(--space-2) 0 0;
    color: var(--text-muted);
    font-size: var(--text-sm);
  }
  .muted { color: var(--text-muted); }

  .error-banner {
    background: var(--error-bg);
    border: 1px solid var(--error);
    color: var(--error);
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius-sm);
    margin-bottom: var(--space-5);
  }

  section { margin-bottom: var(--space-6); }
  section h2 {
    margin-bottom: var(--space-4);
  }

  .stat-row {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: var(--space-4);
  }
  .cap-bar {
    display: block;
    height: 4px;
    background: var(--bg-input);
    border-radius: 999px;
    overflow: hidden;
    margin-top: var(--space-2);
  }
  .cap-fill {
    display: block;
    height: 100%;
    background: var(--accent);
    transition: width var(--dur) var(--ease);
  }
  .cap-bar.warn .cap-fill { background: var(--warn); }
  .cap-bar.hot  .cap-fill { background: var(--error); }
  .cap-label {
    font-size: var(--text-xs);
    color: var(--text-muted);
    margin-top: 2px;
    font-variant-numeric: tabular-nums;
  }
  @media (max-width: 768px) {
    .stat-row { grid-template-columns: repeat(2, 1fr); }
  }
  @media (max-width: 480px) {
    .stat-row { grid-template-columns: 1fr; }
  }

  .section-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    margin-bottom: var(--space-2);
    flex-wrap: wrap;
  }
  .filter-toggle {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    font-size: var(--text-sm);
    color: var(--text-muted);
    cursor: pointer;
    user-select: none;
    min-height: 32px;
  }
  .filter-toggle input[type="checkbox"] {
    /* Use the platform checkbox — no styling fight, fully accessible by default. */
    accent-color: var(--accent);
    width: 16px;
    height: 16px;
  }

  .live-region { min-height: 60px; }
  .live-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .live-row { padding: 0; }
  .live-link {
    display: grid;
    grid-template-columns: auto 1fr auto auto auto;
    gap: var(--space-4);
    align-items: center;
    padding: var(--space-3) var(--space-4);
    color: var(--text);
    text-decoration: none;
    min-height: 48px;
    border-radius: var(--radius-md);
  }
  .live-link:hover {
    background: var(--bg-elevated-hover);
    text-decoration: none;
  }
  .live-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .live-lang { color: var(--text-muted); }
  .live-progress, .live-time {
    font-size: var(--text-sm);
    font-variant-numeric: tabular-nums;
  }
  @media (max-width: 640px) {
    .live-link {
      grid-template-columns: auto 1fr;
      gap: var(--space-2);
    }
    .live-lang, .live-progress, .live-time {
      grid-column: 2;
      font-size: var(--text-xs);
    }
  }

  .empty {
    text-align: center;
    color: var(--text-muted);
  }
  .empty p { margin: var(--space-1) 0; }

  .no-pad { padding: 0; overflow: hidden; }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--text-sm);
  }
  th, td {
    text-align: left;
    padding: var(--space-3) var(--space-4);
    border-bottom: 1px solid var(--border);
  }
  tbody tr:last-child td { border-bottom: none; }
  th {
    font-weight: 500;
    color: var(--text-muted);
    font-size: var(--text-xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    background: var(--bg-elevated);
  }
  td a { color: var(--text); }
  td a:hover { color: var(--accent); }
  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .trunc {
    max-width: 40ch;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  details summary {
    cursor: pointer;
    list-style: none;
    padding: var(--space-2) 0;
    border-radius: var(--radius-sm);
  }
  details summary::-webkit-details-marker { display: none; }
  details summary h2 {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
  }
  details summary h2::before {
    content: "▸";
    color: var(--text-muted);
    font-size: var(--text-sm);
    transition: transform var(--dur) var(--ease);
    display: inline-block;
  }
  details[open] summary h2::before {
    transform: rotate(90deg);
  }
  details .count {
    color: var(--text-muted);
    font-weight: 400;
    font-size: var(--text-base);
  }
  .fail-body { margin-top: var(--space-3); }
  .fail-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .fail-row { padding: 0; }
  .fail-link {
    display: block;
    color: var(--text);
    text-decoration: none;
    padding: var(--space-3) var(--space-4);
  }
  .fail-link:hover { background: var(--bg-elevated-hover); text-decoration: none; }
  .fail-head {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    flex-wrap: wrap;
  }
  .fail-err {
    margin: var(--space-2) 0 0;
    color: var(--error);
    font-size: var(--text-sm);
    font-family: var(--font-mono);
    word-break: break-word;
  }
</style>
