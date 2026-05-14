# Translarr Web UI

Polished standalone web UI for Translarr v0.6.5+. Served by the FastAPI server at `GET /`.

## Stack

- **SvelteKit 5** (Svelte 5 runes, current LTS)
- **TypeScript** strict mode
- **Vite** build tool
- **`@sveltejs/adapter-static`** — fully prerendered static bundle, no Node runtime needed

Why SvelteKit: the user has Svelte 5 momentum on Quillr, `adapter-static` produces a clean prerendered output that drops directly into FastAPI's `StaticFiles`, and the Svelte component model keeps the bundle small. No SSR runtime to host alongside Python.

## Layout

```
ui/
├── package.json
├── svelte.config.js          # adapter-static → ui/dist/
├── vite.config.ts            # dev proxy → FastAPI :9000
├── tsconfig.json
├── src/
│   ├── app.html              # HTML shell
│   ├── app.d.ts              # ambient types
│   └── routes/
│       ├── +layout.ts        # prerender = true
│       └── +page.svelte      # placeholder home
└── static/                   # static assets (favicon, etc.)
```

## Build

```bash
cd ui
npm install
npm run build          # → ui/dist/
```

`npm run dev` runs Vite on `:5173` and proxies `/health`, `/translate`, `/jobs`, `/webhooks` to FastAPI on `:9000` for live development against a running server.

## FastAPI integration

`server/main.py` mounts `ui/dist/` at `/` via `StaticFiles(html=True)` AFTER all API routers, so existing endpoints (`/health`, `/translate`, `/jobs/{id}`, `/webhooks/*`) continue to win route resolution. The mount is guarded: if `ui/dist/` does not exist (e.g., a dev environment that never ran `npm run build`), the server starts cleanly and `/` simply 404s.

Production Docker build should run `npm ci && npm run build` inside `ui/` as a separate build stage, then copy `ui/dist/` into the final image alongside the Python code.

## Roadmap

This task (TR-7p7.13.1) only scaffolds the framework and verifies the FastAPI mount. Real screens land in subsequent beads tasks:

- TR-7p7.13.2 — design consultation
- TR-7p7.13.3 — dashboard
- TR-7p7.13.4 — job detail
- TR-7p7.13.5 — settings
- TR-7p7.13.6 — glossary editor
- TR-7p7.13.7 — coverage matrix
- TR-7p7.13.8 — accessibility review (WCAG 2.2 AA)
