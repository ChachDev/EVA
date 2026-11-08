import { useI18n } from './i18n';
import { AppearanceControls } from './preferences';
import { useCallback, useEffect, useRef, useState } from 'react';
import { LayoutDashboard, MessagesSquare, ClipboardList, GitPullRequest, Plus, RefreshCw, ArrowUpRight, Headphones, Menu, Download, X, ShieldCheck, Palette, CircleHelp, Search, Bell, ChevronDown, Check } from 'lucide-react';
import { api, ApiError } from './api/client';
import type { Checklist, Conversation, Evaluation, EvaluationSnapshot, ReviewCase, TranscriptionJob } from './api/types';
import { EvaLogo, FreedomLogo, EmptyState, StatusBadge } from './eva-ui';
import { WorkspaceTable, WorkspaceAppeals, WorkspaceHelp, WorkspaceSearch, WorkspaceBrand, latestRecord, conversationState, matchesConversation } from './WorkspacePanels';
import LiveOverview from './LiveOverview';
import { hasOpenAppeal } from './workspace-analytics';
import LiveDetail from './LiveDetail';
import { ImportDialog, ChecklistEditor, AppealDialog, AudioDialog } from './LiveForms';
import { displayTime } from './server-input';
import AudioHistory from './AudioHistory';
import type { TranscriptDownload } from './api/types';

