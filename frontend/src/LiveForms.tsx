import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { ChevronDown, ClipboardList, Info, Plus, RotateCcw, Save, Trash2 } from 'lucide-react';
import { api } from './api/client';
import type { AppealDecision, AudioContentType, Checklist, Conversation, ConversationCreate, ReviewCase, TranscriptionJob } from './api/types';
import { Modal } from './eva-ui';
import { parseTranscript } from './server-input';
import { useI18n } from './i18n';

function useOperation() {
  const last = useRef({ signature: '', id: '' });
  return (payload: unknown) => {
    const signature = JSON.stringify(payload);
    if (last.current.signature !== signature) last.current = { signature, id: crypto.randomUUID() };
    return last.current.id;
  };
}
const speakerLabels = { operator: 'Оператор', client: 'Клиент', bot: 'Бот', system: 'Система', unknown: 'Роль неизвестна' };
const errorText = (error: unknown) => error instanceof Error ? error.message : 'Не удалось сохранить. Попробуйте обновить данные.';

export function ImportDialog({ initial, onClose, onSaved }: { initial?: Omit<ConversationCreate,'operation_id'>; onClose: () => void; onSaved: (c: Conversation) => void }) {
  const { t } = useI18n();
  const [text, setText] = useState(initial ? JSON.stringify(initial, null, 2) : '');
  const [fileName, setFileName] = useState('');
  const fileNameId = useId();
  const [language, setLanguage] = useState<Conversation['language']>(initial?.language ?? 'RU');
  const [channel, setChannel] = useState<Conversation['channel']>(initial?.channel ?? 'agent_chat');
  const [operator,setOperator]=useState('');
  const [lostStart,setLostStart]=useState(false);
  const [lostEnd,setLostEnd]=useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const operation = useOperation();
  const fileSequence = useRef(0);
  useEffect(() => () => { fileSequence.current++; }, []);
  async function submit(e: FormEvent) {
    e.preventDefault(); if (busy) return;
    setError(''); setBusy(true);
    try {
      const envelope = text.trim().startsWith('{') ? JSON.parse(text) : null;
      const source={...envelope?.source,...(operator.trim()?{target_operator_id:operator.trim()}:{}),
        ...(lostStart?{complete_start:false}:{}),...(lostEnd?{complete_end:false}:{})};
      const payload = { channel, language, messages: parseTranscript(text, channel), ...(Object.keys(source).length?{source}:{}) };
      const request: ConversationCreate = { ...payload, operation_id: operation(payload) };
      onSaved(await api.createConversation(request));
    } catch (error) { setError(errorText(error)); } finally { setBusy(false); }
  }
  return <Modal title={t("Добавить переписку")} onClose={() => !busy && onClose()} wide>
    <form onSubmit={submit} className="live-form">
      <p className="modal-description">{t("Диалог сохранится на сервере. Оценку можно запустить после проверки реплик и выбора чек-листа.")}</p>
      <details><summary>{t("Источник разговора")}</summary>
        <label className="form-label">{t("ID оцениваемого оператора")}<input value={operator} disabled={busy} onChange={e=>setOperator(e.target.value)}/></label>
        <label><input type="checkbox" checked={lostStart} disabled={busy} onChange={e=>setLostStart(e.target.checked)}/>{t("Начало записи отсутствует")}</label>
        <label><input type="checkbox" checked={lostEnd} disabled={busy} onChange={e=>setLostEnd(e.target.checked)}/>{t("Конец записи отсутствует")}</label>
      </details>
      <div className="live-fields"><label className="form-label">{t("Язык")}<select aria-label={t("Язык")} value={language} disabled={busy} onChange={e => setLanguage(e.target.value as Conversation['language'])}>{['RU','KZ','EN'].map(l => <option key={l}>{l}</option>)}</select></label>
      <label className="form-label">{t("Канал")}<select aria-label={t("Канал")} value={channel} disabled={busy} onChange={e => setChannel(e.target.value as Conversation['channel'])}><option value="agent_chat">{t("Чат поддержки")}</option><option value="voice_call">{t("Готовый транскрипт звонка")}</option></select></label></div>
      <label className="form-label">{t("JSON или TXT, до 1 МБ")}<span className="live-file-picker" data-disabled={busy || undefined}><input className="sr-only" aria-label={t('Выбрать файл переписки')} aria-describedby={fileNameId} disabled={busy} type="file" accept=".json,.txt" onChange={async e => {
        const sequence = ++fileSequence.current;
        const file = e.target.files?.[0]; setFileName(file?.name ?? ''); if (!file) return;
        if (file.size > 1048576) { setError('Файл превышает 1 МБ.'); return; }
        try {
          const raw = await file.text();
          if (sequence !== fileSequence.current) return;
          setText(raw); setError('');
          if (raw.trimStart().startsWith('{')) {
            const data = JSON.parse(raw);
            if (['RU','KZ','EN'].includes(data.language)) setLanguage(data.language);
            if (['voice_call','agent_chat'].includes(data.channel)) setChannel(data.channel);
          }
        } catch { if (sequence === fileSequence.current) setError('Не удалось прочитать файл.'); }
      }}/><span className="button secondary small" aria-hidden="true">{t('Выбрать файл')}</span><span id={fileNameId} className="live-file-name" aria-live="polite">{fileName || t('Файл не выбран')}</span></span></label>
      <label className="form-label">{t("Переписка")}<textarea rows={10} value={text} disabled={busy} onChange={e => { fileSequence.current++; setText(e.target.value); }} placeholder={t("[00:00] клиент: Здравствуйте!\n[00:05] оператор: Здравствуйте! Чем помочь?")} required/></label>
      <p className="form-hint">{t("TXT: [время] клиент / оператор / бот / система: текст. JSON: массив messages или transcript с полями timestamp, speaker, text. Для чата время MM:SS; для звонка ISO с часовым поясом, например 2026-11-08T10:00:00+05:00.")}</p>
      {error && <p role="alert" className="form-error">{t(error)}</p>}
      <div className="modal-actions"><button type="button" className="button secondary" disabled={busy} onClick={onClose}>{t("Отмена")}</button><button className="button primary" disabled={busy}>{busy ? t("Сохраняем…") : t("Сохранить диалог")}</button></div>
    </form>
  </Modal>;
}

