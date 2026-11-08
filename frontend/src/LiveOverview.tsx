import { useId, useMemo, useState } from 'react';
import { ArrowUpRight, AlertTriangle, CalendarDays, ChartNoAxesCombined, GitPullRequest, Info, MessagesSquare, ShieldCheck } from 'lucide-react';
import type { Conversation, Evaluation, ReviewCase } from './api/types';
import { useI18n } from './i18n';
import { summarizeWorkspace, type OverviewPeriod, type QualityDay } from './workspace-analytics';

type Props = {
  conversations: Conversation[];
  evaluations: Evaluation[];
  reviews: Record<string, ReviewCase>;
  loading: boolean;
  onOpen: (id: string) => void;
  onDialogs: () => void;
  onViolations?: () => void;
  onAppeals: () => void;
};

function QualitySeries({ days }: { days: QualityDay[] }) {
  const { t, date, number } = useI18n();
  const fillId = useId();
  const labelId = useId();
  const first = Date.parse(days[0].day);
  const last = Date.parse(days[days.length - 1].day);
  const points = days.map(day => ({ ...day, x: first === last ? 350 : 50 + (Date.parse(day.day) - first) / (last - first) * 590, y: 174 - day.average * 1.44 }));
  const labelIndexes = new Set([0, Math.round((days.length - 1) * .25), Math.round((days.length - 1) * .5), Math.round((days.length - 1) * .75), days.length - 1]);
  const chartDate = (day: string) => date(day + 'T00:00:00Z', { day: 'numeric', month: 'short', timeZone: 'UTC' });
  // A missing day is a gap, not a zero score or an invented interpolated result.
  const segments: typeof points[] = [];
  for (const point of points) {
    const previous = segments[segments.length - 1];
    if (previous && Date.parse(point.day) - Date.parse(previous[previous.length - 1].day) === 86_400_000) previous.push(point);
    else segments.push([point]);
  }
  return <>
    <svg className="live-quality-chart" viewBox="0 0 690 213" role="img" aria-labelledby={labelId}>
      <title id={labelId}>{t('Средний текущий балл по дате оценки. Дни без результатов не соединяются.')}</title>
      <defs><linearGradient id={fillId} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="currentColor" stopOpacity=".18"/><stop offset="100%" stopColor="currentColor" stopOpacity="0"/></linearGradient></defs>
      {[100, 75, 50, 25, 0].map(value => <g key={value}><text x="25" y={178 - value * 1.44} textAnchor="end">{value}</text><line className="quality-gridline" x1="45" x2="650" y1={174 - value * 1.44} y2={174 - value * 1.44}/></g>)}
      {segments.filter(segment => segment.length > 1).map(segment => {
        const path = segment.map((point, index) => `${index ? 'L' : 'M'} ${point.x} ${point.y}`).join(' ');
        return <g key={segment[0].day}><path d={`${path} L ${segment[segment.length - 1].x} 174 L ${segment[0].x} 174 Z`} fill={`url(#${fillId})`}/><path d={path} fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"/></g>;
      })}
      {points.map((point, index) => <g key={point.day}><circle className="quality-point" cx={point.x} cy={point.y} r="5"><title>{chartDate(point.day)}: {number(point.average, { maximumFractionDigits: 2 })} / 100 · {t('Оценок: {count}', { count: point.count })}</title></circle>{labelIndexes.has(index) && <text x={point.x} y="203" textAnchor="middle">{chartDate(point.day)}</text>}</g>)}
    </svg>
    <details className="quality-values"><summary>{t('Значения на графике')}</summary><div className="quality-values-scroll"><table><caption className="sr-only">{t('Текущие баллы по дате оценки · UTC')}</caption><thead><tr><th>{t('Дата')}</th><th>{t('Средний балл')}</th><th>{t('Оценено диалогов')}</th></tr></thead><tbody>{days.map(day => <tr key={day.day}><td>{date(day.day + 'T00:00:00Z', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })}</td><td>{number(day.average, { maximumFractionDigits: 2 })}</td><td>{number(day.count)}</td></tr>)}</tbody></table></div></details>
  </>;
}

