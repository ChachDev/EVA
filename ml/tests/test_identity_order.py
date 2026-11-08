from copy import deepcopy

from eva_ml.audit_trace import Trace
from eva_ml.identity_order import first_action


def trace(items,complete=True):
    return Trace({"id":"test","language":"RU","source":{"complete_start":complete,"complete_end":True},
                  "messages":[{"id":f"m{i}","speaker":role,"text":text,"timestamp":f"00:{i:02}"}
                              for i,(role,text) in enumerate(items,1)]})


def test_same_action_before_or_after_verification_with_routing_notice():
    handoff=("system","Чат передан оператору.")
    confirmation=("system","Личность клиента подтверждена.")
    request=("client","Каков статус моей заявки?")
    action=("operator","Ваша заявка одобрена.")
    for events,mid,expected in (([handoff,request,confirmation,action],"m4",True),
                                ([handoff,request,action,confirmation],"m3",False)):
        result=first_action(trace(events),{mid:{"kind":"actual","quote":action[1]}})
        assert result["value"] is expected
        assert mid in result["evidence"][0]["message_ids"]


def test_unknown_event_incomplete_history_and_bot_are_not_negative_proof():
    request=("client","Проверьте заявку.")
    action=("operator","Заявка одобрена.")
    facts={"m3":{"kind":"actual","quote":action[1]}}
    assert first_action(trace([request,("system","Внешняя проверка"),action]),facts) is None
    assert first_action(trace([request,("system","Чат передан оператору"),action],False),facts) is None
    assert first_action(trace([request,("system","Чат передан оператору"),("bot",action[1])]),facts) is None
    assert first_action(trace([request,("operator","Введите код."),action]),
                        {"m2":{"kind":"auth","quote":""},**facts}) is None
