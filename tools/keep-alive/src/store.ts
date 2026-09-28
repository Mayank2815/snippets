import { existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { KeepAliveSchema, type KeepAlive } from './schema.js';

const dataDir = resolve(process.env.DATA_DIR ?? './data');
const filePath = join(dataDir, 'keep-alive.json');

function read(): KeepAlive {
  if (!existsSync(filePath)) return KeepAliveSchema.parse({});
  try {
    return KeepAliveSchema.parse(JSON.parse(readFileSync(filePath, 'utf8')));
  } catch (err) {
    // A corrupt store must not brick the service — fall back to defaults, leave the bad file in place.
    console.error(`[store] unreadable, using defaults: ${(err as Error).message}`);
    return KeepAliveSchema.parse({});
  }
}

/** Write to a temp file then rename, so a crash mid-write cannot truncate the store. */
function write(data: KeepAlive): void {
  mkdirSync(dirname(filePath), { recursive: true });
  const tmp = `${filePath}.tmp`;
  writeFileSync(tmp, JSON.stringify(data, null, 2), 'utf8');
  renameSync(tmp, filePath);
}

/**
 * Until 28 September 2026 the kept list lived inside task-notif's store.json, under
 * config.keepAlive. On a start where the list here is empty, if LEGACY_TASK_NOTIF_STORE
 * names that file, the block is copied in so nobody has to re-add every instance by hand.
 * Once copied the list is no longer empty, so it is a one-off: later starts skip it, and
 * the env var can be dropped. task-notif's copy is never modified — only read.
 */
function importLegacy(current: KeepAlive): KeepAlive {
  const legacyPath = process.env.LEGACY_TASK_NOTIF_STORE?.trim();
  if (!legacyPath || current.instances.length > 0) return current;

  let raw: unknown;
  try {
    raw = JSON.parse(readFileSync(legacyPath, 'utf8'));
  } catch (err) {
    console.warn(`[store] LEGACY_TASK_NOTIF_STORE (${legacyPath}) could not be read: ${(err as Error).message}`);
    return current;
  }
  // task-notif kept it at config.keepAlive; a bare keepAlive at the top level is accepted too.
  const obj = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const block = (obj.config as Record<string, unknown> | undefined)?.keepAlive ?? obj.keepAlive;
  const parsed = KeepAliveSchema.safeParse(block);
  if (!parsed.success) {
    console.warn(`[store] ${legacyPath} has no usable keepAlive block; nothing imported`);
    return current;
  }
  if (parsed.data.instances.length === 0) {
    console.log(`[store] ${legacyPath} has no kept instances; nothing to import`);
    return current;
  }
  write(parsed.data);
  console.log(`[store] imported ${parsed.data.instances.length} instance(s) and settings from ${legacyPath} into ${filePath}`);
  return parsed.data;
}

let cache: KeepAlive | null = null;

function load(): KeepAlive {
  if (!cache) cache = importLegacy(read());
  return cache;
}

export function getKeepAlive(): KeepAlive {
  return load();
}

export function setKeepAlive(patch: Partial<KeepAlive>): KeepAlive {
  // An explicit undefined would shadow the stored value and let the schema default win,
  // silently resetting a setting the caller never meant to touch.
  const defined = Object.fromEntries(Object.entries(patch).filter(([, v]) => v !== undefined));
  const next = KeepAliveSchema.parse({ ...load(), ...defined });
  cache = next;
  write(next);
  return next;
}

/**
 * A mounted volume often arrives owned by root, overriding whatever the Dockerfile
 * chowned. That turns into a silent crash-loop on the first write, so check up front
 * and say exactly what is wrong.
 */
export function assertDataDirWritable(): void {
  try {
    mkdirSync(dataDir, { recursive: true });
    const probe = join(dataDir, '.write-probe');
    writeFileSync(probe, 'ok');
    rmSync(probe);
  } catch (err) {
    throw new Error(
      `DATA_DIR (${dataDir}) is not writable: ${(err as Error).message}\n` +
      `The process runs as uid ${typeof process.getuid === 'function' ? process.getuid() : 'unknown'}. ` +
      `If this is a mounted volume, its owner overrides the image's — chown it to that uid.`,
    );
  }
}

export const storePath = filePath;
