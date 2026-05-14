<script lang="ts">
  import { onMount, onDestroy } from 'svelte';
  import { page } from '$app/stores';
  import { goto } from '$app/navigation';

  type JobState = 'queued' | 'running' | 'retrying' | 'done' | 'failed' | 'cancelled';

  interface Job {
    id: string;
    state: JobState;
    media_path: string;
    target_lang: string;
    output_path: string | null;
    attempts: number;
    max_attempts: number;
    cost_cents: number;
    tokens_in: number;
    tokens_out: number;
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

  let job = $state<Job | null>(null);
  let health = $state<Health | null>(null);
  let loadError = $state<string | null>(null);
  let lastLoadError = $state<string | null>(null);  // dedupe alert re-announce
  let toast = $state<{ kind: 'success' | 'error'; msg: string } | null>(null);
  let actionPending = $state(false);
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let toastTimer: ReturnType<typeof setTimeout> | null = null;
  // Transition-only announcement string for SR users. Polling re-fetches
  // the same job every 2s; the wrapping live region used to re-announce
  // the entire Status card every tick. We now emit a one-line message
  // ONLY when the state actually transitions (queued -> running -> done).
  let stateAnnouncement = $state('');
  let prevState = $state<JobState | null>(null);

  const jobId = $derived($page.params.id);
  const shortId = $derived(jobId ? jobId.slice(0, 8) : '');
  const terminal = $derived(
    job ? ['done', 'failed', 'cancelled'].includes(job.state) : false
  );

  function dollars(cents: number): string {
    return `$${(cents / 100).toFixed(4)}`;
  }
  function isoTime(epoch: number | null): string {
    if (!epoch) return '—';
    return new Date(epoch * 1000).toISOString();
  }
  function relTime(epoch: number | null): string {
    if (!epoch) return '';
    const diff = Math.max(0, Date.now() / 1000 - epoch);
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} hr ago`;
    return `${Math.floor(diff / 86400)} days ago`;
  }

  async function load() {
    if (!jobId) return;
    try {
      const r = await fetch(`/jobs/${jobId}`);
      if (r.status === 404) {
        loadError = 'Job not found.';
        job = null;
        stopPolling();
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const next = (await r.json()) as Job;
      // Emit a transition-only announcement so SR users only hear about
      // ACTUAL state changes, not every 2s repoll.
      if (prevState && prevState !== next.state) {
        const verb = {
          queued: 'queued',
          running: 'now running',
          retrying: `retrying (attempt ${next.attempts} of ${next.max_attempts})`,
          done: 'completed',
          failed: 'failed',
          cancelled: 'cancelled',
        }[next.state];
        stateAnnouncement = `Job ${shortId} is ${verb}.`;
      }
      prevState = next.state;
      job = next;
      loadError = null;
      lastLoadError = null;
      if (terminal) stopPolling();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      // Only flip loadError when the error TEXT changes — prevents the
      // role="alert" re-announcing the same network error every 2s tick.
      if (msg !== lastLoadError) {
        loadError = msg;
        lastLoadError = msg;
      }
    }
  }

  async function loadHealth() {
    try {
      const r = await fetch('/health');
      if (r.ok) health = await r.json();
    } catch {
      // non-fatal
    }
  }

  function startPolling() {
    if (pollTimer !== null) return;
    pollTimer = setInterval(() => {
      if (terminal) {
        stopPolling();
        return;
      }
      load();
    }, 2000);
  }

  function stopPolling() {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function showToast(kind: 'success' | 'error', msg: string) {
    if (toastTimer !== null) {
      clearTimeout(toastTimer);
      toastTimer = null;
    }
    toast = { kind, msg };
    if (kind === 'success') {
      toastTimer = setTimeout(() => (toast = null), 5000);
    }
  }

  async function cancel() {
    if (!job || actionPending) return;
    actionPending = true;
    try {
      const r = await fetch(`/jobs/${job.id}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      showToast('success', `Job ${shortId} cancelled.`);
      await load();
    } catch (e) {
      showToast('error', `Cancel failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      actionPending = false;
    }
  }

  async function retry() {
    if (!job || actionPending) return;
    actionPending = true;
    try {
      const r = await fetch('/translate', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          media_path: job.media_path,
          target_lang: job.target_lang
        })
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as { job_id?: string };
      if (data.job_id) {
        showToast('success', `Re-queued as ${data.job_id.slice(0, 8)}.`);
        await goto(`/jobs/${data.job_id}`);
      } else {
        showToast('success', 'Re-queued.');
        await load();
      }
    } catch (e) {
      showToast('error', `Retry failed: ${e instanceof Error ? e.message : e}`);
    } finally {
      actionPending = false;
    }
  }

  async function copyPath(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      showToast('success', 'Path copied.');
    } catch {
      showToast('error', 'Copy failed.');
    }
  }

  onMount(() => {
    loadHealth();
    load().then(() => {
      if (!terminal) startPolling();
    });
  });

  onDestroy(() => {
    stopPolling();
    if (toastTimer !== null) clearTimeout(toastTimer);
  });
</script>

<svelte:head>
  <title>Job {shortId} — Translarr</title>
</svelte:head>

<p class="backlink">
  <a href="/">← Back to dashboard</a>
</p>

<header class="page-head">
  <h1>Job <span class="mono" aria-label="Job ID {shortId}">{shortId}</span></h1>
  {#if job}
    <span class="pill big" data-state={job.state}>
      {job.state}
    </span>
  {/if}
</header>

{#if loadError}
  <div class="error-banner" role="alert">{loadError}</div>
{/if}

{#if !job && !loadError}
  <div class="card" aria-busy="true">
    <div class="skeleton" style="width: 60%; height: 16px; margin-bottom: 12px;"></div>
    <div class="skeleton" style="width: 40%; height: 12px; margin-bottom: 12px;"></div>
    <div class="skeleton" style="width: 80%; height: 12px;"></div>
  </div>
{:else if job}
  <!-- Narrow live region: announces ONLY the state transition, not the
       entire Status card on every 2s repoll. WCAG 1.3.1 + APG live-region
       guidance. -->
  <span class="sr-only" aria-live="polite" aria-atomic="true">{stateAnnouncement}</span>
  <section class="card" aria-labelledby="status-heading">
    <h2 id="status-heading">Status</h2>
    <dl class="kv">
      <dt>Media path</dt>
      <dd>
        <button
          type="button"
          class="btn btn-ghost path-btn"
          onclick={() => copyPath(job!.media_path)}
          aria-label="Copy media path to clipboard"
        >
          <span class="mono path-text">{job.media_path}</span>
        </button>
      </dd>

      <dt>Target language</dt>
      <dd class="mono">{job.target_lang}</dd>

      <dt>Provider / model</dt>
      <dd class="mono">
        {#if health}
          {health.llm_provider} / {health.llm_model}
        {:else}
          —
        {/if}
      </dd>

      <dt>Created</dt>
      <dd>
        {#if job.created_at}
          <time datetime={isoTime(job.created_at)}>{isoTime(job.created_at)}</time>
          <span class="muted">· {relTime(job.created_at)}</span>
        {:else}
          —
        {/if}
      </dd>

      <dt>Updated</dt>
      <dd>
        {#if job.updated_at}
          <time datetime={isoTime(job.updated_at)}>{isoTime(job.updated_at)}</time>
          <span class="muted">· {relTime(job.updated_at)}</span>
        {:else}
          —
        {/if}
      </dd>

      <dt>Finished</dt>
      <dd>
        {#if job.finished_at}
          <time datetime={isoTime(job.finished_at)}>{isoTime(job.finished_at)}</time>
          <span class="muted">· {relTime(job.finished_at)}</span>
        {:else}
          —
        {/if}
      </dd>

      <dt>Attempts</dt>
      <dd>{job.attempts} / {job.max_attempts}</dd>

      <dt>Cost</dt>
      <dd>{dollars(job.cost_cents)} <span class="muted">({job.cost_cents}¢)</span></dd>

      <dt>Tokens</dt>
      <dd>
        in <span class="mono">{job.tokens_in.toLocaleString()}</span>
        · out <span class="mono">{job.tokens_out.toLocaleString()}</span>
      </dd>
    </dl>
  </section>

  {#if job.state === 'done' && job.output_path}
    <section class="card output-card" aria-labelledby="output-heading">
      <h2 id="output-heading">Output</h2>
      <p class="muted small">Translated subtitle written to:</p>
      <div class="path-row">
        <code class="code-block">{job.output_path}</code>
        <button
          type="button"
          class="btn btn-ghost"
          onclick={() => copyPath(job!.output_path!)}
          aria-label="Copy output path to clipboard"
        >Copy path</button>
      </div>
    </section>
  {/if}

  {#if job.state === 'failed' && job.error}
    <section class="card error-card" aria-labelledby="error-heading">
      <h2 id="error-heading">
        <span aria-hidden="true">⚠</span>
        <span>Error</span>
      </h2>
      <pre class="error-msg">{job.error}</pre>
    </section>
  {/if}

  <section class="actions" aria-label="Job actions">
    {#if ['queued', 'running', 'retrying'].includes(job.state)}
      <button
        type="button"
        class="btn btn-danger"
        onclick={cancel}
        disabled={actionPending}
      >
        {actionPending ? 'Cancelling…' : 'Cancel job'}
      </button>
    {/if}

    {#if ['failed', 'cancelled'].includes(job.state)}
      <button
        type="button"
        class="btn btn-primary"
        onclick={retry}
        disabled={actionPending}
      >
        {actionPending ? 'Retrying…' : 'Retry'}
      </button>
    {/if}
  </section>
{/if}

{#if toast}
  <div
    class="toast"
    class:toast-success={toast.kind === 'success'}
    class:toast-error={toast.kind === 'error'}
    role={toast.kind === 'error' ? 'alert' : 'status'}
    aria-live={toast.kind === 'error' ? 'assertive' : 'polite'}
  >
    <span>{toast.msg}</span>
    <button
      type="button"
      class="toast-close"
      aria-label="Dismiss notification"
      onclick={() => (toast = null)}
    >✕</button>
  </div>
{/if}

<style>
  .backlink {
    margin: 0 0 var(--space-4);
    font-size: var(--text-sm);
  }
  .backlink a { color: var(--text-muted); }
  .backlink a:hover { color: var(--accent); }

  .page-head {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    margin-bottom: var(--space-5);
    flex-wrap: wrap;
  }
  .page-head h1 { display: inline; }
  .pill.big {
    font-size: var(--text-sm);
    padding: 4px 12px;
  }

  .error-banner {
    background: var(--error-bg);
    border: 1px solid var(--error);
    color: var(--error);
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius-sm);
    margin-bottom: var(--space-5);
  }

  section { margin-bottom: var(--space-5); }
  h2 { margin-bottom: var(--space-4); }
  .small { font-size: var(--text-sm); }
  .muted { color: var(--text-muted); }

  .kv {
    display: grid;
    grid-template-columns: 160px 1fr;
    gap: var(--space-3) var(--space-4);
    margin: 0;
  }
  .kv dt {
    color: var(--text-muted);
    font-size: var(--text-sm);
    font-weight: 500;
  }
  .kv dd {
    margin: 0;
    word-break: break-word;
  }
  @media (max-width: 640px) {
    .kv { grid-template-columns: 1fr; gap: var(--space-1) 0; }
    .kv dt { margin-top: var(--space-2); }
  }

  .path-btn {
    min-height: 36px;
    max-width: 100%;
    padding: var(--space-2) var(--space-3);
    justify-content: flex-start;
    text-align: left;
  }
  .path-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 100%;
  }

  .output-card { border-color: var(--success); }
  .path-row {
    display: flex;
    gap: var(--space-3);
    align-items: flex-start;
    flex-wrap: wrap;
    margin-top: var(--space-3);
  }
  .path-row .code-block { flex: 1; min-width: 0; }

  .error-card { border-color: var(--error); }
  .error-card h2 {
    color: var(--error);
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }
  .error-msg {
    background: var(--bg-input);
    color: var(--error);
    font-family: var(--font-mono);
    font-size: var(--text-sm);
    padding: var(--space-3);
    border-radius: var(--radius-sm);
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
    border: 1px solid var(--border);
  }

  .actions {
    display: flex;
    gap: var(--space-3);
    margin-top: var(--space-5);
  }

  .toast {
    position: fixed;
    /* M6: clear iOS home indicator + side bezel */
    bottom: max(var(--space-4), env(safe-area-inset-bottom));
    right: max(var(--space-4), env(safe-area-inset-right));
    background: var(--bg-elevated);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-sm);
    padding: var(--space-3) var(--space-4);
    display: flex;
    align-items: center;
    gap: var(--space-3);
    min-width: 240px;
    max-width: 480px;
    box-shadow: 0 4px 24px rgba(0, 0, 0, 0.4);
    animation: slide-in var(--dur) var(--ease);
    z-index: 100;
  }
  .toast-success { border-color: var(--success); }
  .toast-error { border-color: var(--error); color: var(--error); }
  .toast-close {
    background: transparent;
    border: none;
    color: inherit;
    font-size: var(--text-base);
    cursor: pointer;
    /* m9: 40x40 touch target — was 24x24 (below quillr-mobile 44 floor;
       40x40 is the pragmatic compromise since the toast is dense). */
    width: 40px;
    height: 40px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-sm);
  }
  .toast-close:hover { background: var(--bg-elevated-hover); }

  @keyframes slide-in {
    from { transform: translateY(8px); opacity: 0; }
    to   { transform: translateY(0);   opacity: 1; }
  }
</style>
