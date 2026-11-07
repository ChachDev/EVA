import { useCallback, useEffect, useRef, useState } from 'react';
import { LayoutDashboard, MessagesSquare, ClipboardList, GitPullRequest, Plus, RefreshCw, ArrowUpRight, Headphones, Menu, Download, X } from 'lucide-react';
import { api, ApiError } from './api/client';
import type { Checklist, Conversation, Evaluation, EvaluationSnapshot, ReviewCase, TranscriptionJob } from './api/types';
import { EvaLogo, FreedomLogo, EmptyState, StatusBadge, Score } from './eva-ui';
import LiveDetail from './LiveDetail';
import { ImportDialog, ChecklistEditor, AppealDialog, AudioDialog } from './LiveForms';
import { displayTime } from './server-input';

type Tab = 'overview' | 'dialogs' | 'checklists' | 'appeals' | 'audio';
type Route = { tab: Tab; id: string; evaluation: string };
type Workspace = { conversations: Conversation[]; checklists: Checklist[]; evaluations: Evaluation[]; reviews: Record<string, ReviewCase> };
const navigation = [{ id: 'overview', title: 'Обзор', icon: LayoutDashboard }, { id: 'dialogs', title: 'Диалоги', icon: MessagesSquare }, { id: 'checklists', title: 'Чек-листы', icon: ClipboardList }, { id: 'appeals', title: 'Апелляции', icon: GitPullRequest }, { id: 'audio', title: 'Аудиозаписи', icon: Headphones }] as const;
const titles = { overview: 'Рабочее пространство', dialogs: 'Диалоги', checklists: 'Чек-листы', appeals: 'Апелляции', audio: 'Распознавание аудио' };
const blank: Workspace = { conversations: [], checklists: [], evaluations: [], reviews: {} };
function readRoute(): Route {
  const [path, query] = location.hash.replace(/^#\/?/, '').split('?');
  const [tab, id] = path.split('/');
  try { return { tab: navigation.some(n => n.id === tab) ? tab as Tab : 'overview', id: id ? decodeURIComponent(id) : '', evaluation: new URLSearchParams(query).get('evaluation') ?? '' }; }
  catch { return { tab: 'dialogs', id: '', evaluation: '' }; }
}
function go(tab: Tab, id = '', evaluation = '') { location.hash = '/' + tab + (id ? '/' + encodeURIComponent(id) : '') + (evaluation ? '?evaluation=' + encodeURIComponent(evaluation) : ''); }
function saveJson(name: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click(); URL.revokeObjectURL(url);
}
const message = (error: unknown) => error instanceof Error ? error.message : 'Не удалось выполнить действие.';
async function reviewMap(evaluations: Evaluation[]): Promise<Record<string, ReviewCase>> {
  const result: Record<string, ReviewCase> = {};
  let index = 0;
  await Promise.all(Array.from({ length: Math.min(4, evaluations.length) }, async () => {
    while (index < evaluations.length) { const item = evaluations[index++]; result[item.id] = await api.getReviewCase(item.id); }
  }));
  return result;
}

export default function LiveApp() {
  const [route, setRoute] = useState(readRoute);
  const [workspace, setWorkspace] = useState<Workspace>(blank);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [mobile, setMobile] = useState(false);
  const [modal, setModal] = useState<'import' | 'audio' | null>(null);
  const [query, setQuery] = useState('');
  const [language, setLanguage] = useState('all');
  const [channel, setChannel] = useState('all');
  const [checklistKey, setChecklistKey] = useState('');
  const [selection, setSelection] = useState<{ id: string; snapshot: EvaluationSnapshot; review: ReviewCase } | null>(null);
  const [legacyReview, setLegacyReview] = useState<ReviewCase | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState('');
  const [evaluating, setEvaluating] = useState(false);
  const evaluationLock = useRef(false);
  const [appealContext, setAppealContext] = useState<{ snapshot: EvaluationSnapshot; review: ReviewCase; criterionId: string; appealId?: string } | null>(null);
  const generation = useRef(0);
  const appealGeneration = useRef(0);
  const [revision, setRevision] = useState(0);
  const [job, setJob] = useState<TranscriptionJob | null>(null);
  const [jobError, setJobError] = useState('');
  const [jobBusy, setJobBusy] = useState(false);
  const [jobLookup, setJobLookup] = useState('');
  const [roles, setRoles] = useState<Array<'client' | 'operator' | ''>>([]);
  const roleOperation = useRef({ signature: '', id: '' });

  const reload = useCallback(async () => {
    const current = ++generation.current; setLoading(true); setError('');
    try {
      const [conversations, checklists, evaluations] = await Promise.all([api.getConversations(), api.getChecklists(), api.getEvaluations()]);
      const reviews = await reviewMap(evaluations);
      if (current !== generation.current) return;
      setWorkspace({ conversations, checklists: [...checklists].sort((a, b) => b.version - a.version || a.id.localeCompare(b.id)), evaluations, reviews });
      setRevision(v => v + 1);
    } catch (error) { if (current === generation.current) setError(message(error)); }
    finally { if (current === generation.current) setLoading(false); }
  }, []);
  useEffect(() => { void reload(); return () => { generation.current++; }; }, [reload]);
  useEffect(() => { const sync = () => { appealGeneration.current++; setRoute(readRoute()); setMobile(false); setDetailError(''); setNotice(''); }; window.addEventListener('hashchange', sync); return () => window.removeEventListener('hashchange', sync); }, []);

  const checklists = workspace.checklists;
  const chosen = checklists.find(c => `${c.id}:${c.version}` === checklistKey) ?? checklists[0] ?? null;
  const conversation = workspace.conversations.find(c => c.id === route.id) ?? null;
  const history = workspace.evaluations.filter(e => e.original.conversation_id === route.id);
  const evaluation = route.evaluation ? history.find(e => e.id === route.evaluation) ?? null : history[0] ?? null;
  const evaluationId = evaluation?.id ?? '';
  useEffect(() => {
    let cancelled = false; setSelection(null); setLegacyReview(null); setDetailError('');
    if (!evaluationId || route.tab !== 'dialogs') { setDetailLoading(false); return; }
    setDetailLoading(true);
    void Promise.all([api.getEvaluationSnapshot(evaluationId).catch(error => { if (error instanceof ApiError && error.status === 409) return null; throw error; }), api.getReviewCase(evaluationId)]).then(([snapshot, review]) => {
      if (cancelled) return;
      if (snapshot) setSelection({ id: evaluationId, snapshot, review });
      else { setLegacyReview(review); setDetailError('У старой оценки нет сохранённого снимка входов. Просмотр доступен, апелляция недоступна. Можно выполнить новую оценку.'); }
    }).catch(error => { if (!cancelled) setDetailError(message(error)); }).finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [evaluationId, revision, route.tab]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setJob(null); setJobError('');
    if (route.tab !== 'audio' || !route.id) return;
    async function poll() {
      try {
        const next = await api.getTranscription(route.id);
        if (cancelled) return;
        setJob(next);
        if (next.status === 'needs_review') setRoles(next.result?.segments.map(s => s.speaker === 'unknown' ? '' : s.speaker) ?? []);
        if (next.status === 'queued' || next.status === 'running') timer = setTimeout(() => void poll(), 2500);
      } catch (error) { if (!cancelled) setJobError(message(error)); }
    }
    void poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, [route.tab, route.id, revision]);

  async function evaluate() {
    if (!conversation || !chosen || evaluationLock.current) return;
    const origin = location.hash;
    evaluationLock.current = true; setEvaluating(true); setDetailError('');
    try {
      const result = await api.evaluate({ conversation_id: conversation.id, checklist_id: chosen.id, checklist_version: chosen.version });
      setWorkspace(w => ({ ...w, evaluations: [result, ...w.evaluations] }));
      if (location.hash === origin) go('dialogs', conversation.id, result.id);
      await reload();
      setNotice('Оценка сохранена. Результат предварительный и требует проверки человеком.');
    } catch (error) { if (location.hash === origin) setDetailError(message(error)); else setError(message(error)); }
    finally { evaluationLock.current = false; setEvaluating(false); }
  }
  async function openAppeal(evaluationId: string, criterionId: string, appealId?: string) {
    const generation = ++appealGeneration.current;
    setError('');
    try {
      const [snapshot, review] = await Promise.all([api.getEvaluationSnapshot(evaluationId), api.getReviewCase(evaluationId)]);
      if (generation !== appealGeneration.current) return;
      if (!snapshot.checklist.criteria.some(c => c.id === criterionId)) throw new Error('Критерий отсутствует в снимке оценки.');
      if (!appealId && review.appeals.some(a => a.criterion_id === criterionId && a.status === 'open')) throw new Error('По этому критерию уже есть открытая апелляция. Перейдите в раздел «Апелляции».');
      setAppealContext({ snapshot, review, criterionId, appealId });
    } catch (error) { if (generation === appealGeneration.current) setError(message(error)); }
  }
  async function savedReview(receipt: ReviewCase) {
    setAppealContext(null);
    try {
      // An idempotent replay can return its historical receipt, not the current revision.
      const review = await api.getReviewCase(receipt.evaluation_id);
      setWorkspace(w => (w.reviews[review.evaluation_id]?.revision ?? -1) > review.revision ? w : ({ ...w, reviews: { ...w.reviews, [review.evaluation_id]: review } }));
      setSelection(s => s?.id === review.evaluation_id && s.review.revision <= review.revision ? { ...s, review } : s);
      setNotice('Действие сохранено на сервере. Отдельное утверждение оценки ещё не реализовано.');
    } catch (error) { setError('Действие сохранено, но текущую ревизию получить не удалось. ' + message(error)); }
  }
  async function confirmRoles() {
    if (!job || jobBusy || roles.some(r => !r)) return;
    setJobBusy(true); setJobError('');
    const signature = JSON.stringify({ id: job.id, roles });
    if (roleOperation.current.signature !== signature) roleOperation.current = { signature, id: crypto.randomUUID() };
    try { const next = await api.reviewTranscription(job.id, { operation_id: roleOperation.current.id, roles: roles as Array<'client' | 'operator'> }); if (readRoute().tab === 'audio' && readRoute().id === job.id) setJob(next); await reload(); }
    catch (error) { if (readRoute().tab === 'audio' && readRoute().id === job.id) setJobError(message(error)); } finally { setJobBusy(false); }
  }

  const appeals = Object.values(workspace.reviews).flatMap(review => review.appeals.map(appeal => ({ review, appeal })));
  const openAppeals = appeals.filter(({ appeal }) => appeal.status === 'open');
  const filtered = workspace.conversations.filter(c => (language === 'all' || c.language === language) && (channel === 'all' || c.channel === channel) && c.id.toLowerCase().includes(query.toLowerCase()));
  const currentSelection = selection?.id === evaluationId ? selection : null;
  const legacy = legacyReview?.evaluation_id === evaluationId ? legacyReview : null;
  const displayChecklist = evaluation ? currentSelection?.snapshot.checklist ?? (legacy ? checklists.find(c => c.id === evaluation.original.checklist_id && c.version === evaluation.original.checklist_version) : null) ?? null : chosen;
  const selector = <label className="form-label live-select">Чек-лист для новой оценки<select disabled={evaluating || loading} value={chosen ? `${chosen.id}:${chosen.version}` : ''} onChange={e => setChecklistKey(e.target.value)}>{!chosen && <option value="">Нет чек-листов</option>}{checklists.map(c => <option key={`${c.id}:${c.version}`} value={`${c.id}:${c.version}`}>{c.name} · v{c.version}</option>)}</select></label>;

  function table(items: Conversation[]) {
    if (!items.length) return <EmptyState title={loading ? 'Загружаем диалоги…' : 'Диалоги не найдены'}>Добавьте переписку или измените фильтры.</EmptyState>;
    return <div className="table-scroll"><table className="conversation-table"><thead><tr><th>Диалог</th><th>Канал</th><th>Язык</th><th>Реплики</th><th>Текущий балл</th><th>Состояние</th><th/></tr></thead><tbody>{items.map(c => {
      const record = workspace.evaluations.find(e => e.original.conversation_id === c.id);
      const review = record ? workspace.reviews[record.id] : null;
      return <tr key={c.id}><td><button className="topic-link" onClick={() => go('dialogs', c.id)}><span><strong>{c.id}</strong><small>{record ? `Оценка: ${displayTime(record.created_at)}` : 'Готов к оценке'}</small></span></button></td><td>{c.channel === 'voice_call' ? 'Звонок' : 'Чат'}</td><td><span className="language-tag">{c.language}</span></td><td>{c.messages.length}</td><td><Score value={review?.final.scoring.total ?? null}/></td><td><StatusBadge kind={review?.final.scoring.has_gross_error ? 'danger' : review ? 'warning' : 'neutral'}>{review?.final.scoring.has_gross_error ? 'Грубая ошибка' : review ? 'Предварительная оценка' : 'Без оценки'}</StatusBadge></td><td><button className="icon-button" aria-label={`Открыть ${c.id}`} onClick={() => go('dialogs', c.id)}><ArrowUpRight/></button></td></tr>;
    })}</tbody></table></div>;
  }

  return <div className="app-shell live-workspace">
    {mobile && <button className="nav-scrim" aria-label="Закрыть меню" onClick={() => setMobile(false)}/>}
    <aside className={'sidebar ' + (mobile ? 'is-open' : '')}>
      <button className="brand-button" aria-label="EVA — на главную" onClick={() => go('overview')}><EvaLogo/></button>
      <div className="workspace-switch"><span><b>Freedom Broker</b><small>Контроль качества</small></span></div>
      <div className="nav-label">РАБОЧЕЕ ПРОСТРАНСТВО</div>
      <nav aria-label="Главная навигация">{navigation.map(n => <button key={n.id} className={'nav-item ' + (route.tab === n.id ? 'active' : '')} aria-current={route.tab === n.id ? 'page' : undefined} onClick={() => go(n.id)}><n.icon/><span>{n.title}</span>{n.id === 'appeals' && openAppeals.length > 0 && <em>{openAppeals.length}</em>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="sidebar-promise"><strong>Каждая оценка —<br/>с обоснованием.</strong><p>Результаты и история сохраняются на сервере.</p></div><div className="sidebar-freedom"><FreedomLogo/><span>СЕРВЕРНЫЙ РЕЖИМ</span></div></div>
    </aside>
    <div className="main-shell"><header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-menu" aria-label="Открыть меню" onClick={() => setMobile(true)}><Menu/></button><span>Рабочее пространство</span><span className="slash">/</span><b>{titles[route.tab]}</b></div><button className="button secondary small" disabled={loading || evaluating} onClick={() => void reload()}><RefreshCw/>{loading ? 'Обновляем…' : 'Обновить'}</button></header>
    <main>
      {error && <div className="live-error" role="alert"><strong>Не удалось обновить данные.</strong> {error} Ранее загруженные данные могут быть устаревшими.</div>}
      {notice && <div className="live-notice" role="status">{notice}<button className="icon-button" aria-label="Закрыть уведомление" onClick={() => setNotice('')}><X/></button></div>}
      <div className="page-heading"><div><div className="eyebrow"><span className="status-dot"/>FREEDOM WORKSPACE</div><h1>{titles[route.tab]}</h1><p>{route.tab === 'overview' ? 'Диалоги, оценки и решения вашей команды.' : 'Сохранённые данные и действия на сервере.'}</p></div><div className="heading-actions"><button className="button secondary" onClick={() => setModal('audio')}><Headphones/>Аудио</button><button className="button primary" onClick={() => setModal('import')}><Plus/>Новый диалог</button></div></div>
      {route.tab === 'overview' && <>
        <div className="live-metrics">{[['Диалоги', workspace.conversations.length], ['Звонки', workspace.conversations.filter(c => c.channel === 'voice_call').length], ['Сохранённые оценки', workspace.evaluations.length], ['Открытые апелляции', openAppeals.length]].map(([title, count]) => <section className="panel live-metric" key={title}><span>{title}</span><strong>{loading && !workspace.conversations.length ? '—' : count}</strong></section>)}</div>
        <div className="live-notice">Каждую оценку должен утвердить человек. Отдельный этап утверждения ещё не реализован: все показанные баллы предварительные.</div>
        <section className="panel"><div className="panel-heading"><div><h2>Сохранённые диалоги</h2><p>Откройте разговор, выберите чек-лист и запустите оценку.</p></div><button className="button secondary small" onClick={() => go('dialogs')}>Все диалоги</button></div>{table(workspace.conversations.slice(0, 8))}</section>
      </>}
      {route.tab === 'dialogs' && !route.id && <section className="panel"><div className="filter-bar"><label className="form-label">Поиск по ID<input value={query} onChange={e => setQuery(e.target.value)} placeholder="Найти диалог"/></label><label className="form-label">Язык<select value={language} onChange={e => setLanguage(e.target.value)}><option value="all">Все языки</option>{['RU','KZ','EN'].map(l => <option key={l}>{l}</option>)}</select></label><label className="form-label">Канал<select value={channel} onChange={e => setChannel(e.target.value)}><option value="all">Все каналы</option><option value="agent_chat">Чаты</option><option value="voice_call">Звонки</option></select></label></div>{table(filtered)}</section>}
      {route.tab === 'dialogs' && route.id && (conversation ? <>
        <div className="live-controls"><button className="button secondary" onClick={() => go('dialogs')}>Все диалоги</button>{selector}<label className="form-label live-select">История оценок<select value={evaluationId} disabled={evaluating || !history.length} onChange={e => go('dialogs', conversation.id, e.target.value)}>{!history.length && <option value="">Оценок пока нет</option>}{history.map(e => <option value={e.id} key={e.id}>{displayTime(e.created_at)} · v{e.original.checklist_version} · {e.id.slice(0, 8)}</option>)}</select></label>{currentSelection && <button className="button secondary" onClick={() => saveJson(`EVA_${conversation.id}.json`, { snapshot: currentSelection.snapshot, review: currentSelection.review })}><Download/>Экспорт</button>}</div>
        {route.evaluation && !evaluation && <div className="live-error" role="alert">Выбранная оценка не найдена у этого диалога.</div>}
        {detailLoading && <p role="status">Загружаем сохранённые входы и текущий результат…</p>}
        <LiveDetail conversation={currentSelection?.snapshot.conversation ?? conversation} checklist={displayChecklist} evaluation={evaluation} review={currentSelection?.review ?? legacy} busy={evaluating} canEvaluate={!detailLoading && !loading && !!chosen} error={detailError} onEvaluate={() => void evaluate()} onAppeal={criterionId => evaluation && void openAppeal(evaluation.id, criterionId)}/>
      </> : <EmptyState title={loading ? 'Загрузка…' : 'Диалог не найден'}>Проверьте адрес или обновите список.</EmptyState>)}
      {route.tab === 'checklists' && <section className="panel live-panel"><div className="panel-heading"><div><h2>Версии и критерии</h2><p>Шкалы и условия N/A передаются модели вместе с каждым запросом.</p></div></div>{selector}{chosen ? <ChecklistEditor key={`${chosen.id}:${chosen.version}`} checklist={chosen} onSaved={c => { setWorkspace(w => ({ ...w, checklists: [c, ...w.checklists] })); setChecklistKey(`${c.id}:${c.version}`); setNotice(`Версия ${c.version} сохранена на сервере.`); }}/> : <EmptyState title="Чек-листов пока нет">Нужна первоначальная версия на backend.</EmptyState>}</section>}
      {route.tab === 'appeals' && <section className="panel live-panel"><div className="panel-heading"><div><h2>Обращения и решения</h2><p>Апелляция привязана к конкретной сохранённой оценке.</p></div></div>{!appeals.length ? <EmptyState title="Апелляций пока нет">Откройте оценку диалога и выберите спорный критерий.</EmptyState> : appeals.map(({ appeal, review }) => <article className="live-appeal" key={appeal.id}><div className="live-controls"><strong>{appeal.criterion_id}</strong><StatusBadge kind={appeal.status === 'open' ? 'warning' : 'info'}>{appeal.status === 'open' ? 'Открыта' : appeal.status === 'accepted' ? 'Принята' : 'Отклонена'}</StatusBadge><span>{displayTime(appeal.created_at)}</span></div><p>{appeal.reason}</p>{review.decisions.filter(d => d.appeal_id === appeal.id).map(d => <p key={d.id}>Решение: {d.reason} · {d.reviewer_id}</p>)}<div className="live-controls"><button className="button secondary small" onClick={() => go('dialogs', review.original.original.conversation_id, review.evaluation_id)}>Открыть оценку</button>{appeal.status === 'open' && <button className="button primary small" onClick={() => void openAppeal(review.evaluation_id, appeal.criterion_id, appeal.id)}>Рассмотреть</button>}</div></article>)}</section>}
      {route.tab === 'audio' && <section className="panel live-panel"><div className="panel-heading"><div><h2>Задание распознавания</h2><p>Загрузка файла и получение транскрипта — отдельные этапы. Сохраните адрес задания для возвращения к нему.</p></div></div><form className="live-controls" onSubmit={e => { e.preventDefault(); if (jobLookup.trim()) go('audio', jobLookup.trim()); }}><label className="form-label live-select">ID сохранённого задания<input value={jobLookup} onChange={e => setJobLookup(e.target.value)} placeholder="Вставьте ID задания"/></label><button className="button secondary">Открыть задание</button></form>
        {jobError && <p role="alert" className="live-error">{jobError}</p>}
        {route.id && !job && !jobError && <p role="status">Загружаем задание…</p>}
        {job && <><p><b>ID:</b> {job.id}</p><StatusBadge>{({ queued: 'В очереди', running: 'Распознаётся', completed: 'Транскрипт готов', needs_review: 'Нужно определить роли', failed: 'Ошибка распознавания' } as const)[job.status]}</StatusBadge>
          <audio controls preload="none" src={api.audioContentUrl(job.asset_id)} className="live-audio"/>
          {job.error_code && <p className="live-error">Распознавание не завершено. Код: {job.error_code}. Оценка не создавалась.</p>}
          {job.status === 'needs_review' && job.result && <><p className="live-notice">Подтвердите роли всех реплик. После сохранения появится разговор для оценки.</p>{job.result.segments.map((s, i) => <label className="form-label" key={i}>{s.start_seconds}–{s.end_seconds} сек. · {s.text}<select value={roles[i] ?? ''} disabled={jobBusy} onChange={e => setRoles(prev => prev.map((r, j) => i === j ? e.target.value as typeof r : r))}><option value="">Выберите роль</option><option value="client">Клиент</option><option value="operator">Оператор</option></select></label>)}<button className="button primary" disabled={jobBusy || roles.some(r => !r)} onClick={() => void confirmRoles()}>Подтвердить роли</button></>}
          {job.conversation_id && <button className="button primary" onClick={async () => { await reload(); go('dialogs', job.conversation_id!); }}>Открыть транскрипт для оценки</button>}
        </>}
      </section>}
      <footer className="live-footer">EVA · Серверные данные. Автоматическая оценка предварительная; отдельное утверждение человеком и полноценный ASR требуют завершения backend/ML.</footer>
    </main></div>
    {modal === 'import' && <ImportDialog onClose={() => setModal(null)} onSaved={c => { setWorkspace(w => ({ ...w, conversations: [c, ...w.conversations.filter(old => old.id !== c.id)] })); setModal(null); go('dialogs', c.id); }}/ >}
    {modal === 'audio' && <AudioDialog onClose={() => setModal(null)} onJob={j => { setModal(null); go('audio', j.id); }}/ >}
    {appealContext && <AppealDialog key={`${appealContext.review.evaluation_id}:${appealContext.review.revision}:${appealContext.criterionId}`} review={appealContext.review} conversation={appealContext.snapshot.conversation} checklist={appealContext.snapshot.checklist} criterionId={appealContext.criterionId} appealId={appealContext.appealId} onClose={() => setAppealContext(null)} onSaved={savedReview}/>}
  </div>;
}
