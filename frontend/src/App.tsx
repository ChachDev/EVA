import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, apiMode } from './api/client';
import type { Checklist, Conversation, Evaluation, EvaluationResponse } from './api/types';

const speakers = { client: 'Клиент', operator: 'Оператор', bot: 'Бот', system: 'Система' };
const languages = { RU: 'Русский', KZ: 'Қазақша', EN: 'English' };
const evidenceKinds = { message: 'Основание', missing_action: 'Ожидаемое действие', not_applicable: 'Контекст неприменимости', uncertain: 'Требуется уточнение' };
type Criterion = Checklist['criteria'][number];
type Result = EvaluationResponse['criteria'][number];
type Filter = 'all' | 'attention' | 'review';
const checklistKey = (item: Checklist) => `${item.id}:${item.version}`;
const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'Не удалось выполнить запрос.';
const number = (value: number) => value.toLocaleString('ru-RU', { maximumFractionDigits: 2 });
const channelLabel = (item: Conversation) => item.channel === 'voice_call' ? 'Звонок' : 'Чат';
const messageAnchor = (id: string) => `eva-message-${encodeURIComponent(id)}`;
const timeLabel = (value: string) => /^\d{4}-\d{2}-\d{2}T/.test(value) && !Number.isNaN(Date.parse(value))
  ? new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short' }).format(new Date(value))
  : value;

function reveal(element: HTMLElement | null | undefined, focus = false) {
  if (!element) return;
  const bounds = element.getBoundingClientRect();
  if (bounds.top < 16 || bounds.bottom > window.innerHeight - 16) element.scrollIntoView({ block: 'nearest', behavior: 'instant' });
  if (focus) element.focus({ preventScroll: true });
}

function readRoute() {
  const hash = window.location.hash.slice(1);
  if (hash === '/checklists') return { page: 'checklists' as const, conversationId: null };
  const match = hash.match(/^\/dialogs\/([^/?]+)$/);
  try { return { page: 'dialogs' as const, conversationId: match ? decodeURIComponent(match[1]) : null }; }
  catch { return { page: 'dialogs' as const, conversationId: null }; }
}

function criterionValue(result: Result | undefined, criterion: Criterion) {
  if (!result) return 'Не оценён';
  if (result.status === 'needs_review') return 'Нужна проверка';
  if (result.value === null) return 'Неприменимо';
  if (criterion.type === 'flag') return result.value ? 'Есть нарушение' : 'Не обнаружены';
  if (typeof result.value === 'boolean') return result.value ? 'Выполнен' : 'Не выполнен';
  return `${number(result.value)} / ${number(Math.max(...criterion.scale))}`;
}

function isProblem(result: Result | undefined, criterion: Criterion) {
  if (!result || result.status !== 'scored' || result.value === null) return false;
  if (criterion.type === 'flag') return result.value === true;
  if (criterion.type === 'boolean') return result.value === false;
  return typeof result.value === 'number' && result.value < Math.max(...criterion.scale);
}

function Highlight({ text, query }: { text: string; query: string }) {
  if (!query.trim()) return <>{text}</>;
  const escaped = query.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return <>{text.split(new RegExp(`(${escaped})`, 'gi')).map((part, index) =>
    part.toLocaleLowerCase() === query.trim().toLocaleLowerCase()
      ? <mark key={index}>{part}</mark> : <Fragment key={index}>{part}</Fragment>)}</>;
}

