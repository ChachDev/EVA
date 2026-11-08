import assert from 'node:assert/strict';
import test from 'node:test';
import { currentScoring, hasOpenAppeal, latestEvaluations, summarizeWorkspace } from '../src/workspace-analytics.ts';

const now = new Date('2026-11-08T12:00:00Z');
const score = (total, status = total === null ? 'needs_review' : 'complete', flag = false) => ({ status, total, has_gross_error: flag, applicable_weight: 100, sections: [], criteria: [] });
const evaluation = (id, conversation, created, total, status, flag) => ({ id, created_at: created, status: total === null ? 'needs_review' : 'completed', original: { conversation_id: conversation }, scoring: score(total, status, flag) });
const review = (item, final, appeals = []) => ({ evaluation_id: item.id, original: item, final: { scoring: final, criteria: [] }, appeals });

test('latest evaluations deduplicate conversations independently of server ordering', () => {
  const earlier = evaluation('e-1', 'a', '2026-11-01T10:00:00Z', 20);
  const later = evaluation('e-2', 'a', '2026-11-07T10:00:00Z', 80);
  const other = evaluation('e-3', 'b', '2026-11-06T10:00:00Z', 60);
  assert.deepEqual(latestEvaluations([earlier, later, other]).map(item => item.id), ['e-2', 'e-3']);
  assert.deepEqual(latestEvaluations([other, later, earlier]).map(item => item.id), ['e-2', 'e-3']);
  assert.equal(summarizeWorkspace([earlier, later, other], {}).average, 70);
});

test('reviewed current scores replace original scores, including unresolved null totals', () => {
  const first = evaluation('e-1', 'a', '2026-11-07T10:00:00Z', 40);
  const second = evaluation('e-2', 'b', '2026-11-07T10:00:00Z', 100);
  const reviews = { 'e-1': review(first, score(80)), 'e-2': review(second, score(null)) };
  const result = summarizeWorkspace([first, second], reviews);
  assert.equal(result.average, 80);
  assert.equal(result.scored, 1);
  assert.equal(result.unresolved, 1);
  assert.equal(currentScoring(second, reviews).total, null);
});

test('a missing review falls back to original, but a mismatched review is ignored', () => {
  const item = evaluation('e-1', 'a', '2026-11-07T10:00:00Z', 65);
  assert.equal(currentScoring(item, {}).total, 65);
  assert.equal(currentScoring(item, { 'e-1': { ...review(item, score(99)), evaluation_id: 'other' } }).total, 65);
});

test('zero is a valid score; uncertain and not-calculable totals never inflate or reduce the mean', () => {
  const items = [
    evaluation('e-1', 'a', '2026-11-07T10:00:00Z', 0),
    evaluation('e-2', 'b', '2026-11-07T10:00:00Z', 100),
    evaluation('e-3', 'c', '2026-11-07T10:00:00Z', null),
    evaluation('e-4', 'd', '2026-11-07T10:00:00Z', null, 'not_calculable'),
    evaluation('e-5', 'e', '2026-11-07T10:00:00Z', 50, 'needs_review'),
  ];
  const result = summarizeWorkspace(items, {});
  assert.equal(result.average, 50);
  assert.equal(result.scored, 2);
  assert.equal(result.selected.length, 5);
  assert.equal(result.series[0].average, 50);
  assert.equal(result.series[0].count, 2);
});

test('gross errors count only confirmed flags while unknown flags remain separate', () => {
  const items = [
    evaluation('e-1', 'a', '2026-11-07T10:00:00Z', 85, 'complete', true),
    evaluation('e-2', 'b', '2026-11-07T10:00:00Z', null, 'needs_review', null),
    evaluation('e-3', 'c', '2026-11-07T10:00:00Z', 100, 'complete', false),
  ];
  const result = summarizeWorkspace(items, {});
  assert.equal(result.grossErrors, 1);
  assert.equal(result.unknownFlags, 1);
});

test('7-day period includes the UTC boundary and excludes old and future evaluations', () => {
  const items = [
    evaluation('old', 'a', '2026-11-01T23:59:59Z', 0),
    evaluation('boundary', 'b', '2026-11-02T00:00:00Z', 80),
    evaluation('today', 'c', now.toISOString(), 100),
    evaluation('future', 'd', '2026-11-08T12:00:01Z', 1),
  ];
  const result = summarizeWorkspace(items, {}, '7d', now);
  assert.deepEqual(result.selected.map(item => item.id), ['today', 'boundary']);
  assert.equal(result.average, 90);
});

test('30-day period uses the current date rather than anchoring itself to old data', () => {
  const items = [evaluation('old', 'a', '2025-11-01T00:00:00Z', 100)];
  assert.equal(summarizeWorkspace(items, {}, '30d', now).average, null);
  assert.equal(summarizeWorkspace(items, {}, 'all', now).average, 100);
});

test('daily series groups real UTC dates and never manufactures missing-day scores', () => {
  const items = [
    evaluation('e-1', 'a', '2026-11-07T01:00:00+05:00', 60),
    evaluation('e-2', 'b', '2026-11-06T23:00:00Z', 100),
    evaluation('e-3', 'c', '2026-11-08T01:00:00Z', 90),
  ];
  assert.deepEqual(summarizeWorkspace(items, {}).series, [
    { day: '2026-11-06', average: 80, count: 2 },
    { day: '2026-11-08', average: 90, count: 1 },
  ]);
});

test('appeals remain a current queue across periods and historical evaluation versions', () => {
  const old = evaluation('old', 'a', '2025-11-01T00:00:00Z', 60);
  const latest = evaluation('new', 'a', '2026-11-08T01:00:00Z', 90);
  const appeals = [{ id: 'open', status: 'open' }, { id: 'closed', status: 'accepted' }];
  const reviews = { old: review(old, score(60), appeals), new: review(latest, score(90)) };
  assert.equal(summarizeWorkspace([old, latest], reviews, '7d', now).openAppeals, 1);
  assert.equal(summarizeWorkspace([old, latest], reviews, '7d', now).selected.length, 1);
});

test('a conversation stays on review while an older evaluation has an open appeal, then clears when resolved', () => {
  const older = evaluation('old', 'a', '2026-11-01T00:00:00Z', 60);
  const newer = evaluation('new', 'a', '2026-11-08T00:00:00Z', 90);
  const unrelated = evaluation('other', 'b', '2026-11-08T00:00:00Z', 70);
  const reviews = {
    old: review(older, score(60), [{ id: 'older-appeal', status: 'open' }]),
    new: review(newer, score(90)),
    other: review(unrelated, score(70), [{ id: 'unrelated-appeal', status: 'open' }]),
  };
  assert.equal(hasOpenAppeal('a', reviews), true);
  assert.equal(hasOpenAppeal('missing', reviews), false);
  for (const status of ['accepted', 'rejected']) {
    const resolved = { ...reviews, old: review(older, score(80), [{ id: 'older-appeal', status }]) };
    assert.equal(hasOpenAppeal('a', resolved), false);
    assert.equal(hasOpenAppeal('b', resolved), true);
  }
  assert.equal(hasOpenAppeal('a', {}), false);
});

test('empty history has no score, no points and no invented baseline', () => {
  const result = summarizeWorkspace([], {}, 'all', now);
  assert.equal(result.average, null);
  assert.equal(result.scored, 0);
  assert.equal(result.grossErrors, 0);
  assert.deepEqual(result.series, []);
});
