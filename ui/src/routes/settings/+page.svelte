<script lang="ts">
  import { onMount, tick } from 'svelte';

  // Field schema from GET /config — keep in lockstep with REGISTRY in
  // server/settings_store.py.
  interface Field {
    key: string;
    section: 'llm' | 'translation' | 'cost' | 'playback' | 'ntfy' | 'arr';
    type: 'str' | 'int' | 'bool' | 'float' | 'url' | 'secret';
    description: string;
    hint: string;
    default_label: string;
    mutable: boolean;
    restart_required: boolean;
    source: 'db' | 'env';
    updated_at: number | null;
    min: number | null;
    max: number | null;
    choices: string[] | null;
    is_secret: boolean;
    // Either present (non-secret) or absent (secret).
    value?: string | number | boolean;
    // Only on secrets: indicates whether anything is configured server-side.
    set?: boolean;
  }

  interface ConfigResponse {
    version: string;
    fields: Field[];
  }

  interface Health {
    status: string;
    version: string;
    llm_provider: string;
    llm_model: string;
  }

  // === State ===============================================================

  let health = $state<Health | null>(null);
  let config = $state<ConfigResponse | null>(null);
  let configError = $state<string | null>(null);
  let healthError = $state<string | null>(null);
  let testing = $state(false);
  let testResult = $state<string>('');
  let ntfyTesting = $state(false);
  let ntfyResult = $state<{ kind: 'ok' | 'err' | ''; msg: string }>({
    kind: '',
    msg: '',
  });

  // Per-field local edit buffer (string for inputs, boolean for toggles).
  // Mirrors the server's "value" but tracks uncommitted edits.
  let local = $state<Record<string, string | boolean>>({});
  // Per-field save state: 'idle' | 'saving' | 'saved' | 'error'.
  let fieldStatus = $state<Record<string, 'idle' | 'saving' | 'saved' | 'error'>>({});
  // Per-field inline error messages from server validation.
  let fieldError = $state<Record<string, string>>({});
  // Per-field "Saved · 2s ago" timestamps. Visible only, never announced.
  let savedAt = $state<Record<string, number>>({});
  // Single page-level announcement. Debounced via setTimeout + RAF reset
  // so identical consecutive messages still re-trigger.
  let announcement = $state('');
  let announceTimer: ReturnType<typeof setTimeout> | null = null;
  // Per-field request counter to discard stale-write race responses.
  let requestSeq: Record<string, number> = {};
  // Initial-load guard so the live region doesn't announce "Saved" for
  // values that were just fetched.
  let initialized = $state(false);

  let serverUrl = $state<string>('—');

  // === Helpers =============================================================

  function dollars(cents: number): string {
    return `$${(cents / 100).toFixed(2)}`;
  }

  function relativeAge(epochSec: number): string {
    const diff = Math.max(0, Date.now() / 1000 - epochSec);
    if (diff < 60) return `${Math.floor(diff)}s ago`;
    if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} hr ago`;
    return `${Math.floor(diff / 86400)} days ago`;
  }

  function announce(msg: string) {
    if (!initialized) return;
    if (announceTimer) clearTimeout(announceTimer);
    announceTimer = setTimeout(() => {
      announcement = '';
      requestAnimationFrame(() => {
        announcement = msg;
      });
    }, 250);
  }

  // === Loaders =============================================================

  async function loadHealth(): Promise<void> {
    try {
      const r = await fetch('/health');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      health = (await r.json()) as Health;
      healthError = null;
    } catch (e) {
      healthError = e instanceof Error ? e.message : String(e);
      health = null;
    }
  }

  async function loadConfig(): Promise<void> {
    try {
      const r = await fetch('/config');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = (await r.json()) as ConfigResponse;
      // Seed local buffer from server values.
      const buf: Record<string, string | boolean> = {};
      for (const f of data.fields) {
        if (f.is_secret) {
          buf[f.key] = '';  // never seeded; secrets are write-only
        } else if (f.type === 'bool') {
          buf[f.key] = (f.value as boolean) ?? false;
        } else if (f.value === null || f.value === undefined) {
          buf[f.key] = '';
        } else {
          buf[f.key] = String(f.value);
        }
      }
      local = buf;
      config = data;
      configError = null;
    } catch (e) {
      configError = e instanceof Error ? e.message : String(e);
      config = null;
    }
  }

  // === Save =================================================================

  function _coerceForWire(field: Field, raw: string | boolean): unknown {
    if (field.type === 'bool') return Boolean(raw);
    if (field.type === 'int') {
      const n = Number(raw);
      return Number.isFinite(n) ? n : raw;
    }
    if (field.type === 'float') {
      const n = Number(raw);
      return Number.isFinite(n) ? n : raw;
    }
    // secret + url + str all go as-is (trimmed by the server).
    return raw;
  }

  async function saveField(key: string, opts: { force?: boolean } = {}): Promise<void> {
    const field = config?.fields.find((f) => f.key === key);
    if (!field) return;
    if (!field.mutable) return;
    // Don't fire for unchanged non-secret values (avoid PATCH-on-Tab spam).
    if (!opts.force && !field.is_secret) {
      const current = field.value;
      const next = _coerceForWire(field, local[key]);
      if (current === next) return;
    }
    // Secrets: empty input means "no change" — don't write empty over a set value.
    if (field.is_secret && local[key] === '') {
      return;
    }

    const seq = (requestSeq[key] = (requestSeq[key] ?? 0) + 1);
    fieldStatus[key] = 'saving';
    fieldError[key] = '';
    announce(`Saving ${humanLabel(field)}…`);

    try {
      const r = await fetch('/config', {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          key,
          value: _coerceForWire(field, local[key]),
        }),
      });
      if (seq !== requestSeq[key]) return;  // stale response

      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        const detail = (body && body.detail) || `HTTP ${r.status}`;
        fieldError[key] = String(detail);
        fieldStatus[key] = 'error';
        announce(`${humanLabel(field)} failed: ${detail}`);
        return;
      }
      // Server may coerce (trim, normalize). Echo the canonical value back
      // into the local buffer + config snapshot so the user sees what the
      // server actually stored.
      const updated: Field = body.field;
      if (config) {
        const idx = config.fields.findIndex((f) => f.key === key);
        if (idx >= 0) config.fields[idx] = updated;
      }
      if (!updated.is_secret) {
        local[key] =
          updated.type === 'bool' ? Boolean(updated.value) : String(updated.value ?? '');
      } else {
        local[key] = '';  // clear the just-written secret from the form
      }
      savedAt[key] = Date.now() / 1000;
      fieldStatus[key] = 'saved';
      announce(`${humanLabel(field)} saved.`);
    } catch (e) {
      if (seq !== requestSeq[key]) return;
      const msg = e instanceof Error ? e.message : String(e);
      fieldError[key] = msg;
      fieldStatus[key] = 'error';
      announce(`${humanLabel(field)} failed: ${msg}`);
    }
  }

  async function revertToDefault(key: string): Promise<void> {
    const field = config?.fields.find((f) => f.key === key);
    if (!field) return;
    fieldStatus[key] = 'saving';
    fieldError[key] = '';
    try {
      const r = await fetch(`/config/${encodeURIComponent(key)}`, { method: 'DELETE' });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        fieldError[key] = (body && body.detail) || `HTTP ${r.status}`;
        fieldStatus[key] = 'error';
        return;
      }
      // Re-fetch the whole config so the source label flips back to env.
      await loadConfig();
      fieldStatus[key] = 'saved';
      announce(`${humanLabel(field)} reverted to default.`);
    } catch (e) {
      fieldError[key] = e instanceof Error ? e.message : String(e);
      fieldStatus[key] = 'error';
    }
  }

  function clearFieldError(key: string) {
    if (fieldError[key]) fieldError[key] = '';
    if (fieldStatus[key] === 'error') fieldStatus[key] = 'idle';
  }

  function onEnterToBlur(e: KeyboardEvent) {
    if (e.key === 'Enter') (e.currentTarget as HTMLInputElement).blur();
  }

  function humanLabel(field: Field): string {
    return field.key
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase());
  }

  // Status text for the visible (aria-hidden) per-field badge.
  function badgeText(key: string): string {
    const s = fieldStatus[key];
    if (s === 'saving') return 'Saving…';
    if (s === 'saved') {
      const ts = savedAt[key];
      return ts ? `Saved · ${relativeAge(ts)}` : 'Saved';
    }
    if (s === 'error') return 'Error';
    if (config?.fields.find((f) => f.key === key)?.source === 'db') return 'Overridden';
    return '';
  }

  // Group fields by section for rendering.
  function fieldsInSection(section: string): Field[] {
    return config?.fields.filter((f) => f.section === section) ?? [];
  }

  // === Test buttons (existing) =============================================

  async function testConnection(): Promise<void> {
    if (testing) return;
    testing = true;
    testResult = 'Testing…';
    await loadHealth();
    if (health) {
      testResult = `Connected. Server reports version ${health.version}.`;
    } else {
      testResult = `Server unreachable: ${healthError ?? 'unknown error'}`;
    }
    testing = false;
  }

  async function sendNtfyTest(): Promise<void> {
    if (ntfyTesting) return;
    ntfyTesting = true;
    ntfyResult = { kind: '', msg: 'Sending…' };
    try {
      const r = await fetch('/test/ntfy', { method: 'POST' });
      const text = await r.text();
      let data: { status?: string; detail?: string } = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        // non-JSON
      }
      if (r.ok && data.status === 'ok') {
        ntfyResult = { kind: 'ok', msg: 'Test push sent. Check your device.' };
      } else {
        ntfyResult = {
          kind: 'err',
          msg: data.detail || `HTTP ${r.status} — see server logs.`,
        };
      }
    } catch (e) {
      ntfyResult = {
        kind: 'err',
        msg: e instanceof Error ? e.message : String(e),
      };
    } finally {
      ntfyTesting = false;
    }
  }

  // === Init ================================================================

  onMount(async () => {
    serverUrl = window.location.origin;
    await Promise.all([loadHealth(), loadConfig()]);
    await tick();
    initialized = true;
  });
</script>

<svelte:head>
  <title>Settings — Translarr</title>
</svelte:head>

<header class="page-head">
  <h1>Settings</h1>
  <p class="sub">
    Edit any value below to save it — changes apply immediately.
    API keys and other secrets are write-only; existing values are never shown.
  </p>
</header>

<!-- Single page-level live region for all auto-save announcements. -->
<div class="sr-only" role="status" aria-live="polite" aria-atomic="true">
  {announcement}
</div>

<section class="card" aria-labelledby="server-heading">
  <h2 id="server-heading">Server connection</h2>
  <dl class="kv">
    <dt>Server URL</dt>
    <dd><code class="mono">{serverUrl}</code></dd>
    <dt>Status</dt>
    <dd>
      {#if health}
        <span class="indicator ok" aria-hidden="true"></span>
        <span>Connected</span>
        <span class="version-pill" aria-label="Server version {health.version}">
          v{health.version}
        </span>
      {:else if healthError}
        <span class="indicator bad" aria-hidden="true"></span>
        <span>Server unreachable</span>
        <span class="muted small">({healthError})</span>
      {:else}
        <span class="indicator pending" aria-hidden="true"></span>
        <span class="muted">Checking…</span>
      {/if}
    </dd>
  </dl>
  <div class="row-actions">
    <button
      type="button"
      class="btn btn-ghost"
      onclick={testConnection}
      disabled={testing}
    >
      {testing ? 'Testing…' : 'Test connection'}
    </button>
    <span
      class="test-result"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >{testResult}</span>
  </div>
</section>

{#if configError}
  <div class="card error-banner" role="alert">
    Couldn't load settings: {configError}
  </div>
{:else if !config}
  <div class="card" aria-busy="true">
    <div class="skeleton" style="width: 60%; height: 16px; margin-bottom: 12px;"></div>
    <div class="skeleton" style="width: 40%; height: 12px;"></div>
  </div>
{:else}
  {#each [
    { id: 'llm',         title: 'LLM provider',        hint: 'Takes effect on the next translation job — no restart needed.' },
    { id: 'translation', title: 'Translation defaults', hint: 'Defaults applied to every translation when the caller doesn\'t override.' },
    { id: 'cost',        title: 'Cost guards',         hint: 'Hard limits to keep API spend bounded. Concurrency adjusts live.' },
    { id: 'playback',    title: 'On-demand translation', hint: 'Press-Play behavior in Emby.' },
    { id: 'ntfy',        title: 'Push notifications',  hint: 'ntfy.sh toast when a translation finishes or fails.' },
    { id: 'arr',         title: 'arr-stack integration', hint: 'Sonarr / Radarr / Emby / Jellyfin endpoints + the shared webhook secret.' },
  ] as section (section.id)}
    <section class="card" aria-labelledby="sec-{section.id}-heading">
      <h2 id="sec-{section.id}-heading">{section.title}</h2>
      <p class="hint">{section.hint}</p>

      {#each fieldsInSection(section.id) as field (field.key)}
        {@const inputId = `field-${field.key}`}
        {@const errId   = `field-${field.key}-err`}
        {@const helpId  = `field-${field.key}-help`}
        {@const statusId = `field-${field.key}-status`}
        {@const hasError = !!fieldError[field.key]}
        <div class="field" class:has-error={hasError}>
          <label for={inputId} class="field-label">
            {humanLabel(field)}
            {#if field.restart_required}
              <span class="restart-tag">restart-only</span>
            {/if}
          </label>

          {#if field.type === 'bool'}
            <label class="toggle-row" for={inputId}>
              <input
                id={inputId}
                type="checkbox"
                bind:checked={local[field.key] as boolean}
                disabled={!field.mutable}
                aria-describedby="{errId} {helpId} {statusId}"
                aria-invalid={hasError ? 'true' : 'false'}
                onchange={() => saveField(field.key, { force: true })}
              />
              <span class="toggle-track" aria-hidden="true">
                <span class="toggle-thumb"></span>
              </span>
              <span class="toggle-label">
                {(local[field.key] as boolean) ? 'On' : 'Off'}
              </span>
            </label>
          {:else if field.choices}
            <!-- M13 (a11y audit): onchange fires on commit; onblur as
                 well is redundant and double-toggles the live region. -->
            <select
              id={inputId}
              bind:value={local[field.key] as string}
              disabled={!field.mutable}
              aria-describedby="{errId} {helpId} {statusId}"
              aria-invalid={hasError ? 'true' : 'false'}
              onchange={() => saveField(field.key)}
              oninput={() => clearFieldError(field.key)}
            >
              {#each field.choices as opt}
                <option value={opt}>{opt}</option>
              {/each}
            </select>
          {:else if field.type === 'int' || field.type === 'float'}
            <input
              id={inputId}
              type="number"
              inputmode="numeric"
              min={field.min ?? undefined}
              max={field.max ?? undefined}
              bind:value={local[field.key] as string}
              readonly={!field.mutable}
              aria-readonly={!field.mutable ? 'true' : undefined}
              aria-describedby="{errId} {helpId} {statusId}"
              aria-invalid={hasError ? 'true' : 'false'}
              autocomplete="off"
              oninput={() => clearFieldError(field.key)}
              onblur={() => saveField(field.key)}
              onkeydown={onEnterToBlur}
            />
          {:else if field.type === 'url'}
            <input
              id={inputId}
              type="url"
              inputmode="url"
              autocomplete="url"
              placeholder="https://…"
              bind:value={local[field.key] as string}
              readonly={!field.mutable}
              aria-readonly={!field.mutable ? 'true' : undefined}
              aria-describedby="{errId} {helpId} {statusId}"
              aria-invalid={hasError ? 'true' : 'false'}
              oninput={() => clearFieldError(field.key)}
              onblur={() => saveField(field.key)}
              onkeydown={onEnterToBlur}
            />
          {:else if field.type === 'secret'}
            <!-- M12 (a11y audit): autocomplete="new-password" tells
                 password managers NOT to autofill saved credentials
                 into LLM-API-key fields. autocomplete="off" alone is
                 ignored by Chrome and Firefox for password inputs. -->
            <input
              id={inputId}
              type="password"
              autocomplete="new-password"
              placeholder={field.set ? '(set — type to replace)' : '(not set)'}
              bind:value={local[field.key] as string}
              readonly={!field.mutable}
              aria-readonly={!field.mutable ? 'true' : undefined}
              aria-describedby="{errId} {helpId} {statusId}"
              aria-invalid={hasError ? 'true' : 'false'}
              oninput={() => clearFieldError(field.key)}
              onblur={() => saveField(field.key)}
              onkeydown={onEnterToBlur}
            />
          {:else}
            <input
              id={inputId}
              type="text"
              autocomplete="off"
              bind:value={local[field.key] as string}
              readonly={!field.mutable}
              aria-readonly={!field.mutable ? 'true' : undefined}
              aria-describedby="{errId} {helpId} {statusId}"
              aria-invalid={hasError ? 'true' : 'false'}
              oninput={() => clearFieldError(field.key)}
              onblur={() => saveField(field.key)}
              onkeydown={onEnterToBlur}
            />
          {/if}

          <p id={errId} class="error-inline" role="alert" hidden={!hasError}>
            {fieldError[field.key]}
          </p>

          <p id={helpId} class="hint">
            {field.description}
            {#if field.hint} <span class="muted small">{field.hint}</span>{/if}
            {#if field.default_label} <span class="muted small">Default: <code class="mono">{field.default_label}</code>.</span>{/if}
          </p>

          <p id={statusId} class="field-status" aria-hidden="true">
            {badgeText(field.key)}
            {#if field.source === 'db' && field.mutable && !hasError}
              <button
                type="button"
                class="link-btn"
                onclick={() => revertToDefault(field.key)}
                aria-label="Revert {humanLabel(field)} to default"
              >revert</button>
            {/if}
          </p>
        </div>
      {/each}

      <!-- Per-section ancillary actions -->
      {#if section.id === 'ntfy'}
        <div class="row-actions">
          <button
            type="button"
            class="btn btn-ghost"
            onclick={sendNtfyTest}
            disabled={!fieldsInSection('ntfy').find((f) => f.key === 'ntfy_url')?.value || ntfyTesting}
          >
            {ntfyTesting ? 'Sending…' : 'Send test notification'}
          </button>
          <span
            class="test-result"
            role={ntfyResult.kind === 'err' ? 'alert' : 'status'}
            aria-live={ntfyResult.kind === 'err' ? 'assertive' : 'polite'}
            aria-atomic="true"
            class:error-inline={ntfyResult.kind === 'err'}
          >{ntfyResult.msg}</span>
        </div>
      {/if}
    </section>
  {/each}
{/if}

<style>
  .page-head { margin-bottom: var(--space-6); }
  .sub {
    margin: var(--space-2) 0 0;
    color: var(--text-muted);
    font-size: var(--text-sm);
    max-width: 60ch;
  }
  .restart-tag {
    display: inline-block;
    font-size: var(--text-xs);
    color: var(--text-dim);
    border: 1px solid var(--border-input);
    border-radius: var(--radius-sm);
    padding: 1px 6px;
    margin-left: var(--space-2);
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  section.card {
    margin-bottom: var(--space-5);
  }
  section.card h2 {
    margin: 0 0 var(--space-3);
  }

  .field {
    display: grid;
    grid-template-columns: 240px 1fr;
    gap: var(--space-3) var(--space-4);
    padding: var(--space-4) 0;
    border-top: 1px solid var(--border);
    align-items: start;
  }
  .field:first-of-type { border-top: 0; padding-top: var(--space-2); }
  .field-label {
    color: var(--text);
    font-weight: 500;
    font-size: var(--text-sm);
    padding-top: 10px;  /* visual baseline alignment with input */
  }
  .field-label .restart-tag { vertical-align: middle; }

  .field input[type="text"],
  .field input[type="url"],
  .field input[type="password"],
  .field input[type="number"],
  .field select {
    background: var(--bg-input);
    color: var(--text);
    border: 1px solid var(--border-input);
    border-radius: var(--radius-sm);
    padding: var(--space-2) var(--space-3);
    font-family: inherit;
    font-size: var(--text-base);
    min-height: 44px;
    width: 100%;
    max-width: 480px;
    transition: border-color var(--dur) var(--ease);
  }
  .field input:focus,
  .field select:focus {
    border-color: var(--accent);
    outline: 2px solid var(--accent);
    outline-offset: 0;
  }
  .field input[readonly],
  .field input:disabled,
  .field select:disabled {
    color: var(--text-muted);
    background: var(--bg-elevated);
    cursor: not-allowed;
  }
  .field input[aria-invalid="true"] {
    border-color: var(--error);
  }

  /* The error <p hidden> is also display:none — but defensive in case
     a browser ignores `hidden` for some reason. */
  .error-inline {
    margin: var(--space-2) 0 0;
    color: var(--error);
    font-size: var(--text-sm);
    font-weight: 500;
  }
  .error-inline[hidden] { display: none; }

  .hint {
    margin: var(--space-2) 0 0;
    color: var(--text-muted);
    font-size: var(--text-sm);
    line-height: 1.5;
    max-width: 60ch;
  }
  .hint code.mono {
    background: var(--bg-input);
    padding: 1px 6px;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border-input);
  }

  .field-status {
    margin: var(--space-1) 0 0;
    font-size: var(--text-xs);
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
    min-height: 16px;
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }

  .link-btn {
    background: none;
    border: 0;
    color: var(--accent);
    font: inherit;
    font-size: var(--text-xs);
    cursor: pointer;
    padding: 4px 6px;
    border-radius: var(--radius-sm);
  }
  .link-btn:hover { text-decoration: underline; }
  .link-btn:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  /* === Toggle (styled checkbox) =========================================
     Native <input type=checkbox> for semantics; visually a switch.
     Per a11y guidance: NEVER role=switch, native checkbox is announced
     reliably across NVDA/JAWS/VoiceOver. */
  .toggle-row {
    display: inline-flex;
    align-items: center;
    gap: var(--space-3);
    cursor: pointer;
    user-select: none;
    min-height: 44px;
  }
  .toggle-row input[type="checkbox"] {
    position: absolute;
    opacity: 0;
    pointer-events: none;
    width: 1px;
    height: 1px;
  }
  .toggle-track {
    width: 44px;
    height: 24px;
    border-radius: 999px;
    background: var(--bg-input);
    border: 1px solid var(--border-input);
    position: relative;
    transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease);
    flex-shrink: 0;
  }
  .toggle-thumb {
    position: absolute;
    top: 2px;
    left: 2px;
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: var(--text-muted);
    transition: transform var(--dur) var(--ease), background var(--dur) var(--ease);
  }
  .toggle-row input:checked ~ .toggle-track {
    background: color-mix(in srgb, var(--accent) 25%, transparent);
    border-color: var(--accent);
  }
  .toggle-row input:checked ~ .toggle-track .toggle-thumb {
    transform: translateX(20px);
    background: var(--accent);
  }
  .toggle-row input:focus-visible ~ .toggle-track {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .toggle-row input:disabled ~ .toggle-track {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .toggle-label {
    font-size: var(--text-sm);
    color: var(--text);
    min-width: 30px;
  }

  /* === Other reusable bits (kept from prior page) ======================== */

  .kv {
    display: grid;
    grid-template-columns: 180px 1fr;
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
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
  }

  .indicator {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    display: inline-block;
    flex-shrink: 0;
  }
  .indicator.ok { background: var(--success); }
  .indicator.bad { background: var(--error); }
  .indicator.warn { background: var(--warn); }
  .indicator.pending { background: var(--text-dim); }

  .version-pill {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    color: var(--text-muted);
    background: var(--bg-input);
    border: 1px solid var(--border-input);
    padding: 2px 8px;
    border-radius: var(--radius-pill);
  }

  .row-actions {
    margin-top: var(--space-4);
    display: flex;
    align-items: center;
    gap: var(--space-3);
    flex-wrap: wrap;
  }
  .test-result {
    color: var(--text-muted);
    font-size: var(--text-sm);
    min-height: 1.5em;
  }

  .error-banner {
    background: var(--error-bg);
    border-color: var(--error);
    color: var(--error);
  }

  .muted { color: var(--text-muted); }
  .small { font-size: var(--text-sm); }

  @media (max-width: 768px) {
    .field { grid-template-columns: 1fr; gap: var(--space-2); }
    .field-label { padding-top: 0; }
    .kv { grid-template-columns: 1fr; gap: var(--space-1) 0; }
    .kv dt { margin-top: var(--space-2); }
  }

  @media (prefers-reduced-motion: reduce) {
    .toggle-thumb,
    .toggle-track {
      transition: none !important;
    }
  }
</style>