export function App() {
  const [route, setRoute] = useState(readRoute);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [checklists, setChecklists] = useState<Checklist[]>([]);
  const [selectedChecklist, setSelectedChecklist] = useState('');
  const [evaluations, setEvaluations] = useState<Record<string, Evaluation>>({});
  const [loading, setLoading] = useState(true);
  const [evaluatingKey, setEvaluatingKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [evaluationErrors, setEvaluationErrors] = useState<Record<string, string>>({});
  const [connection, setConnection] = useState('Подключение…');
  const [queueQuery, setQueueQuery] = useState('');
  const [channel, setChannel] = useState<'all' | Conversation['channel']>('all');
  const [textQuery, setTextQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [criterionId, setCriterionId] = useState('');
  const [focusedMessageId, setFocusedMessageId] = useState('');
  const [announcement, setAnnouncement] = useState('');
  const requestVersion = useRef(0);
  const messageRefs = useRef(new Map<string, HTMLLIElement>());
  const queueRef = useRef<HTMLDetailsElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const focusPageOnRoute = useRef(false);
  const conversationId = route.conversationId ?? conversations[0]?.id ?? '';
  const conversation = conversations.find(item => item.id === conversationId);
  const checklist = checklists.find(item => checklistKey(item) === selectedChecklist);
  const evaluationKey = JSON.stringify([conversationId, selectedChecklist]);
  const activeEvaluationKey = useRef(evaluationKey);
  activeEvaluationKey.current = evaluationKey;
  const displayedError = error ?? (route.page === 'dialogs' ? evaluationErrors[evaluationKey] : null);
  const evaluation = evaluations[evaluationKey];
  const evaluating = evaluatingKey !== null;
  const finalScore = evaluation?.status === 'completed' && evaluation.scoring.status === 'complete' ? evaluation.scoring.total : null;
  const results = useMemo(() => new Map(evaluation?.original.criteria.map(item => [item.criterion_id, item])), [evaluation]);
  const scores = useMemo(() => new Map(evaluation?.scoring.criteria.map(item => [item.criterion_id, item])), [evaluation]);
  const attentionCount = checklist?.criteria.filter(item => isProblem(results.get(item.id), item) || results.get(item.id)?.status === 'needs_review').length ?? 0;
  const reviewCount = checklist?.criteria.filter(item => results.get(item.id)?.status === 'needs_review').length ?? 0;
  const visibleCriteria = checklist?.criteria.filter(item => filter === 'all' ||
    (filter === 'review' ? results.get(item.id)?.status === 'needs_review'
      : results.get(item.id)?.status === 'needs_review' || isProblem(results.get(item.id), item))) ?? [];
  const criterion = visibleCriteria.find(item => item.id === criterionId) ?? visibleCriteria[0];
  const result = criterion ? results.get(criterion.id) : undefined;
  const contribution = criterion ? scores.get(criterion.id) : undefined;
  const evidenceIds = useMemo(() => [...new Set(result?.evidence.flatMap(item => item.message_ids) ?? [])], [result]);
  const evidenceMessages = conversation?.messages.filter(item => evidenceIds.includes(item.id)) ?? [];
  const activeEvidenceIndex = evidenceMessages.findIndex(item => item.id === focusedMessageId);
  const searchMatches = conversation?.messages.filter(item => textQuery.trim() && item.text.toLocaleLowerCase().includes(textQuery.trim().toLocaleLowerCase())) ?? [];
  const activeSearchIndex = searchMatches.findIndex(item => item.id === focusedMessageId);
  const queue = conversations.filter(item => (channel === 'all' || item.channel === channel) &&
    (!queueQuery.trim() || `${item.id} ${item.messages.map(message => message.text).join(' ')}`.toLocaleLowerCase().includes(queueQuery.trim().toLocaleLowerCase())));

  useEffect(() => {
    const onRoute = () => { focusPageOnRoute.current = true; setRoute(readRoute()); };
    window.addEventListener('hashchange', onRoute);
    return () => window.removeEventListener('hashchange', onRoute);
  }, []);
  useEffect(() => {
    if (focusPageOnRoute.current) { reveal(headingRef.current, true); focusPageOnRoute.current = false; }
  }, [route]);

  const load = useCallback(async () => {
    const version = ++requestVersion.current;
    setLoading(true); setError(null); setConnection('Подключение…');
    setConversations([]); setChecklists([]); setEvaluations({}); setEvaluationErrors({});
    try {
      const [health, nextConversations, nextChecklists] = await Promise.all([api.health(), api.getConversations(), api.getChecklists()]);
      if (version !== requestVersion.current) return;
      setConversations(nextConversations); setChecklists(nextChecklists);
      setSelectedChecklist(nextChecklists[0] ? checklistKey(nextChecklists[0]) : '');
      setConnection(apiMode === 'demo' ? 'Деморежим' : health.status === 'ok' ? 'Сервис доступен' : `Сервис: ${health.status}`);
    } catch (failure) {
      if (version !== requestVersion.current) return;
      setError(errorMessage(failure)); setConnection('Нет соединения');
    } finally { if (version === requestVersion.current) setLoading(false); }
  }, []);

  useEffect(() => { void load(); return () => { requestVersion.current += 1; }; }, [load]);
  useEffect(() => {
    setTextQuery(''); setCriterionId(''); setFocusedMessageId(''); setFilter('all'); setAnnouncement('');
  }, [conversationId, selectedChecklist]);
  useEffect(() => {
    const narrow = window.matchMedia('(max-width: 680px)');
    const syncQueue = () => { if (queueRef.current) queueRef.current.open = !narrow.matches; };
    syncQueue(); narrow.addEventListener('change', syncQueue);
    return () => narrow.removeEventListener('change', syncQueue);
  }, []);

  async function evaluate() {
    if (!conversation || !checklist || evaluating) return;
    const version = requestVersion.current, key = evaluationKey;
    setEvaluatingKey(key); setError(null);
    setEvaluationErrors(previous => { const next = { ...previous }; delete next[key]; return next; });
    setEvaluations(previous => { const next = { ...previous }; delete next[key]; return next; });
    try {
      const next = await api.evaluate({ conversation_id: conversation.id, checklist_id: checklist.id, checklist_version: checklist.version });
      if (version !== requestVersion.current) return;
      if (next.original.conversation_id !== conversation.id || next.original.checklist_id !== checklist.id || next.original.checklist_version !== checklist.version) {
        throw new Error('Получена оценка другого диалога или версии чек-листа. Повторите запрос.');
      }
      setEvaluations(previous => ({ ...previous, [key]: next }));
      if (activeEvaluationKey.current === key) { setCriterionId(''); setFilter('all'); setFocusedMessageId(''); }
    } catch (failure) { if (version === requestVersion.current) setEvaluationErrors(previous => ({ ...previous, [key]: errorMessage(failure) })); }
    finally { if (version === requestVersion.current) setEvaluatingKey(null); }
  }

  function jumpToMessage(id: string, focus = true) {
    const message = conversation?.messages.find(item => item.id === id);
    if (!message) return;
    setFocusedMessageId(id);
    setAnnouncement(`${speakers[message.speaker]}, ${timeLabel(message.timestamp)}. Сообщение ${id}.`);
    reveal(messageRefs.current.get(id), focus);
  }
  function moveEvidence(direction: number) {
    if (!evidenceMessages.length) return;
    const index = activeEvidenceIndex < 0 ? (direction > 0 ? 0 : evidenceMessages.length - 1) : (activeEvidenceIndex + direction + evidenceMessages.length) % evidenceMessages.length;
    jumpToMessage(evidenceMessages[index].id, false);
  }
  function moveSearch(direction: number) {
    if (!searchMatches.length) return;
    const index = activeSearchIndex < 0 ? (direction > 0 ? 0 : searchMatches.length - 1) : (activeSearchIndex + direction + searchMatches.length) % searchMatches.length;
    jumpToMessage(searchMatches[index].id, false);
  }
  const status = evaluatingKey === evaluationKey ? 'Проверяем диалог…' : !evaluation ? 'Ожидает оценки'
    : evaluation.scoring.status === 'not_calculable' ? 'Нет применимых критериев'
      : evaluation.status === 'needs_review' || evaluation.scoring.status === 'needs_review' ? 'Нужна ручная проверка' : 'Оценка готова';

  return <div className="app-frame">
    <a className="skip-link" href="#eva-main" onClick={event => { event.preventDefault(); reveal(document.getElementById('eva-main'), true); }}>Перейти к рабочей области</a>
    <header className="topbar">
      <a className="brand" href="#/dialogs" aria-label="EVA — рабочая консоль"><span className="brand-mark" aria-hidden="true"><b /><b /><b /></span><strong translate="no">EVA</strong><span className="brand-caption">Контроль качества</span></a>
      <nav className="primary-nav" aria-label="Основные разделы"><a href="#/dialogs" aria-current={route.page === 'dialogs' ? 'page' : undefined}>Диалоги</a><a href="#/checklists" aria-current={route.page === 'checklists' ? 'page' : undefined}>Чек-листы</a></nav>
      <span className="connection"><span className={`status-dot ${error ? 'offline' : ''}`} aria-hidden="true" />{connection}</span>
    </header>
    <div className="console-layout">
      <aside className="sidebar" aria-label="Очередь диалогов">
        <details className="queue-panel" open ref={queueRef}>
          <summary className="queue-heading"><span>Очередь проверки</span><span className="count">{conversations.length}</span></summary>
          <div className="queue-tools"><label className="field" htmlFor="queue-search">Найти диалог<input id="queue-search" name="queue-search" type="search" autoComplete="off" placeholder="Например, тарифы…" value={queueQuery} onChange={event => setQueueQuery(event.target.value)} /></label>
            <div className="segmented" role="group" aria-label="Канал диалога">{([['all', 'Все'], ['agent_chat', 'Чаты'], ['voice_call', 'Звонки']] as const).map(([value, title]) => <button key={value} aria-pressed={channel === value} onClick={() => setChannel(value)}>{title}</button>)}</div>
          </div>
          <div className="queue-list">{queue.map(item => <a className="queue-item" href={`#/dialogs/${encodeURIComponent(item.id)}`} key={item.id} aria-current={item.id === conversationId && route.page === 'dialogs' ? 'true' : undefined}
              onClick={event => {
                if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                if (window.matchMedia('(max-width:680px)').matches && queueRef.current) { queueRef.current.open = false; reveal(headingRef.current, true); }
              }}>
              <span className="queue-item-top"><strong translate="no">{item.id}</strong><span>{channelLabel(item)}</span></span><span className="queue-preview">{item.messages.find(message => message.speaker === 'client')?.text ?? 'Нет сообщения клиента'}</span><span className="queue-meta">{languages[item.language]} · сообщений: {item.messages.length}</span>
            </a>)}{!queue.length && <div className="queue-empty"><p className="empty">{loading ? 'Загружаем диалоги…' : conversations.length ? 'По этим условиям ничего не найдено.' : 'Диалогов пока нет.'}</p>{!loading && conversations.length > 0 && <button className="button secondary" onClick={() => { setQueueQuery(''); setChannel('all'); document.getElementById('queue-search')?.focus(); }}>Сбросить поиск</button>}</div>}</div>
        </details>
        <div className="sidebar-bottom"><button className="button secondary" disabled={loading || evaluating} onClick={() => void load()}>{loading ? 'Загрузка…' : 'Обновить данные'}</button><p className="connection-detail">{apiMode === 'demo' ? 'Синтетический пример' : 'Данные рабочего сервиса'}</p></div>
      </aside>
      <main className="sheet" id="eva-main" tabIndex={-1} aria-busy={loading}>
        <div className="page-heading"><div><p className="eyebrow">Рабочая консоль / {route.page === 'dialogs' ? 'Диалоги' : 'Чек-листы'}</p><h1 ref={headingRef} tabIndex={-1}>{route.page === 'checklists' ? 'Правила оценки' : conversation ? `${channelLabel(conversation)} ${conversation.id}` : loading ? 'Загрузка диалогов' : 'Диалог не выбран'}</h1><p className="conversation-meta">{route.page === 'checklists' ? 'Условия и версии, по которым проверяются диалоги.' : conversation ? `${languages[conversation.language]} · сообщений: ${conversation.messages.length} · оценивается работа оператора` : 'Выберите диалог в очереди слева.'}</p></div>
          {route.page === 'dialogs' && <span className={`review-state ${evaluation?.status === 'needs_review' ? 'tone-attention' : ''}`} role="status">{status}</span>}</div>
        {apiMode === 'demo' && <p className="demo-notice"><strong>Демонстрация.</strong> Синтетический диалог и ручная разметка. Модель не вызывается.</p>}
        {displayedError && <div className="error-notice" role="alert"><strong>Не удалось получить данные.</strong> {displayedError}</div>}
        <div className="workspace-toolbar"><label className="field" htmlFor="checklist">Чек-лист<select id="checklist" name="checklist" aria-label="Чек-лист" value={selectedChecklist} disabled={loading || evaluating || !checklists.length} onChange={event => setSelectedChecklist(event.target.value)}>{!checklists.length && <option value="">Нет доступных чек-листов</option>}{checklists.map(item => <option key={checklistKey(item)} value={checklistKey(item)}>{item.name} · v{item.version}</option>)}</select></label>
          {route.page === 'dialogs' && <button className="button primary" disabled={loading || evaluating || !conversation || !checklist} onClick={() => void evaluate()}>{evaluatingKey === evaluationKey && <span className="loading-spinner" aria-hidden="true" />}{evaluatingKey === evaluationKey ? 'Проверяем…' : apiMode === 'demo' ? 'Показать пример оценки' : evaluation ? 'Оценить повторно' : 'Оценить диалог'}</button>}</div>
        {route.page === 'checklists' ? <section className="checklist-view" aria-labelledby="rules-heading">
          <div className="section-heading"><h2 id="rules-heading">{checklist?.name ?? 'Нет чек-листа'}</h2><span>{checklist ? `${checklist.criteria.length} критериев · версия ${checklist.version}` : ''}</span></div>
          <div className="checklist-intro"><p className="muted">Изменение правил потребует новой версии чек-листа.</p><span className="readonly-label">Только просмотр</span></div>
          <ol className="checklist-list" role="list">{checklist?.criteria.map((item, index) => <li className="checklist-card" key={item.id}>
            <span className="rule-number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
            <div className="rule-content"><div className="checklist-card-head"><h3>{item.title}</h3></div><p>{item.instruction}</p>{item.allow_na && <p className="na-rule"><strong>Неприменимо, если:</strong> {item.na_condition}</p>}</div>
            <dl className="rule-facts"><div><dt>Тип</dt><dd>{item.type === 'flag' ? 'Флаг нарушения' : item.type === 'boolean' ? 'Да / нет' : 'Шкала'}</dd></div><div><dt>Вес</dt><dd>{number(item.weight)}</dd></div>{item.type === 'scale' && <div><dt>Шкала</dt><dd>{item.scale.map(number).join(' · ')}</dd></div>}</dl>
          </li>)}</ol>{!checklist && <p className="empty">{loading ? 'Загружаем правила…' : 'Нет доступного чек-листа.'}</p>}</section>
        : <>
          {evaluation && evaluation.original.warnings.length > 0 && <details className="warnings" open><summary>Оговорки оценки · {evaluation.original.warnings.length}</summary>{evaluation.original.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</details>}
          {conversation?.channel === 'voice_call' && <p className="demo-notice">{conversation.audio_url ? 'Разбор по расшифровке. Подключение записи ещё не настроено.' : 'Аудиозапись отсутствует. Доступна расшифровка разговора.'}</p>}
          <nav className="section-shortcuts" aria-label="Области разбора"><button className="button secondary" onClick={() => reveal(document.getElementById('transcript-heading'), true)}>К переписке</button><button className="button secondary" onClick={() => reveal(document.getElementById('score-heading'), true)}>К оценке</button></nav>
          <div className="review-grid">
            <section className="transcript" aria-labelledby="transcript-heading"><div className="section-heading"><h2 id="transcript-heading" tabIndex={-1}>{conversation?.channel === 'voice_call' ? 'Расшифровка' : 'Переписка'}</h2><span>Сообщений: {conversation?.messages.length ?? 0}</span></div>
              <div className="transcript-tools"><label className="search-field" htmlFor="transcript-search">Поиск в диалоге<input id="transcript-search" name="transcript-search" type="search" autoComplete="off" placeholder="Например, комиссия…" aria-describedby="search-hint" aria-keyshortcuts="Enter Shift+Enter" value={textQuery} onChange={event => { setTextQuery(event.target.value); setFocusedMessageId(''); }} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); moveSearch(event.shiftKey ? -1 : 1); } else if (event.key === 'Escape') { setTextQuery(''); setFocusedMessageId(''); } }} /></label><span id="search-hint" className="sr-only">Enter — следующее совпадение. Shift + Enter — предыдущее. Escape — очистить поиск.</span>{textQuery.trim() && <div className="search-navigation"><span role="status">{searchMatches.length ? `${activeSearchIndex < 0 ? '—' : activeSearchIndex + 1} / ${searchMatches.length} сообщений` : 'Совпадений нет'}</span><button className="button secondary icon-button" aria-label="Предыдущее совпадение" disabled={!searchMatches.length} onClick={() => moveSearch(-1)}>↑</button><button className="button secondary icon-button" aria-label="Следующее совпадение" disabled={!searchMatches.length} onClick={() => moveSearch(1)}>↓</button></div>}</div>
              {result && evidenceMessages.length > 0 && <p className="evidence-tag">Выделены основания: {evidenceMessages.length} · {criterion?.title}</p>}
              <ol className="messages">{conversation?.messages.map(message => <li id={messageAnchor(message.id)} key={message.id} ref={element => { if (element) messageRefs.current.set(message.id, element); else messageRefs.current.delete(message.id); }} tabIndex={-1} className={`message ${message.speaker}${evidenceIds.includes(message.id) ? ' is-evidence' : ''}${focusedMessageId === message.id ? ' is-current' : ''}`}>
                <div className="message-meta"><strong>{speakers[message.speaker]}</strong><time dateTime={message.timestamp} title={message.timestamp}>{timeLabel(message.timestamp)}</time></div><p><Highlight text={message.text} query={textQuery} /></p><span className="message-id"><span translate="no">{message.id}</span>{evidenceIds.includes(message.id) ? ' · основание оценки' : ''}</span></li>)}</ol>
              {(!conversation || !conversation.messages.length) && <p className="empty">{loading ? 'Загружаем переписку…' : conversation ? 'В этом диалоге нет сообщений.' : 'Выберите доступный диалог в очереди.'}</p>}
            </section>
            <aside className="inspector" aria-label="Оценка и доказательства">
              <button className="button secondary return-to-transcript" onClick={() => reveal(document.getElementById('transcript-heading'), true)}>Вернуться к переписке</button>
              <div className="score-panel"><h2 id="score-heading" tabIndex={-1}>Качество диалога</h2><div className="score"><strong data-testid="total-score">{finalScore == null ? '—' : number(finalScore)}</strong><small>/ 100</small></div><p className="score-caption">{!evaluation ? 'Появится после проверки' : finalScore == null ? status : apiMode === 'demo' ? 'Ручная демонстрационная оценка' : 'Итог рассчитан сервером'}</p><p className="score-facts">Грубые ошибки: <strong>{evaluation?.scoring.has_gross_error == null ? 'неизвестно' : evaluation.scoring.has_gross_error ? 'обнаружены' : 'не обнаружены'}</strong></p></div>
              {criterion && <section className="evidence-panel" aria-labelledby="evidence-heading"><label className="field" htmlFor="active-criterion">Критерий<select id="active-criterion" name="active-criterion" aria-label="Критерий" value={criterion.id} onChange={event => { setCriterionId(event.target.value); setFocusedMessageId(''); }}>{visibleCriteria.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label><p className="eyebrow">{result ? `Основание оценки · ${criterionValue(result, criterion)}` : 'Условие проверки'}</p><h3 id="evidence-heading">{criterion.title}</h3><p className="evidence-explanation">{result?.reason ?? criterion.instruction}</p>
                {result && <>{!!evidenceMessages.length && <div className="evidence-navigation"><span>Реплики: {activeEvidenceIndex < 0 ? '—' : activeEvidenceIndex + 1} / {evidenceMessages.length}</span><button className="button secondary icon-button" aria-label="Предыдущее основание" onClick={() => moveEvidence(-1)}>↑</button><button className="button secondary icon-button" aria-label="Следующее основание" onClick={() => moveEvidence(1)}>↓</button></div>}
                  <div className="quote-list">{result.evidence.map((item, index) => <div key={index}><span className="evidence-kind">{evidenceKinds[item.kind]}</span>{item.message_ids.map(id => {
                    const message = conversation?.messages.find(entry => entry.id === id);
                    const quoteId = `eva-quote-${index}-${encodeURIComponent(id)}`;
                    return message ? <blockquote key={id}><button className="quote-target" onClick={() => jumpToMessage(id)} aria-label={`Перейти к сообщению ${id}`} aria-describedby={`${quoteId}-meta ${quoteId}-text`}><span className="quote-meta" id={`${quoteId}-meta`}>{speakers[message.speaker]} · {timeLabel(message.timestamp)} · <span translate="no">{id}</span></span><span id={`${quoteId}-text`}>{message.text}</span></button></blockquote> : <p className="muted" key={id}>Сообщение {id} не найдено в диалоге.</p>;
                  })}{!item.message_ids.length && <p className="muted">В ответе нет ссылки на сообщение.</p>}</div>)}</div>
                  {contribution && evaluation.scoring.status === 'complete' && criterion.type !== 'flag' && contribution.points != null && contribution.effective_weight != null && <p className="muted">Вклад в итог: {number(contribution.points)} из {number(contribution.effective_weight)} балла.</p>}
                  <details className="criterion-rule"><summary>Правило и условия неприменимости</summary><p>{criterion.instruction}</p>{criterion.allow_na ? <p>Неприменимо, если: {criterion.na_condition}</p> : <p>Критерий применяется ко всем диалогам.</p>}</details>
                </>}
              </section>}
              <details className="criteria-overview"><summary>Все критерии · {checklist?.criteria.length ?? 0}</summary>
              <div className="criterion-filter segmented" role="group" aria-label="Фильтр критериев">{([['all', 'Все'], ['attention', `Замечания ${attentionCount}`], ['review', `Проверить ${reviewCount}`]] as const).map(([value, title]) => <button key={value} aria-pressed={filter === value} disabled={value !== 'all' && !evaluation} onClick={() => { setFilter(value); setCriterionId(''); setFocusedMessageId(''); }}>{title}</button>)}</div>
              <div className="criteria-list">{visibleCriteria.map(item => { const itemResult = results.get(item.id), score = scores.get(item.id); return <button className={`criterion-row ${itemResult?.status === 'needs_review' ? 'tone-attention' : isProblem(itemResult, item) ? 'tone-negative' : 'tone-neutral'}`} key={item.id} aria-pressed={criterion?.id === item.id} onClick={() => { setCriterionId(item.id); setFocusedMessageId(''); setAnnouncement(`Выбран критерий: ${item.title}. ${evaluation ? 'Связанные сообщения выделены в переписке.' : 'Условие показано в панели критерия.'}`); reveal(document.getElementById('active-criterion'), true); }}><span className="criterion-label">{item.title}</span><span className="criterion-value">{criterionValue(itemResult, item)}</span>{itemResult?.status === 'scored' && item.type !== 'flag' && score?.points != null && score.effective_weight != null && evaluation?.scoring.status === 'complete' && <span className="criterion-points">{number(score.points)} / {number(score.effective_weight)} балла</span>}</button>; })}</div>
              {!visibleCriteria.length && <p className="empty">{!checklist ? 'Нет доступных критериев.' : filter === 'review' ? 'Критериев с неопределённым результатом нет.' : 'Замечаний по этой оценке нет.'}</p>}
              </details>
            </aside>
          </div>
        </>}
        <footer className="next-work"><span>{checklist ? `${checklist.name} · версия ${checklist.version}` : 'EVA · контроль качества'}</span><span>{evaluation && route.page === 'dialogs' ? `Проверка: ${new Date(evaluation.created_at).toLocaleString('ru-RU', { timeZoneName: 'short' })}` : 'Рабочая консоль'}</span></footer>
      </main>
    </div>
    <div className="sr-only" role="status" aria-live="polite">{announcement}</div>
  </div>;
}
