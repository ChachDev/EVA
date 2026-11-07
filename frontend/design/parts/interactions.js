(() => {
  const root = document.getElementById('eva-root');
  const apps = [...root.querySelectorAll('[data-eva-design]')];
  const states = {};
  const issueInfo = {
    steps: { title:'Не обозначен срок ответа', body:'Оператор не назвал срок и канал обратной связи. Клиенту пришлось задать уточняющий вопрос.', quote:'«Я передам вопрос специалистам. Они с вами свяжутся.»', deduction:'−9,5 балла', time:138 },
    consultation: { title:'Не объяснён порядок обработки', body:'Сообщено только о передаче вопроса специалистам. Клиенту не объяснили, что произойдёт с заявкой дальше.', quote:'«Я передам вопрос специалистам. Они с вами свяжутся.»', deduction:'−5 баллов', time:138 }
  };
  const formatTime = t => `${String(Math.floor(t / 60)).padStart(2,'0')}:${String(Math.floor(t % 60)).padStart(2,'0')}`;
  function save(){if(window.openai?.setWidgetState){const snapshot=Object.fromEntries(Object.entries(states).map(([key,s])=>[key,{time:s.time,issue:s.issue,confirmed:s.confirmed,appealed:s.appealed,reason:s.reason}]));Promise.resolve(window.openai.setWidgetState({modelContent:{project:'EVA',kind:'visual-directions',directions:['Рабочая консоль','Протокол','Аудиостудия']},privateContent:{variants:snapshot}})).catch(()=>{});}}
  function restore(saved){const data=saved?.privateContent?.variants||{};for(const app of apps){const key=app.dataset.evaDesign, s=states[key],item=data[key];if(!item)continue;if(Number.isFinite(item.time))s.time=Math.min(393,Math.max(0,item.time));if(Object.hasOwn(issueInfo,item.issue))s.issue=item.issue;s.confirmed=item.confirmed===true;s.appealed=item.appealed===true;s.reason=typeof item.reason==='string'?item.reason.slice(0,1000):'';}}
  function update(app){
    const s=states[app.dataset.evaDesign];
    const issue=issueInfo[s.issue];
    for(const el of app.querySelectorAll('[data-current-time]'))el.textContent=formatTime(s.time);
    for(const el of app.querySelectorAll('[data-seek]'))el.value=s.time;
    for(const el of app.querySelectorAll('[data-playhead]'))el.style.left=`${100*s.time/393}%`;
    for(const [attr,key] of [['data-issue-title','title'],['data-issue-body','body'],['data-quote','quote'],['data-deduction','deduction']]){
      for(const el of app.querySelectorAll(`[${attr}]`))el.textContent=issue[key];
    }
    app.querySelectorAll('[data-issue]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.issue===s.issue)));
    const active=s.time>=128&&s.time<178?[128,138,147,155].filter(t=>t<=s.time).at(-1):null;
    app.querySelectorAll('[data-time][aria-pressed]').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.time)===active)));
    app.querySelectorAll('.st-turn').forEach(row=>row.classList.toggle('is-active',Number(row.querySelector('[data-time]').dataset.time)===active));
    for(const el of app.querySelectorAll('[data-review-status]'))el.textContent=s.confirmed?'Подтверждено · демо':'Оценено · демо';
    for(const b of app.querySelectorAll('[data-confirm]')){b.disabled=s.confirmed;b.textContent=s.confirmed?'Оценка подтверждена':'Подтвердить оценку';}
    for(const b of app.querySelectorAll('[data-appeal]')){b.disabled=s.appealed;b.textContent=s.appealed?'Апелляция добавлена':'Оспорить';}
  }
  for(const app of apps){states[app.dataset.evaDesign]={time:138,issue:'steps',confirmed:false,appealed:false,reason:''};for(const wave of app.querySelectorAll('[data-wave]')){const offset=wave.dataset.wave==='operator'?4:0;const nodes=document.createDocumentFragment();for(let i=0;i<120;i++){const span=document.createElement('span');const h=4+Math.abs(Math.sin(i*1.63+offset)*Math.cos(i*.123+offset))*(18+20*Math.abs(Math.sin(i*.19+offset)));span.style.height=`${h.toFixed(1)}px`;nodes.append(span);}wave.replaceChildren(nodes);}if(window.matchMedia('(max-width:600px)').matches)app.querySelectorAll('[data-mobile-queue]').forEach(q=>q.open=false);}
  restore(window.openai?.widgetState);apps.forEach(update);
  const narrow = window.matchMedia('(max-width:600px)');
  narrow.addEventListener('change', event => {root.querySelectorAll('[data-mobile-queue]').forEach(q=>q.open=!event.matches);});
  root.addEventListener('click',event=>{const b=event.target.closest('button');if(!b||b.disabled)return;const app=b.closest('[data-eva-design]');if(!app)return;const s=states[app.dataset.evaDesign],notice=app.querySelector('[data-notice]');if(b.hasAttribute('data-time')){s.time=Number(b.dataset.time);update(app);save();}if(b.hasAttribute('data-issue')&&Object.hasOwn(issueInfo,b.dataset.issue)){s.issue=b.dataset.issue;s.time=issueInfo[s.issue].time;update(app);save();}if(b.hasAttribute('data-confirm')){s.confirmed=true;notice.textContent='Оценка подтверждена в макете. Данные не отправлены на сервер.';update(app);save();}if(b.hasAttribute('data-appeal')){app.querySelector('[data-appeal-panel]').hidden=false;app.querySelector('[data-appeal-reason]').focus();}if(b.hasAttribute('data-close-appeal')){app.querySelector('[data-appeal-panel]').hidden=true;app.querySelector('[data-appeal]').focus();}if(b.hasAttribute('data-submit-appeal')){const reason=app.querySelector('[data-appeal-reason]').value.trim();if(!reason){notice.textContent='Укажите причину пересмотра';app.querySelector('[data-appeal-reason]').focus();return;}s.appealed=true;s.reason=reason.slice(0,1000);app.querySelector('[data-appeal-panel]').hidden=true;notice.textContent=`Апелляция сохранена только в макете: ${s.reason}`;update(app);save();}});
  root.addEventListener('input',event=>{if(!event.target.matches('[data-seek]'))return;const app=event.target.closest('[data-eva-design]');states[app.dataset.evaDesign].time=Number(event.target.value);update(app);});
  root.addEventListener('change',event=>{if(event.target.matches('[data-seek]'))save();});
  window.addEventListener('openai:set_globals',event=>{const saved=event.detail?.globals?.widgetState;if(saved){restore(saved);apps.forEach(update);}});
  if(globalThis.lucide)globalThis.lucide.createIcons({attrs:{width:16,height:16,'stroke-width':1.5}});
  if(globalThis.Tweak){for(const app of apps){const design={appearance:'auto',textSize:14};const apply=()=>{app.style.colorScheme=design.appearance==='auto'?'inherit':design.appearance;app.style.setProperty('--eva-reading-size',`${design.textSize}px`);};const tweak=new Tweak({container:app,onChange:apply});tweak.addSelect(design,'appearance',{label:'Тема',options:[{label:'Как в приложении',value:'auto'},{label:'Светлая',value:'light'},{label:'Тёмная',value:'dark'}]});tweak.addSlider(design,'textSize',{label:'Размер реплик',min:14,max:18,unit:'px'});}}
})();