export function ChecklistEditor({ checklist, onSaved }: { checklist: Checklist; onSaved: (c: Checklist) => void }) {
  const { t, number } = useI18n();
  const [draft, setDraft] = useState(() => structuredClone(checklist));
  const [scaleTexts, setScaleTexts] = useState(() => checklist.criteria.map(c => c.scale.join(', ')));
  const [expanded, setExpanded] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const editorId = useId();
  const dirty = JSON.stringify(draft) !== JSON.stringify(checklist);
  const totalWeight = draft.criteria.reduce((sum, c) => sum + (c.type !== 'flag' && Number.isFinite(c.weight) ? c.weight : 0), 0);
  const patch = (index: number, fields: Partial<Checklist['criteria'][number]>) => setDraft(d => ({ ...d, criteria: d.criteria.map((c, i) => i === index ? { ...c, ...fields } : c) }));
  function resetDraft() {
    setDraft(structuredClone(checklist));
    setScaleTexts(checklist.criteria.map(c => c.scale.join(', ')));
    setExpanded([]); setError('');
  }
  function addCriterion() {
    setExpanded(rows => [...rows, draft.criteria.length]);
    setDraft(d => ({ ...d, criteria: [...d.criteria, { id: 'criterion-' + crypto.randomUUID().slice(0, 8), title: '', instruction: '', section_id: null, type: 'boolean', weight: 10, allow_na: false, na_condition: null, scale: [] }] }));
    setScaleTexts(values => [...values, '']);
  }
  async function submit(e: FormEvent) {
    e.preventDefault(); if (busy) return; setError('');
    const invalidRows = draft.criteria.flatMap((c, i) => !c.id.trim() || !c.title.trim() || !c.instruction.trim() || (c.allow_na && !c.na_condition?.trim()) || !Number.isFinite(c.weight) || c.weight < 0 || draft.criteria.some((other, j) => j !== i && other.id === c.id) || (c.type === 'scale' && (c.scale.length < 2 || new Set(c.scale).size !== c.scale.length || c.scale.some(v => !Number.isFinite(v) || v < 0) || Math.max(...c.scale) <= 0)) ? [i] : []);
    if (!draft.name.trim() || !draft.criteria.length || invalidRows.length) {
      setExpanded(rows => [...new Set([...rows, ...invalidRows])]);
      setError('Проверьте уникальность ID, названия, инструкции, веса, ступени шкалы и условия N/A.'); return;
    }
    setBusy(true);
    try { onSaved(await api.publishChecklist(checklist.id, { base_version: checklist.version, name: draft.name, criteria: draft.criteria })); }
    catch (error) { setError(errorText(error)); } finally { setBusy(false); }
  }
  return <form id="eva-checklist-editor" onSubmit={submit} className="live-form checklist-workbench" aria-busy={busy}>
    <div className="checklist-summary">
      <span className="checklist-summary-icon"><ClipboardList aria-hidden="true"/></span>
      <div className="checklist-summary-copy">
        <div className="checklist-summary-title"><label><span className="sr-only">{t('Название чек-листа')}</span><input value={draft.name} required disabled={busy} onChange={e => setDraft(d => ({ ...d, name: e.target.value }))}/></label><span className="checklist-version">{t('Версия {version}', { version: number(checklist.version) })}</span></div>
        <p>{t('Чаты и звонки · RU / KZ / EN')}</p>
      </div>
      <div className="checklist-weight-total"><span>{t('Сумма весов')}</span><div><strong>{number(totalWeight)}</strong><small>{t('баллов')}</small></div></div>
    </div>
    <section className="criteria-sheet" aria-labelledby={`${editorId}-title`}>
      <div className="criteria-sheet-heading"><div><h2 id={`${editorId}-title`}>{t('Критерии оценки')}<span className="count-badge">{number(draft.criteria.length)}</span></h2><p>{t('Меняйте формулировки, веса и типы ответа без изменения кода.')}</p></div><button type="button" disabled={busy} className="button secondary small" onClick={addCriterion}><Plus/>{t('Добавить критерий')}</button></div>
      <div className="criteria-column-head" aria-hidden="true"><span>{t('Критерий и формулировка')}</span><span>{t('Тип ответа')}</span><span>{t('Вес')}</span><span>N/A</span></div>
      {draft.criteria.map((c, i) => {
        const open = expanded.includes(i);
        const label = t('Критерий {number}', { number: number(i + 1) });
        return <fieldset className="criterion-edit-row" key={i} disabled={busy}><legend className="sr-only">{label}</legend>
          <div className="criterion-edit-copy"><span className="criterion-edit-number" aria-hidden="true">{number(i + 1, { minimumIntegerDigits: 2 })}</span><div>
            <label className="criterion-title-field"><span className="sr-only">{t('Название')} · {label}</span><input value={c.title} required placeholder={t('Название критерия')} onChange={e => patch(i, { title: e.target.value })}/></label>
            <label className="criterion-instruction-field"><span className="sr-only">{t('Инструкция оценки')} · {label}</span><textarea rows={2} required value={c.instruction} placeholder={t('Что должен сделать оператор?')} onChange={e => patch(i, { instruction: e.target.value })}/></label>
            <button type="button" className="criterion-details-toggle" aria-expanded={open} aria-controls={`${editorId}-details-${i}`} onClick={() => setExpanded(rows => open ? rows.filter(n => n !== i) : [...rows, i])}>{t('Настройки критерия')}<ChevronDown aria-hidden="true"/></button>
          </div></div>
          <label className="criterion-control"><span className="criterion-control-label">{t('Тип ответа')}</span><select aria-label={`${t('Тип ответа')} · ${label}`} value={c.type} onChange={e => {
            const type = e.target.value as typeof c.type;
            setScaleTexts(values => values.map((v, j) => i === j ? type === 'scale' ? '0, 1' : '' : v));
            patch(i, { type, scale: type === 'scale' ? [0, 1] : [], ...(type === 'flag' ? { weight: 0, allow_na: false, na_condition: null } : {}) });
            if (type === 'scale') setExpanded(rows => [...new Set([...rows, i])]);
          }}><option value="boolean">{t('Да / нет')}</option><option value="scale">{t('Шкала')}</option><option value="flag">{t('Грубая ошибка')}</option></select>{c.type === 'scale' && <small>{t('Макс.: {value}', { value: c.scale.every(Number.isFinite) && c.scale.length ? number(Math.max(...c.scale)) : '—' })}</small>}</label>
          <label className="criterion-control"><span className="criterion-control-label">{t('Вес')}</span><input aria-label={`${t('Вес')} · ${label}`} type="number" min="0" step="any" required value={c.weight} disabled={c.type === 'flag'} onChange={e => patch(i, { weight: Number(e.target.value) })}/></label>
          <label className="criterion-na-control"><span className="criterion-control-label">N/A</span><input type="checkbox" role="switch" aria-label={`${t('Допускать N/A')} · ${label}`} checked={c.allow_na} disabled={c.type === 'flag'} onChange={e => { patch(i, { allow_na: e.target.checked, na_condition: e.target.checked ? '' : null }); if (e.target.checked) setExpanded(rows => [...new Set([...rows, i])]); }}/><span className="criterion-switch-track" aria-hidden="true"/></label>
          {open && <div id={`${editorId}-details-${i}`} className="criterion-advanced">
            <div className="live-fields"><label className="form-label">ID<input required value={c.id} onChange={e => patch(i, { id: e.target.value })}/></label><label className="form-label">{t('Раздел')}<input value={c.section_id ?? ''} onChange={e => patch(i, { section_id: e.target.value || null })}/></label>
              {c.type === 'scale' && <label className="form-label">{t('Допустимые ступени через запятую')}<input required value={scaleTexts[i] ?? ''} onChange={e => { const text = e.target.value; setScaleTexts(values => values.map((v, j) => i === j ? text : v)); patch(i, { scale: text.split(',').map(v => v.trim() ? Number(v.trim()) : NaN) }); }}/></label>}
            </div>
            {c.allow_na && <label className="form-label">{t('Когда критерий неприменим')}<textarea required rows={2} value={c.na_condition ?? ''} onChange={e => patch(i, { na_condition: e.target.value })}/></label>}
            <button type="button" className="criterion-remove button secondary small" onClick={() => { setDraft(d => ({ ...d, criteria: d.criteria.filter((_, j) => j !== i) })); setScaleTexts(values => values.filter((_, j) => j !== i)); setExpanded(rows => rows.filter(n => n !== i).map(n => n > i ? n - 1 : n)); }}><Trash2/>{t('Удалить критерий')}</button>
          </div>}
        </fieldset>;
      })}
      {!draft.criteria.length && <p className="criteria-empty">{t('Добавьте хотя бы один критерий для публикации.')}</p>}
      <div className="criteria-sheet-note"><Info aria-hidden="true"/><p>{t('N/A не снижает оценку: вес неприменимого критерия распределяется между остальными. Грубые нарушения отмечаются отдельно. Прежние оценки сохраняют свою версию чек-листа.')}</p></div>
    </section>
    {error && <p role="alert" className="form-error">{t(error)}</p>}
    <div className="checklist-publish-bar"><p aria-live="polite">{dirty ? t('Есть неопубликованные изменения.') : t('Новая версия сохранится на сервере после публикации.')}</p><div><button type="button" className="button secondary" disabled={busy || !dirty} onClick={resetDraft}><RotateCcw/>{t('Отменить изменения')}</button><button className="button primary" disabled={busy}><Save/>{busy ? t('Публикуем…') : t('Опубликовать версию {version}', { version: number(checklist.version + 1) })}</button></div></div>
  </form>;
}

