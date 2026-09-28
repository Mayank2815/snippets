import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

/**
 * The import must not run twice. The guard used to be "the list is empty", which meant
 * removing every instance and restarting with LEGACY_TASK_NOTIF_STORE still set (as the
 * deploy stack leaves it) brought back the rows the user had just deleted. Now it is
 * `importedAt`. Own process, like every store test: the store is loaded once per process.
 */
const dir = mkdtempSync(join(tmpdir(), 'keep-alive-import-guard-'));
const legacyPath = join(dir, 'legacy-store.json');
writeFileSync(legacyPath, JSON.stringify({
  config: {
    keepAlive: {
      instances: [{ id: 'client-9-qa-example-cloud', url: 'https://client-9.qa.example.cloud/', label: 'QA9C', keepUntil: null, app: 'QA9C', autoStart: true }],
    },
  },
}));
const dataDir = join(dir, 'data');
mkdirSync(dataDir, { recursive: true });
const IMPORTED_AT = '2026-09-28T09:00:00.000Z';
writeFileSync(join(dataDir, 'keep-alive.json'), JSON.stringify({
  instances: [],
  pingMinutes: 5,
  hours: 8,
  activityPath: '/rest/api/users/isMySessionActive',
  importedAt: IMPORTED_AT,
}));
process.env.DATA_DIR = dataDir;
process.env.LEGACY_TASK_NOTIF_STORE = legacyPath;
const { getKeepAlive, storePath } = await import('../store.js');

test('an emptied list stays empty on restart once the import has run', () => {
  const data = getKeepAlive();
  assert.deepEqual(data.instances, [], 'the deleted instance is not resurrected');
  assert.equal(data.importedAt, IMPORTED_AT, 'the original import time is kept');
  const onDisk = JSON.parse(readFileSync(storePath, 'utf8'));
  assert.deepEqual(onDisk.instances, [], 'nothing was written back either');
});
