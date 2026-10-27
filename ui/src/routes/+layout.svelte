<script lang="ts">
  import '$lib/tokens.css';
  import { page } from '$app/stores';
  import { afterNavigate } from '$app/navigation';
  import { onMount } from 'svelte';

  let { children } = $props();

  let navOpen = $state(false);
  let isMobile = $state(false);
  let unicornMode = $state(false);
  let modeAnnouncement = $state('');

  const THEME_KEY = 'translarr.uiTheme.v1';

  function applyTheme(enabled: boolean): void {
    unicornMode = enabled;
    document.documentElement.dataset.theme = enabled ? 'unicorn' : 'standard';
  }

  function toggleUnicornMode(): void {
    const next = !unicornMode;
    applyTheme(next);
    try {
      localStorage.setItem(THEME_KEY, next ? 'unicorn' : 'standard');
    } catch {
      // Storage can be unavailable in private browsing; the in-page toggle still works.
    }
    modeAnnouncement = next ? 'Unicorn mode enabled.' : 'Unicorn mode disabled.';
  }

  function isActive(href: string, current: string): boolean {
    if (href === '/') return current === '/';
    return current === href || current.startsWith(href + '/');
  }

  // M3: focus <main> after every SPA navigation so screen readers hear
  // the new page heading instead of staying anchored to whatever link
  // the user clicked.
  afterNavigate(() => {
    const m = document.getElementById('main');
    if (m) (m as HTMLElement).focus({ preventScroll: false });
    navOpen = false;
  });

  // Track viewport for mobile-only behaviors (inert nav when closed,
  // outside-click dismiss). Single matchMedia listener — no resize spam.
  onMount(() => {
    try {
      applyTheme(localStorage.getItem(THEME_KEY) === 'unicorn');
    } catch {
      applyTheme(false);
    }
    const mq = window.matchMedia('(max-width: 768px)');
    isMobile = mq.matches;
    const handler = (e: MediaQueryListEvent) => (isMobile = e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  });

  // M1 (a11y audit): if the viewport flips from mobile to desktop while
  // the mobile nav is open, the open state becomes stale (sidenav is now
  // always visible, but `navOpen` still says "open"). Close it so the
  // next mobile resize starts from a known state.
  $effect(() => {
    if (!isMobile && navOpen) navOpen = false;
  });

  // M4: Escape closes the mobile nav and returns focus to the toggle.
  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape' && navOpen) {
      navOpen = false;
      const h = document.querySelector<HTMLButtonElement>('.hamburger');
      h?.focus();
    }
  }

  // M4: pointer-down outside the panel + toggle dismisses the nav.
  function onPointerDown(e: PointerEvent) {
    if (!navOpen) return;
    const t = e.target as HTMLElement | null;
    if (!t) return;
    if (!t.closest('#primary-nav') && !t.closest('.hamburger')) {
      navOpen = false;
    }
  }
</script>

<svelte:window onkeydown={onKeydown} onpointerdown={onPointerDown} />

<svelte:head>
  <title>Translarr</title>
</svelte:head>

<a class="skip-link" href="#main">Skip to main content</a>

<div class="shell">
  <header class="topbar" aria-label="Mobile site header">
    <a href="/" class="brand">
      <span class="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 32 32" focusable="false">
          <path d="M6.5 7.5h19v13h-9l-5.5 4v-4H6.5z" />
          <path d="M11 12h10M11 16h7" />
        </svg>
      </span>
      <span>Translarr</span>
    </a>
    <button
      type="button"
      class="hamburger"
      aria-expanded={navOpen}
      aria-controls="primary-nav"
      aria-label={navOpen ? 'Close navigation' : 'Open navigation'}
      onclick={() => (navOpen = !navOpen)}
    >
      <span aria-hidden="true">{navOpen ? '✕' : '☰'}</span>
    </button>
  </header>

  <nav
    id="primary-nav"
    class="sidenav"
    class:open={navOpen}
    aria-label="Primary"
    aria-hidden={isMobile && !navOpen ? 'true' : undefined}
    inert={isMobile && !navOpen ? true : undefined}
  >
    <div class="brand-block">
      <a href="/" class="brand">
        <span class="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 32 32" focusable="false">
            <path d="M6.5 7.5h19v13h-9l-5.5 4v-4H6.5z" />
            <path d="M11 12h10M11 16h7" />
          </svg>
        </span>
        <span>Translarr</span>
      </a>
      <span class="brand-meta">Subtitle operations</span>
    </div>
    <ul>
      <li>
        <a
          href="/"
          aria-current={isActive('/', $page.url.pathname) ? 'page' : undefined}
        >Dashboard</a>
      </li>
      <li>
        <a
          href="/translate"
          aria-current={isActive('/translate', $page.url.pathname) ? 'page' : undefined}
        >Translate</a>
      </li>
      <li>
        <a
          href="/settings"
          aria-current={isActive('/settings', $page.url.pathname) ? 'page' : undefined}
        >Settings</a>
      </li>
      <li>
        <a
          href="/library"
          aria-current={isActive('/library', $page.url.pathname) ? 'page' : undefined}
        >Library</a>
      </li>
      <li>
        <a
          href="/glossary"
          aria-current={isActive('/glossary', $page.url.pathname) ? 'page' : undefined}
        >Glossary</a>
      </li>
    </ul>
    <div class="nav-footer">
      <button
        type="button"
        class="mode-toggle"
        aria-pressed={unicornMode}
        onclick={toggleUnicornMode}
      >
        <span class="mode-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" focusable="false">
            <path d="m12 3 1.6 5.4L19 10l-5.4 1.6L12 17l-1.6-5.4L5 10l5.4-1.6z" />
            <path d="M18.5 15.5 19 18l2.5.5L19 19l-.5 2.5L18 19l-2.5-.5L18 18z" />
          </svg>
        </span>
        <span>{unicornMode ? 'Unicorn mode on' : 'Unicorn mode'}</span>
      </button>
      <p>A little extra magic. Saved on this browser.</p>
    </div>
  </nav>

  <main id="main" tabindex="-1">
    {@render children()}
  </main>
