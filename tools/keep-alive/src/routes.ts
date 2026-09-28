import { Router } from 'express';
import { z } from 'zod';
import type { Keeper } from './keeper.js';
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
    const parsed = z.object({ url: z.string().url(), label: z.string().default('') }).safeParse(req.body);
    if (!parsed.success) { res.status(400).json({ error: 'enter a full URL, starting with https://' }); return; }
    keeper.add(parsed.data.url, parsed.data.label);
    res.json(keepAliveOf());
  });

  router.delete('/keeper/instances/:id', (req, res) => {
    keeper.remove(req.params.id);
    res.json(keepAliveOf());
  });

  // Start = keep it awake from now, and look at it immediately (which presses the
  // server's Start if it is down). The response waits for that first look.
  router.post('/keeper/instances/:id/start', async (req, res) => {
    const hours = typeof req.body?.hours === 'number' ? req.body.hours : undefined;
    const view = await keeper.keep(req.params.id, hours);
    if (!view) { res.status(404).json({ error: 'no such instance' }); return; }
    res.json({ instance: view, ...keepAliveOf() });
  });

  router.post('/keeper/instances/:id/stop', (req, res) => {
    const view = keeper.release(req.params.id);
    if (!view) { res.status(404).json({ error: 'no such instance' }); return; }
    res.json({ instance: view, ...keepAliveOf() });
  });

  router.post('/keeper/instances/:id/check', async (req, res) => {
    const view = await keeper.ping(req.params.id);
    if (!view) { res.status(404).json({ error: 'no such instance' }); return; }
    res.json({ instance: view, ...keepAliveOf() });
  });

  return router;
}
