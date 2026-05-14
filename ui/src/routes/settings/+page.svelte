<script lang="ts">
  import { onMount } from 'svelte';

  interface Health {
    status: string;
    version: string;
    llm_provider: string;
    llm_model: string;
  }

  interface Config {
    llm_provider: string;
    llm_model: string;
    target_lang: string;
    reading_rate_cps: number;
    max_concurrent: number;
    context_window_lines: number;
    max_cost_cents_per_day: number;
    max_cost_cents_per_job: number;
    job_timeout_seconds: number;
    radarr_translate_tag: string;
    sonarr_translate_tag: string;
    webhook_secret_set: boolean;
    auto_translate_on_playback: boolean;
    ntfy_url_set: boolean;
    ntfy_on_success: boolean;
    ntfy_on_failure: boolean;
    ntfy_on_skip: boolean;
  }

  let health = $state<Health | null>(null);
  let config = $state<Config | null>(null);
  let healthError = $state<string | null>(null);
  let configError = $state<string | null>(null);
  let testing = $state(false);
  let testResult = $state<string>('');
  let ntfyTesting = $state(false);
  let ntfyResult = $state<{ kind: 'ok' | 'err' | ''; msg: string }>({ kind: '', msg: '' });

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
        // non-JSON body, leave empty
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

  // window.location.origin is only meaningful in the browser. We render an
  // em-dash placeholder during SSR/prerender so hydration matches.
  let serverUrl = $state<string>('—');

  function dollars(cents: number): string {
    return `$${(cents / 100).toFixed(2)}`;
  }

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
      config = (await r.json()) as Config;
      configError = null;
    } catch (e) {
      configError = e instanceof Error ? e.message : String(e);
      config = null;
    }
  }

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

  onMount(() => {
    serverUrl = window.location.origin;
    loadHealth();
    loadConfig();
  });
</script>

<svelte:head>
  <title>Settings — Translarr</title>
</svelte:head>

<header class="page-head">
  <h1>Settings</h1>
  <p class="sub">
    Read-only view of server configuration. Edit your <code class="mono">.env</code>
    file and restart Translarr to change any value.
  </p>
</header>

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

