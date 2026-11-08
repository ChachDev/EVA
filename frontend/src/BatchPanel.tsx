import { useEffect, useState } from 'react';
import { api } from './api/client';
import type { Batch, Checklist, Conversation, Quality } from './api/types';

const client=api.batches;
const states:Record<string,string>={queued:'В очереди',running:'Оценивается',done:'Сохранено',failed:'Ошибка'};
const show=(v:unknown)=>v===null?'N/A':v===true?'Да':v===false?'Нет':v==='unknown'?'Недостаточно данных':String(v);
const issue=(e:unknown)=>e instanceof Error?e.message:'Не удалось получить данные.';
function Blind({eid,onExit}:{eid:string;onExit:()=>void}){
  const [task,setTask]=useState<{conversation:Conversation;checklist:Checklist}>();
  const [author,setAuthor]=useState(''),[values,setValues]=useState<Record<string,string>>({}),[error,setError]=useState('');
  const [result,setResult]=useState<Array<{criterion:string;human:unknown;auto:unknown}>>(),[busy,setBusy]=useState(false);
  useEffect(()=>{let active=true;client?.blind(eid).then(x=>{if(active)setTask(x)}).catch(e=>{if(active)setError(issue(e))});return()=>{active=false}},[eid]);
  async function submit(){if(!client||!task)return;setBusy(true);try{
    const answers=Object.fromEntries(Object.entries(values).map(([k,v])=>[k,JSON.parse(v)]));
    setResult((await client.label(eid,author.trim(),answers)).rows);setError('');
  }catch(e){setError(issue(e))}finally{setBusy(false)}}
  return <section className="panel live-panel"><button className="button secondary" onClick={onExit}>К набору</button><h2>Слепая разметка</h2>
    {error&&<p role="alert">{error}</p>}{task&&<><h3>{task.conversation.id}</h3>{task.conversation.messages.map(m=><p key={m.id}><b>{m.id} · {m.speaker}</b>: {m.text}</p>)}
    {result?<><h3>Сравнение с первоначальным ответом модели</h3>{result.map(r=><p key={r.criterion}>{r.criterion}: человек — {show(r.human)}; модель — {show(r.auto)}</p>)}</>:
    <><p>Сначала оцените все критерии самостоятельно. Ответы модели появятся после сохранения.</p>
    <label className="form-label">Разметчик<input value={author} onChange={e=>setAuthor(e.target.value)} /></label>
    {task.checklist.criteria.map(c=><label key={c.id} className="form-label">{c.title}<small>{c.instruction} {c.na_condition}</small>
      <select value={values[c.id]??''} onChange={e=>setValues({...values,[c.id]:e.target.value})}><option value="">Выберите решение</option>
      {[...(c.type==='scale'?c.scale:[true,false]),...(c.allow_na?[null]:[]),'unknown'].map(v=><option key={JSON.stringify(v)} value={JSON.stringify(v)}>{show(v)}</option>)}</select></label>)}
    <button className="button primary" disabled={busy||!author.trim()||!task.checklist.criteria.every(c=>values[c.id])} onClick={()=>void submit()}>Сохранить разметку</button></>}</>}</section>
}

