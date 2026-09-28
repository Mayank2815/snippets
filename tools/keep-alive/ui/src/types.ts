/** One dev/QA instance on the keep-awake list, with what the last ping saw. */
export interface KeptInstanceView {
  id: string;
  url: string;
  label: string;
  keepUntil: string | null;
  app: string | null;
  autoStart: boolean;
  keeping: boolean;
  lastPingAt: string | null;
  nextPingAt: string | null;
  state: 'running' | 'starting' | 'stopped' | 'unreachable' | 'unknown' | null;
  detail: string;
  lastStartAt: string | null;
  lastStartResult: string | null;
}

export interface KeepAliveSettings {
  instances: KeptInstanceView[];
  pingMinutes: number;
  hours: number;
  /** App path fetched on each ping so the request reaches the server, not CloudFront's cache. */
  activityPath: string;
}
