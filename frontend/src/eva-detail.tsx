"use client";
import { useState } from "react";
import { ArrowLeft, ArrowRight, ChevronDown, Check, Minus, AlertTriangle, MessageSquare, Headphones, Clock, GitPullRequest, Download, ShieldCheck, FileText, Info } from "lucide-react";
import { type Conversation, type Criterion, type Appeal, evidence, score, formatScore } from "./eva-data";
import { Gauge } from "./eva-ui";

export default function AuditDetail({conversation:c,checklist,appeals,onBack,onAppeal,onExport}:{conversation:Conversation;checklist:Criterion[];appeals:Appeal[];onBack:()=>void;onAppeal:(criterion:number)=>void;onExport:()=>void}){
 const [expanded,setExpanded]=useState(4);
 const [highlight,setHighlight]=useState<number[]>([]);
 const [transcriptTab,setTranscriptTab]=useState<"all"|"operator">("all");
 const result=c.pending?null:score(c.ratings,checklist);
 const applied=checklist.reduce((a,v,i)=>a+(v.type!=="flag"&&c.ratings[i]!=null?v.weight:0),0);
 const earned=checklist.reduce((a,v,i)=>a+(v.type!=="flag"&&c.ratings[i]!=null&&v.max>0?(c.ratings[i]??0)/v.max*v.weight:0),0);
 const passed=checklist.filter((v,i)=>v.type!=="flag"&&c.ratings[i]===v.max).length;
 const na=checklist.filter((v,i)=>v.type!=="flag"&&c.ratings[i]===null).length;
 const unreviewed=checklist.filter((v,i)=>v.type!=="flag"&&c.ratings[i]===undefined).length;
 const relevantAppeals=appeals.filter(a=>a.conversationId===c.id);
 function jump(indices:number[]){setTranscriptTab("all");setHighlight(indices);window.setTimeout(()=>{document.getElementById("message-"+indices[0])?.scrollIntoView({behavior:window.matchMedia("(prefers-reduced-motion: reduce)").matches?"instant":"smooth",block:"center"});},20);}
 return <div className="detail-view page-enter">
  <button className="back-link" onClick={onBack}><ArrowLeft/> Все диалоги</button>
  <div className="page-heading detail-heading"><div><div className="eyebrow">{c.id} <span className="language-tag">{c.lang}</span></div><h1>{c.topic}</h1><p>{c.operator}<span>·</span>{c.date} ноября 2026, {c.time}<span>·</span>{c.channel==="chat"?"Чат":"Звонок"}</p></div><button className="button secondary" onClick={onExport}><Download/> Экспорт оценки</button></div>
  <section className="audit-summary">
   {c.pending?<div className="pending-score"><Clock/><strong>Без оценки</strong></div>:<Gauge value={result}/>}
   <div className="summary-description"><div className="mini-eyebrow">РЕЗУЛЬТАТ АУДИТА</div><h2>{c.pending?"Транскрипт готов к просмотру":c.ratings[7]===1?"Требуется внимание":result===null?"Итог пока не рассчитан":result>=90?"Отличный уровень сервиса":"Хороший диалог. Есть точки роста."}</h2><p>{c.pending?"Для автоматической оценки необходимо подключить сервис анализа.":c.ratings[7]===1?"Грубое нарушение отмечено отдельно от балльной оценки.":result===null?(unreviewed?"Часть критериев ожидает оценки.":"Нет применимых критериев для расчёта итогового балла."):"Каждый вывод связан с конкретным фрагментом разговора."}</p><div className="summary-tags"><span><ShieldCheck/>Freedom · Чек-лист v{c.evaluationVersion??1}</span><span><Clock/>{c.duration}</span><span><FileText/>{c.messages.length} реплик</span></div></div>
   {!c.pending&&<div className="summary-counts"><div><b className="text-green">{passed}</b><span>выполнено</span></div><div><b className="text-amber">{checklist.filter(v=>v.type!=="flag").length-passed-na-unreviewed}</b><span>точки роста</span></div><div><b>{na}</b><span>неприменимо</span></div>{unreviewed>0&&<div><b>{unreviewed}</b><span>ожидают оценки</span></div>}</div>}
  </section>
  {relevantAppeals.some(a=>a.status!=="open")&&<div className="review-banner"><ShieldCheck/><span>Оценка пересмотрена человеком. Исходный итог <b>{formatScore(relevantAppeals[relevantAppeals.length-1].originalScore)}</b> → финальный <b>{formatScore(result)}</b>.</span></div>}
  <div className="audit-columns">
   <section className="panel transcript-panel">
    <div className="panel-heading"><div><h2>Транскрипт диалога</h2><p>Контекст, который имеет значение</p></div>{c.channel==="chat"?<MessageSquare/>:<Headphones/>}</div>
    {c.channel==="voice"&&<div className="audio-empty"><Headphones/><div><strong>Аудиозапись не подключена</strong><span>В демо доступен транскрипт с исходными метками времени.</span></div></div>}
    <div className="transcript-controls"><div className="segmented"><button className={transcriptTab==="all"?"active":""} onClick={()=>setTranscriptTab("all")}>Все реплики</button><button className={transcriptTab==="operator"?"active":""} onClick={()=>setTranscriptTab("operator")}>Оператор</button></div><span className="muted">{c.lang} · {c.channel==="chat"?"Chat":"Voice"}</span></div>
    <div className="transcript-body">{c.messages.map((m,i)=>transcriptTab==="operator"&&m.role!=="operator"?null:<article id={"message-"+i} key={i} className={"message "+m.role+(highlight.includes(i)?" highlighted":"")}><div className="message-meta"><span>{m.role==="operator"?c.operator:m.role==="client"?"Клиент":m.role==="bot"?"Бот":"Система"}</span><time title={m.time}>{m.time.includes("T")?m.time.slice(11,19):m.time}</time></div><div className="message-bubble">{m.text}</div>{highlight.includes(i)&&<span className="evidence-caption"><Check/> Доказательство по критерию</span>}</article>)}</div>
    <div className="transcript-footer"><span className="status-dot"/>Конец диалога<span>{c.messages.length} реплик</span></div>
   </section>
   <section className="panel criteria-panel">
    <div className="panel-heading"><div><h2>Оценка по критериям</h2><p>От оценки — к доказательству</p></div><span className="count-badge">{checklist.length}</span></div>
    {c.pending?<div className="pending-notice"><Info/><h3>Оценка ещё не выполнена</h3><p>Импортированный диалог можно прочитать. Демо не отправляет его в AI и не генерирует баллы.</p></div>:<div className="criteria-list">{checklist.map((criterion,i)=>{const v=c.ratings[i];const originalEvidence=v===undefined?{indices:[],reason:"Критерий ещё не оценён."}:evidence(c,criterion.id);const decision=relevantAppeals.find(a=>a.criterionId===criterion.id&&a.status!=="open"&&a.reason);const ev={...originalEvidence,reason:decision?"Решение проверяющего: "+decision.reason:originalEvidence.reason};const isOpen=expanded===criterion.id;const isFlag=criterion.type==="flag";const positive=isFlag?v===0:v===criterion.max;const pending=v===undefined;const isNa=v===null;const earnedC=v==null?null:v/criterion.max*criterion.weight;return <div className={"criterion "+(isOpen?"expanded":"")} key={criterion.id}><button className="criterion-toggle" onClick={()=>setExpanded(isOpen?0:criterion.id)} aria-expanded={isOpen}><span className={"criterion-check "+(isNa||pending?"muted":positive?"pass":"fail")}>{isNa||pending?<Minus/>:positive?<Check/>:<AlertTriangle/>}</span><span className="criterion-title"><strong>{criterion.title}</strong><small>{isFlag?"Отдельный флаг":isNa?"Неприменимо":pending?"Ожидает оценки":"Вес "+criterion.weight+" баллов"}</small></span><span className={"criterion-score "+(isNa?"muted":"")}>{pending?"—":isNa?"N/A":isFlag?v===1?"Да":"Нет":formatScore(earnedC,earnedC!==null&&Number.isInteger(earnedC)?0:1)}{!pending&&!isNa&&!isFlag&&<small> / {criterion.weight}</small>}</span><ChevronDown className={isOpen?"rotated":""}/></button>{isOpen&&<div className="criterion-detail"><p>{ev.reason}</p>{ev.indices.length>0&&<button className="evidence-link" onClick={()=>jump(ev.indices)}><span className="evidence-link-heading"><span><MessageSquare/>Основание</span><span>{c.messages[ev.indices[0]]?.time.includes("T")?c.messages[ev.indices[0]]?.time.slice(11,19):c.messages[ev.indices[0]]?.time}<ArrowRight/></span></span><q>{c.messages[ev.indices[0]]?.text}</q></button>}<div className="criterion-bottom"><span>{pending?"Ожидает оценки":isFlag?"Флаг: "+(v===1?"есть нарушение":"не выявлено"):isNa?"Вес исключён из расчёта":criterion.type==="binary"?"Ответ: "+(v===1?"Да":"Нет"):"Шкала: "+v+" / "+criterion.max}</span><button disabled={pending} onClick={()=>onAppeal(criterion.id)}><GitPullRequest/>{relevantAppeals.some(a=>a.criterionId===criterion.id&&a.status==="open")?"Апелляция":"Оспорить"}</button></div></div>}</div>})}</div>}
    {!c.pending&&<div className="calculation"><div><span>Итог с учётом N/A</span><strong>{formatScore(result)} <small>/ 100</small></strong></div><p>{result===null?(unreviewed?"Итог появится после оценки всех применимых критериев.":"Нет применимых критериев; итоговый балл не рассчитывается."):<>{formatScore(earned,2)} / {applied} × 100. Вес N/A распределён между применимыми критериями.</>}</p></div>}
   </section>
  </div>
 </div>;
}
