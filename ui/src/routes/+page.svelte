<script lang="ts">
  // Placeholder home page for v0.6.5 UI scaffold.
  // Real screens (dashboard, job detail, settings, glossary editor, coverage matrix)
  // ship in subsequent beads tasks (TR-7p7.13.3 through .8).
  const version = '0.6.5';
</script>

<svelte:head>
  <title>Translarr {version}</title>
</svelte:head>

<main>
  <h1>Translarr v{version}</h1>
  <p>UI scaffold ready.</p>
</main>

<style>
  main {
    font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
    max-width: 40rem;
    margin: 4rem auto;
    padding: 0 1.5rem;
    color: #1a1a1a;
  }

  h1 {
    font-size: 1.75rem;
    font-weight: 600;
    letter-spacing: -0.01em;
    margin: 0 0 0.5rem;
  }

  p {
    color: #6b6b6b;
    margin: 0;
  }
</style>
