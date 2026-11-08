"""Construct labelled counterfactual endings from cleaned development contexts only."""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ML = Path(__file__).resolve().parents[2]
BUNDLE = ML/"data/clean-dev-de20aae9"
SEED = "eva-clean-c07-20261108-v1"
LABELS = "ABC"

# (operator acknowledgement, client follow-up, two positive, two negative, two disconnect signals).
PHRASES = {
    "train": {
        "ru": ("Здравствуйте, чем могу помочь?", "Понятно, вопрос решён. Спасибо.",
               ("Мы разобрали ваш вопрос. До свидания!", "Если появятся вопросы, напишите ещё. Всего доброго!"),
               ("Хорошо.", "Если понадобится, напишите ещё."), ("Потеряна связь с клиентом.", "Клиент отключился, сообщение не доставлено.")),
        "kz": ("Сәлеметсіз бе! Қалай көмектесейін?", "Түсіндім, мәселем шешілді. Рақмет.",
               ("Сұрағыңызды қарастырдық. Сау болыңыз!", "Тағы сұрақ туындаса, бізге жазыңыз. Күніңіз сәтті өтсін!"),
               ("Жақсы.", "Қажет болса, бізге жазыңыз."), ("Клиентпен байланыс үзілді.", "Клиент желіден шықты, хабарлама жеткізілмеді.")),
        "en": ("Hello, how can I help?", "That resolves my question, thank you.",
               ("We have gone through your request. Goodbye!", "Write back if you need anything else. Have a good day!"),
               ("Okay.", "Message us again if you need to."), ("Connection to customer lost.", "Customer disconnected; message undelivered.")),
    },
    "held": {
        "ru": ("Добрый день! Я вас слушаю.", "Теперь всё получилось.",
               ("Вы подтвердили, что всё получилось. Хорошего вечера!", "Остались вопросы? Буду рад помочь. До встречи!"),
               ("Запрос выполнен.", "До свидания."), ("Соединение прервано клиентским устройством.", "Сессия прервана до ответа сотрудника.")),
        "kz": ("Қайырлы күн! Сізді тыңдап тұрмын.", "Енді бәрі дұрыс жұмыс істейді.",
               ("Бәрі дұрыс жұмыс істейтінін растадыңыз. Кешіңіз жақсы өтсін!", "Өзге сұрағыңыз болса, көмектесемін. Көріскенше!"),
               ("Өтінішіңіз орындалды.", "Сау болыңыз."), ("Абонент жағынан байланыс тоқтады.", "Қызметкер жауап беріп үлгермей, сеанс үзілді.")),
        "en": ("Good afternoon. I'm listening.", "Everything is working now.",
               ("You've confirmed everything is working again. Enjoy your evening!", "Can I assist with anything else? Take care!"),
               ("The request is complete.", "Goodbye."), ("The customer's device ended the connection.", "Session interrupted before the agent could reply.")),
    },
}
QUESTION = {"ru":"Как мне продолжить?", "kz":"Енді не істеуім керек?", "en":"How should I proceed?"}
END = {"ru":"Оператор завершил чат.", "kz":"Оператор чатты жапты.", "en":"Chat closed by the operator."}


def sha(text):
    return hashlib.sha256(text.encode("utf8")).hexdigest()


def safe_prefix(record):
    prefix=[]
    for turn in record["turns"]:
        if turn["role"]=="operator":
            break
        text=re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+\.\w+|(?<!\w)(?:\+?\d[\s()-]*){7,}", "[masked]", turn["text"])
        prefix.append((turn["role"],text))
    if not any(role=="client" for role,_ in prefix):
        raise ValueError("No original customer context before the operator")
    return prefix


def cases():
    # Only the list of development IDs is used. The old adjudicated labels are never targets.
    allowed={json.loads(l)["conversation_id"] for l in (BUNDLE/"labels/silver-development.jsonl").read_text(encoding="utf8").splitlines()}
    clean=(BUNDLE/"cqa_dataset_clean.json").read_bytes()
    if hashlib.sha256(clean).hexdigest()!="de20aae9e226e472a530ec2f6e4cbf39ba898ca47230d839bd6d6f69788cc646":
        raise ValueError("Unexpected dataset revision")
    sources=[r for r in json.loads(clean.decode("utf-8-sig"))["records"] if r["conversation_id"] in allowed]
    assert len(sources)==9 and all(r["channel"]=="chat" for r in sources)
    family=lambda r:r["conversation_id"].rsplit("-",1)[-1]
    counts=Counter(map(family,sources))
    held={family(min((r for r in sources if r["language"]==lang and counts[family(r)]==1),
                     key=lambda r:sha(SEED+r["conversation_id"]))) for lang in ("ru","kz","en")}
    for source in sources:
        group=family(source)
        split="held" if group in held else "train"
        lang=source["language"]
        greeting,confirmation,yes,no,drop=PHRASES[split][lang]
        prefix=safe_prefix(source)
        for i in range(2):
            for label in LABELS:
                turns=prefix+[("operator",greeting)]
                if label=="C":
                    turns.extend([("client",QUESTION[lang]),("system",drop[i])])
                else:
                    turns.extend([("client",confirmation),("operator",(yes if label=="A" else no)[i])])
                    if i==0 or label=="B":
                        turns.append(("system",END[lang]))
                yield dict(id=f"{source['conversation_id']}-{i}-{label}",source=source["conversation_id"],
                           family=group,language=lang,split=split,label=label,turns=turns)


def public_cases():
    from benchmarks.ledger_trial import public_holdout
    for split,name,count in [("public10","public-holdout.json",10),("public16","public-gold16.json",16)]:
        for req,gold,*_ in public_holdout(name,count):
            conv=req["conversation"]
            yield dict(id=conv["id"],source=conv["id"],family=conv["id"],split=split,language=conv["language"].lower(),
                       label={True:"A",False:"B",None:"C"}[gold["closing"]],
                       turns=[(m["speaker"],m["text"]) for m in conv["messages"]])
