import { formatNumber } from "./i18n-core.ts";
export type Message = { time: string; role: "operator" | "client" | "system" | "bot"; text: string };
export type Criterion = { id: number; title: string; weight: number; type: "binary" | "scale" | "flag"; max: number; na: boolean; description: string };
export type Conversation = { id: string; topic: string; operator: string; initials: string; lang: "RU" | "KZ" | "EN"; channel: "chat" | "voice"; date: number; time: string; duration: string; ratings: (number | null)[]; messages: Message[]; pending?: boolean; evaluationChecklist?: Criterion[]; evaluationVersion?: number };
export type Appeal = { id: string; conversationId: string; criterionId: number; comment: string; status: "open" | "accepted" | "confirmed"; reason?: string; originalScore: number | null; finalScore?: number | null; originalValue: number | null; date: string };

export const criteria: Criterion[] = [
  {id: 1, title: "Установление контакта", weight: 10, type: "binary", max: 1, na: false, description: "Оператор поздоровался на языке клиента и представился в первом ответе."},
  {id: 2, title: "Выявление потребности", weight: 15, type: "binary", max: 1, na: true, description: "Известные детали учтены, недостающая информация уточнена до ответа."},
  {id: 3, title: "Идентификация", weight: 20, type: "binary", max: 1, na: true, description: "Личность подтверждена до раскрытия данных или защищённого действия."},
  {id: 4, title: "Корректная консультация", weight: 20, type: "scale", max: 20, na: true, description: "Насколько полно решён запрос или предложен корректный следующий шаг."},
  {id: 5, title: "Эмпатия и клиентоориентированность", weight: 15, type: "binary", max: 1, na: false, description: "Оператор вежлив и реагирует на конкретную проблему клиента."},
  {id: 6, title: "Ведение диалога", weight: 15, type: "scale", max: 10, na: false, description: "Ясность, последовательность ответов и предупреждение об ожидании."},
  {id: 7, title: "Завершение", weight: 5, type: "binary", max: 1, na: true, description: "Оператор подвёл итог или предложил помощь и попрощался."},
  {id: 8, title: "Грубые ошибки", weight: 0, type: "flag", max: 1, na: false, description: "Оскорбления, платные услуги в обход компании или раскрытие конфиденциальной информации."}
];

const tariffChat: Message[] = [
  {time:"00:00",role:"client",text:"Здравствуйте! Хочу разобраться в тарифах. Где посмотреть условия и как выбрать подходящий?"},
  {time:"00:02",role:"operator",text:"Здравствуйте! Меня зовут Алия, я специалист поддержки Freedom. С удовольствием помогу вам."},
  {time:"00:09",role:"operator",text:"Подскажите, пожалуйста, вас интересуют общие условия или тариф по уже открытому счёту?"},
  {time:"00:15",role:"client",text:"Общие условия. Счёт пока не открывал. И подскажите, где потом можно изменить тариф."},
  {time:"00:24",role:"operator",text:"Понимаю, хочется заранее разобраться в условиях. Актуальные тарифы можно посмотреть на официальном сайте в разделе с тарифами. Там есть условия и комиссии по каждому варианту."},
  {time:"00:39",role:"client",text:"Хорошо, посмотрю, спасибо!"},
  {time:"00:50",role:"operator",text:"Рада помочь! Если появятся вопросы — напишите нам. Хорошего дня!"},
  {time:"00:54",role:"system",text:"Диалог завершён оператором."}
];

