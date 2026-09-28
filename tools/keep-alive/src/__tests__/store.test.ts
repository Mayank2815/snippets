import assert from 'node:assert/strict';
import test from 'node:test';
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

/**
 * The one-off import from task-notif, and the write path. Both environment variables
 * are read at import time, so they are set before the store is loaded.
 */
const dir = mkdtempSync(join(tmpdir(), 'keep-alive-store-'));
const legacyPath = join(dir, 'legacy-store.json');
writeFileSync(legacyPath, JSON.stringify({
  config: {
    recipients: [],
    keepAlive: {
      instances: [{ id: 'client-9-qa-example-cloud', url: 'https://client-9.qa.example.cloud/', label: 'QA9C', keepUntil: null, app: 'QA9C', autoStart: true }],
      pingMinutes: 7,
      hours: 4,
      activityPath: '/custom',
    },
  },
  dismissals: [],
  runs: [],
}));
process.env.DATA_DIR = join(dir, 'data');
process.env.LEGACY_TASK_NOTIF_STORE = legacyPath;
const { getKeepAlive, setKeepAlive, storePath } = await import('../store.js');

test('a first start with an empty list copies the kept list and settings out of task-notif', () => {
  const data = getKeepAlive();
  assert.equal(data.instances.length, 1);
  assert.equal(data.instances[0]!.label, 'QA9C');
  assert.equal(data.pingMinutes, 7);
  assert.equal(data.hours, 4);
  assert.equal(data.activityPath, '/custom');
  assert.ok(existsSync(storePath), 'the import is written, so the next start finds a non-empty list and skips it');
  assert.equal(storePath, join(dir, 'data', 'keep-alive.json'));
});

test('task-notif\'s store is only read, never written', () => {
  const after = JSON.parse(readFileSync(legacyPath, 'utf8'));
  assert.equal(after.config.keepAlive.instances.length, 1);
  assert.deepEqual(Object.keys(after), ['config', 'dismissals', 'runs']);
});

test('writes go through a temp file that is renamed away, and round-trip', () => {
  setKeepAlive({ hours: 2, pingMinutes: undefined });
  const onDisk = JSON.parse(readFileSync(storePath, 'utf8'));
  assert.equal(onDisk.hours, 2);
  assert.equal(onDisk.pingMinutes, 7, 'an explicit undefined does not reset a setting to its default');
  assert.deepEqual(Object.keys(onDisk).sort(), ['activityPath', 'hours', 'instances', 'pingMinutes']);
  assert.ok(!existsSync(`${storePath}.tmp`), 'no temp file is left behind');
});
