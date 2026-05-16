<script lang="ts">
  import { onMount } from 'svelte';

  interface GlossarySummary {
    id: string;
    entry_count: number;
    last_updated: number | null;
  }

  interface GlossaryEntry {
    source_term: string;
    target_lang: string;
    translation: string;
    notes: string | null;
  }

  let glossaries = $state<GlossarySummary[]>([]);
  let selectedId = $state<string | null>(null);
  let entries = $state<GlossaryEntry[]>([]);
  let loading = $state(false);
  let entriesLoading = $state(false);
  let error = $state<string | null>(null);
  let entryError = $state<string | null>(null);

  // New entry form.
  let newSource = $state('');
  let newTranslation = $state('');
  let newTargetLang = $state('en');
  let newNotes = $state('');
  let newGlossaryId = $state('');
  let creating = $state(false);

  // Bulk import.
  let importOpen = $state(false);
  let importText = $state('');
  let importing = $state(false);

  function relTime(epoch: number | null): string {
    if (!epoch) return '—';
    const diff = Math.max(0, Date.now() / 1000 - epoch);
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} hr ago`;
    return `${Math.floor(diff / 86400)} days ago`;
  }

  async function loadGlossaries() {
    loading = true;
    error = null;
    try {
      const r = await fetch('/glossaries');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      glossaries = data.glossaries || [];
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  async function selectGlossary(id: string) {
    selectedId = id;
    entriesLoading = true;
    entryError = null;
    try {
      const r = await fetch(`/glossaries/${encodeURIComponent(id)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      entries = data.entries || [];
    } catch (e) {
      entryError = e instanceof Error ? e.message : String(e);
    } finally {
      entriesLoading = false;
    }
  }

  async function addEntry() {
    if (!selectedId || !newSource.trim() || !newTranslation.trim()) return;
    entryError = null;
    try {
      const r = await fetch(`/glossaries/${encodeURIComponent(selectedId)}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          source_term: newSource.trim(),
          translation: newTranslation.trim(),
          target_lang: newTargetLang || 'en',
          notes: newNotes.trim() || null,
        }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${r.status}`);
      }
      newSource = '';
      newTranslation = '';
      newNotes = '';
      await selectGlossary(selectedId);
      await loadGlossaries();
    } catch (e) {
      entryError = e instanceof Error ? e.message : String(e);
    }
  }

  async function deleteEntry(sourceTerm: string, targetLang: string) {
    if (!selectedId) return;
    try {
      const r = await fetch(
        `/glossaries/${encodeURIComponent(selectedId)}/${encodeURIComponent(sourceTerm)}?target_lang=${encodeURIComponent(targetLang)}`,
        { method: 'DELETE' },
      );
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await selectGlossary(selectedId);
      await loadGlossaries();
    } catch (e) {
      entryError = e instanceof Error ? e.message : String(e);
    }
  }

  async function deleteGlossary(id: string) {
    if (!confirm(`Delete entire glossary "${id}"? This cannot be undone.`)) return;
    try {
      const r = await fetch(`/glossaries/${encodeURIComponent(id)}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      if (selectedId === id) {
        selectedId = null;
        entries = [];
      }
      await loadGlossaries();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    }
  }

  async function createGlossary() {
    if (!newGlossaryId.trim()) return;
    // Create by adding a dummy entry then deleting it — or just add the first entry.
    // Actually, glossaries are implicit — they exist when they have entries.
    // Select the new ID and let the user add entries.
    selectedId = newGlossaryId.trim();
    entries = [];
    newGlossaryId = '';
    await loadGlossaries();
  }

  async function bulkImport() {
    if (!selectedId || !importText.trim()) return;
    importing = true;
    entryError = null;
    try {
      // Parse tab-separated: source_term\ttranslation[\tnotes]
      const lines = importText.trim().split('\n');
      const entries_list: { source_term: string; translation: string; notes?: string }[] = [];
      for (const line of lines) {
        const parts = line.split('\t');
        if (parts.length >= 2 && parts[0].trim() && parts[1].trim()) {
          entries_list.push({
            source_term: parts[0].trim(),
            translation: parts[1].trim(),
            notes: parts[2]?.trim() || undefined,
          });
        }
      }
      if (entries_list.length === 0) {
        entryError = 'No valid entries found. Use: source_term<TAB>translation';
        return;
      }
      const r = await fetch(`/glossaries/${encodeURIComponent(selectedId)}/import`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ entries: entries_list, target_lang: newTargetLang || 'en' }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${r.status}`);
      }
      importText = '';
      importOpen = false;
      await selectGlossary(selectedId);
      await loadGlossaries();
    } catch (e) {
      entryError = e instanceof Error ? e.message : String(e);
    } finally {
      importing = false;
    }
  }

  onMount(loadGlossaries);
