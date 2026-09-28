import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtempSync } from 'node:fs';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import express from 'express';

// The store resolves its directory once, at import time, so this has to be set first.
process.env.DATA_DIR = mkdtempSync(join(tmpdir(), 'keep-alive-routes-'));
const { Keeper } = await import('../keeper.js');
const { buildRouter } = await import('../routes.js');

/**
 * The HTTP surface the dashboard talks to, mounted the way server.ts mounts it. The
 * instance behind it is a fake that is always up, so these pin the routes, not the probe.
 */

const URL_A = 'https://client-9.qa.example.cloud/automation-designer';
const ID_A = 'client-9-qa-example-cloud';

const alwaysUp = async () => new Response('<html>app</html>', { status: 200 });
const app = express();
app.use(express.json());
app.use('/api', buildRouter({ keeper: new Keeper(alwaysUp, () => {}) }));
const server = app.listen(0);
await new Promise<void>((resolve) => server.once('listening', resolve));
const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
test.after(() => server.close());

async function api(path: string, init?: RequestInit): Promise<{ status: number; body: Record<string, any> }> {
  const res = await fetch(`${base}/api${path}`, { ...init, headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) } });
  return { status: res.status, body: (await res.json()) as Record<string, any> };
}

test('GET /api/keeper starts empty, with the settings the schema defaults to', async () => {
  const { status, body } = await api('/keeper');
  assert.equal(status, 200);
  assert.deepEqual(body, { instances: [], pingMinutes: 5, hours: 8, activityPath: '/rest/api/users/isMySessionActive' });
});

test('POST /api/keeper/instances adds by URL; a bad URL is a 400 and not a crash', async () => {
  assert.equal((await api('/keeper/instances', { method: 'POST', body: JSON.stringify({ url: 'not a url' }) })).status, 400);
  const { status, body } = await api('/keeper/instances', { method: 'POST', body: JSON.stringify({ url: URL_A, label: 'QA9C' }) });
  assert.equal(status, 200);
  assert.equal(body.instances.length, 1);
  assert.equal(body.instances[0].id, ID_A);
  assert.equal(body.instances[0].label, 'QA9C');
  assert.equal(body.instances[0].keeping, false, 'adding does not start keeping');
});

test('start, check and stop each answer with the instance and the whole list', async () => {
  const started = await api(`/keeper/instances/${ID_A}/start`, { method: 'POST', body: '{}' });
  assert.equal(started.status, 200);
  assert.equal(started.body.instance.keeping, true);
  assert.equal(started.body.instance.state, 'running', 'the first look happens before the response');
  assert.equal(started.body.instances.length, 1);

  const checked = await api(`/keeper/instances/${ID_A}/check`, { method: 'POST' });
  assert.equal(checked.status, 200);
  assert.equal(checked.body.instance.state, 'running');

  const stopped = await api(`/keeper/instances/${ID_A}/stop`, { method: 'POST' });
  assert.equal(stopped.status, 200);
  assert.equal(stopped.body.instance.keeping, false);
});

test('an unknown id is a 404 on every per-instance route', async () => {
  for (const action of ['start', 'stop', 'check']) {
    assert.equal((await api(`/keeper/instances/nope/${action}`, { method: 'POST', body: '{}' })).status, 404, action);
  }
});

test('PUT /api/keeper changes only the settings it is given, and refuses bad ones', async () => {
  const ok = await api('/keeper', { method: 'PUT', body: JSON.stringify({ pingMinutes: 3 }) });
  assert.equal(ok.status, 200);
  assert.equal(ok.body.pingMinutes, 3);
  assert.equal(ok.body.hours, 8, 'untouched settings keep their value');
  assert.equal(ok.body.instances.length, 1, 'the list is not part of the settings and is left alone');

  assert.equal((await api('/keeper', { method: 'PUT', body: JSON.stringify({ pingMinutes: 99 }) })).status, 400);
  assert.equal((await api('/keeper', { method: 'PUT', body: JSON.stringify({ activityPath: 'no-leading-slash' }) })).status, 400);
});

test('DELETE /api/keeper/instances/:id takes it off the list', async () => {
  const { status, body } = await api(`/keeper/instances/${ID_A}`, { method: 'DELETE' });
  assert.equal(status, 200);
  assert.deepEqual(body.instances, []);
});
