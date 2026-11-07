"""Parse unambiguous declarative banking events. Free conversation stays with the model.

Anchored *whole assertions*, trusted speaker role and event order are all required.
A quote, denial, question, conditional or additional clause cannot match these productions.
"""
import re
from unicodedata import normalize

NUMBER=r"[+-]?\d[\d\s,.]*"
UNIT=r"(?:тенге|тг|теңге|₸|tenge|kzt|usd|доллар(?:а|ов)?|руб(?:лей|ля)?\.?)"
AMOUNT=rf"(?:{UNIT}\s*)?{NUMBER}(?:\s*{UNIT})?"
BALANCE=re.compile(rf"(?:на\s+вашем\s+(?:банковском\s+)?сч[её]т[еу]|(?:баланс|остаток)(?:\s+(?:вашего\s+сч[её]та|на\s+вашем\s+сч[её]те))?)\s*(?:составляет|равен|[:—-])?\s*{AMOUNT}"
    rf"|(?:сіздің\s+)?(?:шотыңызда|шотыңыздың\s+(?:қалдығы|балансы))\s*[:—-]?\s*{AMOUNT}(?:\s+бар)?"
    rf"|your\s+(?:account\s+)?balance\s+(?:is|stands at)\s+{AMOUNT}",re.I)
VERIFIED=re.compile(r"(?:личность(?:\s+клиента)?\s+(?:успешно\s+)?подтверждена(?:\s+системой)?"
    r"|(?:проверка\s+личности|идентификация)(?:\s+клиента)?\s+(?:успешно\s+)?(?:пройдена|подтверждена)"
    r"|(?:system\s+)?identity\s+verification\s+(?:succeeded|passed|completed successfully)"
    r"|(?:customer\s+)?identity\s+(?:was\s+|has\s+been\s+)?(?:successfully\s+)?verified"
    r"|(?:жүйе\s+)?(?:клиенттің\s+)?жеке\s+басын\s+растады"
    r"|(?:клиенттің\s+)?жеке\s+басы\s+расталды)",re.I)


def assertion(pattern,text):
    return pattern.fullmatch(normalize("NFKC",text).strip().rstrip(".!").strip()) is not None


def grounded_order(trace):
    if trace.ambiguous or trace.unknown or trace.gaps or trace.complete_start is False:
        return None
    rows=list(trace.records.values())
    actors=[m for m in rows if m["role"]=="operator"]
    # Only the first assessed action may establish that verification preceded all
    # protected actions. Earlier unparsed operator turns require semantic review.
    if not actors: return None
    action=actors[0]
    if not assertion(BALANCE,action["text"]): return None
    checks=[m for m in rows if m["role"]=="system"]
    verified=next((m for m in checks if m.get("event")=="identity_verified" or assertion(VERIFIED,m["text"])),None)
    if verified is None: return None
    if action["order"]<verified["order"] and any(m["order"]<action["order"] for m in checks):
        return None  # an earlier system entry may also confirm identity
    return {"apply":"yes","action":action["utterance_id"],"verified":verified["utterance_id"]}
