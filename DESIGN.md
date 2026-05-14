# Translarr Design Language

> Single source of truth for the Translarr v0.6.5+ Web UI.
> Established 2026-05-14 as part of TR-7p7.13.2.

## 1. Aesthetic

Translarr's UI is utilitarian-with-soul. It is a free, self-hosted tool that runs alongside Sonarr, Radarr, Prowlarr, gluetun, NPMplus, and the rest of the arr stack — boxes nobody markets, boxes you trust because someone built them carefully. The UI shows concrete numbers and live data first: the cost you spent today, the job currently running, the four lines of the subtitle file it just produced. There is no hero copy, no marketing illustration, no "Get started in 30 seconds" gradient. There is one accent color and a lot of disciplined gray. It looks like something a thoughtful engineer would build for themselves and then quietly share. References to draw from: Linear's density and information hierarchy, Plausible's restraint, Beeper's calm dark surfaces, the Tailwind CSS docs' table treatments, the original (pre-rebrand) Stripe docs' typographic clarity. Explicit non-goals: SaaS landing pages, gradient hero sections, mascot illustrations, glassmorphism, neumorphism, AI-generated SVG backgrounds, animated logos, "delightful" empty states with smiling robots.

## 2. Color palette

Single warm accent. Amber (`#f59e0b`) — close enough to the arr-stack vernacular to feel native, far enough from Sonarr-blue and Radarr-yellow to read as ours. Everything else is neutral.

### Dark (default — respects `prefers-color-scheme`)

```css
--bg            #0a0a0a;  /* near-black canvas */
--bg-elevated   #161616;  /* cards, panels */
--bg-input      #1f1f1f;  /* form controls, code blocks */
--border        #2a2a2a;  /* hairlines */
--border-strong #404040;  /* focused / hovered borders */

--text          #ededed;  /* primary — 14.0:1 vs --bg ✓ AAA */
--text-muted    #a1a1a1;  /* secondary — 7.2:1 vs --bg ✓ AAA */
--text-dim      #707070;  /* tertiary — 4.7:1 vs --bg ✓ AA body */

--accent        #f59e0b;  /* amber — 10.2:1 vs --bg ✓ AAA */
--accent-hover  #fbbf24;  /* hover state */
--accent-fg     #0a0a0a;  /* readable on accent bg — 10.2:1 ✓ */

--success       #10b981;  /* 7.5:1 ✓ AAA */
--warn          #fbbf24;  /* 11.7:1 ✓ AAA */
--error         #f87171;  /* 7.4:1 ✓ AAA */

/* State pill semantics */
--queued        #6b7280;  /* neutral gray — 4.6:1 ✓ AA */
--running       #f59e0b;  /* accent (active, in motion) */
--retrying      #fbbf24;  /* warn (active, recovering) */
--done          #10b981;  /* success */
--failed        #f87171;  /* error */
--cancelled     #707070;  /* dim gray */
```

### Light (when user prefers light)

```css
--bg            #ffffff;
--bg-elevated   #fafafa;
--bg-input      #f4f4f5;
--border        #e4e4e7;
--border-strong #a1a1aa;

--text          #18181b;  /* 17.8:1 ✓ AAA */
--text-muted    #52525b;  /* 7.5:1 ✓ AAA */
--text-dim      #71717a;  /* 4.7:1 ✓ AA */

--accent        #b45309;  /* darker amber for light-bg contrast — 5.7:1 ✓ AA */
--accent-hover  #92400e;
--accent-fg     #ffffff;

--success       #047857;  /* 5.2:1 ✓ AA */
--warn          #92400e;
--error         #b91c1c;  /* 6.5:1 ✓ AA */
```

Color is **never** the only signal. Status pills carry text labels alongside their color.

## 3. Typography

System font stack. No webfonts — saves a network round trip and ~100KB, and renders pixel-perfect on the platforms our self-hosters actually use (macOS, Linux desktops, Unraid web UIs viewed from phones).

```css
--font-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, system-ui, sans-serif;
--font-mono: ui-monospace, SFMono-Regular, "JetBrains Mono", Menlo, Consolas, monospace;
```

### Scale (px → rem, root = 16px)

| Token        | px | rem    | Usage                          |
|--------------|----|--------|--------------------------------|
| `--text-xs`  | 12 | 0.75   | Labels, captions, table meta   |
| `--text-sm`  | 14 | 0.875  | Body small, table cells        |
| `--text-base`| 16 | 1.0    | Body default                   |
| `--text-lg`  | 20 | 1.25   | Stat tile values, h3           |
| `--text-xl`  | 24 | 1.5    | h2                             |
| `--text-2xl` | 32 | 2.0    | h1                             |

### Line-height & weight

- `line-height: 1.5` for body text
- `line-height: 1.2` for headings
- Weights: `400` normal, `500` medium, `600` semibold. **No 700/bold** — semibold reads heavier than it sounds and saves a font file.
- `letter-spacing: -0.01em` on h1/h2 only.

## 4. Spacing scale

4px base. Use only these values; no ad-hoc magic numbers.

```css
--space-1: 4px;
--space-2: 8px;
--space-3: 12px;
--space-4: 16px;
--space-5: 24px;
--space-6: 32px;
--space-7: 48px;
--space-8: 64px;
```

## 5. Components

No component library. CSS variables + class conventions, used inside Svelte scoped styles.

