import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    port: 5173,
    // During UI dev, proxy API calls to the FastAPI server on :9000
    proxy: {
      '/health': 'http://localhost:9000',
      '/translate': 'http://localhost:9000',
      '/jobs': 'http://localhost:9000',
      '/webhooks': 'http://localhost:9000'
    }
  }
});