</div>

<div class="sr-only" role="status" aria-live="polite">{modeAnnouncement}</div>

<style>
  .shell {
    display: grid;
    grid-template-columns: 240px 1fr;
    min-height: 100vh;
    /* M2: use dynamic viewport on mobile (URL bar collapse) */
    min-height: 100dvh;
    isolation: isolate;
  }

  .topbar {
    display: none;
  }

  .sidenav {
    background: var(--bg-nav);
    border-right: 1px solid var(--border);
    padding: var(--space-5) var(--space-4);
    position: sticky;
    top: 0;
    height: 100vh;
    height: 100dvh;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
  }

  .brand-block {
    padding: 0 var(--space-2) var(--space-5);
    border-bottom: 1px solid var(--border);
    margin-bottom: var(--space-4);
  }

  .brand {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    font-size: var(--text-lg);
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--text);
    text-decoration: none;
  }

  .brand-mark {
    width: 28px;
    height: 28px;
    display: inline-grid;
    place-items: center;
    color: var(--accent);
    flex: 0 0 auto;
  }
  .brand-mark svg {
    width: 100%;
    height: 100%;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.8;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .brand-meta {
    display: block;
    margin: var(--space-1) 0 0 36px;
    color: var(--text-muted);
    font-size: var(--text-xs);
  }

  .sidenav ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }

  .nav-footer {
    margin-top: auto;
    padding-top: var(--space-5);
    border-top: 1px solid var(--border);
  }
  .nav-footer p {
    margin: var(--space-2) var(--space-2) 0;
    color: var(--text-muted);
    font-size: var(--text-xs);
    line-height: 1.4;
  }
  .mode-toggle {
    width: 100%;
    min-height: 44px;
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    border: 1px solid var(--border-strong);
    border-radius: var(--radius-sm);
    background: transparent;
    color: var(--text-muted);
    font-size: var(--text-sm);
    font-weight: 500;
    transition:
      background var(--dur) var(--ease),
      border-color var(--dur) var(--ease),
      color var(--dur) var(--ease);
  }
  .mode-toggle:hover {
    background: var(--bg-elevated-hover);
    color: var(--text);
  }
  .mode-toggle[aria-pressed="true"] {
    border-color: var(--accent);
    color: var(--accent);
    background: color-mix(in srgb, var(--accent) 10%, transparent);
  }
  .mode-icon {
    width: 20px;
    height: 20px;
    display: inline-grid;
    place-items: center;
  }
  .mode-icon svg {
    width: 100%;
    height: 100%;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.6;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  :global(html[data-theme="unicorn"]) .brand-mark,
  :global(html[data-theme="unicorn"]) .mode-icon {
    filter: drop-shadow(0 4px 8px rgb(240 171 252 / 0.28));
  }

  .sidenav a {
    display: block;
    padding: var(--space-2) var(--space-3);
    color: var(--text-muted);
    border-radius: var(--radius-sm);
    text-decoration: none;
    font-size: var(--text-sm);
    font-weight: 500;
    /* M7-adjacent: nav-link target meets 44px floor */
    min-height: 44px;
    line-height: 28px;
    transition: background var(--dur) var(--ease), color var(--dur) var(--ease);
  }
  .sidenav a:hover {
    background: var(--bg-elevated-hover);
    color: var(--text);
    text-decoration: none;
  }
  .sidenav a[aria-current="page"] {
    background: var(--bg-elevated-hover);
    color: var(--text);
    border-left: 2px solid var(--accent);
    padding-left: calc(var(--space-3) - 2px);
  }

  main {
    padding: var(--space-5);
    max-width: 1200px;
    width: 100%;
    margin: 0 auto;
    outline: none;
  }

  @media (min-width: 1024px) {
    main { padding: var(--space-6); }
  }

  @media (max-width: 768px) {
    .shell {
      grid-template-columns: 1fr;
      /* 44px hamburger + vertical padding + bottom border. The sidenav
         pins below this, so it must match the real topbar height. */
      --topbar-h: calc(44px + var(--space-3) + max(var(--space-3), env(safe-area-inset-top)) + 1px);
    }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--space-3) var(--space-4);
      /* M6: clear iOS notch */
      padding-top: max(var(--space-3), env(safe-area-inset-top));
      background: var(--bg-nav);
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      z-index: 10;
      height: var(--topbar-h);
    }
    .hamburger {
      background: transparent;
      border: 1px solid var(--border-strong);
      color: var(--text);
      border-radius: var(--radius-sm);
      /* M7: 44x44 touch target on mobile */
      width: 44px;
      height: 44px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 18px;
    }
    .sidenav {
      position: fixed;
      top: var(--topbar-h);
      left: 0;
      right: 0;
      height: auto;
      max-height: calc(100vh - var(--topbar-h));
      max-height: calc(100dvh - var(--topbar-h));
      /* M6: clear iOS home indicator */
      padding-bottom: max(var(--space-4), env(safe-area-inset-bottom));
      transform: translateY(-110%);
      transition: transform var(--dur) var(--ease);
      z-index: 9;
      border-right: 0;
      border-bottom: 1px solid var(--border);
    }
    .sidenav.open { transform: translateY(0); }
    .nav-footer { margin-top: var(--space-5); }
    .nav-footer p { display: none; }
    main { padding: var(--space-4); }
  }
</style>
