import { useState } from 'react';
import { ArrowRight, ArrowUpRight, Download, GitPullRequest, Headphones, Info, MessageSquare, Search, ShieldCheck } from 'lucide-react';
import type { Checklist, Conversation, Evaluation, ReviewCase } from './api/types';
import { EmptyState, EvaLogo, Modal, Score, StatusBadge } from './eva-ui';
import { useI18n } from './i18n';
import { displayTime } from './server-input';
import { hasOpenAppeal } from './workspace-analytics';

export function latestRecord(records: Evaluation[], id: string) {
  return records.filter(record => record.original.conversation_id === id)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at) || b.id.localeCompare(a.id))[0];
}
export function conversationState(record?: Evaluation, review?: ReviewCase) {
  const scoring = review ? review.final.scoring : record?.scoring;
  return {
    scoring,
    appealed: !!review?.appeals.some(appeal => appeal.status === 'open'),
    violation: scoring?.has_gross_error === true,
  };
}
export function matchesConversation(conversation: Conversation, query: string) {
  const search = query.trim().toLocaleLowerCase();
  return !search || conversation.id.toLocaleLowerCase().includes(search) || conversation.messages.some(message => message.text.toLocaleLowerCase().includes(search));
}
export function WorkspaceTable({ items, evaluations, reviews, loading, onOpen }: {
  items: Conversation[]; evaluations: Evaluation[]; reviews: Record<string, ReviewCase>; loading: boolean; onOpen: (id: string) => void;
}) {
  const { t, number } = useI18n();
  if (!items.length) return <EmptyState title={loading ? t('Загружаем диалоги…') : t('Диалоги не найдены')}>{t('Добавьте переписку или измените фильтры.')}</EmptyState>;
  return <div className="table-scroll"><table className="conversation-table"><thead><tr><th>{t('Диалог')}</th><th>{t('Канал')}</th><th>{t('Язык')}</th><th>{t('Реплики')}</th><th>{t('Оценка')}</th><th>{t('Статус')}</th><th><span className="sr-only">{t('Открыть')}</span></th></tr></thead><tbody>{items.map(conversation => {
    const record = latestRecord(evaluations, conversation.id);
    const { scoring, violation } = conversationState(record, record && reviews[record.id]);
    const appealed = hasOpenAppeal(conversation.id, reviews);
    const preview = conversation.messages.find(message => message.speaker === 'client')?.text ?? '';
    const status = violation ? 'Нарушение' : appealed ? 'На пересмотре' : scoring?.status === 'needs_review' ? 'Нужна проверка' : scoring?.status === 'not_calculable' ? 'Не рассчитано' : record ? 'Предварительная оценка' : 'Без оценки';
    return <tr key={conversation.id}>
      <td><button className="topic-link" onClick={() => onOpen(conversation.id)}><span className={'channel-icon ' + (conversation.channel === 'voice_call' ? 'voice' : '')}>{conversation.channel === 'voice_call' ? <Headphones/> : <MessageSquare/>}</span><span><strong>{conversation.id}</strong><small className="conversation-preview">{preview.slice(0, 100) || t('Готов к оценке')}</small></span></button></td>
      <td className="muted">{t(conversation.channel === 'voice_call' ? 'Звонок' : 'Чат')}</td><td><span className="language-tag">{conversation.language}</span></td><td className="muted">{number(conversation.messages.length)}</td>
      <td><Score value={scoring?.total ?? null} compact/></td><td><StatusBadge kind={violation ? 'danger' : appealed || scoring?.status === 'needs_review' ? 'warning' : record ? 'info' : 'neutral'}>{t(status)}</StatusBadge></td>
      <td><button className="icon-button row-open" aria-label={t('Открыть {id}', { id: conversation.id })} onClick={() => onOpen(conversation.id)}><ArrowUpRight/></button></td>
    </tr>;
  })}</tbody></table></div>;
}

