import { useCallback, useEffect, useState, type FocusEvent, type KeyboardEvent } from 'react';
import type { KeepAliveSettings, KeptInstanceView } from './types.js';

/**
 * Relative, not "/api/...": the page may be served under a path prefix by a reverse
 * proxy (e.g. /keep-alive/), and a leading slash would escape it. Vite's base is './'
 * for the same reason.
 */
async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`api/keeper${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) } });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((body as { error?: string }).error ?? `HTTP ${res.status}`);
  return body as T;
}

/** The dashboard re-reads every so often so a "starting" instance is seen to come up. */
const REFRESH_MS = 15_000;

const DOT: Record<string, string> = { running: 'ok', starting: 'warn', stopped: 'bad', unreachable: 'bad', unknown: 'warn' };

function clock(iso: string | null): string {
  return iso ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '—';
}

/**
 * Keep a dev or QA instance awake while you work on it. DevOps stop an instance idle
 * for twenty minutes; each Start here pings it every few minutes for a few hours, and
 * presses the server's own Start button if it goes down anyway.
 */
export function KeepAlivePanel() {
  const [data, setData] = useState<KeepAliveSettings | null>(null);
  const [url, setUrl] = useState('');
  const [label, setLabel] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try { setData(await call<KeepAliveSettings>('')); }
    catch (err) { setError((err as Error).message); }
  }, []);

  useEffect(() => {
    void refresh();
    const t = setInterval(() => void refresh(), REFRESH_MS);
    return () => clearInterval(t);
  }, [refresh]);

  async function act(key: string, fn: () => Promise<unknown>) {
    setBusy(key); setError(null);
    try { await fn(); await refresh(); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(null); }
  }

  const add = () => act('add', async () => {
    await call('/instances', { method: 'POST', body: JSON.stringify({ url: url.trim(), label: label.trim() }) });
    setUrl(''); setLabel('');
  });
  const settings = (p: Partial<KeepAliveSettings>) =>
    act('settings', () => call('', { method: 'PUT', body: JSON.stringify(p) }));

  /**
   * The number fields save on blur (or Enter), like the activity path does — not on every
   * keystroke, where typing "12" first saved 1 and clearing the field saved 0 and showed
   * the server's validation error. An emptied field is put back to the saved value.
   */
  const numberField = (key: 'pingMinutes' | 'hours', current: number) => ({
    key: `${key}-${current}`, // remount when a refresh brings a new saved value, so the field shows it
    defaultValue: current,
    onKeyDown: (e: KeyboardEvent<HTMLInputElement>) => { if (e.key === 'Enter') e.currentTarget.blur(); },
    onBlur: (e: FocusEvent<HTMLInputElement>) => {
      const next = Number(e.target.value);
      if (e.target.value.trim() === '' || Number.isNaN(next)) { e.target.value = String(current); return; }
      if (next !== current) void settings({ [key]: next });
    },
  });

  return (
    <section className="card">
      <h2>Keep instances awake</h2>
      <p className="muted" style={{ marginTop: -8 }}>
        Dev and QA servers stop after 20 idle minutes. Press Start on the one you are working on: it is pinged every
        {' '}{data?.pingMinutes ?? 5} minutes for {data?.hours ?? 8} hours, and started again if it goes down.
      </p>

      <div className="grid" style={{ alignItems: 'end' }}>
        <label className="field" style={{ gridColumn: 'span 2' }}>Instance URL
          <input value={url} placeholder="https://client-24.qa.expertly.cloud/automation-designer"
            onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && url.trim()) void add(); }} />
        </label>
        <label className="field">Label (optional)
          <input value={label} placeholder="QA24C" onChange={(e) => setLabel(e.target.value)} />
        </label>
        <div className="field">
          <button className="secondary" disabled={busy !== null || !url.trim()} onClick={() => void add()}>+ Add instance</button>
        </div>
      </div>

      {data && data.instances.length === 0 && <p className="muted">No instances yet — add the URL of the dev or QA server you are working on.</p>}

      {data?.instances.map((i) => (
        <div className="instance" key={i.id} style={{ marginTop: 12, borderColor: i.keeping ? 'var(--accent)' : undefined }}>
          <div className="head">
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
              <span className={`dot ${i.state ? DOT[i.state] : ''}`} style={{ background: i.state ? undefined : 'var(--border)' }} />
              <strong>{i.label || i.app || i.id}</strong>
              <a href={i.url} target="_blank" rel="noreferrer" className="muted" style={{ fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{i.url}</a>
            </div>
            <div className="actions">
              {i.keeping
                ? <button onClick={() => void act(i.id, () => call(`/instances/${i.id}/stop`, { method: 'POST' }))} disabled={busy !== null}>■ Stop</button>
                : <button onClick={() => void act(i.id, () => call(`/instances/${i.id}/start`, { method: 'POST' }))} disabled={busy !== null}>▶ Start</button>}
              <button className="secondary" onClick={() => void act(i.id, () => call(`/instances/${i.id}/check`, { method: 'POST' }))} disabled={busy !== null}>Check now</button>
              <button className="secondary" onClick={() => void act(i.id, () => call(`/instances/${i.id}`, { method: 'DELETE' }))} disabled={busy !== null}>Remove</button>
            </div>
          </div>
          <div className="muted" style={{ fontSize: 12.5 }}>
            {busy === i.id ? 'Checking…' : (
              <>
                <span className="pill">{i.state ?? 'not checked'}</span>{' '}
                {i.detail}
                {i.keeping
                  ? <> · kept until {clock(i.keepUntil)} · last ping {clock(i.lastPingAt)} · next {clock(i.nextPingAt)}</>
                  : <> · not being kept{i.keepUntil ? ` (ended ${clock(i.keepUntil)})` : ''}</>}
                {i.lastStartResult && <> · <em>{i.lastStartResult}</em> at {clock(i.lastStartAt)}</>}
              </>
            )}
          </div>
        </div>
      ))}

      {data && (
        <div className="grid" style={{ marginTop: 16 }}>
          <label className="field">Ping every (minutes)
            <input type="number" min={1} max={15} {...numberField('pingMinutes', data.pingMinutes)} />
          </label>
          <label className="field">Keep for (hours per Start)
            <input type="number" min={0.5} max={24} step={0.5} {...numberField('hours', data.hours)} />
          </label>
          <label className="field" style={{ gridColumn: 'span 2' }}>Activity path (the page itself is cached by CloudFront, so each ping also fetches this from the server)
            <input value={data.activityPath} placeholder="/rest/api/users/isMySessionActive"
              onBlur={(e) => { if (e.target.value !== data.activityPath) void settings({ activityPath: e.target.value }); }}
              onChange={(e) => setData({ ...data, activityPath: e.target.value })} />
          </label>
        </div>
      )}

      {error && <p className="err" style={{ marginTop: 12 }}>{error}</p>}
    </section>
  );
}
