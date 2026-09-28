import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// The store resolves its directory once, at import time, so this has to be set first.
process.env.DATA_DIR = mkdtempSync(join(tmpdir(), 'keeper-'));
const { getKeepAlive, setKeepAlive } = await import('../store.js');
const { Keeper, parseMaintenance, parseApiBase, probeInstance, slugOf, isKept, touchOrigin } = await import('../keeper.js');

/**
 * 23 September: dev and QA instances stop after twenty idle minutes, which is shorter
 * than one fix-and-verify loop. These pin how an instance is recognised as down, how it
 * is kept awake, and that it is started again without anyone pressing anything.
 */

const ORIGIN = 'https://client-9.qa.example.cloud';
const PAGE = `${ORIGIN}/automation-designer`;
const WRAPPER = `<html><body><iframe src="https://dev-server-management.s3.us-east-2.amazonaws.com/maintenance/QA9C.html?origin_host=client-9.qa.example.cloud"></iframe></body></html>`;
const MAINTENANCE = `<script>const APP_NAME = 'QA9C';\n        const API_BASE = 'https://server-management.qa.example.cloud';</script>`;

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const html = (body: string, status = 200) => new Response(body, { status, headers: { 'content-type': 'text/html' } });

/** A fake of the instance, the status endpoint and the management API, with a log of every call. */
function world(state: 'running' | 'stopped' | 'starting') {
  const calls: string[] = [];
  const fetchFn = async (url: string, init?: RequestInit): Promise<Response> => {
    calls.push(`${init?.method ?? 'GET'} ${url}`);
    if (url.startsWith(PAGE) || url === `${ORIGIN}/`) {
      return state === 'running' ? new Response('<html>app</html>', { status: 200, headers: { 'x-cache': 'Hit from cloudfront' } }) : html(WRAPPER);
    }
    if (url.startsWith(`${ORIGIN}/rest/api/users/isMySessionActive`)) return new Response('', { status: 401, headers: { 'x-cache': 'Error from cloudfront' } });
    if (url.startsWith(`${ORIGIN}/_kvs_status`)) return json({ app: 'QA9C', kvs_status: state });
    if (url.includes('/maintenance/QA9C.html')) return html(MAINTENANCE);
    if (url.startsWith('https://server-management.qa.example.cloud/start')) { state = 'starting'; return json({ status: 'starting' }); }
    return html('not found', 404);
  };
  return { calls, fetchFn, get state() { return state; }, up() { state = 'running'; } };
}

const reset = () => setKeepAlive({ instances: [], pingMinutes: 5, hours: 8, activityPath: '/rest/api/users/isMySessionActive' });

test('the maintenance wrapper gives away the app name, and the page its management API', () => {
  assert.deepEqual(parseMaintenance(WRAPPER), {
    app: 'QA9C',
    pageUrl: 'https://dev-server-management.s3.us-east-2.amazonaws.com/maintenance/QA9C.html?origin_host=client-9.qa.example.cloud',
  });
  assert.equal(parseMaintenance('<html>app</html>'), null);
  assert.equal(parseApiBase(MAINTENANCE), 'https://server-management.qa.example.cloud');
});

test('an API base that is not http(s) is ignored, since Start would POST to it', () => {
  // The base is scraped out of a page fetched from a URL anyone with the dashboard can add,
  // so it is the one place a non-web scheme could be smuggled into a request the keeper makes.
  for (const base of ['file:///etc/passwd', 'ftp://server-management.qa.example.cloud', 'gopher://x', 'not a url']) {
    assert.equal(parseApiBase(`<script>const API_BASE = '${base}';</script>`), null, base);
  }
  assert.equal(parseApiBase(`<script>const API_BASE = 'http://server-management.qa.example.cloud';</script>`), 'http://server-management.qa.example.cloud');
});

test('the keeper itself refuses to add anything but http(s)', () => {
  reset();
  const k = new Keeper(world('running').fetchFn, () => {});
  assert.throws(() => k.add('file:///etc/passwd'), /non-http\(s\)/);
  assert.equal(k.views().length, 0);
});

