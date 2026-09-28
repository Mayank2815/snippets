import { z } from 'zod';

/**
 * A dev or QA instance that should be kept awake while someone is working on it.
 * DevOps stop any instance idle for 20 minutes; this pings it well inside that.
 */
export const KeptInstanceSchema = z.object({
  /** Slug of the host, e.g. "client-24-qa-expertly-cloud". */
  id: z.string().min(1),
  url: z.string().url(),
  label: z.string().default(''),
  /** Keep it awake until this instant; null means it is not being kept. */
  keepUntil: z.string().nullable().default(null),
  /** Server-management app name (e.g. QA24C), learned the first time it is seen offline. */
  app: z.string().nullable().default(null),
  /** Start it again from the dashboard if it goes down while being kept. */
  autoStart: z.boolean().default(true),
});

export type KeptInstance = z.infer<typeof KeptInstanceSchema>;

/** Everything the service persists: the kept list and the three settings the dashboard exposes. */
export const KeepAliveSchema = z.object({
  instances: z.array(KeptInstanceSchema).default([]),
  /**
   * Minutes between pings. Five leaves room for three failed pings before the
   * twenty-minute idle stop would fire.
   */
  pingMinutes: z.number().int().min(1).max(15).default(5),
  /**
   * How long one press of Start keeps an instance awake. A forgotten toggle would
   * otherwise keep a server up all night, which is exactly what the idle stop is for.
   */
  hours: z.number().min(0.5).max(24).default(8),
  /**
   * Path fetched on each ping so the request reaches the instance itself. The page URL
   * is served from CloudFront's cache, so on its own it never counts as activity — an
   * instance kept that way still stopped after twenty minutes on 23 September. This
   * path is one the app answers itself, fetched with a cache-buster.
   */
  activityPath: z.string().default('/rest/api/users/isMySessionActive'),
});

export type KeepAlive = z.infer<typeof KeepAliveSchema>;
