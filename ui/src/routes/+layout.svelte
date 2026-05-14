<script lang="ts">
  import '$lib/tokens.css';
  import { page } from '$app/stores';
  import { afterNavigate } from '$app/navigation';
  import { onMount } from 'svelte';

  let { children } = $props();

  let navOpen = $state(false);
  let isMobile = $state(false);

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
    const mq = window.matchMedia('(max-width: 768px)');
    isMobile = mq.matches;
    const handler = (e: MediaQueryListEvent) => (isMobile = e.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
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
    <a href="/" class="brand">Translarr</a>
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
      <span class="brand">Translarr</span>
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
    </ul>
  </nav>

  <main id="main" tabindex="-1">
    {@render children()}
  </main>
</div>

<style>
  .shell {
    display: grid;
    grid-template-columns: 240px 1fr;
    min-height: 100vh;
    /* M2: use dynamic viewport on mobile (URL bar collapse) */
    min-height: 100dvh;
  }

  .topbar {
    display: none;
  }

  .sidenav {
    background: var(--bg-elevated);
    border-right: 1px solid var(--border);
    padding: var(--space-5) var(--space-4);
    position: sticky;
    top: 0;
    height: 100vh;
    height: 100dvh;
    overflow-y: auto;
  }

  .brand-block {
    padding: 0 var(--space-2) var(--space-5);
    border-bottom: 1px solid var(--border);
    margin-bottom: var(--space-4);
  }

  .brand {
    font-size: var(--text-lg);
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--text);
    text-decoration: none;
  }

  .sidenav ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
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
    .shell { grid-template-columns: 1fr; }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: var(--space-3) var(--space-4);
      /* M6: clear iOS notch */
      padding-top: max(var(--space-3), env(safe-area-inset-top));
      background: var(--bg-elevated);
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      z-index: 10;
      min-height: 48px;
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
      top: 48px;
      left: 0;
      right: 0;
      height: auto;
      max-height: calc(100vh - 48px);
      max-height: calc(100dvh - 48px);
      /* M6: clear iOS home indicator */
      padding-bottom: max(var(--space-4), env(safe-area-inset-bottom));
      transform: translateY(-110%);
      transition: transform var(--dur) var(--ease);
      z-index: 9;
      border-right: 0;
      border-bottom: 1px solid var(--border);
    }
    .sidenav.open { transform: translateY(0); }
    main { padding: var(--space-4); }
  }
</style>