test('a running instance is reported running, and nothing is started', async () => {
  const w = world('running');
  const p = await probeInstance(PAGE, w.fetchFn);
  assert.equal(p.state, 'running');
  assert.ok(!w.calls.some((c) => c.includes('/start')));
});

test('each ping also touches the server itself, since the page is a CloudFront cache hit', async () => {
  // 23 September: an instance pinged by its page alone still stopped after twenty minutes —
  // every one of those pings was answered by the edge cache and the server saw nothing.
  const w = world('running');
  const p = await probeInstance(PAGE, w.fetchFn, '/rest/api/users/isMySessionActive');
  assert.equal(p.state, 'running');
  assert.match(p.origin!, /HTTP 401 \(reached the server\)/);
  const touch = w.calls.find((c) => c.includes('/rest/api/users/isMySessionActive'))!;
  assert.match(touch, /\?keepalive=\d+$/, 'cache-busted, so the edge cannot answer it from cache');
});

test('a ping answered from the edge cache is reported as not reaching the server', async () => {
  const cached = async () => new Response('', { status: 200, headers: { 'x-cache': 'Hit from cloudfront' } });
  assert.match(await touchOrigin(ORIGIN, '/rest/api/x', cached), /did not reach the server/);
  const down = async () => { throw new Error('ECONNRESET'); };
  assert.match(await touchOrigin(ORIGIN, '/rest/api/x', down), /failed: ECONNRESET/);
});

test('a stopped instance is recognised through the wrapper, with its app and API', async () => {
  const w = world('stopped');
  const p = await probeInstance(PAGE, w.fetchFn);
  assert.equal(p.state, 'stopped');
  assert.equal(p.app, 'QA9C');
  assert.equal(p.apiBase, 'https://server-management.qa.example.cloud');
});

test('an instance that cannot be reached at all says so rather than pretending it is down', async () => {
  const p = await probeInstance(PAGE, async () => { throw new Error('ENOTFOUND'); });
  assert.equal(p.state, 'unreachable');
  assert.match(p.detail, /ENOTFOUND/);
});

test('ids come from the host, so the same instance is never added twice', () => {
  reset();
  assert.equal(slugOf(PAGE), 'client-9-qa-example-cloud');
  const k = new Keeper(world('running').fetchFn, () => {});
  k.add(PAGE, 'QA9C');
  k.add(`${ORIGIN}/other-page`);
  assert.equal(k.views().length, 1);
  assert.equal(k.views()[0]!.keeping, false, 'adding does not start keeping');
});

test('Start keeps it for the configured hours and pings it straight away', async () => {
  reset();
  const w = world('running');
  const k = new Keeper(w.fetchFn, () => {});
  k.add(PAGE);
  const now = new Date('2026-09-23T10:00:00Z');
  const v = (await k.keep('client-9-qa-example-cloud', undefined, now))!;
  assert.equal(v.keeping, true);
  assert.equal(v.keepUntil, '2026-09-23T18:00:00.000Z');
  assert.equal(v.state, 'running');
  assert.equal(v.lastPingAt, now.toISOString());
  assert.equal(v.nextPingAt, '2026-09-23T10:05:00.000Z');
  assert.ok(w.calls.some((c) => c === `GET ${PAGE}`), 'the page itself is fetched, so the instance sees traffic');
});

test('pings happen every interval and not in between', async () => {
  reset();
  const w = world('running');
  const k = new Keeper(w.fetchFn, () => {});
  k.add(PAGE);
  const t0 = new Date('2026-09-23T10:00:00Z');
  await k.keep('client-9-qa-example-cloud', undefined, t0);
  const pages = () => w.calls.filter((c) => c === `GET ${PAGE}`).length;
  assert.equal(pages(), 1);
  await k.tick(new Date(t0.getTime() + 3 * 60_000));
  assert.equal(pages(), 1, 'three minutes in: too soon');
  await k.tick(new Date(t0.getTime() + 5 * 60_000));
  assert.equal(pages(), 2, 'five minutes in: pinged');
  await k.tick(new Date(t0.getTime() + 6 * 60_000));
  assert.equal(pages(), 2);
});

