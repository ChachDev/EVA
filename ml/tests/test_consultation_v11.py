import json
from copy import deepcopy

import pytest

from eva_ml.consultation_v11 import VERSION, conflicting_fact, select_provider
from eva_ml.issai import ISSAIProvider, table_for


class RecheckEngine:
    model = "synthetic"

    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, route, payload):
        self.calls.append(payload)
        schema = payload["response_format"]["json_schema"]["schema"]
        assert schema["properties"]["id"]["enum"] == ["op"]
        return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(self.response)}}]}


def example(evaluation_request):
    c = next(c for c in evaluation_request["checklist"]["criteria"] if c["id"]=="consultation")
    transcript = {"language":"EN","messages":[{"id":"client","speaker":"client","text":"What can I do?"},
                                              {"id":"op","speaker":"operator","text":"Open the document."}]}
    row = {"facts":["yes","no","no","yes","no","no","no"],"evidence":["client"]}
    return c, transcript, row


def test_same_facts_can_be_consistent_or_conflict_depending_on_remaining_questions(evaluation_request):
    c, conversation, row = example(evaluation_request)
    keys = ["apply", *table_for(c)["questions"]]
    answers = dict(zip(keys,row["facts"],strict=True))
    assert conflicting_fact(answers) == "generic_only"
    assert conflicting_fact({**answers, "major_open":"yes"}) is None
    assert conflicting_fact({**answers, "major_open":"yes", "next_step":"yes"}) == "generic_only"
    assert conflicting_fact({**answers, "no_answer":"yes","next_step":"yes"}) == "no_answer"
    assert conflicting_fact({**answers, "apply":"no"}) is None


@pytest.mark.parametrize("result,expected", [
    ({"answer":"no","id":"op","quote":"Open the document."}, 20),
    ({"answer":"yes","id":"op","quote":"Open the document."}, None),
    ({"answer":"unknown","id":"op","quote":"Open"}, None),
    ({"answer":"no","id":"client","quote":"What can I do?"}, None),
    ({"answer":"no","id":"op","quote":"Invented quotation"}, None),
])
def test_single_check_preserves_other_facts_and_refuses_unsupported_result(evaluation_request, result, expected):
    c, convo, row = example(evaluation_request)
    original = deepcopy(row)
    engine = RecheckEngine(result)
    variant = select_provider(engine, VERSION)
    decision = variant.criterion_result(c,row,convo)
    assert row == original and len(engine.calls)==1
    assert decision["value"] == expected
    assert decision["status"] == ("needs_review" if expected is None else "scored")
    if expected is not None:
        assert decision["evidence"][0]["message_ids"][0]=="op"
    baseline = select_provider(engine,"baseline")
    assert type(baseline) is ISSAIProvider and baseline.criterion_result(c,row,convo)["value"]==5
    assert len(engine.calls)==1


def test_replaced_instruction_and_other_criteria_bypass_experiment(evaluation_request):
    c, convo, row = example(evaluation_request)
    engine = RecheckEngine({})
    variant = select_provider(engine, VERSION)
    same_title_new_meaning = {**c,"instruction":"Оцените подтверждение результата."}
    result = variant.criterion_result(same_title_new_meaning,
        {"value":"10","reason":"Часть требований выполнена.","kind":"message","evidence":["op"]},convo)
    assert result["value"] == 10
    assert variant.criterion_result({**c,"id":"another"},row,convo)["value"]==5
    assert engine.calls == []
