import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import { api } from './api/client';
import type { AppealDecision, AudioContentType, Checklist, Conversation, ConversationCreate, ReviewCase, TranscriptionJob } from './api/types';
import { Modal } from './eva-ui';
import { parseTranscript } from './server-input';

function useOperation() {
  const last = useRef({ signature: '', id: '' });
  return (payload: unknown) => {
    const signature = JSON.stringify(payload);
    if (last.current.signature !== signature) last.current = { signature, id: crypto.randomUUID() };
    return last.current.id;
  };
}
const errorText = (error: unknown) => error instanceof Error ? error.message : 'Не удалось сохранить. Попробуйте обновить данные.';

export function ImportDialog({ onClose, onSaved }: { onClose: () => void; onSaved: (c: Conversation) => void }) {
  const [text, setText] = useState('');
  const [language, setLanguage] = useState<Conversation['language']>('RU');
  const [channel, setChannel] = useState<Conversation['channel']>('agent_chat');
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
  return <Modal title="Добавить переписку" onClose={() => !busy && onClose()} wide>
    <form onSubmit={submit} className="live-form">
      <details><summary>Оператор и полнота записи</summary><label className="form-label">ID оцениваемого оператора<input value={operator} onChange={e=>setOperator(e.target.value)}/></label>
        <label><input type="checkbox" checked={lostStart} onChange={e=>setLostStart(e.target.checked)}/> Утрачено начало</label>
        <label><input type="checkbox" checked={lostEnd} onChange={e=>setLostEnd(e.target.checked)}/> Утрачен конец</label></details>
      <p className="modal-description">Диалог сохранится на сервере. Оценку можно запустить после проверки реплик и выбора чек-листа.</p>
      <div className="live-fields"><label className="form-label">Язык<select value={language} disabled={busy} onChange={e => setLanguage(e.target.value as Conversation['language'])}>{['RU','KZ','EN'].map(l => <option key={l}>{l}</option>)}</select></label>
      <label className="form-label">Канал<select value={channel} disabled={busy} onChange={e => setChannel(e.target.value as Conversation['channel'])}><option value="agent_chat">Чат поддержки</option><option value="voice_call">Готовый транскрипт звонка</option></select></label></div>
      <label className="form-label">JSON или TXT, до 1 МБ<input disabled={busy} type="file" accept=".json,.txt" onChange={async e => {
        const sequence = ++fileSequence.current;
        const file = e.target.files?.[0]; if (!file) return;
        if (file.size > 1048576) { setError('Файл превышает 1 МБ.'); return; }
        try { const text = await file.text(); if (sequence === fileSequence.current) { setText(text); setError(''); } } catch { if (sequence === fileSequence.current) setError('Не удалось прочитать файл.'); }
      }}/></label>
      <label className="form-label">Переписка<textarea rows={10} value={text} disabled={busy} onChange={e => { fileSequence.current++; setText(e.target.value); }} placeholder={'[00:00] клиент: Здравствуйте!\n[00:05] оператор: Здравствуйте! Чем помочь?'} required/></label>
      <p className="form-hint">TXT: [время] клиент / оператор / бот / система: текст. JSON: массив messages или transcript с полями timestamp, speaker, text. Для чата время MM:SS; для звонка ISO с часовым поясом, например 2026-11-08T10:00:00+05:00.</p>
      {error && <p role="alert" className="form-error">{error}</p>}
      <div className="modal-actions"><button type="button" className="button secondary" disabled={busy} onClick={onClose}>Отмена</button><button className="button primary" disabled={busy}>{busy ? 'Сохраняем…' : 'Сохранить диалог'}</button></div>
    </form>
  </Modal>;
}