type Tab = 'overview' | 'dialogs' | 'checklists' | 'appeals' | 'audio' | 'brand';
type Route = { tab: Tab; id: string; evaluation: string };
type Workspace = { conversations: Conversation[]; checklists: Checklist[]; evaluations: Evaluation[]; reviews: Record<string, ReviewCase> };
type UiNotice = string | { source: string; params: Record<string, string | number> };
const navigation = [{ id: 'overview', title: 'Обзор', icon: LayoutDashboard }, { id: 'dialogs', title: 'Диалоги', icon: MessagesSquare }, { id: 'checklists', title: 'Чек-листы', icon: ClipboardList }, { id: 'appeals', title: 'Апелляции', icon: GitPullRequest }, { id: 'audio', title: 'Аудиозаписи', icon: Headphones }] as const;
const titles = { overview: 'Обзор качества', dialogs: 'Диалоги', checklists: 'Чек-листы', appeals: 'Апелляции', audio: 'Распознавание аудио', brand: 'Дизайн-система' };
const subtitles = { overview: 'Большая картина складывается из каждого диалога.', dialogs: 'Вся история общения. Каждая оценка под рукой.', checklists: 'Ваши стандарты качества — в понятных критериях.', appeals: 'Прозрачные решения, которым можно доверять.', audio: 'От записи разговора к проверяемому транскрипту.', brand: 'Согласованная форма. Точные акценты. Человеческий подход.' };
const blank: Workspace = { conversations: [], checklists: [], evaluations: [], reviews: {} };
function readRoute(): Route {
  const [path, query] = location.hash.replace(/^#\/?/, '').split('?');
  const [tab, id] = path.split('/');
  try { return { tab: tab === 'brand' || navigation.some(n => n.id === tab) ? tab as Tab : 'overview', id: id ? decodeURIComponent(id) : '', evaluation: new URLSearchParams(query).get('evaluation') ?? '' }; }
  catch { return { tab: 'dialogs', id: '', evaluation: '' }; }
}
function go(tab: Tab, id = '', evaluation = '') { location.hash = '/' + tab + (id ? '/' + encodeURIComponent(id) : '') + (evaluation ? '?evaluation=' + encodeURIComponent(evaluation) : ''); }
function saveJson(name: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
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
 const { t, number, locale } = useI18n();
  const [route, setRoute] = useState(readRoute);
  const [workspace, setWorkspace] = useState<Workspace>(blank);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState<UiNotice>('');
  const [mobile, setMobile] = useState(false);
  const [modal, setModal] = useState<'import' | 'audio' | 'help' | 'search' | null>(null);
  const [query, setQuery] = useState('');
  const [language, setLanguage] = useState('all');
  const [channel, setChannel] = useState('all');
  const [dialogFilter, setDialogFilter] = useState<'all' | 'appealed' | 'violation'>('all');
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
  const [importSeed, setImportSeed] = useState<TranscriptDownload>();
  const [removing, setRemoving] = useState(false);
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
  useEffect(() => {
    const searchShortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k' && !document.querySelector('dialog[open]')) {
        event.preventDefault(); setModal('search');
      }
    };
    window.addEventListener('keydown', searchShortcut);
    return () => window.removeEventListener('keydown', searchShortcut);
  }, []);

  const checklists = workspace.checklists;
  const chosen = checklists.find(c => `${c.id}:${c.version}` === checklistKey) ?? checklists[0] ?? null;
  const conversation = workspace.conversations.find(c => c.id === route.id) ?? null;
  const history = workspace.evaluations.filter(e => e.original.conversation_id === route.id).sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.id.localeCompare(a.id));
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

  async function removeRecord(prompt: string, remove: () => Promise<void>, next: () => void) {
    if (removing || !window.confirm(t(prompt))) return;
    setRemoving(true);
    try { await remove(); next(); await reload(); }
    catch (reason) { setError(message(reason)); }
    finally { setRemoving(false); }
  }

  async function useTranscript(download: boolean) {
    if (!job || jobBusy) return;
    setJobBusy(true); setJobError('');
    try {
      const data = await api.exportTranscript(job.id);
      if (download) saveJson(`EVA_audio_${job.id}.json`, data);
      else { setImportSeed(data); setModal('import'); }
    } catch (reason) { setJobError(message(reason)); }
    finally { setJobBusy(false); }
  }

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
    } catch (error) {
      setNotice('Действие сохранено, но текущую ревизию получить не удалось.');
      setError(message(error));
    }
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
  const statuses = new Map(workspace.conversations.map(c => { const record = latestRecord(workspace.evaluations, c.id); return [c.id, { ...conversationState(record, record && workspace.reviews[record.id]), appealed: hasOpenAppeal(c.id, workspace.reviews) }] as const; }));
  const dialogCounts = { all: workspace.conversations.length, appealed: [...statuses.values()].filter(s => s.appealed).length, violation: [...statuses.values()].filter(s => s.violation).length };
  const filtered = workspace.conversations.filter(c => (language === 'all' || c.language === language) && (channel === 'all' || c.channel === channel) && matchesConversation(c, query) && (dialogFilter === 'all' || statuses.get(c.id)?.[dialogFilter]));
  const currentSelection = selection?.id === evaluationId ? selection : null;
  const legacy = legacyReview?.evaluation_id === evaluationId ? legacyReview : null;
  const displayChecklist = evaluation ? currentSelection?.snapshot.checklist ?? (legacy ? checklists.find(c => c.id === evaluation.original.checklist_id && c.version === evaluation.original.checklist_version) : null) ?? null : chosen;
  const selector = <label className="form-label live-select">{t("Чек-лист для новой оценки")}<select disabled={evaluating || loading} value={chosen ? `${chosen.id}:${chosen.version}` : ''} onChange={e => setChecklistKey(e.target.value)}>{!chosen && <option value="">{t("Нет чек-листов")}</option>}{checklists.map(c => <option key={`${c.id}:${c.version}`} value={`${c.id}:${c.version}`}>{c.name} · v{c.version}</option>)}</select></label>;

  const table = (items: Conversation[]) => <WorkspaceTable items={items} evaluations={workspace.evaluations} reviews={workspace.reviews} loading={loading} onOpen={id => go('dialogs', id)}/>;
  const openAllDialogs = () => { setDialogFilter('all'); setQuery(''); setLanguage('all'); setChannel('all'); go('dialogs'); };

  return <div className="app-shell live-workspace reference-workspace">
    <button className="workspace-skip" onClick={() => document.getElementById('workspace-content')?.focus()}>{t('Перейти к содержимому')}</button>
    {mobile && <button className="nav-scrim" aria-label={t('Закрыть меню')} onClick={() => setMobile(false)}/>}
    <aside className={'sidebar ' + (mobile ? 'is-open' : '')}>
      <button className="brand-button" aria-label={t('EVA — на главную')} onClick={() => go('overview')}><EvaLogo/></button>
      <details className="workspace-details"><summary className="workspace-switch"><span className="workspace-symbol"><ShieldCheck/></span><span><b>Freedom Broker</b><small>{t('Контроль качества')}</small></span><ChevronDown/></summary><div className="workspace-summary"><b>{t('Рабочее пространство EVA')}</b><p>{t('Диалоги, оценки и решения вашей команды.')}</p><span>{t('Серверный режим')}</span></div></details>
      <div className="nav-label">{t('РАБОЧЕЕ ПРОСТРАНСТВО')}</div>
      <nav aria-label={t('Главная навигация')}>{navigation.map(n => <button key={n.id} aria-label={t(n.title)} title={t(n.title)} className={'nav-item ' + (route.tab === n.id ? 'active' : '')} aria-current={route.tab === n.id ? 'page' : undefined} onClick={() => go(n.id)}><n.icon/><span>{t(n.title)}</span>{n.id === 'dialogs' && <em>{number(workspace.conversations.length)}</em>}{n.id === 'appeals' && openAppeals.length > 0 && <em>{number(openAppeals.length)}</em>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="sidebar-promise"><strong>{t('Каждая оценка —')}<br/>{t('с обоснованием.')}</strong><p>{t('Технологии с человеческим подходом.')}</p><button className="promise-link" onClick={() => setModal('help')}>{t('Знакомство с EVA')}<ArrowUpRight/></button></div><nav className="nav-secondary" aria-label={t('О проекте')}><button className={'nav-item ' + (route.tab === 'brand' ? 'active' : '')} aria-label={t('Дизайн-система')} title={t('Дизайн-система')} aria-current={route.tab === 'brand' ? 'page' : undefined} onClick={() => go('brand')}><Palette/><span>{t('Дизайн-система')}</span></button><button className="nav-item" aria-label={t('Помощь и о проекте')} title={t('Помощь и о проекте')} onClick={() => setModal('help')}><CircleHelp/><span>{t('Помощь и о проекте')}</span></button></nav><div className="sidebar-freedom"><FreedomLogo/><span>{t('СЕРВЕРНЫЙ РЕЖИМ')}</span></div></div>
    </aside>
    <div className="main-shell"><header className="topbar"><div className="breadcrumb"><button className="icon-button mobile-menu" aria-label={t('Открыть меню')} onClick={() => setMobile(true)}><Menu/></button><button className="breadcrumb-home" onClick={() => go('overview')}>{t('Рабочее пространство')}</button><span className="slash">/</span><b>{t(titles[route.tab])}</b></div><div className="top-actions"><button className="global-search" onClick={() => setModal('search')} aria-label={t('Поиск диалога')}><Search/><span>{t('Найти диалог…')}</span><kbd>Ctrl K</kbd></button><AppearanceControls/><button className="icon-button workspace-refresh" title={t('Обновить')} aria-label={t('Обновить')} disabled={loading || evaluating} onClick={() => void reload()}><RefreshCw className={loading ? 'loading-icon' : ''}/></button><button className="icon-button workspace-notifications" aria-label={t('Открытые апелляции: {count}', { count: number(openAppeals.length) })} title={t('Открытые апелляции')} onClick={() => go('appeals')}><Bell/>{openAppeals.length > 0 && <i/>}</button><span className="workspace-avatar" title={t('Контроль качества')}>QA</span></div></header>
    <main id="workspace-content" tabIndex={-1}>
      {error && <div className="live-error" role="alert"><strong>{t("Не удалось обновить данные.")}</strong> {t(error)} {" "}{t("Ранее загруженные данные могут быть устаревшими.")}</div>}
      {notice && <div className="live-notice" role="status">{typeof notice === "string" ? t(notice) : t(notice.source, notice.params)}<button className="icon-button" aria-label={t("Закрыть уведомление")} onClick={() => setNotice('')}><X/></button></div>}
      <div className="page-heading"><div><div className="eyebrow"><span className="status-dot"/>FREEDOM WORKSPACE<span className="workspace-mode">{t('Сервер')}</span></div><h1>{t(titles[route.tab])}</h1><p>{t(subtitles[route.tab])}</p></div><div className="heading-actions">{route.tab === 'checklists' ? chosen && <button type="submit" form="eva-checklist-editor" className="button primary"><Check/>{t('Сохранить чек-лист')}</button> : route.tab === 'brand' ? <a href="/brand/eva-logo.png" download className="button primary"><Download/>{t('Логотип EVA')}</a> : route.tab === 'audio' ? <button className="button primary" onClick={() => setModal('audio')}><Headphones/>{t('Загрузить аудиозапись')}</button> : <button className="button primary" onClick={() => { setImportSeed(undefined); setModal('import'); }}><Plus/>{t('Новый диалог')}</button>}</div></div>
      {route.tab === 'overview' && <>
        <LiveOverview conversations={workspace.conversations} evaluations={workspace.evaluations} reviews={workspace.reviews} loading={loading} onOpen={id => go('dialogs', id)} onDialogs={openAllDialogs} onViolations={() => { setDialogFilter('violation'); setQuery(''); setLanguage('all'); setChannel('all'); go('dialogs'); }} onAppeals={() => go('appeals')}/>
        <section className="panel live-dialog-panel"><div className="panel-heading"><div><h2>{t('Сохранённые диалоги')} <span className="count-badge">{number(workspace.conversations.length)}</span></h2></div><button className="text-button" onClick={openAllDialogs}>{t('Все диалоги')}<ArrowUpRight/></button></div>{table(workspace.conversations.slice(0, 6))}<div className="table-caption"><span>{t('Данные рабочего пространства')}</span><span><Check/>{t('Оценки с обоснованием')}</span></div></section>
      </>}
      {route.tab === 'dialogs' && !route.id && <section className="panel live-dialog-panel"><div className="workspace-table-toolbar"><div className="filter-tabs dialog-tabs" role="group" aria-label={t('Статус диалогов')}>{(['all', 'appealed', 'violation'] as const).map(filter => <button key={filter} aria-pressed={dialogFilter === filter} className={dialogFilter === filter ? 'selected' : ''} onClick={() => setDialogFilter(filter)}>{t({ all: 'Все диалоги', appealed: 'На пересмотре', violation: 'Нарушения' }[filter])}<span>{number(dialogCounts[filter])}</span></button>)}</div><button className="button secondary small" disabled={!filtered.length} onClick={() => saveJson('EVA_dialogs.json', { conversations: filtered, evaluations: workspace.evaluations.filter(record => filtered.some(c => c.id === record.original.conversation_id)).map(record => ({ original: record, review: workspace.reviews[record.id] ?? null })) })}><Download/>{t('Экспорт')}</button></div><div className="filter-bar"><label className="search-field"><Search/><input aria-label={t('ID или текст диалога')} value={query} onChange={e => setQuery(e.target.value)} placeholder={t('ID или текст диалога')}/></label><label><span className="sr-only">{t('Язык')}</span><select aria-label={t('Язык диалога')} value={language} onChange={e => setLanguage(e.target.value)}><option value="all">{t('Все языки')}</option>{['RU','KZ','EN'].map(l => <option key={l}>{l}</option>)}</select></label><label><span className="sr-only">{t('Канал')}</span><select aria-label={t('Канал')} value={channel} onChange={e => setChannel(e.target.value)}><option value="all">{t('Все каналы')}</option><option value="agent_chat">{t('Чаты')}</option><option value="voice_call">{t('Звонки')}</option></select></label>{(query || language !== 'all' || channel !== 'all') && <button className="text-button" onClick={() => { setQuery(''); setLanguage('all'); setChannel('all'); }}>{t('Сбросить фильтры')}</button>}</div>{table(filtered)}<div className="table-caption"><span>{t('Показано {count} из {total}', { count: number(filtered.length), total: number(workspace.conversations.length) })}</span><span>{t('Серверные данные')}</span></div></section>}
      {route.tab === 'dialogs' && route.id && (conversation ? <>
        <div className="live-controls"><button className="button secondary" onClick={() => go('dialogs')}>{t("Все диалоги")}</button>{selector}<label className="form-label live-select">{t("История оценок")}<select value={evaluationId} disabled={evaluating || !history.length} onChange={e => go('dialogs', conversation.id, e.target.value)}>{!history.length && <option value="">{t("Оценок пока нет")}</option>}{history.map(e => <option value={e.id} key={e.id}>{displayTime(e.created_at)} · v{e.original.checklist_version} · {e.id.slice(0, 8)}</option>)}</select></label>{currentSelection && <button className="button secondary" onClick={() => saveJson(`EVA_${conversation.id}.json`, { snapshot: currentSelection.snapshot, review: currentSelection.review })}><Download/>{t("Экспорт")}</button>}<button className="button secondary" disabled={evaluating || removing} onClick={() => void removeRecord('Удалить диалог со всеми оценками и апелляциями?', () => api.deleteConversation(conversation.id), () => go('dialogs'))}>{t('Удалить диалог')}</button>{evaluationId && <button className="button secondary" disabled={evaluating || removing} onClick={() => void removeRecord('Удалить выбранную оценку и её апелляции?', () => api.deleteEvaluation(evaluationId), () => go('dialogs', conversation.id))}>{t('Удалить оценку')}</button>}</div>
        {route.evaluation && !evaluation && <div className="live-error" role="alert">{t("Выбранная оценка не найдена у этого диалога.")}</div>}
        {detailLoading && <p role="status">{t("Загружаем сохранённые входы и текущий результат…")}</p>}
        <LiveDetail conversation={currentSelection?.snapshot.conversation ?? conversation} checklist={displayChecklist} evaluation={evaluation} review={currentSelection?.review ?? legacy} busy={evaluating} canEvaluate={!detailLoading && !loading && !!chosen} error={t(detailError)} onEvaluate={() => void evaluate()} onAppeal={criterionId => evaluation && void openAppeal(evaluation.id, criterionId)}/>
      </> : <EmptyState title={loading ? t("Загрузка…") : t("Диалог не найден")}>{t("Проверьте адрес или обновите список.")}</EmptyState>)}
      {route.tab === 'checklists' && <div className="workspace-checklists"><div className="workspace-version-selector">{selector}{chosen && !(chosen.id === 'freedom-cqa' && chosen.version === 1) && <button className="button secondary" disabled={removing} onClick={() => void removeRecord('Убрать версию чек-листа из выбора? Старые оценки сохранятся.', () => api.deleteChecklist(chosen.id, chosen.version), () => setChecklistKey(''))}>{t('Удалить версию')}</button>}</div>{chosen ? <ChecklistEditor key={`${chosen.id}:${chosen.version}`} checklist={chosen} onSaved={c => { setWorkspace(w => ({ ...w, checklists: [c, ...w.checklists] })); setChecklistKey(`${c.id}:${c.version}`); setNotice({ source: 'Версия {version} сохранена на сервере.', params: { version: c.version } }); }}/> : <section className="panel"><EmptyState title={t('Чек-листов пока нет')}>{t('Нужна первоначальная версия на backend.')}</EmptyState></section>}</div>}
      {route.tab === 'appeals' && <WorkspaceAppeals reviews={workspace.reviews} checklists={workspace.checklists} onOpen={(id, evaluation) => go('dialogs', id, evaluation)} onReview={(evaluation, criterion, appeal) => void openAppeal(evaluation, criterion, appeal)}/>}
      {route.tab === 'brand' && <WorkspaceBrand/>}
      {route.tab === 'audio' && !route.id && <AudioHistory revision={revision} open={id => go('audio', id)}/>}
      {route.tab === 'audio' && route.id && <section className="panel live-panel"><div className="panel-heading"><h2>{t('Задание распознавания')}</h2><button className="button secondary" onClick={() => go('audio')}>{t('История распознавания')}</button></div>
        {jobError && <p role="alert" className="live-error">{t(jobError)}</p>}
        {route.id && !job && !jobError && <p role="status">{t("Загружаем задание…")}</p>}
        {job && <><p><b>ID:</b> {job.id}</p><StatusBadge>{({ queued: t("В очереди"), running: t("Распознаётся"), completed: t("Транскрипт готов"), needs_review: t("Нужно определить роли"), failed: t("Ошибка распознавания") } as const)[job.status]}</StatusBadge>
          <audio controls preload="none" src={api.audioContentUrl(job.asset_id)} className="live-audio"/>
          {job.error_code && <p className="live-error">{t("Распознавание не завершено. Код: {code}. Оценка не создавалась.", { code: job.error_code })}</p>}
          {job.status === 'needs_review' && job.result && <><p className="live-notice">{t("Подтвердите роли всех реплик. После сохранения появится разговор для оценки.")}</p>{job.result.segments.map((s, i) => <label className="form-label" key={i}>{t("{start}–{end} сек.", { start: number(s.start_seconds), end: number(s.end_seconds) })} · {s.text}<select value={roles[i] ?? ''} disabled={jobBusy} onChange={e => setRoles(prev => prev.map((r, j) => i === j ? e.target.value as typeof r : r))}><option value="">{t("Выберите роль")}</option><option value="client">{t("Клиент")}</option><option value="operator">{t("Оператор")}</option></select></label>)}<button className="button primary" disabled={jobBusy || roles.some(r => !r)} onClick={() => void confirmRoles()}>{t("Подтвердить роли")}</button></>}
          {job.result && <div className="live-controls"><button className="button secondary" disabled={jobBusy} onClick={() => void useTranscript(true)}><Download/>{t('Скачать JSON')}</button><button className="button secondary" disabled={jobBusy} onClick={() => void useTranscript(false)}><Plus/>{t('Новый диалог из записи')}</button></div>}
          {job.conversation_id && <button className="button primary" onClick={async () => { await reload(); go('dialogs', job.conversation_id!); }}>{t("Открыть транскрипт для оценки")}</button>}
        </>}
      </section>}
      <footer className="app-footer workspace-footer"><span><i/> EVA · Expert Virtual Auditor</span><span>{t('Серверные данные')} · {t('Оценки требуют проверки человеком')}<a className="product-link" href={`/about/${locale}/`}>{t('О продукте')}</a></span></footer>
    </main></div>
    {modal === 'help' && <WorkspaceHelp onClose={() => setModal(null)} onStart={() => { setModal(null); openAllDialogs(); }}/>}
    {modal === 'search' && <WorkspaceSearch conversations={workspace.conversations} onClose={() => setModal(null)} onOpen={id => { setModal(null); go('dialogs', id); }}/>}
    {modal === 'import' && <ImportDialog initial={importSeed} onClose={() => setModal(null)} onSaved={c => { setWorkspace(w => ({ ...w, conversations: [c, ...w.conversations.filter(old => old.id !== c.id)] })); setModal(null); go('dialogs', c.id); }}/ >}
    {modal === 'audio' && <AudioDialog onClose={() => setModal(null)} onJob={j => { setModal(null); go('audio', j.id); }}/ >}
    {appealContext && <AppealDialog key={`${appealContext.review.evaluation_id}:${appealContext.review.revision}:${appealContext.criterionId}`} review={appealContext.review} conversation={appealContext.snapshot.conversation} checklist={appealContext.snapshot.checklist} criterionId={appealContext.criterionId} appealId={appealContext.appealId} onClose={() => setAppealContext(null)} onSaved={savedReview}/>}
  </div>;
}
