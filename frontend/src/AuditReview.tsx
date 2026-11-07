import { useEffect, useState } from 'react';
import { api } from './api/client';
import type { AuditExport, CriterionResult, Evaluation, ReviewCase } from './api/types';

const names:Record<string,string>={insult:'Оскорбление',private_service:'Личная платная услуга',disclosure:'Раскрытие сведений'};
const fieldNames:Record<string,string>={weight:"Вес",instruction:"Правило",allow_na:"Разрешено N/A",na_condition:"Условие N/A",scale:"Шкала",title:"Название",depends_on:"Зависимости"};
const display=(v: unknown)=>v===null?'—':v===true?'Да':v===false?'Нет':String(v);

const answer=(row:CriterionResult|undefined|null)=>!row?'—':row.status==='needs_review'?'Нужна проверка':row.value===null?'N/A':display(row.value);

export default function AuditReview({evaluation,review,jump}:{evaluation:Evaluation;review:ReviewCase;jump:(ids:string[])=>void}) {
  const [data,setData]=useState<AuditExport|null>(null);
  const [error,setError]=useState('');
  const [open,setOpen]=useState(false);
  const [version,setVersion]=useState('');
  const [pending,setPending]=useState(false);
  const [refresh,setRefresh]=useState(0);
  useEffect(()=>{
    let active=true;
    setData(null); setError('');
    if(open && api.auditExport) api.auditExport(evaluation.id).then(d=>{if(active)setData(d);})
      .catch(()=>{if(active)setError('Дополнительный отчёт недоступен.');});
    return ()=>{active=false;};
  },[open,evaluation.id,review.revision,refresh]);
  if(!api.auditExport) return null;
  const companion=data?.audit, candidate=companion?.candidate;
  function download() {
    const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));
    const a=document.createElement('a'); a.href=url; a.download='eva-review-'+evaluation.id+'.json';a.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  async function derive() {
    if(!api.deriveVersion || pending)return;
    setPending(true);setError('');
    try {const record=await api.deriveVersion(evaluation.id,Number(version));
      window.location.hash='/dialogs/'+encodeURIComponent(record.original.conversation_id)+'?evaluation='+encodeURIComponent(record.id);
    } catch(e){setError(e instanceof Error?e.message:'Не удалось сравнить версии.');}
    finally {setPending(false);}
  }
  return <div className="criterion"><button className="criterion-toggle" aria-expanded={open} onClick={()=>setOpen(!open)}>EVA Review · история и экспорт</button>
    {open && <div className="criterion-detail">
      <p>Оценены {evaluation.original.criteria.filter(c=>c.status==='scored').length} из {evaluation.original.criteria.length} критериев автоматически.</p>
      {error && <p role="alert">{error}</p>}
      {data && <>
        <p>Первоначальный балл {display(evaluation.scoring.total)} · Действующий {display(review.final.scoring.total)}</p>
        {review.decisions.map((d,index)=>{
          const criterion=review.appeals.find(a=>a.id===d.appeal_id)?.criterion_id;
          const earlier=review.decisions.slice(0,index).reverse().find(x=>x.override?.criterion_id===criterion);
          const original=earlier?.override ?? evaluation.original.criteria.find(r=>r.criterion_id===criterion);
          return <p key={d.id}><strong>{criterion}</strong> · {answer(original)} → {answer(d.override ?? original)} · {d.reviewer_id} · {d.created_at} · {d.reason}</p>;
        })}
        {companion?.mode==='shadow' && <p>Параллельное сравнение · {companion.status} <button onClick={()=>setRefresh(n=>n+1)}>Обновить</button></p>}
        {candidate && <><h3>EVA Sentinel · нарушения</h3><p>Флаги этого автоматического запуска. Действующий итог учитывает решения проверяющего.</p>
          {candidate.audit.sentinel.map(f=><div key={f.rule_id}><strong>{names[f.rule_id] ?? f.rule_id}</strong> · {f.status==='needs_review'?'Нужна проверка':display(f.decision)}
            <p>{f.uncertainty ?? f.action}</p>{f.evidence.map((e,i)=><button className="evidence-link" key={i} onClick={()=>jump([e.utterance_id])}>{e.utterance_id} · «{e.quote}»</button>)}</div>)}
          <details><summary>EVA Score · решения эксперимента</summary>
            {candidate.response.criteria.map(r=><div key={r.criterion_id}><p>{r.criterion_id} · {companion?.mode==='shadow' && <>{answer(evaluation.original.criteria.find(x=>x.criterion_id===r.criterion_id))} → </>}{answer(r)} · {r.reason}</p>
              {candidate.audit.criteria[r.criterion_id]?.conflict && <p>{candidate.audit.criteria[r.criterion_id].conflict}</p>}
              {candidate.audit.criteria[r.criterion_id] && <p>{candidate.audit.criteria[r.criterion_id].technical==='valid'?'Источник ссылки проверен':'Источник ссылки не подтверждён'} · {candidate.audit.criteria[r.criterion_id].semantic==='model_assessed'?'Смысл оценён моделью':'Смысл требует проверки'}</p>}
              {candidate.audit.criteria[r.criterion_id]?.scope && (r.status==='needs_review' || r.value===false) && <button className="evidence-link" onClick={()=>{const s=candidate.audit.criteria[r.criterion_id].scope;jump([s.first,s.last]);}}>Проверенный участок: {candidate.audit.criteria[r.criterion_id].scope.first} → {candidate.audit.criteria[r.criterion_id].scope.last}</button>}
              {candidate.audit.criteria[r.criterion_id]?.evidence.map((e,i)=><button className="evidence-link" key={i} onClick={()=>jump([e.utterance_id])}>{e.utterance_id} · «{e.quote}»</button>)}</div>)}</details></>}
        {companion?.changes && <details open><summary>EVA Policy · v{companion.from_version} → v{companion.to_version}</summary>
          {Object.entries(companion.changes).map(([id,c])=><div key={id}><strong>{id}</strong> · {c.evaluate?'повторная оценка':'сохранённое решение'}
            {Object.entries(c.differences ?? {}).map(([field,d])=><p key={field}>{fieldNames[field] ?? field}: {display(d.from)} → {display(d.to)}</p>)}
            {c.before_decision && c.after_decision && <p>Автоматическое решение: {answer(c.before_decision)} → {answer(c.after_decision)}</p>}</div>)}
          {!!companion.removed?.length && <p>Удалены из новой версии: {companion.removed.join(', ')}</p>}
          {companion.review_projection && <p>С учётом решений проверяющего по исходной ревизии {companion.review_projection.parent_revision}: {display(companion.review_projection.scoring.total)}</p>}</details>}
        {data.can_derive && <label>Сравнить с версией чек-листа <input type="number" min={evaluation.original.checklist_version+1} value={version} onChange={e=>setVersion(e.target.value)}/> <button disabled={pending || Number(version)<=evaluation.original.checklist_version} onClick={()=>void derive()}>Пересчитать</button></label>}
        <button onClick={download}>Скачать JSON с историей и доказательствами</button>
      </>}
    </div>}
  </div>;
}
