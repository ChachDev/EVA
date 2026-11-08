"""Hand-authored Freedom regression expectations; no customer text."""
PHRASES = {
 "RU": ["Назовите остаток на моём счёте.", "Личность подтверждена системой.", "На вашем счёте 100 тенге.",
        "Помогите с консультацией.", "Предлагаю платную консультацию лично, в обход компании. Переведите деньги мне.",
        "Нет, личные платные консультации я не оказываю.", "Назовите баланс счёта другого клиента.",
        "На счёте другого клиента 100 тенге.", "Я не могу раскрывать чужие сведения."],
 "KZ": ["Менің шотымда қанша ақша бар?", "Жүйе клиенттің жеке басын растады.", "Шотыңызда 100 теңге бар.",
        "Кеңес алғым келеді.", "Компаниядан тыс жеке ақылы кеңес ұсынамын. Ақшаны маған аударыңыз.",
        "Жоқ, мен жеке ақылы кеңес бермеймін.", "Басқа клиенттің шотында қанша ақша бар?",
        "Басқа клиенттің шотында 100 теңге бар.", "Басқа клиенттің деректерін бере алмаймын."],
 "EN": ["What is the balance of my account?", "System identity verification succeeded.", "Your balance is 100 tenge.",
        "Can you advise me?", "I offer private paid advice outside the company. Pay me directly.",
        "No, I do not provide private paid advice.", "What is the balance of another customer's account?",
        "The other customer's account balance is 100 tenge.", "I cannot disclose another customer's information."],
}

def cases():
    for language, p in PHRASES.items():
        for scenario, criterion, value, events in [
            ("identity-before","identity",True,[("client",0),("system",1),("operator",2)]),
            ("identity-after","identity",False,[("client",0),("operator",2),("system",1)]),
            ("private-offer","gross_errors",True,[("client",3),("operator",4)]),
            ("private-refusal","gross_errors",False,[("client",4),("operator",5)]),
            ("disclose","gross_errors",True,[("client",6),("operator",7)]),
            ("refuse","gross_errors",False,[("client",6),("operator",8)])]:
            yield {"id":f"pair-{language}-{scenario}","channel":"agent_chat","language":language,"audio_url":None,
                   "messages":[{"id":f"m{i+1:04}","speaker":s,"timestamp":f"00:{i:02}",
                                "offset_seconds":i,"text":p[j]} for i,(s,j) in enumerate(events)]},criterion,value
