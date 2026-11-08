from copy import deepcopy
from types import SimpleNamespace

from eva_ml.audit_trace import Trace
from eva_ml.consultation_v11 import select_provider
from eva_ml.engine import EngineError
from eva_ml.interaction_guard import GuardedInteractionReviewer, corroborated
from eva_ml.interaction_review import episode_proof, episodes, finding_for, forced_repetition, supported_row
from eva_ml.stage_review import evidence_text


def sample(template):
    request = deepcopy(template)
    request["conversation"]["messages"] = [
        {"id": f"m{i}", "speaker": s, "text": t, "timestamp": None, "offset_seconds": None}
        for i, (s, t) in enumerate([("client", "Не могу войти."), ("operator", "Установите приложение."),
                                   ("client", "Какое приложение? Я на компьютере.")], 1)]
    return request


def test_correct_actor_order_and_source_quotes(evaluation_request):
    c = sample(evaluation_request)["conversation"]
    trace, (fragments, _) = Trace(c), evidence_text(c["messages"])
    finding = {"finding": "reask", "refs": ["u1.1", "u2.1", "u3.1"], "reason": "Клиент уточнил непонятный совет."}
    result = supported_row("dialogue", finding, fragments, trace)
    assert result["value"] == 4 and result["evidence"][0]["message_ids"] == ["m1", "m2", "m3"]
    assert "Какое приложение?" in result["reason"]
    assert supported_row("dialogue", dict(finding, refs=["u3.1", "u2.1", "u1.1"]), fragments, trace) is None
    assert supported_row("needs", dict(finding, finding="clarified"), fragments, trace) is None


def test_ready_and_missed_are_distinct_and_unknown_keeps_primary(evaluation_request):
    c = sample(evaluation_request)["conversation"]
    trace, (fragments, _) = Trace(c), evidence_text(c["messages"])
    for f, refs, expected, kind in [("ready", ["u1.1", "u2.1"], None, "not_applicable"),
                                    ("missed", ["u1.1", "u2.1", "u3.1"], False, "missing_action")]:
        row = supported_row("needs", {"finding": f, "refs": refs, "reason": "Проверено"}, fragments, trace)
        assert row["status"] == "scored" and row["value"] is expected and row["evidence"][0]["kind"] == kind
    assert supported_row("needs", {"finding": "unknown", "reason": "", "refs": []}, fragments, trace) is None


def test_fallback_and_unchanged_rubric(evaluation_request, monkeypatch):
    req = sample(evaluation_request)
    reviewer = select_provider(SimpleNamespace(model="local"), "interaction-review-v1")
    base = {"criteria": [], "warnings": []}
    def unavailable(*args):
        raise EngineError("offline")
    monkeypatch.setattr(reviewer, "read_facts", unavailable)
    res, state = reviewer.review_interaction(req, base)
    assert state == "failed" and res["criteria"] == base["criteria"] and base["warnings"] == []
    req["conversation"]["source"] = {"complete_start": False}
    assert reviewer.review_interaction(req, base) == (base, "out_of_scope")
    req["conversation"]["source"] = {}
    for criterion in req["checklist"]["criteria"]:
        criterion["instruction"] += " Иное правило."
    assert reviewer.review_interaction(req, base) == (base, "out_of_scope")


def test_episode_requires_the_real_reaction_to_that_operator(evaluation_request):
    c = sample(evaluation_request)["conversation"]
    rows = c["messages"]
    fragments = {m["id"]: (i, m, m["text"]) for i, m in enumerate(rows)}
    trace = Trace(c)
    exchanges = episodes(rows)
    finding = {"finding": "reask", "request": "m1", "reply": "m2", "reason": "Уточнил, какое приложение имеется в виду."}
    result = episode_proof("dialogue", finding, exchanges, fragments, trace)
    assert result["value"] == 4 and result["evidence"][0]["message_ids"] == ["m1", "m2", "m3"]
    assert episode_proof("dialogue", dict(finding, request="m3"), exchanges, fragments, trace) is None
    assert finding_for("dialogue", {"quality": "clear", "reaction": "reask"}) == "reask"
    assert finding_for("needs", {"specificity": "missing", "handling": "elicited"}) == "clarified"


def test_new_question_after_resolution_is_not_a_reask(evaluation_request):
    c = sample(evaluation_request)["conversation"]
    c["messages"][-1]["text"] = "Таптым. Басқа картаны қалай қосамын?"
    fragments = {m["id"]: (i, m, m["text"]) for i, m in enumerate(c["messages"])}
    finding = {"finding": "reask", "request": "m1", "reply": "m2", "reason": "Повтор."}
    assert episode_proof("dialogue", finding, episodes(c["messages"]), fragments, Trace(c)) is None


def test_explicit_restatement_requires_generic_operator_question(evaluation_request):
    c = sample(evaluation_request)["conversation"]
    c["messages"][1]["text"] = "Здравствуйте. Чем могу вам помочь?"
    c["messages"][-1]["text"] = "Я уже писал выше. Не могу войти."
    fragments = {m["id"]: (i, m, m["text"]) for i, m in enumerate(c["messages"])}
    assert forced_repetition({"request": "m1"}, episodes(c["messages"]), fragments, Trace(c))["refs"] == ["m1", "m2", "m3"]
    assert forced_repetition({"request": "m3"}, episodes(c["messages"]), fragments, Trace(c)) is None


def test_extra_source_guard_keeps_original_on_followup_about_a_new_topic(evaluation_request):
    req = sample(evaluation_request)
    c = req["conversation"]
    old = {"criterion_id": "dialogue", "status": "scored", "value": 10, "reason": "Исходная оценка", "evidence": []}
    primary = {"criteria": [old], "warnings": []}
    change = dict(old, value=4, evidence=[{"kind": "message", "message_ids": ["m1", "m2", "m3"]}])
    candidate = {"criteria": [change], "warnings": []}
    assert corroborated("dialogue", old, change, c)
    c["messages"][2]["text"] = "Спасибо! А какие ещё тарифы доступны?"
    reviewer = GuardedInteractionReviewer(SimpleNamespace(model="local"))
    after = reviewer.gate(req, primary, candidate)
    assert after["criteria"] == primary["criteria"] and candidate["criteria"][0]["value"] == 4


def test_needs_only_never_changes_dialogue_and_has_its_own_version(evaluation_request):
    req = sample(evaluation_request)
    engine = SimpleNamespace(model="local")
    reviewer = GuardedInteractionReviewer(engine, apply="needs")
    other = GuardedInteractionReviewer(engine, apply="both")
    assert reviewer.guarded_hash != other.guarded_hash
    old = {"criterion_id": "dialogue", "status": "scored", "value": 10, "evidence": []}
    primary = {"criteria": [old], "warnings": []}
    proposal = dict(old, value=4, evidence=[{"kind":"message", "message_ids":["m1", "m2", "m3"]}])
    candidate = {"criteria":[proposal], "warnings":[]}
    accepted = other.gate(req, primary, candidate)["criteria"][0]
    assert accepted["value"] == 4 and accepted["evidence"] == proposal["evidence"] and "Какое приложение?" in accepted["reason"]
    assert reviewer.gate(req, primary, candidate)["criteria"] == [old]
