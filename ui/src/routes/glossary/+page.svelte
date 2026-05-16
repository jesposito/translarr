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

  interface Candidate {
    name: string;          // display name (directory basename)
    path: string;          // path relative to MEDIA_ROOT
    kind: string;          // parent category, e.g. "Movies" / "TV"; "" for flat layout
    glossary_id: string;   // slugified ID — what we'd use if you pick this
    glossary_entry_count: number;
    has_series_config: boolean;
  }

  let glossaries = $state<GlossarySummary[]>([]);
  let selectedId = $state<string | null>(null);
  let selectedTitle = $state<string | null>(null); // human title for the picked candidate
  let entries = $state<GlossaryEntry[]>([]);
  let loading = $state(false);
  let entriesLoading = $state(false);
  let error = $state<string | null>(null);
  let entryError = $state<string | null>(null);

  // Picker state — Option B from the design discussion.
  let pickerQuery = $state('');
  let pickerResults = $state<Candidate[]>([]);
  let pickerLoading = $state(false);
  let pickerDebounceTimer: ReturnType<typeof setTimeout> | null = null;
  let advancedOpen = $state(false); // "Create with custom ID" disclosure
  let pickerCount = $state(0); // how many results came back (for SR + display)

  // New entry form.
  let newSource = $state('');
  let newTranslation = $state('');
  let newTargetLang = $state('en');
  let newNotes = $state('');
  let newGlossaryId = $state('');

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

  async function selectGlossary(id: string, title?: string) {
    selectedId = id;
    // If the caller didn't pass a friendly title, see if we can recover
    // one from the picker results (case: user clicked an entry in the
    // "Existing glossaries" disclosure rather than via the picker).
    if (title) {
      selectedTitle = title;
    } else {
      const match = pickerResults.find((c) => c.glossary_id === id);
      selectedTitle = match?.name ?? null;
    }
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

  // Picker — server-side substring search over the real media library.
  // Debounced so typing doesn't fire 8 requests/second.
  async function searchPicker(query: string) {
    pickerLoading = true;
    try {
      const url = `/series/candidates?q=${encodeURIComponent(query)}&limit=50`;
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      pickerResults = data.candidates || [];
      pickerCount = pickerResults.length;
    } catch (e) {
      // Picker errors land in the page-level error banner — they're
      // not entry-specific.
      error = e instanceof Error ? e.message : String(e);
      pickerResults = [];
      pickerCount = 0;
    } finally {
      pickerLoading = false;
    }
  }

  function onPickerInput() {
    // Debounce — wait 150ms after the last keystroke.
    if (pickerDebounceTimer) clearTimeout(pickerDebounceTimer);
    pickerDebounceTimer = setTimeout(() => searchPicker(pickerQuery), 150);
  }

  function pickCandidate(c: Candidate) {
    // Selecting a candidate opens its glossary (which may be empty —
    // adding the first entry creates the glossary row implicitly).
    selectGlossary(c.glossary_id, c.name);
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

  // C3 (a11y audit): inline accessible confirm row replaces window.confirm()
  // which is unannounced by some screen readers and never returns focus.
  let pendingDeleteId = $state<string | null>(null);
  function requestDeleteGlossary(id: string) {
    pendingDeleteId = id;
  }
  async function confirmDeleteGlossary() {
    if (!pendingDeleteId) return;
    const id = pendingDeleteId;
    pendingDeleteId = null;
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

  onMount(async () => {
    await loadGlossaries();
    // Prime the picker with the first 50 candidates so the empty state
    // already shows users what's in their library.
    await searchPicker('');
    // If URL has ?series=<id>, auto-select that glossary.
    const params = new URLSearchParams(window.location.search);
    const seriesParam = params.get('series');
    if (seriesParam) {
      await selectGlossary(seriesParam);
    }
  });
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
  <!-- Left panel: picker + existing glossaries -->
  <section class="panel" aria-labelledby="picker-heading">
    <div class="panel-head">
      <h2 id="picker-heading">Pick a series or film</h2>
    </div>
    <p class="hint">
      Search your library. Pick a title to start a glossary of character
      names, attack names, and other terms that should stay consistent
      across episodes.
    </p>

    <div class="picker">
      <label for="picker-input" class="sr-only">Filter by title</label>
      <!-- A11y minor #2: aria-controls IDREF would be broken in the
           skeleton / empty states because the list only exists in the
           {:else} branch. Drop it — the list immediately follows in
           DOM order so the implicit relationship is clear.
           A11y minor #5: Escape clears the filter (no native click on
           the browser's ✕ clear button reliably fires oninput). -->
      <input
        id="picker-input"
        type="search"
        placeholder="Start typing a title…"
        bind:value={pickerQuery}
        oninput={onPickerInput}
        onkeydown={(e) => {
          if (e.key === 'Escape' && pickerQuery) {
            pickerQuery = '';
            searchPicker('');
            e.stopPropagation();
          }
        }}
        autocomplete="off"
        aria-describedby="picker-count"
      />
      <!-- A11y minor #3: avoid the U+2026 ellipsis which some older TTS
           engines (JAWS profiles) read as "dot dot dot". -->
      <p id="picker-count" class="hint sr-only" aria-live="polite" aria-atomic="true">
        {pickerLoading ? 'Searching' : `${pickerCount} ${pickerCount === 1 ? 'match' : 'matches'}`}
      </p>
      <!-- A11y MAJOR (audit finding 1): announce when the right panel's
           selected glossary changes. Without this, picking a result
           feels silent to a screen reader user — the picker keeps focus
           and the only update is in a different region. -->
      <p id="picker-selection-status" class="sr-only" aria-live="polite" aria-atomic="true">
        {selectedTitle ? `Loaded glossary for ${selectedTitle}.` : ''}
      </p>

      {#if pickerLoading && pickerResults.length === 0}
        <div class="card" aria-hidden="true">
          <div class="skeleton" style="width: 70%; height: 14px; margin-bottom: 8px;"></div>
          <div class="skeleton" style="width: 50%; height: 12px;"></div>
        </div>
      {:else if pickerResults.length === 0 && pickerQuery}
        <p class="muted picker-empty">No titles match "{pickerQuery}".</p>
      {:else if pickerResults.length === 0}
        <p class="muted picker-empty">
          No series or films found in your media library.
          Add some media under MEDIA_ROOT first.
        </p>
      {:else}
        <ul id="picker-list" class="picker-list" aria-label="Series and film results">
          {#each pickerResults as c (c.path)}
            <li>
              <button
                type="button"
                class="picker-item"
                class:selected={selectedId === c.glossary_id}
                onclick={() => pickCandidate(c)}
                aria-current={selectedId === c.glossary_id ? 'true' : undefined}
              >
                <span class="picker-row1">
                  <span class="picker-name">{c.name}</span>
                  {#if c.kind}<span class="picker-kind">{c.kind}</span>{/if}
                </span>
                <span class="picker-row2">
                  {#if c.glossary_entry_count > 0}
                    <span class="picker-count">
                      {c.glossary_entry_count} {c.glossary_entry_count === 1 ? 'entry' : 'entries'}
                    </span>
                  {:else}
                    <span class="muted">No glossary yet</span>
                  {/if}
                  {#if c.has_series_config}
                    <span class="badge-config">series config</span>
                  {/if}
                </span>
              </button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>

    <details class="advanced-create" bind:open={advancedOpen}>
      <summary>Advanced: create with a custom ID</summary>
      <p class="hint">
        Use this if the glossary isn't tied to a specific title — e.g. a
        global term dictionary shared across shows. The ID becomes the URL
        slug. Lowercase letters, numbers, and hyphens only.
      </p>
      <div class="create-row">
        <label for="new-glossary-id" class="sr-only">Custom glossary ID</label>
        <input
          id="new-glossary-id"
          type="text"
          placeholder="e.g. anime-shared-terms"
          bind:value={newGlossaryId}
          onkeydown={(e) => e.key === 'Enter' && createGlossary()}
        />
        <button type="button" class="btn btn-primary btn-sm" onclick={createGlossary} disabled={!newGlossaryId.trim()}>
          Create
        </button>
      </div>
    </details>

    {#if glossaries.length > 0}
      <details class="existing-list" open>
        <summary>
          Existing glossaries
          <span class="muted">({glossaries.length})</span>
        </summary>
        <ul class="glossary-list">
          {#each glossaries as g (g.id)}
            <li>
              <button
                type="button"
                class="glossary-item"
                class:selected={selectedId === g.id}
                onclick={() => selectGlossary(g.id)}
                aria-current={selectedId === g.id ? 'true' : undefined}
              >
                <span class="g-name">{g.id}</span>
                <span class="g-meta">{g.entry_count} entries · {relTime(g.last_updated)}</span>
              </button>
              <button
                type="button"
                class="btn-icon"
                aria-label="Delete glossary {g.id}"
                onclick={() => requestDeleteGlossary(g.id)}
              >✕</button>
              {#if pendingDeleteId === g.id}
                <div class="confirm-row" role="alertdialog" aria-labelledby="confirm-label-{g.id}">
                  <span id="confirm-label-{g.id}">
                    Delete "{g.id}"? Cannot be undone.
                  </span>
                  <button type="button" class="btn btn-danger btn-sm" onclick={confirmDeleteGlossary}>Delete</button>
                  <button type="button" class="btn btn-ghost btn-sm" onclick={() => (pendingDeleteId = null)}>Cancel</button>
                </div>
              {/if}
            </li>
          {/each}
        </ul>
      </details>
    {/if}
  </section>

  <!-- Right panel: entries for selected glossary -->
  <section class="panel" aria-labelledby="entries-heading">
    {#if !selectedId}
      <div class="card empty placeholder">
        <p>
          Pick a series or film on the left to start editing its glossary.
        </p>
      </div>
    {:else}
      <div class="panel-head">
        <h2 id="entries-heading">
          {selectedTitle ?? selectedId}
          {#if selectedTitle}
            <span class="g-meta-inline mono">({selectedId})</span>
          {/if}
        </h2>
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

      <!-- Add entry form (M8 a11y: visible-only-on-focus labels) -->
      <form class="add-form" onsubmit={(e) => { e.preventDefault(); addEntry(); }}>
        <label for="entry-source" class="sr-only">Source term</label>
        <input
          id="entry-source"
          type="text"
          placeholder="Source term"
          bind:value={newSource}
          required
        />
        <label for="entry-translation" class="sr-only">Translation</label>
        <input
          id="entry-translation"
          type="text"
          placeholder="Translation"
          bind:value={newTranslation}
          required
        />
        <label for="entry-lang" class="sr-only">Target language</label>
        <input
          id="entry-lang"
          type="text"
          placeholder="en"
          bind:value={newTargetLang}
          class="lang-input"
        />
        <button type="submit" class="btn btn-primary btn-sm" disabled={!newSource.trim() || !newTranslation.trim()}>
          Add
        </button>
      </form>

      <!-- Bulk import -->
      {#if importOpen}
        <div class="import-box">
          <label for="import-area">
            Paste entries, one per line: <code class="mono">source_term{'\t'}translation{'\t'}notes</code>
          </label>
          <!-- Mo8 (a11y audit): an SR-only help paragraph spells out the
               format in words, since placeholder text uses literal tab
               characters that read as a confusing run-on string. -->
          <p id="import-help" class="sr-only">
            Tab-separated fields, one entry per line. Notes column is optional.
          </p>
          <textarea
            id="import-area"
            rows="6"
            bind:value={importText}
            aria-describedby="import-help"
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

  /* Picker — server-side search over the real library tree */
  .picker {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    margin-bottom: var(--space-4);
  }
  .picker input[type="search"] {
    width: 100%;
    padding: var(--space-2) var(--space-3);
    background: var(--bg);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-sm);
    color: var(--text);
    font-size: var(--text-sm);
    min-height: 44px;
  }
  .picker input[type="search"]:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .picker-empty {
    margin: var(--space-2) 0 0;
    font-size: var(--text-sm);
  }
  .picker-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-height: 420px;
    overflow-y: auto;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--bg);
  }
  .picker-item {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: var(--space-2) var(--space-3);
    border: 0;
    background: transparent;
    color: var(--text);
    text-align: left;
    cursor: pointer;
    min-height: 44px;
    border-radius: var(--radius-sm);
  }
  .picker-item:hover {
    background: var(--bg-elevated-hover);
  }
  .picker-item:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: -2px;
  }
  .picker-item.selected {
    background: var(--bg-elevated-hover);
    border-left: 2px solid var(--accent);
    padding-left: calc(var(--space-3) - 2px);
  }
  /* A11y minor #4: stronger visual weight on the selected name so the
     selected vs hover distinction is obvious at a glance. */
  .picker-item.selected .picker-name {
    font-weight: 600;
  }
  .picker-row1 {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
    flex-wrap: wrap;
  }
  .picker-name {
    font-weight: 500;
  }
  .picker-kind {
    font-size: var(--text-xs);
    color: var(--text-muted);
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    padding: 1px 6px;
    border-radius: var(--radius-sm);
  }
  .picker-row2 {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-size: var(--text-xs);
    color: var(--text-muted);
  }
  .picker-count {
    color: var(--success);
  }
  .badge-config {
    font-size: var(--text-xs);
    background: color-mix(in srgb, var(--accent) 16%, transparent);
    color: var(--accent);
    border-radius: var(--radius-sm);
    padding: 1px 6px;
  }

  /* Advanced disclosure for custom-ID create */
  .advanced-create {
    margin-top: var(--space-4);
    padding: var(--space-3);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    background: var(--bg-elevated);
  }
  .advanced-create summary {
    cursor: pointer;
    font-size: var(--text-sm);
    font-weight: 500;
    color: var(--text-muted);
  }
  .advanced-create summary:hover { color: var(--text); }
  .advanced-create .hint {
    margin: var(--space-2) 0;
  }
  .advanced-create .create-row {
    display: flex;
    gap: var(--space-2);
    margin: 0;
  }
  .advanced-create input {
    flex: 1;
    min-height: 36px;
  }

  /* Existing glossaries collapsible */
  .existing-list {
    margin-top: var(--space-4);
  }
  .existing-list summary {
    cursor: pointer;
    font-size: var(--text-sm);
    font-weight: 600;
    color: var(--text);
    padding: var(--space-2) 0;
  }
  .existing-list summary:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
    border-radius: var(--radius-sm);
  }

  .g-meta-inline {
    font-size: var(--text-xs);
    color: var(--text-muted);
    font-weight: 400;
    margin-left: var(--space-2);
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
    flex-wrap: wrap;
  }
  /* C3 (a11y audit): inline confirm replaces window.confirm(). */
  .confirm-row {
    flex-basis: 100%;
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    margin: var(--space-1) 0;
    background: var(--error-bg);
    border: 1px solid var(--error);
    border-radius: var(--radius-sm);
    color: var(--error);
    font-size: var(--text-sm);
  }
  .btn-danger {
    background: var(--error);
    color: var(--bg);
    border: 1px solid var(--error);
  }
  .btn-danger:hover {
    background: color-mix(in srgb, var(--error) 80%, black);
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
