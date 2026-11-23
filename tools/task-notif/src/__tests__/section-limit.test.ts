import assert from 'node:assert/strict';
import test from 'node:test';
import { renderDigest } from '../slack/digest-message.js';
import { renderReminder, summarySections } from '../slack/message.js';

const SECTION_MAX = 3000;
const texts = (blocks: unknown[]) =>
  (blocks as { type: string; text?: { text?: string } }[])
    .filter((b) => b.type === 'section')
    .map((b) => b.text?.text ?? '');

/** Built the way the factual summary is: bullet lines, each listing linked tasks. */
const link = (i: number) => `<https://projects.example.com/app/tasks/${27400000 + i}|A task name long enough to look real ${i}> (Ready for QA)`;
const line = (label: string, from: number) => `• *${label}* — ${[0, 1, 2, 3].map((k) => link(from + k)).join('; ')} +9 more`;
/** About the 3,720 characters Friday's weekly carried in one block. */
const longSummary = Array.from({ length: 9 }, (_, i) => line(`Worked on ${14 - i} tasks`, i * 10)).join('\n');

test('the fixture is really over the limit', () => {
  assert.ok(longSummary.length > SECTION_MAX, `fixture is ${longSummary.length} chars`);
});

test('a summary over the limit becomes several sections, each inside it', () => {
  const sections = summarySections(longSummary, '*🗒️ The week in short*');
  assert.ok(sections.length > 1);
  for (const t of texts(sections)) assert.ok(t.length <= SECTION_MAX, `a section is ${t.length} chars`);
});

test('nothing is lost in the split', () => {
  const joined = texts(summarySections(longSummary, '*🗒️ The week in short*')).join('\n');
  assert.equal(joined, `*🗒️ The week in short*\n${longSummary}`);
});

test('it splits only between lines, so no link is cut through', () => {
  for (const t of texts(summarySections(longSummary))) {
    assert.ok(t.startsWith('• '), 'every section starts at the beginning of a line');
    assert.equal((t.match(/</g) ?? []).length, (t.match(/>/g) ?? []).length, 'every link that opens also closes');
  }
});

test('a short summary stays one section with its heading, as before', () => {
  const sections = summarySections('• *Worked on 1 task* — x', '*🗒️ For tomorrow\'s stand-up*');
  assert.equal(sections.length, 1);
  assert.equal(texts(sections)[0], "*🗒️ For tomorrow's stand-up*\n• *Worked on 1 task* — x");
});

test('one line too long for any section is cut between two items', () => {
  const huge = `• *Talked in* — ${Array.from({ length: 40 }, (_, i) => link(i)).join('; ')}`;
  const [t] = texts(summarySections(huge));
  assert.ok(t!.length <= SECTION_MAX);
  assert.ok(t!.endsWith(')…'), 'the cut falls after a whole item');
});

const digest = (summary: string) => ({
  recipient: { id: 'alice', label: 'Alice' },
  identity: { displayName: 'Alice' },
  dayLabel: 'Monday, 7 September – Friday, 11 September',
  updates: Array.from({ length: 15 }, (_, i) => ({
    taskId: i, taskName: `Task ${i}`, taskLink: `https://tw/app/tasks/${i}`, link: `https://tw/app/tasks/${i}?c=1`,
    project: 'Web', text: 'x'.repeat(240), at: '2026-09-10T10:00:00Z', isDone: false, isBlocker: false, prLinks: [],
  })),
  completed: Array.from({ length: 6 }, (_, i) => ({ taskId: i, taskName: `Done ${i}`, project: 'Web', stage: null, link: `https://tw/${i}`, at: '' })),
  statusChanges: [], newlyAssigned: [], mentionsOpen: [], mentionsAnswered: [],
  slackReplied: [], slackAwaiting: [], slackActivity: [], meetings: [],
  dayOffset: 0, total: 21, summary,
}) as never;

