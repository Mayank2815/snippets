import type { KeptInstance } from '../config/schema.js';
import { getConfig, setConfig } from '../config/store.js';

/**
 * Keeps dev and QA instances awake while someone is working on them.
 *
 * DevOps stop any instance that has seen no traffic for twenty minutes, and it takes
 * five to ten minutes to come back. In the middle of a fix that is exactly long enough
 * to lose the instance between writing a change and checking it. So while an instance is
 * "kept", this pings it every few minutes — and if it goes down anyway, presses the same
 * Start button the maintenance page shows, so nobody has to.
 *
 * Kept only for a fixed number of hours from each press of Start: the idle stop exists
 * to save money overnight, and a forgotten toggle should not defeat it.
 */

export type InstanceState = 'running' | 'starting' | 'stopped' | 'unreachable' | 'unknown';

export interface Probe {
  state: InstanceState;
  detail: string;
  /** What the activity request got back from the instance itself, e.g. "HTTP 200 (Miss)". */
  origin?: string;
  /** Server-management app name, known only from the maintenance page. */
  app: string | null;
  /** The management API the maintenance page talks to; differs per environment. */
  apiBase: string | null;
}

export interface Runtime {
  lastPingAt: string | null;
  state: InstanceState | null;
  detail: string;
  lastStartAt: string | null;
  lastStartResult: string | null;
  apiBase: string | null;
}

export interface InstanceView extends KeptInstance, Runtime {
  keeping: boolean;
  nextPingAt: string | null;
}

export type FetchLike = (url: string, init?: RequestInit) => Promise<Response>;

/** One check a minute: the ping interval is in minutes, so this is precise enough. */
const TICK_MS = 60_000;
/** The management API has its own cooldown; asking more often than this only earns a "wait". */
const START_COOLDOWN_MS = 10 * 60_000;
/**
 * After Start is pressed the instance is looked at every minute until it is up, whatever
 * the ping interval. 23 September: with a ten-minute interval the dashboard sat on
 * "stopped — start requested" for the whole boot, and only moved when someone pressed
 * Check now. A boot takes five to ten minutes; fifteen covers a slow one.
 */
const BOOT_POLL_MS = 60_000;
const BOOT_WINDOW_MS = 15 * 60_000;
/** A page that takes longer than this is not "up" in any useful sense. */
const FETCH_TIMEOUT_MS = 20_000;

/** Traffic from here should be recognisable in a log, not mistaken for a browser. */
const USER_AGENT = 'task-notif keep-alive';

/**
 * CloudFront serves this wrapper in place of a stopped instance: a page embedding the
 * environment's maintenance page, whose file name is the app name.
 */
