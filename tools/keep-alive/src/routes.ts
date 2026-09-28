import { Router } from 'express';
import { z } from 'zod';
import { isHttpUrl, type Keeper } from './keeper.js';
import { KeepAliveSchema } from './schema.js';
import { getKeepAlive, setKeepAlive } from './store.js';

export interface RouterDeps {
  /**
   * The server owns the keeper's lifecycle — started once the port is bound, stopped on
   * SIGINT/SIGTERM — so the router only borrows it. Tests pass one with a fake fetch.
   */
  keeper: Keeper;
}

/** The three settings the dashboard may change; bounds come from the schema so they are stated once. */
const SettingsPatch = z.object({
  pingMinutes: KeepAliveSchema.shape.pingMinutes.optional(),
  hours: KeepAliveSchema.shape.hours.optional(),
  activityPath: z.string().regex(/^\//, 'must start with /').optional(),
});

/**
 * Start's body. `hours` goes through the schema's bounds so 1e999 (Infinity once
 * JSON-parsed) or 100000 is a 400. Unchecked, `new Date(now + Infinity).toISOString()`
 * throws inside an async handler, which Express 4 does not catch, and the process dies.
 */
const StartBody = z.object({ hours: KeepAliveSchema.shape.hours.optional() });

const AddBody = z.object({
  url: z.string().url().refine(isHttpUrl, 'only http(s) URLs can be kept'),
  label: z.string().default(''),
});

const NOT_FOUND = { error: 'no such instance' };

/** Mounted at /api by the server, so these are /api/keeper and /api/keeper/instances/... */
export function buildRouter({ keeper }: RouterDeps): Router {
  const router = Router();

  const keepAliveOf = () => {
    const { pingMinutes, hours, activityPath } = getKeepAlive();
    return { instances: keeper.views(), pingMinutes, hours, activityPath };
  };

  router.get('/keeper', (_req, res) => res.json(keepAliveOf()));

  router.put('/keeper', (req, res) => {
    const parsed = SettingsPatch.safeParse(req.body);
    if (!parsed.success) { res.status(400).json({ error: 'invalid settings', issues: parsed.error.issues }); return; }
    setKeepAlive(parsed.data);
    res.json(keepAliveOf());
  });

  router.post('/keeper/instances', (req, res) => {
    const parsed = AddBody.safeParse(req.body);
    if (!parsed.success) { res.status(400).json({ error: 'enter a full URL, starting with https://' }); return; }
    keeper.add(parsed.data.url, parsed.data.label);
    res.json(keepAliveOf());
  });

  router.delete('/keeper/instances/:id', (req, res) => {
    if (!keeper.remove(req.params.id)) { res.status(404).json(NOT_FOUND); return; }
    res.json(keepAliveOf());
  });

  // Start = keep it awake from now, and look at it immediately (which presses the
  // server's Start if it is down). The response waits for that first look.
  router.post('/keeper/instances/:id/start', async (req, res) => {
    const parsed = StartBody.safeParse(req.body ?? {});
    if (!parsed.success) { res.status(400).json({ error: 'invalid hours', issues: parsed.error.issues }); return; }
    const view = await keeper.keep(req.params.id, parsed.data.hours);
    if (!view) { res.status(404).json(NOT_FOUND); return; }
    res.json({ instance: view, ...keepAliveOf() });
  });

  router.post('/keeper/instances/:id/stop', (req, res) => {
    const view = keeper.release(req.params.id);
    if (!view) { res.status(404).json(NOT_FOUND); return; }
    res.json({ instance: view, ...keepAliveOf() });
  });

  router.post('/keeper/instances/:id/check', async (req, res) => {
    const view = await keeper.ping(req.params.id);
    if (!view) { res.status(404).json(NOT_FOUND); return; }
    res.json({ instance: view, ...keepAliveOf() });
  });

  // Anything else under /api is a JSON 404. The router is mounted at /api ahead of the
  // server's SPA fallback, which would otherwise answer GET /api/typo with index.html
  // and a 200 — which a script would take for success.
  router.use((_req, res) => res.status(404).json({ error: 'not found' }));

  return router;
}
