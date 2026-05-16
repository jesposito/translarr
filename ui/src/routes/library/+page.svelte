<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { page } from '$app/stores';

  interface DirEntry {
    name: string;
    path: string;
    has_media: boolean;
  }

  interface Translation {
    lang: string;
    path: string;
    size: number;
  }

  interface FileEntry {
    name: string;
    path: string;
    size: number;
    translated: boolean;
    translations: Translation[];
  }

  interface BrowseResult {
    path: string;
    parent: string | null;
    dirs: DirEntry[];
    files: FileEntry[];
    total_files: number;
    translated_files: number;
    error?: string;
  }

  interface SeriesMatch {
    id: string;
    source_lang: string | null;
    target_lang: string | null;
    path_prefix: string | null;
    auto_translate: number;
  }

  let currentPath = $state('');
  let data = $state<BrowseResult | null>(null);
  let loading = $state(false);
  let error = $state<string | null>(null);
  let crumbs = $state<{ label: string; path: string }[]>([]);
  let seriesMatch = $state<SeriesMatch | null>(null);
  let seriesLoading = $state(false);
  // Inline series config form.
  let showSeriesForm = $state(false);
  let seriesFormLang = $state('');
  let seriesFormTarget = $state('');
  let seriesFormId = $state('');
  let seriesFormSaving = $state(false);
  // C2 (a11y audit): remember which button opened the series form so we
  // can return focus to it on close. Keyboard users otherwise lose tab
  // position after Cancel / Save.
  let seriesFormTrigger: HTMLElement | null = null;
  // C3 (a11y audit): inline toast — replaces alert() so non-modal
  // notifications don't yank focus and screen-reader users hear them
  // via a live region instead of an OS dialog.
  let toast = $state<{ kind: 'success' | 'error'; msg: string } | null>(null);
  function showToast(kind: 'success' | 'error', msg: string) {
    toast = { kind, msg };
    if (kind === 'success') {
      setTimeout(() => { if (toast?.msg === msg) toast = null; }, 5000);
    }
  }

  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  }

  function dirName(path: string): string {
    const parts = path.split('/').filter(Boolean);
    return parts[parts.length - 1] || path;
  }

  function slugify(name: string): string {
    return name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
  }

  function buildCrumbs(path: string): { label: string; path: string }[] {
    if (!path || path === '/') return [{ label: 'Media root', path: '' }];
    const parts = path.split('/').filter(Boolean);
    const result: { label: string; path: string }[] = [{ label: 'Media root', path: '' }];
    let accumulated = '';
    for (const part of parts) {
      accumulated = accumulated ? `${accumulated}/${part}` : part;
      result.push({ label: part, path: accumulated });
    }
    return result;
  }

  async function browse(path: string = '') {
    loading = true;
    error = null;
    showSeriesForm = false;
    try {
      const url = path ? `/browse?path=${encodeURIComponent(path)}` : '/browse';
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const result: BrowseResult = await r.json();
      if (result.error) {
        error = result.error;
        return;
      }
      data = result;
      currentPath = result.path === '/' ? '' : result.path;
      crumbs = buildCrumbs(result.path);
      // C1 (a11y audit): the URL is now driven by SvelteKit's router
      // (real <a> navigation + $effect below). No history manipulation
      // here — that broke middle-click / cmd-click "open in new tab".
      // Look up series config for this path.
      await lookupSeries(path);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  async function lookupSeries(path: string) {
    if (!path) { seriesMatch = null; return; }
    seriesLoading = true;
    try {
      const r = await fetch(`/series/lookup?path=${encodeURIComponent(path)}`);
      if (!r.ok) { seriesMatch = null; return; }
      const data = await r.json();
      seriesMatch = data.match;
    } catch {
      seriesMatch = null;
    } finally {
      seriesLoading = false;
    }
  }

  async function openSeriesForm(e: MouseEvent | KeyboardEvent) {
    // C2 (a11y audit): capture the trigger so we can return focus on close.
    seriesFormTrigger = e.currentTarget as HTMLElement;
    seriesFormId = seriesMatch?.id || slugify(dirName(currentPath));
    seriesFormLang = seriesMatch?.source_lang || '';
    seriesFormTarget = seriesMatch?.target_lang || '';
    showSeriesForm = true;
    await tick();
    document.getElementById('series-id')?.focus();
  }

  function closeSeriesForm() {
    showSeriesForm = false;
    // Restore focus to whichever button opened the form.
    setTimeout(() => seriesFormTrigger?.focus(), 0);
  }

  function onSeriesFormKey(e: KeyboardEvent) {
    // C2: Escape closes the form, matching dialog conventions.
    if (e.key === 'Escape' && showSeriesForm) {
      e.preventDefault();
      closeSeriesForm();
    }
  }

  async function saveSeriesConfig() {
    if (!seriesFormId.trim()) return;
    seriesFormSaving = true;
    try {
      const r = await fetch(`/series/${encodeURIComponent(seriesFormId.trim())}`, {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          source_lang: seriesFormLang.trim() || null,
          target_lang: seriesFormTarget.trim() || null,
          path_prefix: currentPath,
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await lookupSeries(currentPath);
      showToast('success', `Series "${seriesFormId.trim()}" saved`);
      closeSeriesForm();
    } catch (e) {
      // C3 (a11y audit): toast instead of alert().
      showToast('error', e instanceof Error ? e.message : String(e));
    } finally {
      seriesFormSaving = false;
    }
  }

  async function translateFile(filePath: string) {
    try {
      const r = await fetch('/translate', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ media_path: filePath }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${r.status}`);
      }
      const result = await r.json();
      await browse(currentPath);
      showToast('success', `Job queued: ${result.job_id || result.id || 'see Dashboard'}`);
    } catch (e) {
      showToast('error', e instanceof Error ? e.message : String(e));
    }
  }

  // C1 (a11y audit): drive the displayed directory from the URL. Real
  // <a href> navigation updates $page.url; we re-fetch when ?path changes.
  // Side effect: middle-click / cmd-click "open in new tab" now works
  // because we no longer preventDefault on the row anchors.
  $effect(() => {
    const path = $page.url.searchParams.get('path') || '';
    if (path !== currentPath) {
      browse(path);
    }
  });

  onMount(() => {
    // First load: read the path query before $effect tracks it.
    const params = new URLSearchParams(window.location.search);
    browse(params.get('path') || '');
  });
</script>

<svelte:window onkeydown={onSeriesFormKey} />

<svelte:head>
  <title>Library — Translarr</title>
</svelte:head>

<header class="page-head">
  <h1>Library</h1>
  <p class="sub">Browse your media library. See which files have translations.</p>
</header>

{#if error}
  <div class="error-banner" role="alert">{error}</div>
{/if}

{#if toast}
  <div
    class="error-banner"
    class:toast-success={toast.kind === 'success'}
    role={toast.kind === 'error' ? 'alert' : 'status'}
    aria-atomic="true"
  >
    <span>{toast.msg}</span>
    <button
      type="button"
      class="toast-dismiss"
      aria-label="Dismiss notification"
      onclick={() => (toast = null)}
    >✕</button>
  </div>
{/if}

<nav class="breadcrumbs" aria-label="Breadcrumb">
  <ol>
    {#each crumbs as crumb (crumb.path)}
      <li>
        {#if crumb.path === currentPath}
          <span aria-current="page">{crumb.label}</span>
        {:else}
          <!-- C1 (a11y audit): real <a> navigation. Browser handles
               middle-click / cmd-click correctly; SvelteKit handles
               regular clicks; $effect re-fetches on URL change. -->
          <a href="/library{crumb.path ? `?path=${encodeURIComponent(crumb.path)}` : ''}">{crumb.label}</a>
        {/if}
      </li>
    {/each}
  </ol>
</nav>

{#if data && data.total_files > 0}
  <div class="coverage-bar">
    <span class="coverage-text">
      {data.translated_files}/{data.total_files} files translated
      ({data.total_files > 0 ? ((data.translated_files / data.total_files) * 100).toFixed(0) : 0}%)
    </span>
    <div class="cap-bar" aria-hidden="true">
      <div class="cap-fill" style="width: {data.total_files > 0 ? (data.translated_files / data.total_files) * 100 : 0}%"></div>
    </div>
  </div>
{/if}

<!-- Series config bar: shown when browsing a directory with media files -->
{#if data && (data.files.length > 0 || data.dirs.some(d => d.has_media)) && currentPath}
  <div class="series-bar">
    {#if seriesLoading}
      <span class="muted">Checking series config…</span>
    {:else if seriesMatch}
      <div class="series-info">
        <span class="series-label">Series:</span>
        <strong>{seriesMatch.id}</strong>
        {#if seriesMatch.source_lang}
          <span class="muted">{seriesMatch.source_lang} → {seriesMatch.target_lang || 'en'}</span>
        {/if}
        <a href="/glossary?series={encodeURIComponent(seriesMatch.id)}" class="btn btn-ghost btn-sm">Glossary</a>
        <button type="button" class="btn btn-ghost btn-sm" onclick={openSeriesForm}>Edit</button>
      </div>
    {:else}
      <div class="series-info">
        <span class="muted">No series config for this directory.</span>
        <button type="button" class="btn btn-ghost btn-sm" onclick={openSeriesForm}>
          Set up series
        </button>
      </div>
    {/if}
  </div>

  {#if showSeriesForm}
    <!-- C2 (a11y audit): role=region + labelledby gives SR users a way
         to find the form via landmark navigation. The form is NOT a
         modal (no overlay, no focus trap) but it does push focus to
         the first field on open and restore it on close. -->
    <section class="card series-form" role="region" aria-labelledby="series-form-heading">
      <h3 id="series-form-heading">Configure series</h3>
      <p class="hint">
        Set source/target language defaults for all files under this directory.
        Also creates a glossary scoped to this series.
      </p>
      <div class="form-row">
        <label for="series-id">Series ID</label>
        <input id="series-id" type="text" bind:value={seriesFormId} placeholder="e.g. demon-slayer" />
      </div>
      <div class="form-row">
        <label for="series-src">Source language</label>
        <input id="series-src" type="text" bind:value={seriesFormLang} placeholder="e.g. ja, ko, ru (leave blank for auto)" />
      </div>
      <div class="form-row">
        <label for="series-target">Target language</label>
        <input id="series-target" type="text" bind:value={seriesFormTarget} placeholder="e.g. en (leave blank for default)" />
      </div>
      <div class="form-actions">
        <button type="button" class="btn btn-primary" onclick={saveSeriesConfig} disabled={seriesFormSaving}>
          {seriesFormSaving ? 'Saving…' : 'Save'}
        </button>
        <button type="button" class="btn btn-ghost" onclick={closeSeriesForm}>Cancel</button>
      </div>
    </section>
  {/if}
{/if}

<div class="listing" aria-busy={loading}>
  {#if loading}
    <div class="card" aria-hidden="true">
      <div class="skeleton" style="width: 60%; height: 16px; margin-bottom: 12px;"></div>
      <div class="skeleton" style="width: 40%; height: 12px;"></div>
    </div>
  {:else if data}
    {#if data.parent !== null}
      <!-- M10 (a11y audit): real anchor (no preventDefault) + descriptive
           aria-label. ".." alone is meaningless to screen readers. -->
      <a
        href="/library{data.parent ? `?path=${encodeURIComponent(data.parent)}` : ''}"
        class="row dir-row"
        aria-label="Go up to parent directory"
      >
        <span class="row-icon" aria-hidden="true">📁</span>
        <span class="row-name" aria-hidden="true">..</span>
      </a>
    {/if}

    {#each data.dirs as dir (dir.path)}
      <a
        href="/library?path={encodeURIComponent(dir.path)}"
        class="row dir-row"
        aria-label="{dir.name}{dir.has_media ? ', has media' : ', no media'}"
      >
        <span class="row-icon" aria-hidden="true">{dir.has_media ? '📁' : '📂'}</span>
        <span class="row-name">{dir.name}</span>
        {#if !dir.has_media}
          <span class="muted small" aria-hidden="true">no media</span>
        {/if}
      </a>
    {/each}

    {#each data.files as file (file.path)}
      <div class="row file-row" class:translated={file.translated}>
        <span class="row-icon" aria-hidden="true">🎬</span>
        <span class="row-name" title={file.path}>{file.name}</span>
        <span class="row-size muted">{formatSize(file.size)}</span>
        {#if file.translated}
          <span class="pill" data-state="done">translated</span>
          <ul class="translations" aria-label="Available translations">
            {#each file.translations as t (t.lang)}
              <li class="lang-badge">{t.lang}</li>
            {/each}
          </ul>
        {:else}
          <!-- M10: aria-label includes the file name so SR users hear
               which file the button translates. -->
          <button
            type="button"
            class="btn btn-ghost btn-sm"
            aria-label="Translate {file.name}"
            onclick={() => translateFile(file.path)}
          >Translate</button>
        {/if}
      </div>
    {/each}

    {#if data.dirs.length === 0 && data.files.length === 0}
      <div class="card empty">
        <p>This directory is empty.</p>
      </div>
    {/if}
  {/if}
</div>

<style>
  .page-head { margin-bottom: var(--space-5); }
  .sub {
    margin: var(--space-2) 0 0;
    color: var(--text-muted);
    font-size: var(--text-sm);
  }

  .error-banner {
    background: var(--error-bg);
    border: 1px solid var(--error);
    color: var(--error);
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius-sm);
    margin-bottom: var(--space-4);
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }

  /* Breadcrumbs */
  .breadcrumbs {
    margin-bottom: var(--space-4);
  }
  .breadcrumbs ol {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    align-items: center;
    gap: var(--space-1);
    flex-wrap: wrap;
    font-size: var(--text-sm);
  }
  .breadcrumbs li {
    display: flex;
    align-items: center;
    gap: var(--space-1);
  }
  .breadcrumbs li + li::before {
    content: '/';
    color: var(--text-dim);
    margin-right: var(--space-1);
  }
  .breadcrumbs a {
    color: var(--accent);
    text-decoration: none;
  }
  .breadcrumbs a:hover { text-decoration: underline; }
  .breadcrumbs span[aria-current="page"] {
    color: var(--text);
    font-weight: 500;
  }

  /* Coverage bar */
  .coverage-bar {
    margin-bottom: var(--space-4);
  }
  .coverage-text {
    font-size: var(--text-sm);
    color: var(--text-muted);
    margin-bottom: var(--space-2);
    display: block;
  }
  .cap-bar {
    height: 6px;
    background: var(--bg-input);
    border-radius: 999px;
    overflow: hidden;
  }
  .cap-fill {
    display: block;
    height: 100%;
    background: var(--success);
    border-radius: 999px;
    transition: width var(--dur) var(--ease);
  }

  /* Series config bar */
  .series-bar {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: var(--space-3) var(--space-4);
    margin-bottom: var(--space-4);
  }
  .series-info {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    flex-wrap: wrap;
  }
  .series-label {
    font-size: var(--text-sm);
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 500;
  }
  .series-info strong {
    color: var(--accent);
  }

  /* Series form */
  .series-form {
    margin-bottom: var(--space-4);
  }
  .series-form h3 {
    margin-bottom: var(--space-2);
  }
  .hint {
    color: var(--text-muted);
    font-size: var(--text-sm);
    margin-bottom: var(--space-4);
  }
  .form-row {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    margin-bottom: var(--space-3);
  }
  .form-row label {
    font-size: var(--text-sm);
    font-weight: 500;
    min-width: 130px;
    color: var(--text-muted);
  }
  .form-row input {
    flex: 1;
    max-width: 300px;
    background: var(--bg-input);
    color: var(--text);
    border: 1px solid var(--border-input);
    border-radius: var(--radius-sm);
    padding: var(--space-2) var(--space-3);
    font-family: inherit;
    font-size: var(--text-sm);
    min-height: 36px;
  }
  .form-row input:focus {
    border-color: var(--accent);
    outline: 2px solid var(--accent);
    outline-offset: 0;
  }
  .form-actions {
    display: flex;
    gap: var(--space-3);
    margin-top: var(--space-3);
  }

  /* Rows */
  .listing {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .row {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius-sm);
    min-height: 44px;
    transition: background var(--dur) var(--ease);
    text-decoration: none;
    color: var(--text);
  }
  .row:hover {
    background: var(--bg-elevated-hover);
    text-decoration: none;
  }
  .row-icon {
    font-size: var(--text-base);
    flex-shrink: 0;
    width: 24px;
    text-align: center;
  }
  .row-name {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-weight: 500;
  }
  .row-size {
    font-size: var(--text-sm);
    font-variant-numeric: tabular-nums;
    flex-shrink: 0;
  }
  .dir-row .row-name { font-weight: 400; }

  /* ul -> li semantics (M10 a11y) but visually identical to the old
     div+span layout. */
  .translations {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    gap: var(--space-1);
  }
  .lang-badge {
    font-size: var(--text-xs);
    font-family: var(--font-mono);
    padding: 2px 6px;
    background: color-mix(in srgb, var(--success) 16%, transparent);
    color: var(--success);
    border-radius: var(--radius-sm);
    text-transform: uppercase;
  }

  /* C3 (a11y audit): toast styles match error-banner shape. */
  .error-banner.toast-success {
    background: color-mix(in srgb, var(--success) 12%, transparent);
    border-color: var(--success);
    color: var(--success);
  }
  .toast-dismiss {
    background: transparent;
    border: 0;
    color: inherit;
    cursor: pointer;
    font-size: var(--text-md);
    line-height: 1;
    padding: 0 var(--space-2);
    margin-left: auto;
    min-width: 32px;
    min-height: 32px;
  }

  .btn-sm {
    min-height: 32px;
    padding: 0 var(--space-3);
    font-size: var(--text-xs);
  }

  .empty {
    text-align: center;
    color: var(--text-muted);
    padding: var(--space-6);
  }
  .empty p { margin: var(--space-1) 0; }

  .muted { color: var(--text-muted); }
  .small { font-size: var(--text-sm); }

  @media (max-width: 768px) {
    .form-row {
      flex-direction: column;
      align-items: flex-start;
    }
    .form-row input {
      max-width: 100%;
    }
  }
</style>