export function WorkspaceAppeals({ reviews, checklists, onOpen, onReview }: {
  reviews: Record<string, ReviewCase>; checklists: Checklist[]; onOpen: (id: string, evaluation: string) => void; onReview: (evaluation: string, criterion: string, appeal: string) => void;
}) {
  const { t, number } = useI18n();
  const [filter, setFilter] = useState<'open' | 'resolved'>('open');
  const entries = Object.values(reviews).flatMap(review => review.appeals.map(appeal => ({ review, appeal }))).sort((a, b) => Date.parse(b.appeal.created_at) - Date.parse(a.appeal.created_at));
  const open = entries.filter(({ appeal }) => appeal.status === 'open');
  const resolved = entries.filter(({ appeal }) => appeal.status !== 'open');
  const visible = filter === 'open' ? open : resolved;
  return <div className="live-appeals-page page-enter">
    <section className="appeals-intro"><div><GitPullRequest/><div><b>{t('Проверка человеком — часть процесса.')}</b><small>{t('Исходная оценка, доказательства и решение остаются в истории.')}</small></div></div><div><strong>{number(open.length)}</strong><span>{t('ожидают решения')}</span></div><div><strong>{number(resolved.length)}</strong><span>{t('рассмотрено')}</span></div></section>
    <div className="filter-tabs appeal-tabs" role="group" aria-label={t('Статус апелляций')}><button className={filter === 'open' ? 'selected' : ''} aria-pressed={filter === 'open'} onClick={() => setFilter('open')}>{t('Открытые')} <span>{number(open.length)}</span></button><button className={filter === 'resolved' ? 'selected' : ''} aria-pressed={filter === 'resolved'} onClick={() => setFilter('resolved')}>{t('Рассмотренные')} <span>{number(resolved.length)}</span></button></div>
    {!visible.length && <section className="panel"><EmptyState title={filter === 'open' ? t('Все обращения рассмотрены') : t('Решений пока нет')}>{t('Откройте оценку диалога и выберите спорный критерий.')}</EmptyState></section>}
    <div className="appeals-list">{visible.map(({ review, appeal }) => {
      const original = review.original.original;
      const criterion = checklists.find(checklist => checklist.id === original.checklist_id && checklist.version === original.checklist_version)?.criteria.find(item => item.id === appeal.criterion_id);
      return <article className="panel appeal-card" key={appeal.id}>
        <div className="appeal-card-top"><span className="appeal-id">{appeal.id.slice(0, 8)} <span>·</span> {displayTime(appeal.created_at)}</span><StatusBadge kind={appeal.status === 'open' ? 'warning' : 'info'}>{t(appeal.status === 'open' ? 'Ожидает решения' : appeal.status === 'accepted' ? 'Принята' : 'Отклонена')}</StatusBadge></div>
        <h2>{criterion?.title ?? appeal.criterion_id}</h2><button className="appeal-conversation-link" onClick={() => onOpen(original.conversation_id, review.evaluation_id)}>{original.conversation_id} <ArrowUpRight/></button>
        <p className="appeal-comment">{appeal.reason}</p>
        {review.decisions.filter(decision => decision.appeal_id === appeal.id).map(decision => <div className="decision-reason" key={decision.id}><ShieldCheck/><span><b>{t('Решение проверяющего')}</b>{decision.reason}</span></div>)}
        <div className="appeal-card-bottom"><button className="text-button" onClick={() => onOpen(original.conversation_id, review.evaluation_id)}>{t('Открыть оценку')}<ArrowUpRight/></button><div className="appeal-scores"><span>{t('Исходный итог')}<b>{number(review.original.scoring.total)}</b></span>{appeal.status !== 'open' && <><ArrowRight/><span>{t('Текущий балл')}<b>{number(review.final.scoring.total)}</b></span></>}</div>{appeal.status === 'open' && <button className="button primary" onClick={() => onReview(review.evaluation_id, appeal.criterion_id, appeal.id)}>{t('Рассмотреть')}<ArrowRight/></button>}</div>
      </article>;
    })}</div>
  </div>;
}