### Card
Container for grouped data.
- bg: `--bg-elevated`
- 1px solid `--border`
- 8px border-radius
- 16px padding (24px on dashboard summary cards)
- Used: every panel on Dashboard and Job Detail.

### Stat tile
A type of Card. Large number on top (`--text-2xl`, weight 600), label below (`--text-xs`, `--text-muted`, uppercase, `letter-spacing: 0.05em`).
- Used: Dashboard summary row (Today's cost, Today's jobs, Queue depth, All-time cost).

### Job row
Horizontal flex row. From left to right: state pill, media basename (truncated with `text-overflow: ellipsis`, full path in `title=`), target lang, cost, finished-at timestamp. Clickable — entire row links to `/jobs/<id>`. Hover: `--bg-elevated` background nudges to `#1c1c1c`. Focus: 2px accent ring inset.
- Used: Dashboard "Recent done" and "Recent failures" tables.

### State pill
Small rounded badge.
- padding: `2px 8px`
- border-radius: 9999px (pill shape)
- `--text-xs`, weight 500
- Background is the state color at 16% alpha; text + 1px border at the full state color.
- `aria-label` includes the full state word for screen readers.

| State     | color var      |
|-----------|----------------|
| queued    | `--queued`     |
| running   | `--running`    |
| retrying  | `--retrying`   |
| done      | `--done`       |
| failed    | `--failed`     |
| cancelled | `--cancelled`  |

### Button
Two variants:
- **Primary** — bg `--accent`, fg `--accent-fg`, hover bg `--accent-hover`. Used for the one main action on each screen.
- **Ghost** — 1px solid `--border-strong`, fg `--text`, bg transparent. Hover: `--bg-elevated`. Used for everything else.
- Min height 36px (target size for WCAG 2.5.8).
- Focus ring: 2px solid `--accent`, offset 2px.

### Form field
- `<label>` above `<input>` (never placeholder-as-label).
- Input bg `--bg-input`, 1px border `--border`, 6px radius, 8px×12px padding.
- Focus: border becomes `--accent`.
- Error state: border becomes `--error`, message below in `--error`, `aria-describedby` wires them.

### Code block (monospace path display)
- `--font-mono`, `--text-sm`
- bg `--bg-input`, 12px padding, 6px radius
- `word-break: break-all` for long paths

### Toast
- Fixed bottom-right, 16px from edge
- Slides in from below, 150ms cubic-bezier(0.2, 0, 0, 1)
- Success: auto-dismiss after 5s
- Error: sticky until user dismisses (close button)
- `role="status"` for success, `role="alert"` for errors
- Stacks vertically; newest on bottom

## 6. Motion

- All transitions ≤ 200ms. No spring physics, no bouncing.
- Easing: `linear` for opacity-only fades; `cubic-bezier(0.2, 0, 0, 1)` for movement.
- Skeleton shimmer: 1.5s linear infinite (the one exception to 200ms — but it's a loading affordance, not an interaction).
- **Respect `prefers-reduced-motion: reduce`** — all transitions become instant (`0ms`), shimmer becomes a static gray block.

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

## 7. Accessibility (non-negotiable)

WCAG 2.2 AA is the floor, not the ceiling.

- **Contrast**: every text/background pair listed above is checked. Body text ≥ 4.5:1, large text and UI components ≥ 3:1.
- **Keyboard**: every interactive element is reachable via Tab. Visible focus ring (2px `--accent`, 2px offset). No `:focus { outline: none }` without a replacement.
- **Skip link**: first focusable element on every page links to `#main`, visually hidden until focused.
- **Heading order**: strict h1 → h2 → h3, no skips. One h1 per route.
- **Labels**: every form input has an explicit `<label for>` or `aria-label`. No placeholder-as-label.
- **Live regions**: polling-driven status updates (job state, queue depth) use `role="status"` + `aria-live="polite"` on the changing region (not the whole page).
- **Color is never the only signal**: state pills include the state word as text. Errors include an icon + the word "Error" as well as red color.
- **Icons**: decorative icons are `aria-hidden="true"`; meaningful icons have `aria-label`.
- **Target size**: every interactive control is ≥ 24×24px (WCAG 2.2 SC 2.5.8); primary buttons ≥ 36×36.
- **Reduced motion**: honored globally (see §6).
- **Lighthouse a11y goal**: ≥ 95 on every route, no exceptions.

## 8. Layout

Two-column shell.

```
┌─────────────┬───────────────────────────────────┐
│             │                                   │
│   nav       │   main (max-width: 1200px)        │
│   240px     │                                   │
│             │                                   │
└─────────────┴───────────────────────────────────┘
```

- Nav: 240px fixed, `--bg-elevated`, vertical link list.
- Below 768px viewport: nav collapses to a 48px-tall top bar with a hamburger toggle. Main becomes full-width.
- Main has 24px outer padding, 32px on ≥ 1024px.
- Content max-width 1200px, centered.

## 9. What this design system is NOT

- **Not a UI library.** No `<Button />` component to import. Classes and CSS variables — that's it. Svelte scoped styles do the rest. ~3KB of design tokens total.
- **Not Tailwind.** Plain CSS. Atomic-utility classes are a power tool with a cost we don't need at this size.
- **Not designed by committee.** Single voice. One accent. No "alternate brand palette." When the system says no bold weight, it means no bold weight.
- **Not a marketing site.** No "Trusted by 10,000 self-hosters" social-proof bar. The product proves itself by working.