const MAINTENANCE_IFRAME = /src="(https?:\/\/[^"]*\/maintenance\/([A-Za-z0-9_-]+)\.html[^"]*)"/;
const API_BASE_LINE = /const\s+API_BASE\s*=\s*['"]([^'"]+)['"]/;

export function slugOf(url: string): string {
  return new URL(url).hostname.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

/** The app name and maintenance page a stopped instance's wrapper points at, if this is one. */
export function parseMaintenance(html: string): { app: string; pageUrl: string } | null {
  const m = MAINTENANCE_IFRAME.exec(html);
  return m ? { app: m[2]!, pageUrl: m[1]!.replace(/&amp;/g, '&') } : null;
}

export function parseApiBase(maintenanceHtml: string): string | null {
  return API_BASE_LINE.exec(maintenanceHtml)?.[1] ?? null;
}

function withTimeout(init: RequestInit = {}): RequestInit {
  return { ...init, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS), headers: { 'user-agent': USER_AGENT, ...(init.headers ?? {}) } };
}

/**
 * A request the instance itself must answer. The page is a CloudFront cache hit, so a
 * ping made of the page alone never reaches the server and it idles out regardless;
 * this goes to the app with a cache-buster, and reports whether the edge passed it on.
 */
export async function touchOrigin(origin: string, path: string, fetchFn: FetchLike = globalThis.fetch, now: Date = new Date()): Promise<string> {
  const sep = path.includes('?') ? '&' : '?';
  try {
    const res = await fetchFn(`${origin}${path}${sep}keepalive=${now.getTime()}`, withTimeout({ headers: { 'cache-control': 'no-cache' } }));
    await res.text().catch(() => '');
    const edge = res.headers.get('x-cache') ?? '';
    // "Hit" means the edge answered from cache and the server saw nothing.
    return `HTTP ${res.status}${edge ? ` (${/hit/i.test(edge) ? 'cached — did not reach the server' : 'reached the server'})` : ''}`;
  } catch (err) {
    return `failed: ${(err as Error).message}`;
  }
}

/** What the instance is doing right now, as the maintenance page would work it out. */
export async function probeInstance(url: string, fetchFn: FetchLike = globalThis.fetch, activityPath?: string): Promise<Probe> {
  const origin = new URL(url).origin;
  let res: Response;
  try {
    res = await fetchFn(url, withTimeout({ redirect: 'follow' }));
  } catch (err) {
    return { state: 'unreachable', detail: (err as Error).message, app: null, apiBase: null };
  }
  const body = await res.text().catch(() => '');
  const maintenance = parseMaintenance(body);

  if (!maintenance && res.ok) {
    const touched = activityPath ? await touchOrigin(origin, activityPath, fetchFn) : undefined;
    return { state: 'running', detail: touched ? `up · activity ping ${touched}` : `HTTP ${res.status}`, origin: touched, app: null, apiBase: null };
  }
  if (!maintenance && res.status !== 503) {
    return { state: 'unknown', detail: `HTTP ${res.status}`, app: null, apiBase: null };
  }

  // Either the maintenance wrapper or a bare 503: ask the status endpoint what is going on.
  let app = maintenance?.app ?? null;
  let state: InstanceState = 'stopped';
  let detail = maintenance ? 'maintenance page shown' : 'HTTP 503';
  try {
    const kvs = await fetchFn(`${origin}/_kvs_status${app ? `?app=${app}` : ''}`, withTimeout());
    const data = (await kvs.json()) as { app?: string; kvs_status?: string };
    app = app ?? data.app ?? null;
    if (data.kvs_status === 'running') { state = 'running'; detail = 'status says running'; }
    else if (data.kvs_status === 'starting') { state = 'starting'; detail = 'starting — usually 5 to 10 minutes'; }
    else { state = 'stopped'; detail = `status: ${data.kvs_status ?? 'stopped'}`; }
  } catch {
    // The wrapper alone is enough to know it is down; the precise status is a bonus.
  }

  let apiBase: string | null = null;
  if (maintenance) {
    try {
      apiBase = parseApiBase(await (await fetchFn(maintenance.pageUrl, withTimeout())).text());
    } catch {
      // Without it there is nothing to press Start on; the view says so.
    }
  }
  return { state, detail, app, apiBase };
}

/** Presses Start. Returns a one-line result for the dashboard. */
export async function startInstance(apiBase: string, app: string, fetchFn: FetchLike = globalThis.fetch): Promise<string> {
  const res = await fetchFn(`${apiBase}/start?app=${encodeURIComponent(app)}`, withTimeout({ method: 'POST' }));
  const data = (await res.json().catch(() => ({}))) as { status?: string; message?: string; cooldown_remaining_minutes?: number };
  switch (data.status) {
    case 'starting':
    case 'already_starting': return 'start requested — usually 5 to 10 minutes';
    case 'already_running': return 'already running';
    case 'cooldown': return `management API asks to wait ${data.cooldown_remaining_minutes ?? '?'} min before starting again`;
    default: return `start failed: ${data.message ?? `HTTP ${res.status}`}`;
  }
}

export class Keeper {
  private timer: NodeJS.Timeout | null = null;
  private readonly runtime = new Map<string, Runtime>();
  private inFlight = new Set<string>();

  constructor(
    private readonly fetchFn: FetchLike = globalThis.fetch,
    private readonly log: (m: string) => void = (m) => console.log(`[keep-alive] ${new Date().toLocaleTimeString('en-GB')} ${m}`),
  ) {}

  start(): void {
    if (this.timer) return;
    void this.tick();
    this.timer = setInterval(() => void this.tick(), TICK_MS);
    this.timer.unref?.();
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** Pings every kept instance whose interval has elapsed. */
  async tick(now: Date = new Date()): Promise<void> {
    const { instances, pingMinutes } = getConfig().keepAlive;
    for (const inst of instances) {
      if (!isKept(inst, now)) continue;
      const rt = this.runtime.get(inst.id);
      const interval = rt && this.booting(rt, now) ? BOOT_POLL_MS : pingMinutes * 60_000;
      if (rt?.lastPingAt && now.getTime() - new Date(rt.lastPingAt).getTime() < interval) continue;
      await this.ping(inst.id, now).catch((err: Error) => this.log(`${inst.label || inst.id}: ping failed — ${err.message}`));
    }
  }

  /** One ping: probe, remember what was seen, and press Start if it is down and allowed. */
  async ping(id: string, now: Date = new Date()): Promise<InstanceView | null> {
    const inst = getConfig().keepAlive.instances.find((i) => i.id === id);
    if (!inst || this.inFlight.has(id)) return inst ? this.view(inst, now) : null;
    this.inFlight.add(id);
    try {
      const probe = await probeInstance(inst.url, this.fetchFn, getConfig().keepAlive.activityPath);
      const prev = this.runtime.get(id);
      const rt: Runtime = {
        lastPingAt: now.toISOString(),
        state: probe.state,
        detail: probe.detail,
        lastStartAt: prev?.lastStartAt ?? null,
        lastStartResult: prev?.lastStartResult ?? null,
        apiBase: probe.apiBase ?? prev?.apiBase ?? null,
      };
      if (probe.app && probe.app !== inst.app) this.patchInstance(id, { app: probe.app });
      const app = probe.app ?? inst.app;

      const cooled = !rt.lastStartAt || now.getTime() - new Date(rt.lastStartAt).getTime() >= START_COOLDOWN_MS;
      if (probe.state === 'stopped' && inst.autoStart && isKept(inst, now) && cooled) {
        if (rt.apiBase && app) {
          rt.lastStartAt = now.toISOString();
          rt.lastStartResult = await startInstance(rt.apiBase, app, this.fetchFn).catch((e: Error) => `start failed: ${e.message}`);
          this.log(`${inst.label || inst.id}: down — ${rt.lastStartResult}`);
          if (/start requested|already/.test(rt.lastStartResult)) {
            rt.state = 'starting';
            rt.detail = 'start requested — checked every minute until it is up';
          }
        } else {
          rt.lastStartResult = 'down, but the management API for it could not be found';
        }
      }
      this.runtime.set(id, rt);
      if (prev?.state !== rt.state) this.log(`${inst.label || inst.id}: ${rt.state} (${rt.detail})`);
      return this.view({ ...inst, app }, now);
    } finally {
      this.inFlight.delete(id);
    }
  }

  add(url: string, label = ''): InstanceView[] {
    const clean = new URL(url).toString();
    const id = slugOf(clean);
    const { instances } = getConfig().keepAlive;
    if (!instances.some((i) => i.id === id)) {
      this.save([...instances, { id, url: clean, label, keepUntil: null, app: null, autoStart: true }]);
    }
    return this.views();
  }

  remove(id: string): InstanceView[] {
    this.save(getConfig().keepAlive.instances.filter((i) => i.id !== id));
    this.runtime.delete(id);
    return this.views();
  }

  /** Start keeping it awake for the configured hours, and look at it straight away. */
  async keep(id: string, hours = getConfig().keepAlive.hours, now: Date = new Date()): Promise<InstanceView | null> {
    if (!this.patchInstance(id, { keepUntil: new Date(now.getTime() + hours * 3_600_000).toISOString() })) return null;
    this.runtime.delete(id); // a fresh press means "check now", whatever the last ping said
    return this.ping(id, now);
  }

  release(id: string): InstanceView | null {
    if (!this.patchInstance(id, { keepUntil: null })) return null;
    const inst = getConfig().keepAlive.instances.find((i) => i.id === id)!;
    return this.view(inst);
  }

  views(now: Date = new Date()): InstanceView[] {
    return getConfig().keepAlive.instances.map((i) => this.view(i, now));
  }

  private view(inst: KeptInstance, now: Date = new Date()): InstanceView {
    const rt = this.runtime.get(inst.id) ?? { lastPingAt: null, state: null, detail: '', lastStartAt: null, lastStartResult: null, apiBase: null };
    const keeping = isKept(inst, now);
    const interval = this.booting(rt, now) ? BOOT_POLL_MS : getConfig().keepAlive.pingMinutes * 60_000;
    const nextPingAt = keeping && rt.lastPingAt
      ? new Date(new Date(rt.lastPingAt).getTime() + interval).toISOString()
      : keeping ? now.toISOString() : null;
    return { ...inst, ...rt, keeping, nextPingAt };
  }

  /** A start was asked for recently and the instance is not up yet. */
  private booting(rt: Runtime, now: Date): boolean {
    return rt.state !== 'running' && Boolean(rt.lastStartAt)
      && now.getTime() - new Date(rt.lastStartAt!).getTime() < BOOT_WINDOW_MS;
  }

  private patchInstance(id: string, patch: Partial<KeptInstance>): boolean {
    const { instances } = getConfig().keepAlive;
    if (!instances.some((i) => i.id === id)) return false;
    this.save(instances.map((i) => (i.id === id ? { ...i, ...patch } : i)));
    return true;
  }

  private save(instances: KeptInstance[]): void {
    setConfig({ keepAlive: { ...getConfig().keepAlive, instances } });
  }
}

export function isKept(inst: KeptInstance, now: Date = new Date()): boolean {
  return Boolean(inst.keepUntil) && new Date(inst.keepUntil!).getTime() > now.getTime();
}