export const conversations: Conversation[] = [
 {id:"DEMO-RU-001",topic:"Информация о тарифах",operator:"Алия Садыкова",initials:"АС",lang:"RU",channel:"chat",date:7,time:"14:32",duration:"00:54",ratings:[1,1,null,15,1,10,0,0],messages:tariffChat},
 {id:"DEMO-KZ-002",topic:"Жалпы ақпарат",operator:"Данияр Касенов",initials:"ДК",lang:"KZ",channel:"chat",date:7,time:"13:48",duration:"02:14",ratings:[1,1,null,20,1,10,1,0],messages:[
 {time:"00:00",role:"client",text:"Сәлеметсіз бе! Қосымша туралы ақпаратты қайдан табуға болады?"},
 {time:"00:04",role:"operator",text:"Сәлеметсіз бе! Менің атым Данияр. Freedom қолдау қызметіне қош келдіңіз."},
 {time:"00:15",role:"operator",text:"Қосымшаны жүктеу немесе оны пайдалану туралы білгіңіз келе ме?"},
 {time:"00:28",role:"client",text:"Пайдалану туралы нұсқаулық керек еді."},
 {time:"00:43",role:"operator",text:"Түсіндім. Ресми сайттағы анықтама бөлімін қараңыз. Қандай да бір қадам түсініксіз болса, бізге жазыңыз."},
 {time:"01:30",role:"client",text:"Рақмет, таптым!"},
 {time:"02:14",role:"operator",text:"Көмектесе алғаныма қуаныштымын! Тағы сұрақтарыңыз болса, хабарласыңыз. Күніңіз жақсы өтсін!"}]},
 {id:"DEMO-EN-003",topic:"Account access",operator:"Марк Алимов",initials:"МА",lang:"EN",channel:"voice",date:2,time:"11:16",duration:"03:42",ratings:[0,1,1,10,1,7,1,0],messages:[
 {time:"2026-11-02T11:16:00+05:00",role:"client",text:"Hello, I cannot access my account. I also need to update my contact details."},
 {time:"2026-11-02T11:16:05+05:00",role:"operator",text:"Which error message do you see?"},
 {time:"2026-11-02T11:16:32+05:00",role:"client",text:"It says my password is incorrect."},
 {time:"2026-11-02T11:17:00+05:00",role:"system",text:"Identity verification completed successfully."},
 {time:"2026-11-02T11:17:14+05:00",role:"operator",text:"I understand this is frustrating. Please use the password reset option in the official app."},
 {time:"2026-11-02T11:18:21+05:00",role:"client",text:"That worked. What about changing my details?"},
 {time:"2026-11-02T11:19:42+05:00",role:"operator",text:"Please contact support again for that request. Thank you for calling. Goodbye."}]},
 {id:"DEMO-RU-004",topic:"Получение отчёта",operator:"Алия Садыкова",initials:"АС",lang:"RU",channel:"chat",date:6,time:"16:05",duration:"02:18",ratings:[1,1,1,15,1,10,1,0],messages:[
 {time:"00:00",role:"client",text:"Добрый день! Нужен отчёт по счёту за прошлый месяц. В каком формате он доступен?"},
 {time:"00:03",role:"operator",text:"Добрый день! Меня зовут Алия. Подскажите, за какой именно месяц нужен отчёт?"},
 {time:"00:22",role:"client",text:"За октябрь."},
 {time:"00:35",role:"system",text:"Идентификация клиента успешно пройдена."},
 {time:"00:49",role:"operator",text:"Понимаю. Отчёт можно найти в соответствующем разделе личного кабинета, указав нужный период."},
 {time:"01:36",role:"client",text:"Нашёл, спасибо."},
 {time:"02:18",role:"operator",text:"Рада помочь! Если появятся дополнительные вопросы, обращайтесь. Всего доброго!"}]},
 {id:"DEMO-KZ-005",topic:"Қосымшаға кіру",operator:"Данияр Касенов",initials:"ДК",lang:"KZ",channel:"chat",date:4,time:"10:24",duration:"03:11",ratings:[1,1,1,15,1,7,1,0],messages:[
 {time:"00:00",role:"client",text:"Сәлеметсіз бе! Қосымшаға кіре алмай жатырмын."},
 {time:"00:05",role:"operator",text:"Сәлеметсіз бе! Мен Даниярмын. Экранда қандай қате көрсетілген?"},
 {time:"00:21",role:"client",text:"Құпиясөз дұрыс емес деп тұр. Құпиясөзді ауыстырғаннан кейін не істеу керек?"},
 {time:"00:49",role:"system",text:"Клиентті сәйкестендіру аяқталды."},
 {time:"02:40",role:"operator",text:"Қолайсыздық үшін кешірім сұраймын. Қосымшадағы құпиясөзді қалпына келтіру батырмасын басыңыз."},
 {time:"03:00",role:"client",text:"Түсінікті, рақмет."},
 {time:"03:11",role:"operator",text:"Сұрақтарыңыз болса, хабарласыңыз. Сау болыңыз!"}]},
 {id:"DEMO-RU-006",topic:"Обращение клиента",operator:"Тимур Беков",initials:"ТБ",lang:"RU",channel:"voice",date:1,time:"15:08",duration:"04:06",ratings:[1,0,1,15,0,10,1,1],messages:[
 {time:"2026-11-01T15:08:00+05:00",role:"client",text:"Я не понимаю, почему мой вопрос ещё не решён."},
 {time:"2026-11-01T15:08:04+05:00",role:"operator",text:"Здравствуйте, меня зовут Тимур."},
 {time:"2026-11-01T15:08:29+05:00",role:"system",text:"Идентификация клиента подтверждена."},
 {time:"2026-11-01T15:09:15+05:00",role:"operator",text:"Проверю ваше обращение, пожалуйста, подождите."},
 {time:"2026-11-01T15:10:32+05:00",role:"operator",text:"Ваше обращение находится в работе."},
 {time:"2026-11-01T15:11:10+05:00",role:"client",text:"Мне нужен понятный срок ответа."},
 {time:"2026-11-01T15:11:24+05:00",role:"operator",text:"Вы что, глупый? Я же уже всё объяснил."},
 {time:"2026-11-01T15:12:06+05:00",role:"operator",text:"Ответ поступит по обращению. До свидания."}]},
 {id:"DEMO-EN-007",topic:"General information",operator:"Марк Алимов",initials:"МА",lang:"EN",channel:"chat",date:5,time:"09:43",duration:"02:26",ratings:[1,1,null,15,1,10,1,0],messages:[
 {time:"00:00",role:"client",text:"Hello! Where can I find general information and the support hours?"},
 {time:"00:02",role:"operator",text:"Hello! My name is Mark. Do you mean information about the mobile app?"},
 {time:"00:16",role:"client",text:"Yes, that is right."},
 {time:"00:37",role:"operator",text:"Happy to help. You can find information about the app in the help section on the official website."},
 {time:"01:58",role:"client",text:"Thank you!"},
 {time:"02:26",role:"operator",text:"You are welcome! Let us know if you have further questions. Have a good day."}]},
 {id:"DEMO-RU-008",topic:"Статус обращения",operator:"Тимур Беков",initials:"ТБ",lang:"RU",channel:"chat",date:3,time:"17:20",duration:"01:52",ratings:[1,1,1,5,1,10,1,0],messages:[
 {time:"00:00",role:"client",text:"Здравствуйте! Какой статус у моего обращения?"},
 {time:"00:05",role:"operator",text:"Здравствуйте! Меня зовут Тимур. Уточните, пожалуйста, номер обращения."},
 {time:"00:19",role:"client",text:"Номер 042. Когда будет ответ и нужно ли что-то от меня?"},
 {time:"00:32",role:"system",text:"Идентификация клиента успешно пройдена."},
 {time:"00:58",role:"operator",text:"Понимаю ваше беспокойство. Пожалуйста, ожидайте ответа."},
 {time:"01:26",role:"client",text:"Понятно."},
 {time:"01:52",role:"operator",text:"Если понадобится дополнительная помощь, обращайтесь. Хорошего дня!"}]}
];

