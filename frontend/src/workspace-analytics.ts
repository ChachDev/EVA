import type { Evaluation, ReviewCase, ScoreSummary } from './api/types.ts';

export type OverviewPeriod = 'all' | '7d' | '30d';
export type QualityDay = { day: string; average: number; count: number };
const DAY = 86_400_000;

/** One current evaluation per conversation, independent of API page ordering. */
export function latestEvaluations(evaluations: Evaluation[]): Evaluation[] {
  const byConversation = new Map<string, Evaluation>();
  const timestamp = (item: Evaluation) => Date.parse(item.created_at) || 0;
  for (const item of evaluations) {
    const previous = byConversation.get(item.original.conversation_id);
    if (!previous || timestamp(item) > timestamp(previous)
      || (timestamp(item) === timestamp(previous) && item.id.localeCompare(previous.id) > 0)) {
      byConversation.set(item.original.conversation_id, item);
    }
  }
  return [...byConversation.values()].sort((a, b) => timestamp(b) - timestamp(a) || b.id.localeCompare(a.id));
}

export function currentScoring(evaluation: Evaluation, reviews: Record<string, ReviewCase>): ScoreSummary {
  const review = reviews[evaluation.id];
  // A null reviewed total is meaningful uncertainty. Never replace it with an old score.
  return review?.evaluation_id === evaluation.id ? review.final.scoring : evaluation.scoring;
}

/** A newer evaluation does not close outstanding appeals on an earlier one. */
export function hasOpenAppeal(conversationId: string, reviews: Record<string, ReviewCase>): boolean {
  return Object.values(reviews).some(review => review.original.original.conversation_id === conversationId
    && review.appeals.some(appeal => appeal.status === 'open'));
}

/** Dates are UTC calendar days, matching the axis labels and inclusive period boundaries. */
export function summarizeWorkspace(
  evaluations: Evaluation[], reviews: Record<string, ReviewCase>, period: OverviewPeriod = 'all', now = new Date(),
) {
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const start = period === 'all' ? -Infinity : today - (period === '7d' ? 6 : 29) * DAY;
  const latest = latestEvaluations(evaluations);
  const selected = latest.filter(item => {
    const created = Date.parse(item.created_at);
    return period === 'all' || (created >= start && created <= now.getTime());
  });
  const days = new Map<string, number[]>();
  let sum = 0;
  let scored = 0;
  let grossErrors = 0;
  let unknownFlags = 0;
  let unresolved = 0;
  for (const item of selected) {
    const scoring = currentScoring(item, reviews);
    if (scoring.has_gross_error === true) grossErrors++;
    if (scoring.has_gross_error === null) unknownFlags++;
    if (scoring.status === 'needs_review') unresolved++;
    if (scoring.status !== 'complete' || typeof scoring.total !== 'number' || !Number.isFinite(scoring.total)) continue;
    sum += scoring.total;
    scored++;
    const created = Date.parse(item.created_at);
    if (!Number.isFinite(created)) continue;
    const day = new Date(created).toISOString().slice(0, 10);
    const values = days.get(day) ?? [];
    values.push(scoring.total);
    days.set(day, values);
  }
  const series: QualityDay[] = [...days.entries()].sort(([a], [b]) => a.localeCompare(b))
    .map(([day, values]) => ({ day, average: values.reduce((a, b) => a + b, 0) / values.length, count: values.length }));
  // Appeals are today's queue, including appeals against earlier evaluation versions.
  const openAppeals = new Set(Object.values(reviews).flatMap(review => review.appeals.filter(appeal => appeal.status === 'open').map(appeal => appeal.id))).size;
  return { latest, selected, average: scored ? sum / scored : null, scored, unresolved, grossErrors, unknownFlags, openAppeals, series };
}
