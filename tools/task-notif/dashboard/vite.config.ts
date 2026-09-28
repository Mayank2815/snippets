import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  // Relative asset URLs, so the built page also works under a path prefix such as the
  // workbench's /task-notif/ without any change to the server.
  base: './',
  server: {
    port: 5310,
    proxy: { '/api': 'http://localhost:4310' },
  },
});
