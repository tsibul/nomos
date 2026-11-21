import { defineConfig, loadEnv } from 'vite';
import { resolve } from 'path';

const frontend = resolve(import.meta.dirname, 'src/nomos/site/frontend');

// подпуть сайта из .env (NOMOS_URL_PREFIX), как в settings.py: статика — <подпуть>/static/
const env = loadEnv('', import.meta.dirname, '');
const prefix = (env.NOMOS_URL_PREFIX || '').trim().replace(/^\/+|\/+$/g, '');
const base = prefix ? `/${prefix}/static/` : '/static/';

export default defineConfig({
  root: frontend,
  base: base,
  build: {
    manifest: 'manifest.json',
    outDir: resolve(import.meta.dirname, 'src/nomos/site/static/dist'),
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(frontend, 'js/main.js'),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
  },
});