export function ChecklistEditor({ checklist, onSaved }: { checklist: Checklist; onSaved: (c: Checklist) => void }) {
  const [draft, setDraft] = useState(() => structuredClone(checklist));
  const [scaleTexts, setScaleTexts] = useState(() => checklist.criteria.map(c => c.scale.join(', ')));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const patch = (index: number, fields: Partial<Checklist['criteria'][number]>) => setDraft(d => ({ ...d, criteria: d.criteria.map((c, i) => i === index ? { ...c, ...fields } : c) }));
  async function submit(e: FormEvent) {
    e.preventDefault(); if (busy) return; setError('');
    if (!draft.criteria.length || draft.criteria.some(c => !c.id.trim() || !c.title.trim() || !c.instruction.trim() || (c.allow_na && !c.na_condition?.trim()) || !Number.isFinite(c.weight) || c.weight < 0 || (c.type === 'scale' && (c.scale.length < 2 || new Set(c.scale).size !== c.scale.length || c.scale.some(v => !Number.isFinite(v) || v < 0) || Math.max(...c.scale) <= 0))) || new Set(draft.criteria.map(c => c.id)).size !== draft.criteria.length) {
      setError('Проверьте уникальность ID, названия, инструкции, веса, ступени шкалы и условия N/A.'); return;
    }
    setBusy(true);
    try { onSaved(await api.publishChecklist(checklist.id, { base_version: checklist.version, name: draft.name, criteria: draft.criteria })); }
    catch (error) { setError(errorText(error)); } finally { setBusy(false); }
  }
  return <form onSubmit={submit} className="live-form">
    <div className="live-notice">Новая версия не изменит прежние оценки. Для повторной оценки версия выбирается явно.</div>
    <label className="form-label">Название<input value={draft.name} required disabled={busy} onChange={e => setDraft(d => ({ ...d, name: e.target.value }))}/></label>
    {draft.criteria.map((c, i) => <fieldset className="live-criterion-editor" key={i} disabled={busy}><legend>Критерий {i + 1}</legend>
      <div className="live-fields"><label className="form-label">ID<input required value={c.id} onChange={e => patch(i, { id: e.target.value })}/></label><label className="form-label">Название<input required value={c.title} onChange={e => patch(i, { title: e.target.value })}/></label></div>
      <label className="form-label">Инструкция оценки<textarea required rows={3} value={c.instruction} onChange={e => patch(i, { instruction: e.target.value })}/></label>
      <div className="live-fields"><label className="form-label">Тип<select value={c.type} onChange={e => { const type = e.target.value as typeof c.type; setScaleTexts(values => values.map((v, j) => i === j ? type === 'scale' ? '0, 1' : '' : v)); patch(i, { type, scale: type === 'scale' ? [0, 1] : [], ...(type === 'flag' ? { weight: 0, allow_na: false, na_condition: null } : {}) }); }}><option value="boolean">Да / нет</option><option value="scale">Шкала</option><option value="flag">Грубая ошибка</option></select></label>
      <label className="form-label">Вес<input type="number" min="0" step="any" required value={c.weight} disabled={c.type === 'flag'} onChange={e => patch(i, { weight: Number(e.target.value) })}/></label>
      <label className="form-label">Зависит от критериев<select multiple value={c.depends_on ?? []} onChange={e=>patch(i,{depends_on:Array.from(e.target.selectedOptions,o=>o.value)})}>{draft.criteria.filter(other=>other.id!==c.id).map(other=><option key={other.id} value={other.id}>{other.title || other.id}</option>)}</select></label>
      <label className="form-label">Раздел<input value={c.section_id ?? ''} onChange={e => patch(i, { section_id: e.target.value || null })}/></label></div>
      {c.type === 'scale' && <label className="form-label">Допустимые ступени через запятую<input required value={scaleTexts[i] ?? ''} onChange={e => { const text = e.target.value; setScaleTexts(values => values.map((v, j) => i === j ? text : v)); patch(i, { scale: text.split(',').map(v => v.trim() ? Number(v.trim()) : NaN) }); }}/></label>}
      <label className="live-checkbox"><input type="checkbox" checked={c.allow_na} disabled={c.type === 'flag'} onChange={e => patch(i, { allow_na: e.target.checked, na_condition: e.target.checked ? '' : null })}/>Допускать N/A</label>
      {c.allow_na && <label className="form-label">Когда критерий неприменим<textarea required value={c.na_condition ?? ''} onChange={e => patch(i, { na_condition: e.target.value })}/></label>}
      <button type="button" className="button secondary small" onClick={() => { setDraft(d => ({ ...d, criteria: d.criteria.filter((_, j) => j !== i) })); setScaleTexts(values => values.filter((_, j) => j !== i)); }}><Trash2/>Удалить критерий</button>
    </fieldset>)}
    <button type="button" disabled={busy} className="button secondary" onClick={() => { setDraft(d => ({ ...d, criteria: [...d.criteria, { id: 'criterion-' + crypto.randomUUID().slice(0, 8), title: '', instruction: '', section_id: null, type: 'boolean', weight: 10, allow_na: false, na_condition: null, scale: [] }] })); setScaleTexts(values => [...values, '']); }}><Plus/>Добавить критерий</button>
    {error && <p role="alert" className="form-error">{error}</p>}
    <div className="modal-actions"><button className="button primary" disabled={busy}>{busy ? 'Публикуем…' : 'Опубликовать новую версию'}</button></div>
  </form>;
}