test("the weekly that failed on 11 September now fits every section inside Slack's limit", () => {
  const msg = renderDigest(digest(longSummary), 'Asia/Kolkata', undefined, 'week');
  for (const t of texts(msg.blocks)) assert.ok(t.length <= SECTION_MAX, `a section is ${t.length} chars`);
  assert.ok(msg.blocks.length <= 50);
});

test('a week in review says "this week", not "today"', () => {
  const s = JSON.stringify(renderDigest(digest('• x'), 'Asia/Kolkata', undefined, 'week').blocks);
  assert.match(s, /Completed this week/);
  assert.ok(!s.includes('Completed today'));
});

test('the evening digest still says "today"', () => {
  assert.match(JSON.stringify(renderDigest(digest('• x'), 'Asia/Kolkata', undefined, 'day').blocks), /Completed today/);
});

test("a long Monday stand-up in the morning reminder fits too", () => {
  const msg = renderReminder({
    recipient: { id: 'alice', label: 'Alice' }, identity: { displayName: 'Alice' }, total: 0, groups: [],
  } as never, 'Asia/Kolkata', [], undefined, longSummary, 'Friday, 11 September – Sunday, 13 September', 3, true);
  for (const t of texts(msg.blocks)) assert.ok(t.length <= SECTION_MAX, `a section is ${t.length} chars`);
  assert.ok(msg.blocks.length <= 50);
});

// On 2026-11-23 one recipient's evening digest returned invalid_blocks on every run: a
// single Teamwork comment ran past Slack's 3,000-char per-section limit. The per-item
// update/mention rows were the only sections whose comment text was never length-capped.
const giantComment = 'word '.repeat(1000); // ~5,000 chars — well past the limit on its own
const digestWithGiantComment = () => ({
  recipient: { id: 'rohit', label: 'Rohit' },
  identity: { displayName: 'Rohit' },
  dayLabel: 'Monday, 23 November',
  updates: [{
    taskId: 1, taskName: 'A real task', taskLink: 'https://tw/app/tasks/1', link: 'https://tw/app/tasks/1?c=1',
    project: 'Web', text: giantComment, at: '2026-11-23T10:00:00Z', isDone: false, isBlocker: false, prLinks: [],
  }],
  completed: [], statusChanges: [], newlyAssigned: [],
  mentionsOpen: [{ taskId: 2, taskName: 'Asked task', link: 'https://tw/app/tasks/2?c=1', text: giantComment, author: 'Someone', at: '2026-11-23T11:00:00Z' }],
  mentionsAnswered: [], slackReplied: [], slackAwaiting: [], slackActivity: [], meetings: [],
  dayOffset: 0, total: 2, summary: null,
}) as never;

test('the fixture comment is really over the per-section limit', () => {
  assert.ok(giantComment.length > SECTION_MAX, `fixture is ${giantComment.length} chars`);
});

test('a single oversized comment can no longer breach a section (the 2026-11-23 invalid_blocks bug)', () => {
  const msg = renderDigest(digestWithGiantComment(), 'Asia/Kolkata', undefined, 'day');
  for (const t of texts(msg.blocks)) assert.ok(t.length <= SECTION_MAX, `a section is ${t.length} chars`);
  // The whole text is paginated across messages; every continuation must also stay inside.
  for (const part of msg.continuation ?? []) {
    for (const t of texts(part.blocks)) assert.ok(t.length <= SECTION_MAX, `a continuation section is ${t.length} chars`);
  }
});

test('the oversized comment is shown as a preview, not dropped or sent whole', () => {
  const [row] = texts(renderDigest(digestWithGiantComment(), 'Asia/Kolkata', undefined, 'day').blocks)
    .filter((t) => t.includes('A real task'));
  assert.ok(row!.includes('word word'), 'the start of the comment is kept');
  assert.ok(row!.trimEnd().endsWith('…'), 'a trimmed comment ends in an ellipsis');
});
