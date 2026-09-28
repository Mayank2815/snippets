import 'dotenv/config';
import express from 'express';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { basicAuth } from './auth.js';
import { Keeper } from './keeper.js';
import { buildRouter } from './routes.js';
import { assertDataDirWritable, storePath } from './store.js';

const here = dirname(fileURLToPath(import.meta.url));
// WHY 4311: task-notif, which this was carved out of, listens on 4310 on the same VM,
// so 4311 is the next free port after it. Hosts that inject PORT override it.
const port = Number(process.env.PORT ?? 4311);

try {
  assertDataDirWritable();
} catch (err) {
  console.error(`[server] ${(err as Error).message}`);
  process.exit(1);
}

const app = express();
app.use(express.json());

// Declared before auth: a platform health check sends no credentials, and a 401
// would make the host consider the service dead and restart it forever.
app.get('/healthz', (_req, res) => {
  res.json({ ok: true, uptimeSeconds: Math.round(process.uptime()) });
});

const dashboardPassword = process.env.DASHBOARD_PASSWORD ?? '';
if (dashboardPassword) {
  app.use(basicAuth(dashboardPassword));
  console.log('[server] dashboard is password protected');
}

const keeper = new Keeper();
app.use('/api', buildRouter({ keeper }));

// Built by `npm run build`; absent under `npm run dev`, where Vite serves the UI on 5311 instead.
const uiDist = resolve(here, '../ui/dist');
if (existsSync(uiDist)) {
  app.use(express.static(uiDist));
  app.get('*', (_req, res) => res.sendFile(join(uiDist, 'index.html')));
}

const server = app.listen(port, () => {
  console.log(`[server] listening on http://localhost:${port}`);
  console.log(`[server] store: ${storePath}`);
  if (!dashboardPassword) {
    console.warn(
      '[server] DASHBOARD_PASSWORD is not set — fine behind loopback, ' +
      'but set it on any host where the port is publicly reachable',
    );
  }
  // Started only once the port is bound, so a clash exits cleanly before anything is pinged.
  keeper.start();
});

// Without this, a stale process holding the port makes a supervisor crash-loop in silence.
server.on('error', (err: NodeJS.ErrnoException) => {
  if (err.code === 'EADDRINUSE') {
    console.error(
      `[server] port ${port} is already in use — another copy is running.\n` +
      `[server] find it with:  lsof -nP -iTCP:${port} -sTCP:LISTEN`,
    );
  } else {
    console.error(`[server] failed to start: ${err.message}`);
  }
  process.exit(1);
});

for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.on(signal, () => {
    keeper.stop();
    process.exit(0);
  });
}
