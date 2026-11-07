import { criteria as seedCriteria, type Appeal, type Conversation, type Criterion, type Message } from "./eva-data";

export type DemoState = {
  tag: 1;
  items: Conversation[];
  appeals: Appeal[];
  checklist: Criterion[];
  version: number;
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isText = (value: unknown): value is string => typeof value === "string";
const isNonemptyText = (value: unknown): value is string => isText(value) && value.trim().length > 0;
const isFiniteNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const isPositiveInteger = (value: unknown): value is number => isFiniteNumber(value) && Number.isSafeInteger(value) && value > 0;
const isScore = (value: unknown): value is number | null => value === null || (isFiniteNumber(value) && value >= 0 && value <= 100);
const hasUniqueIds = (values: { id: string | number }[]) => new Set(values.map(value => value.id)).size === values.length;

function isCriterion(value: unknown): value is Criterion {
  return isRecord(value) && isPositiveInteger(value.id) && isNonemptyText(value.title)
    && isText(value.description) && typeof value.na === "boolean"
    && isFiniteNumber(value.weight) && value.weight >= 0
    && isFiniteNumber(value.max) && value.max > 0
    && (value.type === "scale" || ((value.type === "binary" || value.type === "flag") && value.max === 1))
    && (value.type !== "flag" || value.weight === 0);
}

function isChecklist(value: unknown): value is Criterion[] {
  if (!Array.isArray(value) || !value.length || value.length > 1000 || !value.every(isCriterion) || !hasUniqueIds(value)) return false;
  const weight = value.reduce((total, criterion) => total + criterion.weight, 0);
  return Number.isFinite(weight) && weight > 0;
}

function isMessage(value: unknown): value is Message {
  return isRecord(value) && isText(value.time) && isText(value.text)
    && (value.role === "operator" || value.role === "client" || value.role === "system" || value.role === "bot");
}

function isRating(value: unknown, criterion: Criterion, pending = false): value is number | null {
  if (pending) return value === null;
  if (value === null) return criterion.na;
  return isFiniteNumber(value) && value >= 0 && value <= criterion.max
    && (criterion.type === "scale" || value === 0 || value === 1);
}

function isConversation(value: unknown, version: number): value is Conversation {
  if (!isRecord(value) || !isNonemptyText(value.id) || !isText(value.topic) || !isText(value.operator)
    || !isText(value.initials) || !isText(value.time) || !isText(value.duration)
    || !isPositiveInteger(value.date) || value.date > 31
    || (value.lang !== "RU" && value.lang !== "KZ" && value.lang !== "EN")
    || (value.channel !== "chat" && value.channel !== "voice")
    || (value.pending !== undefined && typeof value.pending !== "boolean")
    || !Array.isArray(value.messages) || !value.messages.length || value.messages.length > 10000
    || !value.messages.every(isMessage)) return false;
  const snapshot = value.evaluationChecklist;
  if (snapshot !== undefined && !isChecklist(snapshot)) return false;
  if (snapshot !== undefined && (!isPositiveInteger(value.evaluationVersion) || value.evaluationVersion > version)) return false;
  if (snapshot === undefined && value.evaluationVersion !== undefined && value.evaluationVersion !== 1) return false;
  const checklist = snapshot ?? seedCriteria;
  return Array.isArray(value.ratings) && value.ratings.length === checklist.length
    && value.ratings.every((rating, index) => isRating(rating, checklist[index], value.pending === true));
}

function isAppeal(value: unknown, conversations: Map<string, Conversation>): value is Appeal {
  if (!isRecord(value) || !isNonemptyText(value.id) || !isText(value.conversationId)
    || !isPositiveInteger(value.criterionId) || !isNonemptyText(value.comment) || !isText(value.date)
    || (value.status !== "open" && value.status !== "accepted" && value.status !== "confirmed")
    || !isScore(value.originalScore) || (value.finalScore !== undefined && !isScore(value.finalScore))
    || (value.reason !== undefined && !isText(value.reason))) return false;
  const conversation = conversations.get(value.conversationId);
  const criterion = (conversation?.evaluationChecklist ?? seedCriteria).find(item => item.id === value.criterionId);
  if (!conversation || conversation.pending || !criterion || !isRating(value.originalValue, criterion)) return false;
  return value.status === "open" || (isNonemptyText(value.reason) && value.finalScore !== undefined);
}

// Browser storage is untrusted input; return null so the caller can restore the demo safely.
export function parseDemoState(raw: string): DemoState | null {
  if (raw.length > 5 * 1024 * 1024) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (!isRecord(value) || value.tag !== 1 || !isChecklist(value.checklist)) return null;
    const version = value.version ?? 1;
    if (!isPositiveInteger(version) || !Array.isArray(value.items) || value.items.length > 10000
      || !value.items.every((item): item is Conversation => isConversation(item, version)) || !hasUniqueIds(value.items)) return null;
    const conversations = new Map(value.items.map(item => [item.id, item]));
    if (!Array.isArray(value.appeals) || value.appeals.length > 10000
      || !value.appeals.every((appeal): appeal is Appeal => isAppeal(appeal, conversations)) || !hasUniqueIds(value.appeals)) return null;
    return { tag: 1, items: value.items, appeals: value.appeals, checklist: value.checklist, version };
  } catch {
    return null;
  }
}
