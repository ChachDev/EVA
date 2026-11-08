"""Author-labelled diagnostic pairs. The translations of a pair form one family."""
from benchmarks.audit_run import CHECK


CASES = {
    "RU": (
        ("Не вижу квитанцию. Как открыть её копию?", "Где открыть копию квитанции и как изменить адрес доставки?",
         "В журнале заказов выберите заказ и нажмите «Квитанция».", 20, 10),
        ("Как добавить товар в избранное?", "Как добавить товар в избранное?",
         ("Попробуйте что-нибудь в приложении.", "Откройте карточку товара и нажмите звезду."), 5, 20),
        ("Где выключить автозапуск? И если знаете, какого цвета переключатель? Цвет мне просто интересен.",
         "Где выключить автозапуск?", "В настройках откройте «Запуск» и снимите галочку «Автозапуск».", 15, 20),
        ("Как открыть журнал ошибок?", "Добрый день.", "Здравствуйте! Чем могу помочь?", 0, None),
    ),
    "KZ": (
        ("Түбіртегімді таба алмай тұрмын. Көшірмесін қалай ашамын?",
         "Түбіртектің көшірмесін қайдан ашамын және жеткізу мекенжайын қалай өзгертемін?",
         "Тапсырыстар тарихынан тапсырысты таңдап, «Түбіртек» батырмасын басыңыз.", 20, 10),
        ("Тауарды таңдаулыларға қалай қосамын?", "Тауарды таңдаулыларға қалай қосамын?",
         ("Қолданбада бірдеңе жасап көріңіз.", "Тауардың бетіне кіріп, жұлдызшаны басыңыз."), 5, 20),
        ("Автоматты іске қосуды қайдан өшіремін? Айта кетсеңіз, қосқыштың түсі қандай? Түсі тек қызық болып тұр.",
         "Автоматты іске қосуды қайдан өшіремін?",
         "Баптауларда «Іске қосу» бөлімін ашып, «Автоматты іске қосу» белгісін алыңыз.", 15, 20),
        ("Қателер журналын қалай ашамын?", "Сәлеметсіз бе.", "Сәлеметсіз бе! Қалай көмектесе аламын?", 0, None),
    ),
    "EN": (
        ("I can't find my receipt. How do I open a copy?", "How do I open a copy of my receipt and change my delivery address?",
         "In Order History, select your order and press Receipt.", 20, 10),
        ("How do I add an item to favourites?", "How do I add an item to favourites?",
         ("Try doing something in the app.", "Open the item page and tap its star."), 5, 20),
        ("Where do I disable auto-start? Also, what colour is the switch? I'm just curious about the colour.",
         "Where do I disable auto-start?", "Open Startup in Settings and uncheck Auto-start.", 15, 20),
        ("How do I open the error log?", "Hello.", "Hello! How can I help?", 0, None),
    ),
}


def synthetic():
    for lang, families in CASES.items():
        for family, (q1, q2, answers, left, right) in enumerate(families):
            for side, (question, expected) in enumerate(((q1, left), (q2, right))):
                answer = answers[side] if isinstance(answers, tuple) else answers
                cid = f"ledger-{lang}-{family}-{side}"
                messages = [{"id": f"m{i:04}", "speaker": role, "text": text,
                             "offset_seconds": pos, "timestamp": f"00:{pos:02}"}
                            for i, (role, text, pos) in enumerate(
                                (("client", question, 0), ("operator", answer, 5),
                                 ("system", "Session ended", 8)), 1)]
                conv = {"id": cid, "language": lang, "channel": "agent_chat", "audio_url": None,
                        "source": {"complete_start": True, "complete_end": True}, "messages": messages}
                yield ({"schema_version": "1.0.0", "conversation": conv, "checklist": CHECK},
                       {"consultation": expected}, str(family))
