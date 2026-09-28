import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

/**
 * The import brings the instances across and nothing else. It used to write the whole
 * legacy block, which overwrote pingMinutes/hours/activityPath an operator had already
 * tuned here. A store that predates `importedAt` may also hold rows already, so those are
 * kept and the legacy ones are added beside them. Own process, like every store test.
 */
const dir = mkdtempSync(join(tmpdir(), 'keep-alive-import-merge-'));
const legacyPath = join(dir, 'legacy-store.json');
writeFileSync(legacyPath, JSON.stringify({
  config: {
    keepAlive: {
      instances: [
        { id: 'client-9-qa-example-cloud', url: 'https://client-9.qa.example.cloud/', label: 'from task-notif', keepUntil: null, app: 'QA9C', autoStart: true },
        { id: 'client-24-qa-example-cloud', url: 'https://client-24.qa.example.cloud/', label: 'QA24C', keepUntil: null, app: null, autoStart: true },
      ],
      pingMinutes: 12,
      hours: 1,
      activityPath: '/legacy-path',
    },
  },
}));
const dataDir = join(dir, 'data');
mkdirSync(dataDir, { recursive: true });
// A store written before importedAt existed: tuned settings, one row, no importedAt key.
writeFileSync(join(dataDir, 'keep-alive.json'), JSON.stringify({
  instances: [{ id: 'client-9-qa-example-cloud', url: 'https://client-9.qa.example.cloud/', label: 'tuned here', keepUntil: null, app: 'QA9C', autoStart: false }],
  pingMinutes: 3,
  hours: 2,
  activityPath: '/mine',
}));
process.env.DATA_DIR = dataDir;
process.env.LEGACY_TASK_NOTIF_STORE = legacyPath;
const { getKeepAlive } = await import('../store.js');

test('the import adds the legacy instances and leaves the tuned settings alone', () => {
  const data = getKeepAlive();
  assert.equal(data.pingMinutes, 3);
  assert.equal(data.hours, 2);
  assert.equal(data.activityPath, '/mine');
  assert.equal(data.instances.length, 2);
  assert.equal(data.instances[0]!.label, 'tuned here', 'the row already here wins over the legacy copy of the same id');
  assert.equal(data.instances[0]!.autoStart, false);
  assert.equal(data.instances[1]!.id, 'client-24-qa-example-cloud', 'the row only task-notif had is added');
  assert.ok(data.importedAt, 'and the import is recorded');
});
