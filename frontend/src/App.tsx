import { useCallback, useEffect, useRef, useState } from 'react';
import { api, apiMode } from './api/client';
import type { Checklist, Conversation, Evaluation, EvaluationResponse } from './api/types';

const speakers = { client: 'Клиент', operator: 'Оператор', bot: 'Бот', system: 'Система' };
const checklistKey = (checklist: Checklist) => `${checklist.id}:${checklist.version}`;
const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'Не удалось выполнить запрос.';

function criterionValue(result: EvaluationResponse['criteria'][number], type: string): string {
  if (result.status === 'needs_review') return 'Нужна проверка';
  if (result.value === null) return 'Неприменимо';
  if (type === 'flag') return result.value ? 'Есть нарушение' : 'Не обнаружены';
  if (typeof result.value === 'boolean') return result.value ? 'Выполнен' : 'Не выполнен';
  return String(result.value);
}

export function App() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [checklists, setChecklists] = useState<Checklist[]>([]);
  const [conversationId, setConversationId] = useState('');
  const [selectedChecklist, setSelectedChecklist] = useState('');
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [loading, setLoading] = useState(true);
  const [evaluating, setEvaluating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connection, setConnection] = useState('Подключение…');
  const requestVersion = useRef(0);
  const conversation = conversations.find((item) => item.id === conversationId);
  const checklist = checklists.find((item) => checklistKey(item) === selectedChecklist);

  const load = useCallback(async () => {
    const version = ++requestVersion.current;
    setLoading(true);
    setError(null);
    setConnection('Подключение…');
    try {
      const [health, nextConversations, nextChecklists] = await Promise.all([
        api.health(), api.getConversations(), api.getChecklists(),
      ]);
      if (version !== requestVersion.current) return;
      setConversations(nextConversations);
      setChecklists(nextChecklists);
      setConversationId(nextConversations[0]?.id ?? '');
      setSelectedChecklist(nextChecklists[0] ? checklistKey(nextChecklists[0]) : '');
      setEvaluation(null);
      setConnection(apiMode === 'demo' ? 'Локальные данные' : `API: ${health.status}`);
    } catch (failure) {
      if (version !== requestVersion.current) return;
      setError(errorMessage(failure));
      setConnection('Нет соединения');
    } finally {
      if (version === requestVersion.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    return () => { requestVersion.current += 1; };
  }, [load]);

  async function evaluate() {
    if (!conversation || !checklist) return;
    setEvaluating(true);
    setError(null);
    try {
      const result = await api.evaluate({
        conversation_id: conversation.id,
        checklist_id: checklist.id,
        checklist_version: checklist.version,
      });
      setEvaluation(result);
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setEvaluating(false);
    }
  }

  return (
    <div className="app-frame">
      <header className="topbar">
        <div className="brand" aria-label="EVA — Expert Virtual Auditor">
          <svg viewBox="0 0 30 24" aria-hidden="true"><path d="M2 4h10M2 12h7M2 20h10M17 4l5 16 6-16" /></svg>
          <strong>EVA</strong><span>Expert Virtual Auditor</span>
        </div>
        <span className="connection"><span aria-hidden="true" className="status-dot" />{connection}</span>
      </header>

      <aside className="sidebar" aria-label="Выбор данных">
        <div className="workspace-name">Рабочее место аудитора</div>
        <label className="field" htmlFor="conversation">
          Диалог
          <select id="conversation" value={conversationId} disabled={loading || evaluating || !conversations.length}
            onChange={(event) => { setConversationId(event.target.value); setEvaluation(null); }}>
            {!conversations.length && <option value="">Нет диалогов</option>}
            {conversations.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}
          </select>
        </label>
        <label className="field" htmlFor="checklist">
          Чек-лист
          <select id="checklist" value={selectedChecklist} disabled={loading || evaluating || !checklists.length}
            onChange={(event) => { setSelectedChecklist(event.target.value); setEvaluation(null); }}>
            {!checklists.length && <option value="">Нет чек-листов</option>}
            {checklists.map((item) => <option key={checklistKey(item)} value={checklistKey(item)}>{item.name} · v{item.version}</option>)}
          </select>
        </label>
        {checklist && <p className="selection-detail">{checklist.name}<br />Версия {checklist.version} · {checklist.criteria.length} критериев</p>}
        <button className="button secondary" onClick={() => void load()} disabled={loading || evaluating}>
          {loading ? 'Загрузка…' : 'Обновить данные'}
        </button>
        <div className="foundation-note">
          <strong>Основа команды</strong>
          <p>Транскрипт, критерии и результат проверки связаны единым контрактом.</p>
          <span>Mansur · Frontend</span>
        </div>
      </aside>

      <main className="sheet" aria-busy={loading}>
        <div className="page-heading">
          <div><h1>Проверка диалога</h1><p>Результат по каждому критерию — рядом с исходной перепиской.</p></div>
          <button className="button primary" disabled={loading || evaluating || !conversation || !checklist}
            onClick={() => void evaluate()}>
            {evaluating ? 'Проверяем…' : apiMode === 'demo' ? 'Показать пример оценки' : 'Оценить диалог'}
          </button>
        </div>

        {apiMode === 'demo' && <p className="demo-notice">Деморежим · синтетический диалог и ручная разметка. Запросы к ML не отправляются.</p>}
        {error && <div className="error-notice" role="alert"><strong>Не удалось обновить проверку.</strong> {error}</div>}

        <section className="evaluation-strip" aria-label="Результат оценки" aria-live="polite">
          <div><span>Итоговая оценка</span><strong className="score">{evaluation?.scoring.total == null ? '—' : evaluation.scoring.total}<small> / 100</small></strong></div>
          <div><span>Результат</span><strong>{evaluating ? 'Проверка выполняется' : !evaluation ? 'Пока нет оценки' : evaluation.status === 'needs_review' ? 'Нужна ручная проверка' : 'Проверка завершена'}</strong></div>
          <div><span>Грубые ошибки</span><strong>{evaluation?.scoring.has_gross_error == null ? 'Пока неизвестно' : evaluation.scoring.has_gross_error ? 'Обнаружены' : 'Не обнаружены'}</strong></div>
        </section>

        {evaluation && evaluation.original.warnings.length > 0 && <div className="warnings" aria-label="Оговорки оценки">
          {evaluation.original.warnings.map((warning, index) => <p key={`${index}-${warning}`}>{warning}</p>)}
        </div>}

        <div className="review-grid">
          <section className="transcript" aria-labelledby="transcript-heading">
            <div className="section-heading"><h2 id="transcript-heading">Переписка</h2><span>{conversation ? `${conversation.language} · ${conversation.channel === 'agent_chat' ? 'Чат' : 'Звонок'}` : '—'}</span></div>
            {conversation ? <>
              <p className="object-id">{conversation.id} · {conversation.messages.length} сообщения</p>
              <ol className="messages">
                {conversation.messages.map((message) => <li id={`message-${message.id}`} key={message.id} className={`message ${message.speaker}`} tabIndex={-1}>
                  <div className="message-meta"><strong>{speakers[message.speaker]}</strong><time>{message.timestamp}</time></div>
                  <p>{message.text}</p><span className="message-id">{message.id}</span>
                </li>)}
              </ol>
              {!conversation.messages.length && <p className="empty">В этом диалоге нет сообщений.</p>}
            </> : <p className="empty">{loading ? 'Загружаем переписку…' : 'Добавьте диалог через backend, затем обновите данные.'}</p>}
          </section>

          <section className="criteria" aria-labelledby="criteria-heading">
            <div className="section-heading"><h2 id="criteria-heading">Критерии качества</h2><span>{checklist ? `Версия ${checklist.version}` : '—'}</span></div>
            <p className="criteria-caption">{evaluation ? 'Раскройте критерий, чтобы увидеть обоснование.' : 'Условия проверки доступны до запуска оценки.'}</p>
            <div className="criteria-list">
              {checklist?.criteria.map((criterion) => {
                const result = evaluation?.original.criteria.find((item) => item.criterion_id === criterion.id);
                const score = evaluation?.scoring.criteria.find((item) => item.criterion_id === criterion.id);
                return <details className="criterion" key={`${selectedChecklist}:${criterion.id}`}>
                  <summary><span>{criterion.title}</span><span className={`criterion-value ${result?.status === 'needs_review' ? 'needs-review' : ''}`}>
                    {result ? criterionValue(result, criterion.type) : criterion.type === 'flag' ? 'Флаг нарушения' : `Вес ${criterion.weight}`}
                  </span></summary>
                  <div className="criterion-body">
                    <p>{criterion.instruction}</p>
                    {criterion.allow_na && <p className="muted">Неприменимо, если: {criterion.na_condition}</p>}
                    {result && <>
                      <p><strong>Обоснование.</strong> {result.reason}</p>
                      {result.evidence.map((evidence, index) => <div className="evidence" key={`${evidence.kind}-${index}`}>
                        <span>{evidence.kind === 'not_applicable' ? 'Контекст исключения:' : evidence.kind === 'missing_action' ? 'Отсутствующее действие:' : evidence.kind === 'uncertain' ? 'Требуется уточнение:' : 'Основание:'}</span>
                        {evidence.message_ids.length ? evidence.message_ids.map((id) => <a href={`#message-${id}`} key={id}>{id}</a>) : <span>нет ссылки на сообщение</span>}
                      </div>)}
                      {score && <p className="muted">Вклад в итог: {score.points == null ? '—' : score.points} из {score.effective_weight == null ? '—' : score.effective_weight} баллов.</p>}
                    </>}
                  </div>
                </details>;
              })}
            </div>
            {!checklist && <p className="empty">{loading ? 'Загружаем критерии…' : 'Нет доступного чек-листа.'}</p>}
          </section>
        </div>

        <footer className="next-work"><strong>Следующий этап</strong><p>Редактор чек-листов, апелляции и встраивание в Dynamics 365 — задачи следующего этапа.</p></footer>
      </main>
    </div>
  );
}
