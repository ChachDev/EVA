import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle, ArrowRight, Check, ChevronDown, Clock, FileText,
  GitPullRequest, Headphones, Info, MessageSquare, Minus, ShieldCheck,
} from "lucide-react";
import type { Checklist, Conversation, Evaluation, EvaluationResponse, ReviewCase } from "./api/types";

export type LiveDetailProps = {
  conversation: Conversation;
  checklist: Checklist | null;
  evaluation: Evaluation | null;
  review: ReviewCase | null;
  busy: boolean;
  canEvaluate?: boolean;
  error: string;
  onEvaluate: () => void;
  onAppeal: (criterionId: string) => void;
};

type Criterion = Checklist["criteria"][number];
type Result = EvaluationResponse["criteria"][number];
const speakers = { operator: "Оператор", client: "Клиент", system: "Система", bot: "Бот" };
const evidenceKinds = {
  message: "Основание", missing_action: "Участок ожидаемого действия",
  not_applicable: "Основание неприменимости", uncertain: "Контекст для ручной проверки",
};
const formatNumber = (value: number | null) => value === null ? "—" : value.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
const messageAnchor = (conversationId: string, messageId: string) => `live-message-${encodeURIComponent(conversationId)}-${encodeURIComponent(messageId)}`;
const timeLabel = (value: string) => /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(value) ? value.slice(11, 19) : value;

function resultLabel(result: Result | undefined, criterion: Criterion): string {
  if (!result) return "Не оценён";
  if (result.status === "needs_review") return "Нужна проверка";
  if (result.value === null) return "N/A — неприменимо";
  if (criterion.type === "flag") return result.value === true ? "Есть нарушение" : "Не обнаружено";
  return typeof result.value === "boolean" ? result.value ? "Да" : "Нет" : formatNumber(result.value);
}