export function WorkspaceHelp({ onClose, onStart }: { onClose: () => void; onStart: () => void }) {
  const { t } = useI18n();
  return <Modal title={t('Знакомство с EVA')} onClose={onClose}><div className="workspace-help"><EvaLogo/><p className="modal-description">{t('EVA помогает оценивать качество диалогов, проверять доказательства и сохранять историю решений.')}</p><ol className="workspace-help-steps">{[
    ['Откройте диалог', 'Посмотрите общий балл и оценки по критериям.'],
    ['Проверьте обоснование', 'Нажмите на доказательство, чтобы перейти к нужной реплике.'],
    ['Разберите спорный балл', 'Откройте апелляцию, укажите решение и проверьте новый итог.'],
  ].map(([title, text], index) => <li key={title}><span>{String(index + 1).padStart(2, '0')}</span><div><b>{t(title)}</b><p>{t(text)}</p></div></li>)}</ol><div className="workspace-help-note"><Info/><p>{t('Рабочее пространство подключено к серверу. Автоматические оценки предварительные и требуют проверки человеком. Распознавание аудио доступно при подключённом ASR.')}</p></div><button className="button primary full-width" onClick={onStart}>{t('Открыть диалоги')}<ArrowRight/></button></div></Modal>;
}

export function WorkspaceSearch({ conversations, onClose, onOpen }: { conversations: Conversation[]; onClose: () => void; onOpen: (id: string) => void }) {
  const { t, number } = useI18n();
  const [query, setQuery] = useState('');
  const matches = conversations.filter(conversation => matchesConversation(conversation, query));
  return <Modal title={t('Поиск диалога')} onClose={onClose}><div className="workspace-search"><label className="search-field"><Search/><input autoFocus aria-label={t('ID или текст диалога')} placeholder={t('ID или текст диалога')} value={query} onChange={event => setQuery(event.target.value)}/></label><p className="form-hint" role="status">{t('Найдено: {count}', { count: number(matches.length) })}</p><div className="workspace-search-results">{matches.slice(0, 30).map(conversation => <button key={conversation.id} onClick={() => onOpen(conversation.id)}><MessageSquare/><span><b>{conversation.id}</b><small>{conversation.messages.find(message => message.speaker === 'client')?.text.slice(0, 100)}</small></span><ArrowUpRight/></button>)}</div>{!matches.length && <EmptyState title={t('Диалоги не найдены')}>{t('Попробуйте изменить запрос или сбросить фильтры.')}</EmptyState>}</div></Modal>;
}

export function WorkspaceBrand() {
  const { t } = useI18n();
  return <div className="brand-grid page-enter workspace-brand">
    <section className="brand-showcase panel"><span className="mini-eyebrow">{t('01 / ЛОГОТИП')}</span><img src="/brand/eva-logo.png" alt="EVA — Expert Virtual Auditor"/><div><p>{t('Сигнал. Проверка. Ясность.')}</p><a className="text-button" href="/brand/eva-logo.png" download>{t('Скачать PNG')}<Download/></a></div></section>
    <section className="brand-voice"><span className="mini-eyebrow">{t('02 / ХАРАКТЕР')}</span><h2>{t('Внимание')}<br/>{t('к каждому')}<br/><em>{t('диалогу.')}</em></h2><img src="/brand/eva-signal-art.svg" alt=""/></section>
    <section className="panel palette-panel"><span className="mini-eyebrow">{t('03 / ПАЛИТРА')}</span><h2>{t('Зелёный, который говорит о качестве.')}</h2><div className="swatches">{[{ name: 'Forest', hex: '#10251D' }, { name: 'Freedom green', hex: '#00B849' }, { name: 'Signal lime', hex: '#C8F06D' }, { name: 'Charcoal', hex: '#20231F' }].map(color => <div key={color.hex}><span style={{ background: color.hex }}/><b>{color.name}</b><small>{color.hex}</small></div>)}</div></section>
    <section className="panel type-panel"><span className="mini-eyebrow">{t('04 / ТИПОГРАФИКА')}</span><div className="type-sample">Aa Бб Әә</div><h2>Open Sans</h2><p>{t('Спокойный ритм, читаемые числа и поддержка трёх языков.')}</p><div className="font-weights"><span>Regular</span><b>Semibold</b><strong>Bold</strong></div></section>
    <section className="panel brand-download"><div><h2>{t('Материалы EVA')}</h2><p>{t('Логотипы и графика, которые используются в интерфейсе.')}</p></div><div className="brand-asset-links"><a className="button secondary" href="/brand/eva-signal-art.svg" download><Download/>{t('Графика EVA')}</a><a className="button secondary" href="/brand/freedom-broker.png" download><Download/>Freedom Broker</a><a className="button primary" href="/brand/eva-logo.png" download><Download/>{t('Логотип EVA')}</a></div></section>
  </div>;
}