export function AppealDialog({ review, conversation, checklist, criterionId, appealId, onClose, onSaved }: {
  review: ReviewCase; conversation: Conversation; checklist: Checklist; criterionId: string; appealId?: string; onClose: () => void; onSaved: (r: ReviewCase) => void;
}) {
  const { t, number } = useI18n();
  const criterion = checklist.criteria.find(c => c.id === criterionId)!;
  const existing = review.appeals.find(a => a.id === appealId);
  const [reason, setReason] = useState('');
  const [ids, setIds] = useState<string[]>(existing?.message_ids ?? []);
  const [decision, setDecision] = useState<'accepted' | 'rejected'>('rejected');
  const [value, setValue] = useState('');
  const [kind, setKind] = useState<'message' | 'missing_action'>('message');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const operation = useOperation();
  async function submit(e: FormEvent) {
    e.preventDefault(); if (busy) return; setError('');
    if (!reason.trim() || (!ids.length && (!appealId || decision === 'accepted')) || (appealId && decision === 'accepted' && !value)) { setError('Укажите причину, решение и подтверждающие реплики.'); return; }
    setBusy(true);
    try {
      if (appealId) {
        const override = decision === 'accepted' ? { criterion_id: criterionId, status: 'scored' as const, value: value === 'na' ? null : criterion.type === 'scale' ? Number(value) : value === 'true', reason: reason.trim(), evidence: [{ kind: value === 'na' ? 'not_applicable' as const : kind, message_ids: ids }] } : null;
        const fields = { expected_revision: review.revision, reason: reason.trim() };
        const payload = override ? { ...fields, decision: 'accepted' as const, override } : { ...fields, decision: 'rejected' as const };
        const request: AppealDecision = { ...payload, operation_id: operation(payload) };
        onSaved(await api.decideAppeal(appealId, request));
      } else {
        const payload = { expected_revision: review.revision, criterion_id: criterionId, reason: reason.trim(), message_ids: ids };
        onSaved(await api.createAppeal(review.evaluation_id, { ...payload, operation_id: operation(payload) }));
      }
    } catch (error) { setError(errorText(error)); } finally { setBusy(false); }
  }
  return <Modal title={appealId ? t("Решение по апелляции") : t("Подать апелляцию")} onClose={() => !busy && onClose()} wide><form onSubmit={submit} className="live-form">
    <strong>{criterion.title}</strong><p className="form-hint">{t('Ревизия {revision}. Изменение сохранится на сервере с причиной и доказательствами.', { revision: number(review.revision) })}</p>
    {existing && <p className="live-notice">{t('Причина обращения: {reason}', { reason: existing.reason })}</p>}
    {appealId && <><label className="form-label">{t("Решение")}<select disabled={busy} value={decision} onChange={e => setDecision(e.target.value as typeof decision)}><option value="rejected">{t("Отклонить — оставить текущее значение")}</option><option value="accepted">{t("Принять — изменить значение")}</option></select></label>
      {decision === 'accepted' && <><label className="form-label">{t("Новое значение")}<select required disabled={busy} value={value} onChange={e => setValue(e.target.value)}><option value="">{t("Выберите значение")}</option>{criterion.type === 'scale' ? criterion.scale.map(v => <option key={v} value={v}>{number(v)}</option>) : <><option value="true">{t("Да")}</option><option value="false">{t("Нет")}</option></>}{criterion.allow_na && <option value="na">N/A</option>}</select></label>{value === 'na' ? <p className="form-hint">{t("Условие N/A:")} {criterion.na_condition}</p> : <label className="form-label">{t("Основание")}<select value={kind} onChange={e => setKind(e.target.value as typeof kind)}><option value="message">{t("Сказанное в репликах")}</option><option value="missing_action">{t("Участок, где ожидалось действие")}</option></select></label>}</>}
    </>}
    <label className="form-label">{t("Причина")}<textarea required maxLength={4000} disabled={busy} value={reason} onChange={e => setReason(e.target.value)} rows={3}/></label>
    {(!appealId || decision === 'accepted') && <fieldset className="live-evidence-picker" disabled={busy}><legend>{t("Подтверждающие реплики")}</legend>{conversation.messages.map(m => <label key={m.id} className="live-checkbox"><input type="checkbox" checked={ids.includes(m.id)} onChange={e => setIds(prev => e.target.checked ? [...prev, m.id] : prev.filter(id => id !== m.id))}/><span><b>{m.timestamp} · {t(speakerLabels[m.speaker])}</b><br/>{m.text}</span></label>)}</fieldset>}
    {error && <p role="alert" className="form-error">{t(error)}</p>}
    <div className="modal-actions"><button type="button" disabled={busy} onClick={onClose} className="button secondary">{t("Отмена")}</button><button disabled={busy} className="button primary">{busy ? t("Сохраняем…") : t("Сохранить")}</button></div>
  </form></Modal>;
}

