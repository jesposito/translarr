<script lang="ts">
  /**
   * TimingQualityBadge — non-color severity indicator for the reading-rate
   * adapter's per-job quality score (0-100). Color is reinforced with a
   * text label (Good / Marginal / Poor) and a unicode glyph (decorative,
   * hidden from AT). Matches the existing `.pill` pattern in tokens.css.
   *
   * Mark this badge `aria-hidden="true"` when it lives inside a parent
   * link whose accessible name already includes the score (avoids
   * double-announcement). Use as a standalone badge with `decorative={false}`
   * (default) when nothing else conveys the score.
   */

  type Props = {
    score: number | null | undefined;
    badge?: 'green' | 'yellow' | 'red' | null;
    /** When true, the badge has no accessible name (parent link names the score). */
    decorative?: boolean;
    /** Compact variant for table cells; default is comfortable. */
    size?: 'compact' | 'comfortable';
  };

  let { score, badge, decorative = false, size = 'comfortable' }: Props = $props();

  function bucket(s: number): 'green' | 'yellow' | 'red' {
    if (s >= 95) return 'green';
    if (s >= 80) return 'yellow';
    return 'red';
  }

  // Score may be null on legacy / non-completed jobs — render nothing.
  const resolved = $derived.by(() => {
    if (score == null) return null;
    const b = badge ?? bucket(score);
    const label = b === 'green' ? 'Good' : b === 'yellow' ? 'Marginal' : 'Poor';
    // Unicode glyphs: check / dash / cross. Decorative — aria-hidden.
    const glyph = b === 'green' ? '✓' : b === 'yellow' ? '–' : '×';
    return { score: Math.round(score), bucket: b, label, glyph };
  });
</script>

{#if resolved}
  <span
    class="qbadge"
    class:compact={size === 'compact'}
    data-quality={resolved.bucket}
    aria-hidden={decorative ? 'true' : undefined}
    aria-label={decorative ? undefined : `Timing quality ${resolved.score}, ${resolved.label.toLowerCase()}`}
  >
    <span class="glyph" aria-hidden="true">{resolved.glyph}</span>
    <span class="score">{resolved.score}</span>
    <span class="label">{resolved.label}</span>
  </span>
{/if}

<style>
  /* Same shape language as `.pill` in tokens.css — currentColor border + 16%
     tint background. Color tokens already pass WCAG AA against bg-elevated
     in both themes (--success/--warn/--error are tuned for that). */
  .qbadge {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    padding: 2px 8px;
    border-radius: var(--radius-pill);
    font-size: var(--text-xs);
    font-weight: 500;
    border: 1px solid currentColor;
    background: color-mix(in srgb, currentColor 16%, transparent);
    white-space: nowrap;
    line-height: 1.4;
    font-variant-numeric: tabular-nums;
  }
  .qbadge.compact {
    padding: 1px 6px;
  }
  .qbadge[data-quality="green"]  { color: var(--success); }
  .qbadge[data-quality="yellow"] { color: var(--warn); }
  .qbadge[data-quality="red"]    { color: var(--error); }

  /* Glyph + score use tabular-nums so values don't jitter on poll re-render. */
  .glyph {
    font-weight: 700;
    line-height: 1;
  }
  .score { font-weight: 600; }
  .label {
    color: inherit;
    opacity: 0.85;
  }
</style>