function audioLink(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

function ScoreGauge({ value }: { value: number | null }) {
  return <div className="gauge">
    <svg viewBox="0 0 144 144" aria-hidden="true">
      <circle cx="72" cy="72" r="61" fill="none" stroke="#e8eee9" strokeWidth="8" />
      {value !== null && <circle cx="72" cy="72" r="61" fill="none" stroke="#00b849" strokeWidth="8"
        strokeDasharray={`${value / 100 * 383.27} 383.27`} transform="rotate(-90 72 72)" strokeLinecap="round" />}
    </svg>
    <div><strong>{formatNumber(value)}</strong><span>из 100 баллов</span></div>
  </div>;
}

export default function LiveDetail({ conversation, checklist, evaluation, review, busy, canEvaluate = true, error, onEvaluate, onAppeal }: LiveDetailProps) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<string[]>([]);
  const [transcriptTab, setTranscriptTab] = useState<"all" | "operator">("all");
  const jumpTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const currentEvaluation = evaluation?.original.conversation_id === conversation.id ? evaluation : null;
  const currentReview = currentEvaluation && review?.evaluation_id === currentEvaluation.id ? review : null;
  const checklistMatches = !currentEvaluation || (checklist?.id === currentEvaluation.original.checklist_id
    && checklist.version === currentEvaluation.original.checklist_version);
  const criteria = checklistMatches ? checklist?.criteria ?? [] : [];
  const resultRows = currentReview?.final.criteria ?? [];
  const scoring = currentReview?.final.scoring ?? null;
  const results = new Map(resultRows.map(result => [result.criterion_id, result]));
  const contributions = new Map(scoring?.criteria.map(result => [result.criterion_id, result]) ?? []);
  const messages = new Map(conversation.messages.map(message => [message.id, message]));
  const highlighted = new Set(highlight);
  const total = scoring?.status === "complete" ? scoring.total : null;
  const savedAudio = audioLink(conversation.audio_url);
  const legacy = currentReview?.snapshot_available === false;
  const source = currentEvaluation?.original;
  const stateLabel = busy ? "Выполняется оценка" : !currentEvaluation ? "Без оценки" : !currentReview ? "Текущий результат недоступен"
    : scoring?.status === "needs_review" ? "Нужна ручная проверка"
      : scoring?.status === "not_calculable" ? "Нет применимых критериев" : "Оценка получена";
  const naCount = resultRows.filter(row => row.status === "scored" && row.value === null).length;
  const reviewCount = resultRows.filter(row => row.status === "needs_review").length;

  useEffect(() => {
    setExpanded(null); setHighlight([]); setTranscriptTab("all");
    return () => { if (jumpTimer.current !== null) clearTimeout(jumpTimer.current); };
  }, [conversation.id, currentEvaluation?.id, checklist?.id, checklist?.version]);

  function jump(ids: string[]) {
    const known = ids.filter(id => messages.has(id));
    if (!known.length) return;
    setTranscriptTab("all"); setHighlight(known);
    if (jumpTimer.current !== null) clearTimeout(jumpTimer.current);
    jumpTimer.current = setTimeout(() => {
      const target = document.getElementById(messageAnchor(conversation.id, known[0]));
      target?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "center" });
      target?.focus({ preventScroll: true });
    }, 20);
  }

  return <div className="detail-view page-enter" aria-busy={busy}>
    <div className="page-heading detail-heading">
      <div><div className="eyebrow">{conversation.id} <span className="language-tag">{conversation.language}</span></div>
        <h1>{conversation.channel === "voice_call" ? "Разбор звонка" : "Разбор диалога"}</h1>
        <p>{conversation.channel === "voice_call" ? "Звонок" : "Чат"}<span>·</span>Оператор не указан</p>
      </div>
      <button className="button primary" disabled={busy || !canEvaluate} onClick={onEvaluate}>
        {busy ? <Clock aria-hidden="true" /> : <ShieldCheck aria-hidden="true" />}
        {busy ? "Оцениваем…" : currentEvaluation ? "Оценить повторно" : "Оценить диалог"}
      </button>
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}
    {!checklist && <div className="info-callout"><Info aria-hidden="true" /><span>Чек-лист недоступен. Выберите сохранённую версию, чтобы запустить оценку.</span></div>}
    {checklist && !checklistMatches && <div className="info-callout"><Info aria-hidden="true" /><span>Показанный итог относится к другой версии чек-листа. Для подробного разбора откройте её сохранённый снимок или запустите новую оценку по выбранной версии.</span></div>}
    <section className="audit-summary">
      <ScoreGauge value={total} />
      <div className="summary-description"><div className="mini-eyebrow">РЕЗУЛЬТАТ АУДИТА</div>
        <h2 aria-live="polite">{stateLabel}</h2>
        <p>{!currentEvaluation ? "Транскрипт сохранён. Автоматическая оценка запускается по кнопке."
          : scoring?.status === "needs_review" ? "Неопределённые критерии требуют проверки. Они не исключаются из расчёта как N/A."
            : scoring?.status === "not_calculable" ? "Применимого веса нет; сервер не рассчитывает числовой итог."
              : "Итог и вклад каждого критерия рассчитаны сервером."}</p>
        <div className="summary-tags">
          {checklist && checklistMatches && <span><ShieldCheck aria-hidden="true" />{checklist.name} · v{checklist.version}</span>}
          {source && !checklistMatches && <span><ShieldCheck aria-hidden="true" />{source.checklist_id} · v{source.checklist_version}</span>}
          <span><FileText aria-hidden="true" />{conversation.messages.length} реплик</span>
          {source && <span>{source.provider} · {source.model}</span>}
        </div>
      </div>
      {currentEvaluation && <div className="summary-counts"><div><b>{naCount}</b><span>неприменимо</span></div>
        <div><b className="text-amber">{reviewCount}</b><span>нужна проверка</span></div>
      </div>}
    </section>
    {currentEvaluation && <div className="info-callout"><Info aria-hidden="true" /><span>
      Результат предварительный. Решение по апелляции не означает утверждение аудита.
      {source?.provider === "stub" && " Активна заглушка: качество работы оператора не оценено моделью."}
      {source?.provider === "fixture" && " Это демонстрационная разметка, а не вывод модели."}
    </span></div>}
    {scoring && <p className="muted">Грубые нарушения: {scoring.has_gross_error === null ? "требуется проверка" : scoring.has_gross_error ? "обнаружены" : "не обнаружены"}. Флаг учитывается отдельно от балла.</p>}
    {source?.warnings.map((warning, index) => <p className="info-callout" key={index}><Info aria-hidden="true" /><span>{warning}</span></p>)}
    {legacy && <div className="info-callout"><Info aria-hidden="true" /><span>У этой исторической оценки нет снимка входных данных. Просмотр доступен, пересмотр недоступен.</span></div>}
    {currentReview && currentReview.decisions.length > 0 && <div className="review-banner"><ShieldCheck aria-hidden="true" /><span>
      Есть сохранённые решения по апелляциям. Исходный итог <b>{formatNumber(currentReview.original.scoring.total)}</b> → текущий <b>{formatNumber(total)}</b>.
      {currentReview.review_status === "appealed" ? " Остаются открытые обращения." : " Обращения рассмотрены."}
    </span></div>}
    <div className="audit-columns">
      <section className="panel transcript-panel">
        <div className="panel-heading"><div><h2>Транскрипт диалога</h2><p>Исходные реплики и доказательства</p></div>
          {conversation.channel === "voice_call" ? <Headphones aria-hidden="true" /> : <MessageSquare aria-hidden="true" />}</div>
        {conversation.channel === "voice_call" && <div className="audio-empty"><Headphones aria-hidden="true" /><div>
          <strong>{savedAudio ? "Аудиозапись доступна по ссылке" : "Аудиозапись недоступна"}</strong>
          <span>{savedAudio ? "Запись откроется только по вашему нажатию. Переход к точному фрагменту пока недоступен." : "Для просмотра и оценки используется сохранённая расшифровка."}</span>
          {savedAudio && <a className="button secondary small" href={savedAudio} target="_blank" rel="noopener noreferrer">Открыть запись <ArrowRight aria-hidden="true" /></a>}
        </div></div>}
        <div className="transcript-controls"><div className="segmented" role="group" aria-label="Показываемые реплики">
          <button className={transcriptTab === "all" ? "active" : ""} aria-pressed={transcriptTab === "all"} onClick={() => setTranscriptTab("all")}>Все реплики</button>
          <button className={transcriptTab === "operator" ? "active" : ""} aria-pressed={transcriptTab === "operator"} onClick={() => setTranscriptTab("operator")}>Оператор</button>
        </div><span className="muted">{conversation.language}</span></div>
        <div className="transcript-body">{conversation.messages.map(message => transcriptTab === "operator" && message.speaker !== "operator" ? null :
          <article id={messageAnchor(conversation.id, message.id)} tabIndex={-1} key={message.id}
            className={`message ${message.speaker}${highlighted.has(message.id) ? " highlighted" : ""}`}>
            <div className="message-meta"><span>{speakers[message.speaker]}</span><time title={message.timestamp}>{timeLabel(message.timestamp)}</time></div>
            <div className="message-bubble">{message.text}</div>
            {highlighted.has(message.id) && <span className="evidence-caption"><Check aria-hidden="true" />{message.id} · основание оценки</span>}
          </article>)}</div>
        <div className="transcript-footer"><span className="status-dot" />Конец сохранённого транскрипта<span>{conversation.messages.length} реплик</span></div>
      </section>
      <section className="panel criteria-panel">
        <div className="panel-heading"><div><h2>Оценка по критериям</h2><p>От решения — к исходной реплике</p></div><span className="count-badge">{criteria.length}</span></div>
        {!currentEvaluation && <div className="pending-notice"><Info aria-hidden="true" /><h3>Оценка ещё не выполнена</h3><p>Выберите версию чек-листа и запустите проверку. При недоступности модели результат не подменяется демонстрационными баллами.</p></div>}
        <div className="criteria-list">{criteria.map(criterion => {
          const row = results.get(criterion.id);
          const contribution = contributions.get(criterion.id);
          const pending = !row || row.status === "needs_review";
          const na = row?.status === "scored" && row.value === null;
          const flag = criterion.type === "flag";
          const maximum = criterion.type === "scale" ? Math.max(...criterion.scale) : null;
          const positive = !pending && !na && (flag ? row?.value === false : row?.value === true || row?.value === maximum);
          const open = expanded === criterion.id;
          const existingAppeal = currentReview?.appeals.some(appeal => appeal.criterion_id === criterion.id && appeal.status === "open");
          const serverPoints = !flag && !pending && !na && scoring?.status === "complete" ? contribution?.points ?? null : null;
          return <div className={`criterion${open ? " expanded" : ""}`} key={criterion.id}>
            <button className="criterion-toggle" onClick={() => setExpanded(open ? null : criterion.id)} aria-expanded={open}>
              <span className={`criterion-check ${pending || na ? "muted" : positive ? "pass" : "fail"}`}>{pending || na ? <Minus aria-hidden="true" /> : positive ? <Check aria-hidden="true" /> : <AlertTriangle aria-hidden="true" />}</span>
              <span className="criterion-title"><strong>{criterion.title}</strong><small>{resultLabel(row, criterion)} · {flag ? "Отдельный флаг" : `Исходный вес ${formatNumber(criterion.weight)}`}</small></span>
              <span className={`criterion-score${pending || na ? " muted" : ""}`}>
                {serverPoints !== null ? formatNumber(serverPoints) : pending ? "—" : na ? "N/A" : typeof row?.value === "boolean" ? row.value ? "Да" : "Нет" : formatNumber(row?.value ?? null)}
                {serverPoints !== null && contribution?.effective_weight != null && <small> / {formatNumber(contribution.effective_weight)}</small>}
              </span><ChevronDown className={open ? "rotated" : ""} aria-hidden="true" />
            </button>
            {open && <div className="criterion-detail">
              <p>{row?.reason ?? criterion.instruction}</p>
              {row?.evidence.map((entry, index) => <div key={index}>
                <p className="muted">{evidenceKinds[entry.kind]}</p>
                {entry.message_ids.map(id => {
                  const message = messages.get(id);
                  return message ? <button className="evidence-link" key={id} onClick={() => jump([id, ...entry.message_ids.filter(other => other !== id)])} aria-label={`Перейти к реплике ${id}`}>
                    <span className="evidence-link-heading"><span><MessageSquare aria-hidden="true" />{speakers[message.speaker]} · {timeLabel(message.timestamp)} · {id}</span><ArrowRight aria-hidden="true" /></span>
                    <q>{message.text}</q>
                  </button> : <p className="muted" key={id}>Реплика {id} отсутствует в открытом транскрипте.</p>;
                })}
              </div>)}
              {criterion.allow_na && criterion.na_condition && <p className="muted">Неприменимо, если: {criterion.na_condition}</p>}
              <div className="criterion-bottom"><span>{resultLabel(row, criterion)}{serverPoints !== null ? ` · вклад ${formatNumber(serverPoints)} балла` : ""}</span>
                <button disabled={!currentEvaluation || busy || legacy} onClick={() => onAppeal(criterion.id)}><GitPullRequest aria-hidden="true" />{existingAppeal ? "Апелляция открыта" : "Оспорить"}</button>
              </div>
            </div>}
          </div>;
        })}</div>
        {scoring && <div className="calculation"><div><span>Серверный итог с учётом N/A</span><strong>{formatNumber(total)} <small>/ 100</small></strong></div>
          <p>{scoring.status === "needs_review" ? "Итог появится после разрешения неопределённых критериев."
            : scoring.status === "not_calculable" ? "Нет применимых критериев для числового итога."
              : `Применимый вес: ${formatNumber(scoring.applicable_weight)}. Итог и нормированные вклады получены от Backend.`}</p>
        </div>}
      </section>
    </div>
  </div>;
}
