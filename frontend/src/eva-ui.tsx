"use client";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ArrowUpRight, X, MessageSquare, Headphones, Check, Search } from "lucide-react";
import { type Conversation, type Criterion, type Appeal, score, formatScore } from "./eva-data";

export function EvaLogo({compact=false}:{compact?:boolean}) {
 return <div className={"eva-logo "+(compact?"compact":"")}><svg width="37" height="37" viewBox="0 0 40 40" aria-hidden="true"><path d="M6 7h28l-5 7H6zM6 17h23l-5 7H6zM6 27h18l-5 7H6z" fill="currentColor"/></svg><div><strong>EVA</strong>{!compact&&<small>Expert Virtual Auditor</small>}</div></div>;
}
export function FreedomLogo(){return <div className="freedom-logo"><svg viewBox="0 0 30 36" width="23" height="28" aria-hidden="true"><path d="M2 2h26v12c0 10-7 17-13 20C8 31 2 24 2 14Z" fill="currentColor"/><path d="M9 9h14l-2 5H9zm0 9h10l-2 5H9z" fill="#10291e"/></svg><span>FREEDOM<small>BROKER</small></span></div>}
export function StatusBadge({kind,children}:{kind?:string;children:ReactNode}){return <span className={"badge "+(kind??"neutral")}><i/>{children}</span>}
export function Score({value,compact=false}:{value:number|null;compact?:boolean}){return <span className={"score-label "+(value===null?"muted":value>=90?"good":value>=75?"warning":"danger")}><i/>{formatScore(value,compact?1:2)}{!compact&&<small>/ 100</small>}</span>}
export function Modal({title,onClose,children,wide=false}:{title:string;onClose:()=>void;children:ReactNode;wide?:boolean}){
 const ref=useRef<HTMLDialogElement>(null);
 const titleId=useId();
 useEffect(()=>{const d=ref.current;d?.showModal();return()=>d?.close();},[]);
 return <dialog ref={ref} aria-labelledby={titleId} className={"modal "+(wide?"wide":"")} onCancel={onClose} onClick={e=>{if(e.target===e.currentTarget)onClose();}}><div className="modal-head"><h2 id={titleId}>{title}</h2><button type="button" className="icon-button" aria-label="Закрыть" onClick={onClose}><X/></button></div>{children}</dialog>;
}
export function EmptyState({title,children}:{title:string;children?:ReactNode}){return <div className="empty-state"><div className="empty-icon"><Search/></div><h3>{title}</h3><p>{children}</p></div>}
export function QualityChart({items}:{items:Conversation[]}){
 const [small,setSmall]=useState(false);
 useEffect(()=>{const m=window.matchMedia("(max-width:560px)");const sync=()=>setSmall(m.matches);sync();m.addEventListener("change",sync);return()=>m.removeEventListener("change",sync);},[]);
 const chartWidth=small?370:690;
 const endX=chartWidth-40;
 const dayX=(day:number)=>48+(day-1)*(endX-48)/6;
 const scoreY=(value:number)=>170-value*1.4;
 const days=[1,2,3,4,5,6,7].map(day=>{const scores=items.filter(i=>i.date===day&&!i.pending).map(i=>score(i.ratings,i.evaluationChecklist)).filter((s):s is number=>s!==null);return {day,value:scores.length?scores.reduce((a,b)=>a+b,0)/scores.length:null};});
 const points=days.filter((d):d is {day:number,value:number}=>d.value!==null).map(d=>({...d,x:dayX(d.day),y:scoreY(d.value)}));
 let path="";
 points.forEach((p,i)=>{if(!i)path="M "+p.x+" "+p.y;else {const prev=points[i-1],cx=(prev.x+p.x)/2;path+=" C "+cx+" "+prev.y+", "+cx+" "+p.y+", "+p.x+" "+p.y;}});
 return <svg className="quality-chart" viewBox={"0 0 "+chartWidth+" 204"} role="img" aria-label="Демонстрационная динамика среднего балла по дням ноября"><defs><linearGradient id="chart-fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#00b849" stopOpacity=".17"/><stop offset="100%" stopColor="#00b849" stopOpacity="0"/></linearGradient></defs>
 {[100,75,50,25,0].map(v=><g key={v}><text x="0" y={scoreY(v)+4}>{v}</text><line x1="42" x2={endX} y1={scoreY(v)} y2={scoreY(v)} stroke="#e9eeeb" strokeDasharray="3 5"/></g>)}
 <line x1="42" x2={endX} y1={scoreY(90)} y2={scoreY(90)} stroke="#b5cbb9" strokeDasharray="5 5"/><text x={endX-2} y={scoreY(90)-7} textAnchor="end" className="chart-goal">Цель 90</text>
 {points.length>0&&<><path d={path+" L "+points[points.length-1].x+" 170 L "+points[0].x+" 170 Z"} fill="url(#chart-fill)"/><path d={path} fill="none" stroke="#00a348" strokeWidth="3" strokeLinecap="round"/>{points.map(p=><g key={p.day}><circle cx={p.x} cy={p.y} r="7" fill="#fff"/><circle cx={p.x} cy={p.y} r="4.5" fill="#00a348"/><circle cx={p.x} cy={p.y} r="15" fill="transparent"><title>{p.day+" ноября: "+formatScore(p.value)}</title></circle></g>)}</>}
 {days.map(d=><text key={d.day} x={dayX(d.day)} y="198" textAnchor="middle">{d.day+" нояб."}</text>)}</svg>;
}
export function Gauge({value}:{value:number|null}){const v=value??0;return <div className="gauge"><svg viewBox="0 0 144 144" aria-hidden="true"><circle cx="72" cy="72" r="61" fill="none" stroke="#e8eee9" strokeWidth="8"/><circle cx="72" cy="72" r="61" fill="none" stroke="#00b849" strokeWidth="8" strokeDasharray={v/100*383.27+" 383.27"} transform="rotate(-90 72 72)" strokeLinecap="round"/></svg><div><strong>{formatScore(value,2)}</strong><span>из 100 баллов</span></div></div>}
export function ConversationTable({items,appeals,checklist,onOpen,small=false}:{items:Conversation[];appeals:Appeal[];checklist:Criterion[];onOpen:(id:string)=>void;small?:boolean}){
 if(!items.length)return <EmptyState title="Диалоги не найдены">Попробуйте изменить запрос или сбросить фильтры.</EmptyState>;
 return <div className="table-scroll"><table className="conversation-table"><thead><tr><th>Диалог</th><th>Оператор</th><th>Канал</th><th>Язык</th><th>Оценка</th><th>Статус</th><th><span className="sr-only">Открыть</span></th></tr></thead><tbody>{items.map((c)=>{const a=appeals.find(a=>a.conversationId===c.id&&a.status==="open")??appeals.find(a=>a.conversationId===c.id);const violation=c.ratings[7]===1;return <tr key={c.id}><td><button className="topic-link" onClick={()=>onOpen(c.id)}><span className={"channel-icon "+(c.channel==="voice"?"voice":"")}>{c.channel==="chat"?<MessageSquare/>:<Headphones/>}</span><span><strong>{c.topic}</strong><small>{c.id+" · "+c.date+" нояб., "+c.time}</small></span></button></td><td><div className="operator-cell"><span className={"avatar-mini avatar-"+c.initials.charCodeAt(0)%4}>{c.initials}</span><span>{c.operator}</span></div></td><td className="muted">{c.channel==="chat"?"Чат":"Звонок"}</td><td><span className="language-tag">{c.lang}</span></td><td>{c.pending?<span className="muted">—</span>:<Score value={score(c.ratings,c.evaluationChecklist??checklist)} compact/>}</td><td>{c.pending?<StatusBadge>Без оценки</StatusBadge>:violation?<StatusBadge kind="danger">Нарушение</StatusBadge>:a?.status==="open"?<StatusBadge kind="warning">На пересмотре</StatusBadge>:a?<StatusBadge kind="info">Пересмотрен</StatusBadge>:<StatusBadge kind="good">Оценён</StatusBadge>}</td><td><button className="icon-button row-open" aria-label={"Открыть "+c.id} onClick={()=>onOpen(c.id)}><ArrowUpRight/></button></td></tr>})}</tbody></table>{small&&<div className="table-caption"><span>Последние диалоги из демонстрационного набора</span><span><Check size={13}/> Оценки с обоснованием</span></div>}</div>;
}
