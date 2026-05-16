import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    // Static-export adapter: produces a fully prerendered bundle in ui/dist/
    // which FastAPI mounts at GET / via StaticFiles.
    adapter: adapter({
      pages: 'dist',
      assets: 'dist',
      fallback: 'index.html',
      precompress: false,
      strict: false
    }),
    // SPA fallback so client-side routing works under FastAPI StaticFiles.
    // handleHttpError stays 'warn' so the prerender crawl noting /backup
    // and other backend-only routes as 404s doesn't fail the build —
    // those routes only exist behind FastAPI at runtime.
    prerender: {
      handleHttpError: 'warn'
    }
  }
};

export default config;
