// Job detail is a dynamic route; resolved client-side via the SPA fallback.
// Override the layout's prerender=true so the build doesn't try to prerender
// every possible id.
export const prerender = false;
export const ssr = false;