export default function BatchPanel({checklist,selector}:{checklist:Checklist|null|undefined;selector:React.ReactNode}){
  const [groups,setGroups]=useState<string[]>([]),[group,setGroup]=useState(''),[ids,setIds]=useState<string[]>([]),[id,setId]=useState('');
  const [batch,setBatch]=useState<Batch>(),[quality,setQuality]=useState<Quality>(),[blind,setBlind]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [revision,setRevision]=useState(0);
  useEffect(()=>{let active=true; if(client)Promise.all([client.groups(),client.list()]).then(([g,js])=>{
    if(active){setGroups(g);setGroup(g[0]??'');setIds(js);setId(js[0]??'')}
  }).catch(e=>{if(active)setError(issue(e))});return()=>{active=false}},[]);
  useEffect(()=>{let active=true,timer:ReturnType<typeof setTimeout>;setBatch(undefined);setQuality(undefined);
    async function load(){if(!client||!id)return;try{const b=await client.get(id);if(active){setBatch(b);setError('');if(b.counts.running||b.counts.queued)timer=setTimeout(load,1800)}}
      catch(e){if(active){setError(issue(e));timer=setTimeout(load,4000)}}}void load();return()=>{active=false;clearTimeout(timer)}},[id,revision]);
  async function act(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn()}catch(e){setError(issue(e))}finally{setBusy(false)}}
  if(!client)return null;
  const pick=(b:Batch)=>{setIds(x=>x.includes(b.id)?x:[b.id,...x]);setId(b.id);setBatch(b);setRevision(n=>n+1)};
  if(blind)return <Blind key={blind} eid={blind} onExit={()=>{setBlind('');setQuality(undefined)}}/>;
  return <section className="panel live-panel"><h2>Пакетная оценка</h2><p>Прогресс хранится на сервере. Можно закрыть страницу и вернуться позже.</p>
    <div className="live-controls"><label className="form-label">Импортированный набор<select value={group} onChange={e=>setGroup(e.target.value)}>{groups.map(g=><option key={g}>{g}</option>)}</select></label>{selector}
      <button className="button primary" disabled={!checklist||!group||busy} onClick={()=>void act(async()=>pick(await client.start(group,checklist!)))}>Оценить весь набор</button></div>
    <label className="form-label">Сохранённый запуск<select value={id} onChange={e=>setId(e.target.value)}>{ids.map(x=><option key={x}>{x}</option>)}</select></label>
    {error&&<p role="alert">{error}</p>}
    {batch&&<><h3>{batch.group_id} · {batch.checklist_id} v{batch.version}</h3><p>Конфигурация: {batch.mode} · {batch.revision}</p><p>{Object.entries(batch.counts).map(([k,n])=>(states[k]??k)+': '+n).join(' · ')}</p>
    <div className="live-controls"><button className="button secondary" disabled={busy||!batch.counts.failed} onClick={()=>void act(async()=>pick(await client.retry(batch.id)))}>Повторить ошибки</button>
    <button className="button secondary" disabled={busy||!!(batch.counts.queued||batch.counts.running)||!['DEV','PROD'].includes(batch.group_id.toUpperCase())} onClick={()=>void act(async()=>{
      const file=await client.results(batch.id),url=URL.createObjectURL(new Blob([JSON.stringify(file,null,2)],{type:'application/json'}));
      const link=document.createElement('a');link.href=url;link.download='results.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)})}>Скачать results.json</button>
    <button className="button secondary" disabled={busy} onClick={()=>void act(async()=>setQuality(await client.quality(batch.id)))}>Качество по разметке</button></div>
    {quality&&<><h3>Размечено диалогов: {quality.labeled} из {quality.total}</h3><p>Анкет: {quality.submissions}. Ответы разных разметчиков считаются отдельно.</p>{quality.rows.map(r=><p key={r.language+r.criterion}>{r.language} · {r.criterion}: совпало {r.matched??0}/{r.denominator??0}; ложные / пропущенные N/A: {r.false_na??0}/{r.missed_na??0}; не решено моделью: {r.unresolved??0}{r.criterion==='gross_errors' && <>; ложные обвинения / пропуски: {r.false_accusations??0}/{r.missed_positive??0}</>}</p>)}</>}
    {batch.items.map(x=><div className="live-controls" key={x.id}><strong>{x.cid}</strong><span>{states[x.state]} · попыток: {x.attempt}</span>{x.error&&<span>{x.error}</span>}
      {x.state==='done'&&<button className="button secondary small" onClick={()=>setBlind(x.id)}>Разметить вслепую</button>}</div>)}</>}
  </section>
}
