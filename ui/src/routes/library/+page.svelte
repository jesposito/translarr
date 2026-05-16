<script lang="ts">
  import { onMount } from 'svelte';

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

  let currentPath = $state('');
  let data = $state<BrowseResult | null>(null);
  let loading = $state(false);
  let error = $state<string | null>(null);
  // Breadcrumb segments derived from currentPath.
  let crumbs = $state<{ label: string; path: string }[]>([]);

  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
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
      // Update URL without navigation.
      const params = path ? `?path=${encodeURIComponent(path)}` : '';
      window.history.replaceState({}, '', `/library${params}`);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
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
      // Refresh to show updated state.
      await browse(currentPath);
      // Announce.
      alert(`Job queued: ${result.job_id || result.id || 'see Dashboard'}`);
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    }
  }

  onMount(() => {
    // Read initial path from URL query string.
    const params = new URLSearchParams(window.location.search);
    browse(params.get('path') || '');
  });
</script>

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

<nav class="breadcrumbs" aria-label="Breadcrumb">
  <ol>
    {#each crumbs as crumb (crumb.path)}
      <li>
        {#if crumb.path === currentPath}
          <span aria-current="page">{crumb.label}</span>
        {:else}
          <a href="/library{crumb.path ? `?path=${encodeURIComponent(crumb.path)}` : ''}"
            onclick={(e) => { e.preventDefault(); browse(crumb.path); }}
          >{crumb.label}</a>
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

<div class="listing" aria-busy={loading}>
  {#if loading}
    <div class="card" aria-hidden="true">
      <div class="skeleton" style="width: 60%; height: 16px; margin-bottom: 12px;"></div>
      <div class="skeleton" style="width: 40%; height: 12px;"></div>
    </div>
  {:else if data}
    {#if data.parent !== null}
      <a
        href="/library{data.parent ? `?path=${encodeURIComponent(data.parent)}` : ''}"
        class="row dir-row"
        onclick={(e) => { e.preventDefault(); browse(data.parent || ''); }}
      >
        <span class="row-icon" aria-hidden="true">📁</span>
        <span class="row-name">..</span>
      </a>
    {/if}

    {#each data.dirs as dir (dir.path)}
      <a
        href="/library?path={encodeURIComponent(dir.path)}"
        class="row dir-row"
        onclick={(e) => { e.preventDefault(); browse(dir.path); }}
      >
        <span class="row-icon" aria-hidden="true">{dir.has_media ? '📁' : '📂'}</span>
        <span class="row-name">{dir.name}</span>
        {#if !dir.has_media}
          <span class="muted small">no media</span>
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
          <div class="translations">
            {#each file.translations as t (t.lang)}
              <span class="lang-badge">{t.lang}</span>
            {/each}
          </div>
        {:else}
          <button
            type="button"
            class="btn btn-ghost btn-sm"
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

  .translations {
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
</style>
