// Prerender all routes as static HTML so the build produces a static bundle
// FastAPI can serve via StaticFiles. SSR is off — this is a SPA-style app
// once it hydrates, but the initial shell is prerendered.
export const prerender = true;
export const ssr = true;