test('a kept instance that went down is started again, and not nagged while it starts', async () => {
  reset();
  const w = world('stopped');
  const k = new Keeper(w.fetchFn, () => {});
  k.add(PAGE, 'QA9C');
  const t0 = new Date('2026-09-23T10:00:00Z');
  const v = (await k.keep('client-9-qa-example-cloud', undefined, t0))!;
  assert.equal(v.app, 'QA9C');
  assert.match(v.lastStartResult!, /start requested/);
  assert.equal(w.calls.filter((c) => c.includes('/start?app=QA9C')).length, 1);
  assert.equal(getKeepAlive().instances[0]!.app, 'QA9C', 'the app name is remembered');

  assert.equal(v.state, 'starting', 'shown as starting the moment Start is pressed, not "stopped"');
  assert.equal(v.nextPingAt, '2026-09-23T10:01:00.000Z', 'looked at again in a minute, not after the full interval');

  // While it boots it is checked every minute, and Start is not pressed again.
  const pages = () => w.calls.filter((c) => c === `GET ${PAGE}`).length;
  await k.tick(new Date(t0.getTime() + 1 * 60_000));
  assert.equal(pages(), 2);
  await k.tick(new Date(t0.getTime() + 2 * 60_000));
  assert.equal(pages(), 3);
  assert.equal(w.calls.filter((c) => c.includes('/start')).length, 1);
  assert.equal(k.views()[0]!.state, 'starting');
});

test('once the started instance is up, the dashboard shows it without anyone pressing Check', async () => {
  reset();
  const w = world('stopped');
  const k = new Keeper(w.fetchFn, () => {});
  k.add(PAGE, 'QA9C');
  const t0 = new Date('2026-09-23T10:00:00Z');
  await k.keep('client-9-qa-example-cloud', undefined, t0);
  w.up(); // the server finished booting
  await k.tick(new Date(t0.getTime() + 1 * 60_000));
  assert.equal(k.views(new Date(t0.getTime() + 1 * 60_000))[0]!.state, 'running');
  // Back to the normal interval: not pinged again a minute later.
  const pages = () => w.calls.filter((c) => c === `GET ${PAGE}`).length;
  const n = pages();
  await k.tick(new Date(t0.getTime() + 2 * 60_000));
  assert.equal(pages(), n);
  await k.tick(new Date(t0.getTime() + 6 * 60_000));
  assert.equal(pages(), n + 1);
});

test('Stop ends the keeping, after which nothing is pinged', async () => {
  reset();
  const w = world('running');
  const k = new Keeper(w.fetchFn, () => {});
  k.add(PAGE);
  const t0 = new Date('2026-09-23T10:00:00Z');
  await k.keep('client-9-qa-example-cloud', undefined, t0);
  const before = w.calls.length;
  assert.equal(k.release('client-9-qa-example-cloud')!.keeping, false);
  await k.tick(new Date(t0.getTime() + 10 * 60_000));
  assert.equal(w.calls.length, before);
});

test('keeping runs out on its own, so a forgotten Start does not defeat the idle stop', async () => {
  reset();
  const w = world('running');
  const k = new Keeper(w.fetchFn, () => {});
  k.add(PAGE);
  const t0 = new Date('2026-09-23T10:00:00Z');
  await k.keep('client-9-qa-example-cloud', 1, t0);
  const inst = getKeepAlive().instances[0]!;
  assert.equal(isKept(inst, new Date(t0.getTime() + 59 * 60_000)), true);
  assert.equal(isKept(inst, new Date(t0.getTime() + 61 * 60_000)), false);
  const before = w.calls.length;
  await k.tick(new Date(t0.getTime() + 61 * 60_000));
  assert.equal(w.calls.length, before, 'expired: not pinged');
  assert.equal(k.views(new Date(t0.getTime() + 61 * 60_000))[0]!.keeping, false);
});

test('the kept list survives a restart of the agent', async () => {
  reset();
  const k = new Keeper(world('running').fetchFn, () => {});
  k.add(PAGE, 'QA9C');
  await k.keep('client-9-qa-example-cloud', undefined, new Date('2026-09-23T10:00:00Z'));
  const fresh = new Keeper(world('running').fetchFn, () => {});
  const v = fresh.views(new Date('2026-09-23T10:30:00Z'))[0]!;
  assert.equal(v.keeping, true);
  assert.equal(v.label, 'QA9C');
});
