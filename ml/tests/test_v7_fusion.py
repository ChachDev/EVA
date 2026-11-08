from copy import deepcopy
from types import SimpleNamespace

import pytest

from eva_ml.audit_trace import Trace
from eva_ml.local_v7 import LocalV7
from eva_ml.v7_fusion import V7Fusion, dialogue_repair, named_opening, waiting


def turns(*lines):
    return [dict(id=f"m{n}",speaker=s,text=t,timestamp=None,offset_seconds=None) for n,(s,t) in enumerate(lines,1)]


def test_explicit_wait_and_prior_notice():
    rows=turns(("operator","Добрый день."),("client","Где взять выписку?"),("client","Вы ещё здесь?"),("operator","В отчётах."))
    assert waiting(rows) == rows[1:3]
    rows[0]["text"] = "Пожалуйста, подождите, я проверю."
    assert waiting(rows) is None
    assert waiting(rows[1:]) is None


def test_repair_needs_both_advice_and_customer_correction():
    rows=turns(("client","Не могу войти."),("operator","Переустановите приложение."),("client","Какое приложение? Я на сайте."))
    assert dialogue_repair(rows,7)["evidence"][0]["message_ids"] == ["m2","m3"]
    rows[-1]["text"] = "Нашёл. А где открыть другой счёт?"
    assert dialogue_repair(rows,7) is None


def test_positive_opening_requires_own_direct_introduction(evaluation_request):
    conv=deepcopy(evaluation_request["conversation"])
    conv["language"]="RU"
    conv["messages"]=turns(("client","Как получить отчёт?"),("operator","Добрый вечер, я Анна. Чем помочь?"))
    assert named_opening(Trace(conv))=="m2"
    conv["messages"][1]["text"]="Добрый вечер. Если сказать «я Анна», что изменится?"
    assert named_opening(Trace(conv)) is None


def test_corrupt_archive_is_not_loaded(tmp_path):
    bad=tmp_path/"local.zip"
    bad.write_bytes(b"not the supplied zip")
    from benchmarks.friend_v7 import MODEL
    with pytest.raises(ValueError,match="Unexpected archive"):
        LocalV7(SimpleNamespace(model=MODEL),bad)


def test_incomplete_sources_preserve_primary(evaluation_request):
    request=deepcopy(evaluation_request)
    request["conversation"]["source"]={"gaps":True}
    base={"criteria":[],"warnings":[]}
    v=object.__new__(V7Fusion)
    assert v.combine(request,base,{"criteria":[]},{"criteria":[]})==base


def test_local_candidate_validates_order_and_excludes_unrelated_custom_rule(evaluation_request):
    request=deepcopy(evaluation_request)
    request["conversation"]["messages"]=turns(("client","Какой отчёт мне нужен?"),("operator","Как называется скачанный документ?"),
        ("client","Отчёт брокера."),("operator","Перейдите на первую страницу отчёта."))
    service=object.__new__(V7Fusion)
    service.reader=SimpleNamespace(read_facts=lambda *a: {"needs":"elicited"})
    rows={"needs":{"criterion_id":"needs","value":None}}
    service.check_facts(request,rows,{"needs"})
    assert rows["needs"]["value"] is True
    assert rows["needs"]["evidence"][0]["message_ids"]==["m1","m2","m3","m4"]
    rows={"needs":{"criterion_id":"needs","value":None}}
    service.check_facts(request,rows,set())
    assert rows["needs"]["value"] is None


def test_combination_does_not_apply_old_rules_to_edited_checklist(evaluation_request):
    req=deepcopy(evaluation_request)
    rule=next(c for c in req["checklist"]["criteria"] if c["id"]=="closing")
    rule["instruction"]="Отметить только подписанное соглашение."
    req["checklist"]["criteria"]=[rule]
    base={"criteria":[{"criterion_id":"closing","status":"scored","value":False,"reason":"Исходное решение","evidence":[]}],
          "warnings":[]}
    v7=deepcopy(base); v7["criteria"][0]["value"]=True
    v=object.__new__(V7Fusion); v.revision="test"
    after=v.combine(req,base,v7,base)
    assert after["criteria"]==base["criteria"] and after["criteria"]!=v7["criteria"]


def test_private_offer_after_an_answer_is_not_automatic_zero(evaluation_request):
    req=deepcopy(evaluation_request)
    req["conversation"]["messages"]=turns(("client","Где посмотреть реквизиты?"),
        ("operator","Откройте «Счёт», затем «Реквизиты»."),
        ("client","Спасибо. Нашёл."),
        ("operator","Ещё можете перевести деньги мне на карту."))
    v=object.__new__(V7Fusion)
    v.reader=SimpleNamespace(read_facts=lambda *args: {"consultation":"keep"})
    rows={"consultation":{"criterion_id":"consultation","value":15}}
    v.check_facts(req,rows,{"consultation"})
    assert rows["consultation"]["value"]==15
