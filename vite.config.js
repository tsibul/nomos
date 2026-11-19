import { defineConfig } from 'vite';
import { resolve } from 'path';

const frontend = resolve(__dirname, 'src/nomos/site/frontend');

export default defineConfig({
  root: frontend,
  base: '/static/',
  build: {
    manifest: 'manifest.json',
    outDir: resolve(__dirname, 'src/nomos/site/static/dist'),
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