<section class="card" aria-labelledby="llm-heading">
  <h2 id="llm-heading">LLM provider</h2>
  {#if config}
    <dl class="kv">
      <dt>Provider</dt>
      <dd><span class="mono">{config.llm_provider}</span></dd>

      <dt>Model</dt>
      <dd><span class="mono">{config.llm_model}</span></dd>
    </dl>
    <p class="hint">
      Set with <code class="mono">LLM_PROVIDER=…</code> and
      <code class="mono">LLM_MODEL=…</code> env vars.
    </p>
  {:else if configError}
    <p class="error-inline" role="alert">Couldn't load config: {configError}</p>
  {:else}
    <div class="skeleton" style="width: 60%; height: 16px; margin-bottom: 10px;"></div>
    <div class="skeleton" style="width: 40%; height: 16px;"></div>
  {/if}
</section>

<section class="card" aria-labelledby="defaults-heading">
  <h2 id="defaults-heading">Translation defaults</h2>
  {#if config}
    <dl class="kv">
      <dt>Target language</dt>
      <dd><span class="mono">{config.target_lang}</span></dd>

      <dt>Reading rate</dt>
      <dd>
        <span class="mono">{config.reading_rate_cps}</span>
        <span class="muted small">chars/sec</span>
      </dd>

      <dt>Context window</dt>
      <dd>
        <span class="mono">{config.context_window_lines}</span>
        <span class="muted small">lines</span>
      </dd>
    </dl>
    <p class="hint">
      Lines exceeding the reading rate get auto-split. The industry standard is
      17 chars/sec for English. Set
      <code class="mono">TARGET_LANG</code>,
      <code class="mono">READING_RATE_CPS</code>,
      <code class="mono">CONTEXT_WINDOW_LINES</code>.
    </p>
  {:else if configError}
    <p class="error-inline" role="alert">Couldn't load config.</p>
  {:else}
    <div class="skeleton" style="width: 60%; height: 16px;"></div>
  {/if}
</section>

<section class="card" aria-labelledby="cost-heading">
  <h2 id="cost-heading">Cost guards</h2>
  {#if config}
    <dl class="kv">
      <dt>Daily cap</dt>
      <dd>
        <span class="mono">{dollars(config.max_cost_cents_per_day)}/day cap</span>
      </dd>

      <dt>Per-job cap</dt>
      <dd>
        <span class="mono">{dollars(config.max_cost_cents_per_job)}/job cap</span>
      </dd>

      <dt>Job timeout</dt>
      <dd><span class="mono">{config.job_timeout_seconds}s</span></dd>

      <dt>Concurrency</dt>
      <dd>
        <span class="mono">{config.max_concurrent}</span>
        <span class="muted small">workers</span>
      </dd>
    </dl>
    <p class="hint">
      Translarr stops accepting new work when the daily cap is hit. Set
      <code class="mono">MAX_COST_CENTS_PER_DAY</code>,
      <code class="mono">MAX_COST_CENTS_PER_JOB</code>,
      <code class="mono">JOB_TIMEOUT_SECONDS</code>,
      <code class="mono">MAX_CONCURRENT</code>.
    </p>
  {:else if configError}
    <p class="error-inline" role="alert">Couldn't load config.</p>
  {:else}
    <div class="skeleton" style="width: 60%; height: 16px;"></div>
  {/if}
</section>

<section class="card" aria-labelledby="playback-heading">
  <h2 id="playback-heading">On-demand translation</h2>
  {#if config}
    <dl class="kv">
      <dt>Auto-translate on Play</dt>
      <dd>
        {#if config.auto_translate_on_playback}
          <span class="indicator ok" aria-hidden="true"></span>
          <span>Enabled</span>
        {:else}
          <span class="indicator pending" aria-hidden="true"></span>
          <span>Disabled</span>
        {/if}
        <span class="sr-only"> — auto-translate on Play</span>
      </dd>
    </dl>
    <p class="hint">
      When enabled, pressing Play on an item with foreign-only subtitles
      kicks off an immediate translation; the new track appears in the player
      ~1-2 minutes later. Existing daily and per-job cost caps still apply.
      Defaults to <em>disabled</em> — set
      <code class="mono">AUTO_TRANSLATE_ON_PLAYBACK=true</code> in your
      <code class="mono">.env</code> and restart to opt in.
    </p>
  {:else if configError}
    <p class="error-inline" role="alert">Couldn't load config.</p>
  {:else}
    <div class="skeleton" style="width: 60%; height: 16px;"></div>
  {/if}
</section>

<section class="card" aria-labelledby="ntfy-heading">
  <h2 id="ntfy-heading">Push notifications</h2>
  {#if config}
    <dl class="kv">
      <dt>ntfy endpoint</dt>
      <dd>
        {#if config.ntfy_url_set}
          <span class="indicator ok" aria-hidden="true"></span>
          <span>Configured</span>
          <span class="sr-only"> — push notifications endpoint</span>
        {:else}
          <span class="indicator pending" aria-hidden="true"></span>
          <span>Not set</span>
          <span class="sr-only"> — push notifications endpoint</span>
        {/if}
      </dd>

      <dt>Notify on</dt>
      <dd>
        <span class="pill" data-state={config.ntfy_on_success ? 'done' : 'cancelled'}>
          success {config.ntfy_on_success ? 'on' : 'off'}
        </span>
        <span class="pill" data-state={config.ntfy_on_failure ? 'failed' : 'cancelled'}>
          failure {config.ntfy_on_failure ? 'on' : 'off'}
        </span>
        <span class="pill" data-state={config.ntfy_on_skip ? 'queued' : 'cancelled'}>
          skip {config.ntfy_on_skip ? 'on' : 'off'}
        </span>
      </dd>
    </dl>
    <div class="row-actions">
      <button
        type="button"
        class="btn btn-ghost"
        onclick={sendNtfyTest}
        disabled={!config.ntfy_url_set || ntfyTesting}
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
    <p class="hint">
      Translarr POSTs a short toast to <code class="mono">NTFY_URL</code>
      after every terminal job state. Works with public ntfy.sh
      (<code class="mono">https://ntfy.sh/&lt;random-topic&gt;</code>) or a
      self-hosted endpoint. Use the test button to verify wiring without
      queueing a real translation. Toggle categories with
      <code class="mono">NTFY_ON_SUCCESS</code>,
      <code class="mono">NTFY_ON_FAILURE</code>,
      <code class="mono">NTFY_ON_SKIP</code>.
    </p>
  {:else if configError}
    <p class="error-inline" role="alert">Couldn't load config.</p>
  {:else}
    <div class="skeleton" style="width: 60%; height: 16px;"></div>
  {/if}
</section>

<section class="card" aria-labelledby="arr-heading">
  <h2 id="arr-heading">arr-stack integration</h2>
  {#if config}
    <dl class="kv">
      <dt>Radarr tag</dt>
      <dd><span class="mono">{config.radarr_translate_tag}</span></dd>

      <dt>Sonarr tag</dt>
      <dd><span class="mono">{config.sonarr_translate_tag}</span></dd>

      <dt>Webhook secret</dt>
      <dd>
        {#if config.webhook_secret_set}
          <span class="indicator ok" aria-hidden="true"></span>
          <span>Configured</span>
        {:else}
          <span class="indicator warn" aria-hidden="true"></span>
          <span>Not set</span>
        {/if}
      </dd>
    </dl>
    <p class="hint">
      Apply <code class="mono">{config.radarr_translate_tag}</code> to Radarr
      movies (or <code class="mono">{config.sonarr_translate_tag}</code> to
      Sonarr series) you want auto-translated. Set
      <code class="mono">WEBHOOK_SECRET</code> to require a shared secret on
      <code class="mono">/webhooks/*</code>.
    </p>
  {:else if configError}
    <p class="error-inline" role="alert">Couldn't load config.</p>
  {:else}
    <div class="skeleton" style="width: 60%; height: 16px;"></div>
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

  section.card {
    margin-bottom: var(--space-5);
  }
  section.card h2 {
    margin: 0 0 var(--space-4);
  }

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
  @media (max-width: 640px) {
    .kv { grid-template-columns: 1fr; gap: var(--space-1) 0; }
    .kv dt { margin-top: var(--space-2); }
  }

  .hint {
    margin: var(--space-4) 0 0;
    color: var(--text-muted);
    font-size: var(--text-sm);
    line-height: 1.5;
  }
  .hint code.mono {
    background: var(--bg-input);
    padding: 1px 6px;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border);
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
    border: 1px solid var(--border);
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

  .muted { color: var(--text-muted); }
  .small { font-size: var(--text-sm); }

  .error-inline {
    margin: 0;
    color: var(--error);
    font-size: var(--text-sm);
  }
</style>