</script>

<svelte:head>
  <title>Glossary — Translarr</title>
</svelte:head>

<header class="page-head">
  <h1>Glossary</h1>
  <p class="sub">
    Per-series term dictionaries for consistent character name and term translation.
  </p>
</header>

{#if error}
  <div class="error-banner" role="alert">{error}</div>
{/if}

<div class="layout">
  <!-- Left panel: glossary list -->
  <section class="panel" aria-labelledby="list-heading">
    <div class="panel-head">
      <h2 id="list-heading">Glossaries</h2>
      <div class="create-row">
        <input
          type="text"
          placeholder="New glossary ID…"
          bind:value={newGlossaryId}
          onkeydown={(e) => e.key === 'Enter' && createGlossary()}
        />
        <button type="button" class="btn btn-primary" onclick={createGlossary} disabled={!newGlossaryId.trim()}>
          Create
        </button>
      </div>
    </div>

    {#if loading}
      <div class="card" aria-hidden="true">
        <div class="skeleton" style="width: 80%; height: 16px; margin-bottom: 10px;"></div>
        <div class="skeleton" style="width: 60%; height: 12px;"></div>
      </div>
    {:else if glossaries.length === 0}
      <div class="card empty">
        <p>No glossaries yet.</p>
        <p class="muted">Create one above to start building term dictionaries.</p>
      </div>
    {:else}
      <ul class="glossary-list">
        {#each glossaries as g (g.id)}
          <li>
            <button
              type="button"
              class="glossary-item"
              class:selected={selectedId === g.id}
              onclick={() => selectGlossary(g.id)}
              aria-pressed={selectedId === g.id}
            >
              <span class="g-name">{g.id}</span>
              <span class="g-meta">{g.entry_count} entries · {relTime(g.last_updated)}</span>
            </button>
            <button
              type="button"
              class="btn-icon"
              title="Delete {g.id}"
              aria-label="Delete glossary {g.id}"
              onclick={() => deleteGlossary(g.id)}
            >✕</button>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <!-- Right panel: entries for selected glossary -->
  <section class="panel" aria-labelledby="entries-heading">
    {#if !selectedId}
      <div class="card empty placeholder">
        <p>Select a glossary to view entries.</p>
      </div>
    {:else}
      <div class="panel-head">
        <h2 id="entries-heading">{selectedId}</h2>
        <button
          type="button"
          class="btn btn-ghost"
          onclick={() => (importOpen = !importOpen)}
        >
          {importOpen ? 'Cancel' : 'Bulk import'}
        </button>
      </div>

      {#if entryError}
        <div class="error-inline" role="alert">{entryError}</div>
      {/if}

      <!-- Add entry form -->
      <form class="add-form" onsubmit={(e) => { e.preventDefault(); addEntry(); }}>
        <input
          type="text"
          placeholder="Source term"
          bind:value={newSource}
          required
        />
        <input
          type="text"
          placeholder="Translation"
          bind:value={newTranslation}
          required
        />
        <input
          type="text"
          placeholder="en"
          bind:value={newTargetLang}
          class="lang-input"
          aria-label="Target language"
        />
        <button type="submit" class="btn btn-primary btn-sm" disabled={!newSource.trim() || !newTranslation.trim()}>
          Add
        </button>
      </form>

      <!-- Bulk import -->
      {#if importOpen}
        <div class="import-box">
          <label for="import-area">
            Paste entries — one per line: <code class="mono">source_term{'\t'}translation{'\t'}notes</code>
          </label>
          <textarea
            id="import-area"
            rows="6"
            bind:value={importText}
            placeholder={"tanjiro&#9;Tanjiro Kamado&#9;protagonist name&#10;nezuko&#9;Nezuko Kamado&#9;sister"}
          ></textarea>
          <button
            type="button"
            class="btn btn-primary"
            onclick={bulkImport}
            disabled={importing || !importText.trim()}
          >
            {importing ? 'Importing…' : 'Import'}
          </button>
        </div>
      {/if}

      <!-- Entries table -->
      {#if entriesLoading}
        <div class="card" aria-hidden="true">
          {#each Array(3) as _}
            <div class="skeleton" style="width: 100%; height: 16px; margin-bottom: 10px;"></div>
          {/each}
        </div>
      {:else if entries.length === 0}
        <div class="card empty">
          <p>No entries yet. Add one above.</p>
        </div>
      {:else}
        <div class="card no-pad">
          <table>
            <caption class="sr-only">Glossary entries for {selectedId}</caption>
            <thead>
              <tr>
                <th scope="col">Source</th>
                <th scope="col">Translation</th>
                <th scope="col">Lang</th>
                <th scope="col">Notes</th>
                <th scope="col"><span class="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {#each entries as entry (entry.source_term + entry.target_lang)}
                <tr>
                  <td class="mono">{entry.source_term}</td>
                  <td>{entry.translation}</td>
                  <td class="mono">{entry.target_lang}</td>
                  <td class="muted">{entry.notes || '—'}</td>
                  <td>
                    <button
                      type="button"
                      class="btn-icon"
                      title="Delete {entry.source_term}"
                      aria-label="Delete entry {entry.source_term}"
                      onclick={() => deleteEntry(entry.source_term, entry.target_lang)}
                    >✕</button>
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    {/if}
  </section>
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
  .error-inline {
    color: var(--error);
    font-size: var(--text-sm);
    margin-bottom: var(--space-3);
  }

  .layout {
    display: grid;
    grid-template-columns: 320px 1fr;
    gap: var(--space-5);
    align-items: start;
  }

  .panel {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: var(--space-4);
  }

  .panel-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    margin-bottom: var(--space-4);
    flex-wrap: wrap;
  }
  .panel-head h2 { margin: 0; }

  /* Create row */
  .create-row {
    display: flex;
    gap: var(--space-2);
    width: 100%;
    margin-bottom: var(--space-3);
  }
  .create-row input {
    flex: 1;
    background: var(--bg-input);
    color: var(--text);
    border: 1px solid var(--border-input);
    border-radius: var(--radius-sm);
    padding: var(--space-2) var(--space-3);
    font-family: inherit;
    font-size: var(--text-sm);
    min-height: 36px;
  }

  /* Glossary list */
  .glossary-list {
    list-style: none;
    padding: 0;
    margin: 0;
  }
  .glossary-list li {
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }
  .glossary-item {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    padding: var(--space-3);
    border-radius: var(--radius-sm);
    background: none;
    border: 1px solid transparent;
    text-align: left;
    cursor: pointer;
    transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease);
    min-height: 44px;
  }
  .glossary-item:hover { background: var(--bg-elevated-hover); }
  .glossary-item.selected {
    background: var(--bg-elevated-hover);
    border-color: var(--accent);
  }
  .g-name {
    font-weight: 500;
    color: var(--text);
  }
  .g-meta {
    font-size: var(--text-xs);
    color: var(--text-muted);
  }

  .btn-icon {
    background: none;
    border: 0;
    color: var(--text-dim);
    cursor: pointer;
    padding: var(--space-2);
    border-radius: var(--radius-sm);
    font-size: var(--text-sm);
    min-width: 32px;
    min-height: 32px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .btn-icon:hover { color: var(--error); background: var(--error-bg); }
  .btn-icon:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  /* Add form */
  .add-form {
    display: flex;
    gap: var(--space-2);
    margin-bottom: var(--space-4);
    flex-wrap: wrap;
  }
  .add-form input {
    background: var(--bg-input);
    color: var(--text);
    border: 1px solid var(--border-input);
    border-radius: var(--radius-sm);
    padding: var(--space-2) var(--space-3);
    font-family: inherit;
    font-size: var(--text-sm);
    min-height: 36px;
  }
  .add-form input:first-child { flex: 1; min-width: 120px; }
  .add-form input:nth-child(2) { flex: 1; min-width: 120px; }
  .lang-input { width: 60px !important; flex: 0 0 60px !important; }

  .btn-sm {
    min-height: 36px;
    padding: 0 var(--space-3);
    font-size: var(--text-sm);
  }

  /* Import box */
  .import-box {
    margin-bottom: var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .import-box label {
    font-size: var(--text-sm);
    color: var(--text-muted);
  }
  .import-box textarea {
    background: var(--bg-input);
    color: var(--text);
    border: 1px solid var(--border-input);
    border-radius: var(--radius-sm);
    padding: var(--space-3);
    font-family: var(--font-mono);
    font-size: var(--text-sm);
    resize: vertical;
  }

  /* Table */
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

  .placeholder {
    min-height: 200px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .empty {
    text-align: center;
    color: var(--text-muted);
    padding: var(--space-5);
  }
  .empty p { margin: var(--space-1) 0; }

  .muted { color: var(--text-muted); }

  @media (max-width: 768px) {
    .layout {
      grid-template-columns: 1fr;
    }
  }
</style>