export default function LiveOverview({ conversations, evaluations, reviews, loading, onOpen, onDialogs, onViolations, onAppeals }: Props) {
  const { t, number } = useI18n();
  const [period, setPeriod] = useState<OverviewPeriod>('all');
  const summary = useMemo(() => summarizeWorkspace(evaluations, reviews, period), [evaluations, reviews, period]);
  const initialLoading = loading && !conversations.length && !evaluations.length;
  const value = (count: number) => initialLoading ? '—' : number(count);
  const firstEvaluated = summary.latest.find(item => conversations.some(conversation => conversation.id === item.original.conversation_id));
  const openInsight = () => firstEvaluated ? onOpen(firstEvaluated.original.conversation_id) : onDialogs();
  const languages = [...new Set(conversations.map(item => item.language))].sort().join(' · ');

  return <div className="live-overview" aria-busy={loading}>
    <div className="overview-toolbar"><p>{t('По последней оценке каждого диалога')}</p><label className="overview-period"><CalendarDays aria-hidden="true"/><span className="sr-only">{t('Период оценок')}</span><select value={period} onChange={event => setPeriod(event.target.value as OverviewPeriod)}><option value="all">{t('За всё время')}</option><option value="7d">{t('Последние 7 дней')}</option><option value="30d">{t('Последние 30 дней')}</option></select></label></div>
    <div className="metrics-grid">
      <section className="metric-card featured"><div className="metric-top"><span>{t('Средний балл')}</span><span className="metric-icon"><ShieldCheck/></span></div><div className="metric-value">{initialLoading || summary.average === null ? '—' : number(summary.average, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}<small>/ 100</small></div><div className="metric-footer"><span className="metric-chip good">{t('Качество общения')}</span><small>{t('С итоговым баллом: {count}', { count: value(summary.scored) })}</small></div></section>
      <button type="button" className="metric-card metric-button" onClick={onDialogs}><div className="metric-top"><span>{t('Оценено диалогов')}</span><span className="metric-icon"><MessagesSquare/></span></div><div className="metric-value">{value(summary.selected.length)}<small>{t('диалогов')}</small></div><div className="metric-footer"><span className="language-dots">{languages || 'RU · KZ · EN'}</span><small>{summary.unresolved ? t('Неопределённый итог: {count}', { count: value(summary.unresolved) }) : t('за выбранный период')}</small></div></button>
      <button type="button" className="metric-card metric-button" onClick={onViolations ?? onDialogs}><div className="metric-top"><span>{t('Грубые нарушения')}</span><span className="metric-icon danger"><AlertTriangle/></span></div><div className="metric-value">{value(summary.grossErrors)}<small>{t('диалогов')}</small></div><div className="metric-footer"><span className="metric-chip danger">{t('Подтверждённые флаги')}</span>{summary.unknownFlags ? <small>{t('Не проверено: {count}', { count: value(summary.unknownFlags) })}</small> : <ArrowUpRight/>}</div></button>
      <button type="button" className="metric-card metric-button" onClick={onAppeals}><div className="metric-top"><span>{t('Открытые апелляции')}</span><span className="metric-icon amber"><GitPullRequest/></span></div><div className="metric-value">{value(summary.openAppeals)}<small>{t('на пересмотре')}</small></div><div className="metric-footer"><span className="metric-chip warning">{t('Ожидают решения')}</span><small>{t('Сейчас · все периоды')}</small></div></button>
    </div>
    <div className="overview-middle"><section className="panel chart-panel live-chart-panel"><div className="panel-heading"><div><h2>{t('Динамика качества')}</h2><p>{t('Текущие баллы по дате оценки · UTC')}</p></div><span className="chart-legend"><i/>{t('Общая оценка')}</span></div>{summary.series.length ? <QualitySeries days={summary.series}/> : <div className="quality-empty"><span className="quality-empty-icon"><ChartNoAxesCombined/></span><h3>{initialLoading ? t('Загружаем оценки…') : t('Здесь появится динамика качества')}</h3><p>{summary.selected.length ? t('Для графика нужен вычисленный итог. Неопределённые оценки не считаются нулём.') : t('Оцените первый диалог, чтобы увидеть результат и его обоснование.')}</p>{!initialLoading && <button type="button" className="text-button" onClick={() => conversations[0] ? onOpen(conversations[0].id) : onDialogs()}>{t('Перейти к диалогам')}<ArrowUpRight/></button>}</div>}</section>
      <section className="insight-card"><div className="insight-label"><span className="eva-spark" aria-hidden="true">✳</span>EVA INSIGHT<span className="insight-live">{t('AI + человек')}</span></div><h2>{t('За каждой оценкой —')}<br/>{t('живой диалог.')}</h2><p>{t('Замечайте важное.')}<br/>{t('Проверяйте обоснование.')}<br/>{t('Принимайте точные решения.')}</p><img src="/brand/eva-signal-art.svg" alt="" className="signal-art"/><button type="button" onClick={openInsight}>{firstEvaluated ? t('Посмотреть разбор') : t('Открыть диалоги')}<ArrowUpRight/></button></section>
    </div>
    <p className="overview-review-note"><Info aria-hidden="true"/>{t('Баллы предварительные. Проверяйте доказательства; отдельное утверждение оценки ещё не подключено.')}</p>
  </div>;
}