export function AudioDialog({ onClose, onJob }: { onClose: () => void; onJob: (job: TranscriptionJob) => void }) {
  const { t } = useI18n();
  const [file, setFile] = useState<File | null>(null);
  const fileNameId = useId();
  const [assetId, setAssetId] = useState('');
  const [language, setLanguage] = useState<Conversation['language']>('RU');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const uploadId = useRef(crypto.randomUUID());
  const operation = useOperation();
  async function submit(e: FormEvent) {
    e.preventDefault(); if (busy || !file) return; setError(''); setBusy(true);
    try {
      const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
      const mimeByExtension: Record<string, AudioContentType> = { wav: 'audio/wav', mp3: 'audio/mpeg', flac: 'audio/flac', ogg: 'audio/ogg' };
      const asset = assetId || (await api.uploadAudio(file, uploadId.current, mimeByExtension[extension])).id;
      setAssetId(asset);
      const payload = { asset_id: asset, language };
      onJob(await api.createTranscription({ ...payload, operation_id: operation(payload) }));
    } catch (error) { setError(errorText(error)); } finally { setBusy(false); }
  }
  return <Modal title={t("Загрузить аудиозапись")} onClose={() => !busy && onClose()}><form className="live-form" onSubmit={submit}>
    <p className="modal-description">{t("WAV, MP3, FLAC или Ogg до 25 МБ. Распознавание требует подключённого локального ASR. Если он недоступен, файл сохранится отдельно без выдуманного транскрипта.")}</p>
    <label className="form-label">{t("Запись")}<span className="live-file-picker" data-disabled={busy || undefined}><input className="sr-only" aria-label={t('Выбрать аудиофайл')} aria-describedby={fileNameId} type="file" accept=".wav,.mp3,.flac,.ogg" required disabled={busy} onChange={e => { setFile(e.target.files?.[0] ?? null); setAssetId(''); uploadId.current = crypto.randomUUID(); setError(''); }}/><span className="button secondary small" aria-hidden="true">{t('Выбрать файл')}</span><span id={fileNameId} className="live-file-name" aria-live="polite">{file?.name || t('Файл не выбран')}</span></span></label>
    <label className="form-label">{t("Язык")}<select disabled={busy} value={language} onChange={e => setLanguage(e.target.value as typeof language)}>{['RU','KZ','EN'].map(l => <option key={l}>{l}</option>)}</select></label>
    {assetId && <p className="live-notice">{t('Файл сохранён. ID: {id}', { id: assetId })}</p>}
    {error && <p role="alert" className="form-error">{t(error)}</p>}
    <div className="modal-actions"><button className="button primary" disabled={busy || !file}>{busy ? t("Обрабатываем…") : assetId ? t("Запросить распознавание") : t("Загрузить и распознать")}</button></div>
  </form></Modal>;
}