export function score(ratings: (number|null)[], checklist: Criterion[] = criteria): number | null {
  if(checklist.some((c,i)=>c.type!=="flag"&&(ratings[i]===undefined||(ratings[i]!==null&&!Number.isFinite(ratings[i])))))return null;
 let earned=0, total=0;
 checklist.forEach((c,i)=>{ const v=ratings[i]; if(c.type!=="flag" && v!==null && v!==undefined && c.max>0){total+=c.weight;earned+=Math.max(0,Math.min(v,c.max))/c.max*c.weight;} });
 return total ? Math.round(earned/total*10000)/100 : null;
}
export const formatScore = (v: number | null, precision=2) => v===null ? "—" : formatNumber(v,{minimumFractionDigits:precision, maximumFractionDigits:precision});
export const defaultAppeals: Appeal[] = [
 {id:"APL-001",conversationId:"DEMO-RU-001",criterionId:7,comment:"Оператор предложил дальнейшую помощь и попрощался в реплике 00:50. Прошу пересмотреть оценку завершения.",status:"open",originalScore:87.5,originalValue:0,date:"7 нояб., 14:40"},
 {id:"APL-002",conversationId:"DEMO-EN-003",criterionId:4,comment:"Прошу проверить полноту консультации: вопрос о доступе к аккаунту был решён.",status:"open",originalScore:75.5,originalValue:10,date:"7 нояб., 12:18"}
];

