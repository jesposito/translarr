<script lang="ts">
  import { onMount, tick } from 'svelte';

  interface Config {
    target_lang: string;
    [k: string]: unknown;
  }

  interface Submission {
    job_id: string;
    media_path: string;
    target_lang: string;
    submitted_at: number;
    dedup: boolean;
  }

  type ResultPanel =
    | { kind: 'success'; jobId: string; state: string; dedup: boolean }
    | { kind: 'error'; status: number | null; message: string };

  const STORAGE_KEY = 'translarr.recentSubmissions.v1';
  const MAX_RECENT = 5;

  // --- Form state ----------------------------------------------------------
  let mediaPath = $state('');
  let targetLang = $state('en');
  let sourceLang = $state('');
  let sourceTrack = $state<string>('');
  let force = $state(false);

  // --- Submission state ----------------------------------------------------
  let submitting = $state(false);
  let result = $state<ResultPanel | null>(null);
  let mediaPathError = $state<string | null>(null);
  let targetLangError = $state<string | null>(null);
  let sourceTrackError = $state<string | null>(null);
  let copied = $state(false);

  // --- Persistent recents --------------------------------------------------
  let recents = $state<Submission[]>([]);

  function loadRecents(): void {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as Submission[];
      if (Array.isArray(parsed)) {
        recents = parsed.slice(0, MAX_RECENT);
      }
    } catch {
      // Corrupt storage — start fresh, don't surface to user.
    }
  }

  function pushRecent(sub: Submission): void {
    // De-dup by job_id, newest first, cap to MAX_RECENT.
    const next = [sub, ...recents.filter((r) => r.job_id !== sub.job_id)].slice(
      0,
      MAX_RECENT
    );
    recents = next;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      // Quota or private-mode — non-fatal.
    }
  }

  function basename(p: string): string {
    if (!p) return '';
    const parts = p.split('/');
    return parts[parts.length - 1] || p;
  }

  function relTime(epoch: number): string {
    const diff = Math.max(0, Date.now() / 1000 - epoch);
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} hr ago`;
    return `${Math.floor(diff / 86400)} days ago`;
  }

  function isoTime(epoch: number): string {
    return new Date(epoch * 1000).toISOString();
  }

  // --- Validation ----------------------------------------------------------
  function validate(): boolean {
    let ok = true;
    mediaPathError = null;
    targetLangError = null;
    sourceTrackError = null;

    if (!mediaPath.trim()) {
      mediaPathError = 'Media path is required.';
      ok = false;
    }

    const tl = targetLang.trim();
    if (!tl) {
      targetLangError = 'Target language is required.';
      ok = false;
    } else if (!/^[a-z]{2,3}$/.test(tl)) {
      targetLangError = 'Use 2 or 3 lowercase letters (e.g. en, eng).';
      ok = false;
    }

    if (sourceTrack !== '') {
      const n = Number(sourceTrack);
      if (!Number.isInteger(n) || n < 0 || n > 20) {
        sourceTrackError = 'Track index must be an integer 0–20.';
        ok = false;
      }
    }

    return ok;
  }

  async function submit(e: Event): Promise<void> {
    e.preventDefault();
    if (submitting) return;
    if (!validate()) {
      // M9: wait for Svelte to render the err <p> elements so the
      // aria-describedby links resolve, THEN focus the first invalid
      // field. Without `tick`, the SR can race the render and miss
      // the description on first focus.
      await tick();
      const firstInvalid = document.querySelector<HTMLElement>(
        '[aria-invalid="true"]'
      );
      firstInvalid?.focus();
      return;
    }

    submitting = true;
    result = null;
    copied = false;

    const body: Record<string, unknown> = {
      media_path: mediaPath.trim(),
      target_lang: targetLang.trim(),
      force
    };
    if (sourceLang.trim()) body.source_lang = sourceLang.trim();
    if (sourceTrack !== '') body.source_track_index = Number(sourceTrack);

    try {
      const r = await fetch('/translate', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body)
      });
      const text = await r.text();
      let data: { status?: string; job_id?: string; detail?: unknown } = {};
      try {
        data = text ? (JSON.parse(text) as typeof data) : {};
      } catch {
        // Non-JSON body — fall through with empty data, surface raw status.
      }

      if (!r.ok) {
        const detailMsg =
          typeof data.detail === 'string'
            ? data.detail
            : data.detail
              ? JSON.stringify(data.detail)
              : text || 'No additional detail.';
        result = {
          kind: 'error',
          status: r.status,
          message: `HTTP ${r.status}: ${detailMsg}`
        };
        return;
      }

      if (!data.job_id) {
        result = {
          kind: 'error',
          status: r.status,
          message: 'Server responded without a job_id.'
        };
        return;
      }

      const dedup = data.status === 'dedup';
      result = {
        kind: 'success',
        jobId: data.job_id,
        state: dedup ? 'already queued' : 'queued',
        dedup
      };

      pushRecent({
        job_id: data.job_id,
        media_path: body.media_path as string,
        target_lang: body.target_lang as string,
        submitted_at: Date.now() / 1000,
        dedup
      });
    } catch (err) {
      result = {
        kind: 'error',
        status: null,
        message: err instanceof Error ? err.message : String(err)
      };
    } finally {
      submitting = false;
      // M7 (a11y audit): on a server error the submit button is now
      // disabled — focus the result heading so screen-reader and
      // keyboard users land on the error message instead of getting
      // stuck on a dead button.
      if (result?.kind === 'error') {
        await tick();
        document.getElementById('result-heading')?.focus();
      }
    }
  }

  function clearForm(): void {
    mediaPath = '';
    sourceLang = '';
    sourceTrack = '';
    force = false;
    mediaPathError = null;
    targetLangError = null;
    sourceTrackError = null;
    result = null;
    copied = false;
    // Re-focus the first field for keyboard users.
    document.getElementById('media-path')?.focus();
  }

  async function copyJobId(jobId: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(jobId);
      copied = true;
      setTimeout(() => (copied = false), 2000);
    } catch {
      copied = false;
    }
  }

  async function loadConfigDefaults(): Promise<void> {
    try {
      const r = await fetch('/config');
      if (!r.ok) return;
      const cfg = (await r.json()) as Config;
      if (cfg.target_lang && targetLang === 'en') {
        targetLang = cfg.target_lang;
      }
    } catch {
      // Non-fatal — user keeps the default 'en'.
    }
  }

  onMount(() => {
    loadRecents();
    loadConfigDefaults();
  });
</script>

<svelte:head>
  <title>Translate — Translarr</title>
</svelte:head>

<header class="page-head">
  <h1>Translate</h1>
  <p class="sub">
    Submit a single file translation. For automation, use arr-stack webhooks
    instead.
  </p>
</header>

<section class="card" aria-labelledby="form-heading">
  <h2 id="form-heading" class="sr-only">Submit a translation</h2>

  <form onsubmit={submit} novalidate>
    <fieldset>
      <legend class="sr-only">Translation request</legend>
      <!-- Mo9 (a11y audit): a key explaining what * means, for cognitive
           accessibility. The aria-hidden span on each * still hides the
           character from SR (which already gets "required" from
           aria-required), but sighted readers now have a key. -->
      <p class="form-legend muted small">
        Fields marked <span class="req" aria-hidden="true">*</span> are required.
      </p>

      <div class="field">
        <label for="media-path">
          Media path
          <span class="req" aria-hidden="true">*</span>
        </label>
        <input
          id="media-path"
          type="text"
          bind:value={mediaPath}
          placeholder="Movies/Example/example.mkv"
          required
          aria-required="true"
          aria-invalid={mediaPathError ? 'true' : undefined}
          aria-describedby="media-path-help{mediaPathError ? ' media-path-err' : ''}"
          autocomplete="off"
          spellcheck="false"
        />
        <p id="media-path-help" class="hint">
          The Translarr server reads this file. If it's inside
          <code class="mono">MEDIA_ROOT</code>, use a relative path; otherwise
          provide an absolute container path.
        </p>
        {#if mediaPathError}
          <p id="media-path-err" class="err">{mediaPathError}</p>
        {/if}
      </div>

      <div class="field">
        <label for="target-lang">
          Target language
          <span class="req" aria-hidden="true">*</span>
        </label>
        <input
          id="target-lang"
          type="text"
          bind:value={targetLang}
          maxlength="3"
          placeholder="en"
          required
          aria-required="true"
          aria-invalid={targetLangError ? 'true' : undefined}
          aria-describedby="target-lang-help{targetLangError ? ' target-lang-err' : ''}"
          autocomplete="off"
          spellcheck="false"
        />
        <p id="target-lang-help" class="hint">
          ISO 639-1 (2 letters) or 639-2 (3 letters). Lowercase.
        </p>
        {#if targetLangError}
          <p id="target-lang-err" class="err">{targetLangError}</p>
        {/if}
      </div>

      <div class="field">
        <label for="source-lang">Source language</label>
        <input
          id="source-lang"
          type="text"
          bind:value={sourceLang}
          maxlength="3"
          placeholder="(auto-detect)"
          aria-describedby="source-lang-help"
          autocomplete="off"
          spellcheck="false"
        />
        <p id="source-lang-help" class="hint">
          Optional. Auto-detected from the chosen subtitle track when blank.
        </p>
      </div>

      <div class="field">
        <label for="source-track">Source track index</label>
        <input
          id="source-track"
          type="number"
          bind:value={sourceTrack}
          min="0"
          max="20"
          step="1"
          placeholder="(auto-pick)"
          aria-invalid={sourceTrackError ? 'true' : undefined}
          aria-describedby="source-track-help{sourceTrackError ? ' source-track-err' : ''}"
        />
        <p id="source-track-help" class="hint">
          Optional. Pin a specific subtitle stream from ffprobe (0–20). Leave
          blank to let Translarr pick the best non-target track.
        </p>
        {#if sourceTrackError}
          <p id="source-track-err" class="err">{sourceTrackError}</p>
        {/if}
      </div>

      <div class="field checkbox-field">
        <label class="checkbox-label">
          <input type="checkbox" bind:checked={force} />
          <span>Force re-translate even if output already exists</span>
        </label>
        <p class="hint">
          Backs up any existing <code class="mono">.translarr.srt</code> before
          overwriting.
        </p>
      </div>
    </fieldset>

    <div class="actions">
      <button
        type="submit"
        class="btn btn-primary"
        disabled={submitting || !mediaPath.trim()}
      >
        {submitting ? 'Submitting…' : 'Translate'}
      </button>
      <button
        type="button"
        class="btn btn-ghost"
        onclick={clearForm}
        disabled={submitting}
      >
        Clear form
      </button>
    </div>
  </form>
</section>

{#if result}
  <!-- M6 (a11y audit): role=alert / role=status already imply assertive
       / polite live politeness. Setting aria-live on top caused some
       screen readers (NVDA in certain configs) to announce twice. -->
  <section
    class="card result-card"
    class:result-error={result.kind === 'error'}
    class:result-success={result.kind === 'success'}
    aria-labelledby="result-heading"
    role={result.kind === 'error' ? 'alert' : 'status'}
    aria-atomic="true"
  >
    <!-- M7 (a11y audit): tabindex makes the heading programmatically
         focusable so we can move focus here on a server error (the
         submit button has gone disabled and there's nowhere to read
         the error message otherwise). -->
    <h2 id="result-heading" tabindex="-1">
      {result.kind === 'success' ? 'Submitted' : 'Submission failed'}
    </h2>
    {#if result.kind === 'success'}
      {@const successJobId = result.jobId}
      {@const successState = result.state}
      <dl class="kv">
        <dt>Job ID</dt>
        <dd>
          <span class="mono">{successJobId}</span>
          <button
            type="button"
            class="btn btn-ghost btn-sm"
            onclick={() => copyJobId(successJobId)}
            aria-label="Copy job ID to clipboard"
          >
            {copied ? 'Copied' : 'Copy'}
          </button>
        </dd>

        <dt>State</dt>
        <dd>
          <span class="pill" data-state="queued">
            {successState}
          </span>
        </dd>

        <dt>Next</dt>
        <dd>
          <a class="btn btn-primary btn-sm" href={`/jobs/${successJobId}`}>
            Watch progress
          </a>
        </dd>
      </dl>
      {#if result.dedup}
        <p class="hint">
          A matching job was already queued; we returned that one instead of
          creating a duplicate.
        </p>
      {/if}
    {:else}
      <p class="err">{result.message}</p>
      {#if result.status !== null}
        <p class="hint">
          HTTP status code: <code class="mono">{result.status}</code>.
        </p>
      {/if}
    {/if}
  </section>
{/if}

<section aria-labelledby="recent-heading">
  <h2 id="recent-heading">Recent submissions</h2>
  {#if recents.length === 0}
    <div class="card empty">
      <p>Nothing submitted from this browser yet.</p>
    </div>
  {:else}
    <ul class="recent-list">
      {#each recents as r (r.job_id)}
        <li class="card recent-row">
          <a href={`/jobs/${r.job_id}`} class="recent-link">
            <span class="mono short-id">{r.job_id.slice(0, 8)}</span>
            <span class="recent-name" title={r.media_path}>
              {basename(r.media_path)}
            </span>
            <span class="recent-lang mono"><span aria-hidden="true">→ </span>{r.target_lang}</span>
            <time class="recent-time muted" datetime={isoTime(r.submitted_at)}>
              {relTime(r.submitted_at)}
            </time>
          </a>
        </li>
      {/each}
    </ul>
    <p class="hint">
      Stored locally in this browser only. Clears when you wipe site data.
    </p>
  {/if}
</section>

<style>
  .page-head {
    margin-bottom: var(--space-6);
  }
  .sub {
    margin: var(--space-2) 0 0;
    color: var(--text-muted);
    font-size: var(--text-sm);
    max-width: 60ch;
  }

  section {
    margin-bottom: var(--space-5);
  }
  section h2 {
    margin: 0 0 var(--space-4);
  }

  fieldset {
    border: 0;
    padding: 0;
    margin: 0;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    margin-bottom: var(--space-4);
  }
  .field:last-child {
    margin-bottom: 0;
  }

  .field label {
    font-size: var(--text-sm);
    font-weight: 500;
    color: var(--text);
  }
  .req {
    color: var(--accent);
    margin-left: 2px;
  }

  input[type="text"],
  input[type="number"] {
    background: var(--bg-input);
    color: var(--text);
    /* C1: --border-input meets WCAG 1.4.11 (3:1 vs bg) — was --border. */
    border: 1px solid var(--border-input);
    border-radius: var(--radius-sm);
    padding: var(--space-2) var(--space-3);
    font-family: inherit;
    font-size: var(--text-base);
    /* M7/quillr-mobile-equal-citizen: 44px hit area on mobile */
    min-height: 44px;
    width: 100%;
    max-width: 480px;
    transition: border-color var(--dur) var(--ease);
  }
  input[type="text"]:focus,
  input[type="number"]:focus {
    border-color: var(--accent);
    outline: 2px solid var(--accent);
    outline-offset: 0;
  }
  input[aria-invalid="true"] {
    border-color: var(--error);
  }

  .checkbox-field {
    flex-direction: column;
  }
  .checkbox-label {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    cursor: pointer;
    font-weight: 500;
  }
  .checkbox-label input[type="checkbox"] {
    width: 18px;
    height: 18px;
    accent-color: var(--accent);
    cursor: pointer;
  }

  .hint {
    margin: 0;
    color: var(--text-muted);
    font-size: var(--text-sm);
    line-height: 1.5;
    max-width: 60ch;
  }
  .hint code.mono {
    background: var(--bg-input);
    padding: 1px 6px;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border);
  }

  .err {
    margin: 0;
    color: var(--error);
    font-size: var(--text-sm);
  }

  .actions {
    display: flex;
    gap: var(--space-3);
    margin-top: var(--space-5);
    flex-wrap: wrap;
  }

  .btn-sm {
    min-height: 28px;
    padding: 0 var(--space-3);
    font-size: var(--text-xs);
  }

  .result-card {
    margin-bottom: var(--space-5);
  }
  .result-card.result-success {
    border-color: var(--success);
  }
  .result-card.result-error {
    border-color: var(--error);
  }
  .result-card .err {
    font-family: var(--font-mono);
    word-break: break-word;
  }

  .kv {
    display: grid;
    grid-template-columns: 120px 1fr;
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
    display: inline-flex;
    align-items: center;
    gap: var(--space-3);
    flex-wrap: wrap;
    word-break: break-all;
  }
  @media (max-width: 640px) {
    .kv { grid-template-columns: 1fr; gap: var(--space-1) 0; }
    .kv dt { margin-top: var(--space-2); }
  }

  .recent-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .recent-row {
    padding: 0;
  }
  .recent-link {
    display: grid;
    grid-template-columns: auto 1fr auto auto;
    gap: var(--space-4);
    align-items: center;
    padding: var(--space-3) var(--space-4);
    color: var(--text);
    text-decoration: none;
    min-height: 44px;
    border-radius: var(--radius-md);
  }
  .recent-link:hover {
    background: var(--bg-elevated-hover);
    text-decoration: none;
  }
  .short-id {
    color: var(--text-muted);
    font-size: var(--text-sm);
  }
  .recent-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }
  .recent-lang {
    color: var(--text-muted);
    font-size: var(--text-sm);
  }
  .recent-time {
    font-size: var(--text-sm);
    font-variant-numeric: tabular-nums;
  }
  @media (max-width: 640px) {
    .recent-link {
      grid-template-columns: auto 1fr;
      gap: var(--space-2);
    }
    .recent-lang, .recent-time {
      grid-column: 2;
      font-size: var(--text-xs);
    }
  }
  .empty {
    text-align: center;
    color: var(--text-muted);
  }
  .muted { color: var(--text-muted); }
</style>