export function AppealDialog({ review, conversation, checklist, criterionId, appealId, onClose, onSaved }: {
  review: ReviewCase; conversation: Conversation; checklist: Checklist; criterionId: string; appealId?: string; onClose: () => void; onSaved: (r: ReviewCase) => void;
}) {
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
  return <Modal title={appealId ? 'Решение по апелляции' : 'Подать апелляцию'} onClose={() => !busy && onClose()} wide><form onSubmit={submit} className="live-form">
    <strong>{criterion.title}</strong><p className="form-hint">Ревизия {review.revision}. Изменение сохранится на сервере с причиной и доказательствами.</p>
    {existing && <p className="live-notice">Причина обращения: {existing.reason}</p>}
    {appealId && <><label className="form-label">Решение<select disabled={busy} value={decision} onChange={e => setDecision(e.target.value as typeof decision)}><option value="rejected">Отклонить — оставить текущее значение</option><option value="accepted">Принять — изменить значение</option></select></label>
      {decision === 'accepted' && <><label className="form-label">Новое значение<select required disabled={busy} value={value} onChange={e => setValue(e.target.value)}><option value="">Выберите значение</option>{criterion.type === 'scale' ? criterion.scale.map(v => <option key={v} value={v}>{v}</option>) : <><option value="true">Да</option><option value="false">Нет</option></>}{criterion.allow_na && <option value="na">N/A</option>}</select></label>{value === 'na' ? <p className="form-hint">Условие N/A: {criterion.na_condition}</p> : <label className="form-label">Основание<select value={kind} onChange={e => setKind(e.target.value as typeof kind)}><option value="message">Сказанное в репликах</option><option value="missing_action">Участок, где ожидалось действие</option></select></label>}</>}
    </>}
    <label className="form-label">Причина<textarea required maxLength={4000} disabled={busy} value={reason} onChange={e => setReason(e.target.value)} rows={3}/></label>
    {(!appealId || decision === 'accepted') && <fieldset className="live-evidence-picker" disabled={busy}><legend>Подтверждающие реплики</legend>{conversation.messages.map(m => <label key={m.id} className="live-checkbox"><input type="checkbox" checked={ids.includes(m.id)} onChange={e => setIds(prev => e.target.checked ? [...prev, m.id] : prev.filter(id => id !== m.id))}/><span><b>{m.timestamp} · {m.speaker}</b><br/>{m.text}</span></label>)}</fieldset>}
    {error && <p role="alert" className="form-error">{error}</p>}
    <div className="modal-actions"><button type="button" disabled={busy} onClick={onClose} className="button secondary">Отмена</button><button disabled={busy} className="button primary">{busy ? 'Сохраняем…' : 'Сохранить'}</button></div>
  </form></Modal>;
}

export function AudioDialog({ onClose, onJob }: { onClose: () => void; onJob: (job: TranscriptionJob) => void }) {
  const [file, setFile] = useState<File | null>(null);
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
  return <Modal title="Загрузить аудиозапись" onClose={() => !busy && onClose()}><form className="live-form" onSubmit={submit}>
    <p className="modal-description">WAV, MP3, FLAC или Ogg до 25 МБ. Распознавание требует подключённого локального ASR. Если он недоступен, файл сохранится отдельно без выдуманного транскрипта.</p>
    <label className="form-label">Запись<input type="file" accept=".wav,.mp3,.flac,.ogg" required disabled={busy} onChange={e => { setFile(e.target.files?.[0] ?? null); setAssetId(''); uploadId.current = crypto.randomUUID(); setError(''); }}/></label>
    <label className="form-label">Язык<select disabled={busy} value={language} onChange={e => setLanguage(e.target.value as typeof language)}>{['RU','KZ','EN'].map(l => <option key={l}>{l}</option>)}</select></label>
    {assetId && <p className="live-notice">Файл сохранён. ID: {assetId}</p>}
    {error && <p role="alert" className="form-error">{error}</p>}
    <div className="modal-actions"><button className="button primary" disabled={busy || !file}>{busy ? 'Обрабатываем…' : assetId ? 'Запросить распознавание' : 'Загрузить и распознать'}</button></div>
  </form></Modal>;
}