export function evidence(conversation: Conversation, criterionId: number): {indices:number[]; reason:string} {
 const m=conversation.messages;
 const lastOperator=m.map(x=>x.role).lastIndexOf("operator");
 const firstOperator=m.findIndex(x=>x.role==="operator");
 const identity=m.findIndex(x=>x.role==="system");
 const v=conversation.ratings[criterionId-1];
 if(conversation.id==="DEMO-RU-001") {
  const examples: Record<number,{indices:number[];reason:string}>={
   1:{indices:[1],reason:"Алия поздоровалась на языке клиента и представилась в первом ответе."},
   2:{indices:[2,3],reason:"До консультации оператор уточнил, относится ли вопрос к общим условиям или к действующему счёту."},
   3:{indices:[3],reason:"Клиент запрашивал общую информацию. Доступ к счёту и раскрытие персональных данных не требовались. Вес исключён из расчёта."},
   4:{indices:[3,4],reason:"Оператор объяснил, где найти тарифы, но не ответил, где впоследствии можно изменить выбранный тариф. Пропущена второстепенная деталь."},
   5:{indices:[4],reason:"Оператор вежлив и поддерживает желание клиента заранее разобраться в условиях."},
   6:{indices:[1,2,4,6],reason:"Ответы ясные и последовательные. Значительного ожидания в этом фрагменте не было."},
   7:{indices:[6,7],reason:v===1?"При пересмотре подтверждено: оператор предложил дальнейшую помощь и попрощался в 00:50.":"В исходной демонстрационной оценке завершение ошибочно отмечено как пропущенное. Проверенный участок: 00:50–00:54. Эта реплика является основанием для апелляции."},
   8:{indices:[1,2,4,6],reason:"В проверенных репликах оператора не выявлены оскорбления, услуги в обход компании или раскрытие конфиденциальной информации."}
  };return examples[criterionId]??{indices:[],reason:"Новый критерий ожидает оценки."};
 }
 if(criterionId===1)return {indices:[firstOperator],reason:v===1?"В первом ответе есть приветствие и представление оператора.":"В первом ответе оператор перешёл к уточнению без приветствия и представления."};
 if(criterionId===2)return {indices:[Math.min(firstOperator+1,m.length-1),firstOperator],reason:v===1?"Оператор уточнил необходимые детали запроса.":"До ответа не уточнены необходимые сведения об обращении клиента."};
 if(criterionId===3)return {indices:[v===null?0:Math.max(0,identity)],reason:v===null?"Вопрос об общей информации не требует идентификации; персональные данные не раскрывались.":"Системное сообщение подтверждает успешную идентификацию клиента."};
 if(criterionId===4)return {indices:[Math.max(0,lastOperator-2),Math.max(0,lastOperator-1)],reason:v===20?"Основной запрос закрыт, предложен понятный следующий шаг.":v===15?"Основной вопрос рассмотрен, но второстепенная деталь запроса осталась без ответа.":v===10?"Часть запроса решена, существенный дополнительный вопрос остался открытым.":"Дана общая подсказка, но конкретные дальнейшие действия клиенту не объяснены."};
 if(criterionId===5)return {indices:[conversation.id==="DEMO-RU-006"?6:Math.max(firstOperator,lastOperator-2)],reason:v===1?"Общение вежливое, оператор отреагировал на запрос клиента.":"Оператор использовал оскорбительную формулировку вместо ответа на проблему клиента."};
 if(criterionId===6)return {indices:[Math.max(0,lastOperator-2),lastOperator],reason:v===10?"Ответы последовательны и понятны; при ожидании оператор предупредил клиента.":"В разговоре есть заметный сбой: пауза или переход между вопросами без пояснения."};
 if(criterionId===7)return {indices:[lastOperator],reason:"Оператор обозначил следующий шаг или предложил помощь и попрощался."};
 if(criterionId===8)return {indices:v===1?[6]:[firstOperator,lastOperator],reason:v===1?"Обнаружено прямое оскорбление клиента. Грубое нарушение отмечается отдельно и не обнуляет итоговый балл автоматически.":"В демонстрационном диалоге грубых нарушений не выявлено."};
 return {indices:[],reason:"Новый критерий ожидает оценки."};
}
