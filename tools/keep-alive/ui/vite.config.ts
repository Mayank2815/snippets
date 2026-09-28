import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  // Relative asset URLs, so the built page also works when a reverse proxy serves it
  // under a path prefix such as /keep-alive/. Every fetch in the panel is relative too.
  base: './',
  server: {
    // 5311 next to the API's 4311, the same way task-notif pairs 5310 with 4310.
    port: 5311,
    proxy: {
      '/api': 'http://localhost:4311',
    },
  },
});
