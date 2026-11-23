import assert from 'node:assert/strict';
import test from 'node:test';
import { DateTime } from 'luxon';
import { ConfigSchema, type RunRecord } from '../config/schema.js';
import { recipientsOwedForSlot, recipientsSettledForSlot } from '../scheduler/index.js';

const IST = 'Asia/Kolkata';
let nextUserId = 1;
const recip = (id: string, enabled = true) => ({ id, label: id, teamworkUserId: nextUserId++, handles: [`@${id}`], enabled });
// A 21:00 digest with four recipients — the real shape on 2026-11-23 when one of four
// (Rohit) kept failing with invalid_blocks and the other three were re-spammed.
const config = ConfigSchema.parse({
  timezone: IST,
  jobs: { digest: { time: '21:00', daysOfWeek: [1, 2, 3, 4, 5] } },
  recipients: [recip('mayank'), recip('priya'), recip('shriyam'), recip('rohit')],
  catchUpGraceMinutes: 300,
});
const at = (iso: string) => DateTime.fromISO(iso, { zone: IST });
const slot = at('2026-11-23T21:00'); // a Monday

/** A run, defaulting to the digest slot at 21:00:30, listing per-recipient outcomes. */
const run = (
  perRecipient: { id: string; delivered: boolean; error: string | null }[],
  overrides: Partial<RunRecord> = {},
): RunRecord => ({
  at: '2026-11-23T21:00:30.000+05:30',
  job: 'digest',
  trigger: 'scheduled',
  ok: perRecipient.every((p) => p.error === null),
  detail: '',
  perRecipient: perRecipient.map((p) => ({ matched: 1, ...p })),
  ...overrides,
});

const owedAt = (now: string, runs: RunRecord[]) =>
  recipientsOwedForSlot(config, 'digest', at(now), runs).sort();

// ---- recipientsSettledForSlot: who a run has finished with for the slot -----------------

test('a delivered recipient is settled; a failed one is not', () => {
  const settled = recipientsSettledForSlot('digest', slot, [run([
    { id: 'mayank', delivered: true, error: null },
    { id: 'rohit', delivered: false, error: 'invalid_blocks' },
  ])]);
  assert.ok(settled.has('mayank'));
  assert.ok(!settled.has('rohit'));
});

test('an intentional "nothing to send" also settles (error is null, delivered false)', () => {
  const settled = recipientsSettledForSlot('digest', slot, [run([{ id: 'priya', delivered: false, error: null }])]);
  assert.ok(settled.has('priya'));
});

test('a run before the slot does not settle anyone', () => {
  const yesterday = run([{ id: 'mayank', delivered: true, error: null }], { at: '2026-11-22T21:00:30.000+05:30' });
  assert.equal(recipientsSettledForSlot('digest', slot, [yesterday]).size, 0);
});

test('a run for another job does not settle this one', () => {
  const reminder = run([{ id: 'mayank', delivered: true, error: null }], { job: 'reminder' });
  assert.equal(recipientsSettledForSlot('digest', slot, [reminder]).size, 0);
});

test('a scan-failure run (no per-recipient rows) settles nobody', () => {
  const scanFail = run([], { ok: false, detail: 'scan failed' });
  assert.equal(recipientsSettledForSlot('digest', slot, [scanFail]).size, 0);
});

// ---- recipientsOwedForSlot: the 2026-11-23 amplification regression ---------------------

test('with no run yet, every enabled recipient is owed (the on-time send goes to all)', () => {
  assert.deepEqual(owedAt('2026-11-23T21:00', []), ['mayank', 'priya', 'rohit', 'shriyam']);
});

test('one recipient failing re-sends the slot to THAT person only, never to the others', () => {
  // The exact bug: 3 delivered, Rohit failed. Catch-up must owe only Rohit.
  const partial = run([
    { id: 'mayank', delivered: true, error: null },
    { id: 'priya', delivered: true, error: null },
    { id: 'shriyam', delivered: true, error: null },
    { id: 'rohit', delivered: false, error: 'slack chat.postMessage failed: invalid_blocks' },
  ]);
  assert.deepEqual(owedAt('2026-11-23T21:15', [partial]), ['rohit']);
});

test('once the last failing recipient succeeds on a later run, nothing is owed', () => {
  const partial = run([
    { id: 'mayank', delivered: true, error: null },
    { id: 'priya', delivered: true, error: null },
    { id: 'shriyam', delivered: true, error: null },
    { id: 'rohit', delivered: false, error: 'invalid_blocks' },
  ]);
  // The catch-up that targeted only Rohit, now succeeding (e.g. after the digest fix).
  const rohitFixed = run([{ id: 'rohit', delivered: true, error: null }], { at: '2026-11-23T21:15:10.000+05:30' });
  assert.deepEqual(owedAt('2026-11-23T21:30', [partial, rohitFixed]), []);
});

test('nobody is owed outside the grace window, even with failures on record', () => {
  const partial = run([{ id: 'rohit', delivered: false, error: 'invalid_blocks' }]);
  assert.deepEqual(owedAt('2026-11-24T03:00', [partial]), []); // long past 21:00 + 5h grace
});

test('a disabled recipient is never owed', () => {
  const withDisabled = ConfigSchema.parse({
    ...config,
    recipients: [recip('mayank'), recip('rohit', false)],
  });
  const owed = recipientsOwedForSlot(withDisabled, 'digest', at('2026-11-23T21:15'), []);
  assert.deepEqual(owed.sort(), ['mayank']);
});
