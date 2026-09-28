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
 * config.keepAlive. If LEGACY_TASK_NOTIF_STORE names a copy of that file and the import
 * has never run here (`importedAt` is null), the instances are copied in so nobody has to
 * re-add them by hand. Only the instances: the three settings stay whatever this service
 * already has, so an operator's tuning is not overwritten. `importedAt` is then recorded,
 * and that is what makes it a one-off — the guard used to be "the list is empty", which
 * refilled a list the user had deliberately emptied. The legacy file is only read.
 *
 * The file must be a snapshot taken before the new task-notif started: task-notif's
 * schema strips the keepAlive key and every one of its writes persists the stripped
 * object, so the live store.json loses the block within hours. deploy/deploy.sh takes
 * that snapshot before the stack restarts.
 */
function importLegacy(current: KeepAlive): KeepAlive {
  const legacyPath = process.env.LEGACY_TASK_NOTIF_STORE?.trim();
  if (!legacyPath || current.importedAt !== null) return current;

  let raw: unknown;
  try {
    raw = JSON.parse(readFileSync(legacyPath, 'utf8'));
  } catch (err) {
    // A missing file is the normal case on a host that never ran task-notif (the deploy
    // sets the variable unconditionally); anything else deserves a warning.
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') console.log(`[store] no legacy store at ${legacyPath}; nothing to import`);
    else console.warn(`[store] LEGACY_TASK_NOTIF_STORE (${legacyPath}) could not be read: ${(err as Error).message}`);
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
  // Current rows win on an id clash: a store that predates `importedAt` may already hold them.
  const known = new Set(current.instances.map((i) => i.id));
  const added = parsed.data.instances.filter((i) => !known.has(i.id));
  const next: KeepAlive = { ...current, instances: [...current.instances, ...added], importedAt: new Date().toISOString() };
  write(next);
  console.log(`[store] imported ${added.length} instance(s) from ${legacyPath} into ${filePath}; settings left as they were`);
  return next;
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
