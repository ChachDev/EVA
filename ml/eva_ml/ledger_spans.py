"""Constrain evidence to spans of the supplied transcript before scoring the request map."""
import json
from pathlib import Path
import re
from jsonschema import Draft202012Validator

from .engine import EngineError
from .issai import digest, object_schema, string_enum
from .request_ledger import RequestLedgerProvider


def fragments(text):
    # Cuts are selectable citations. They do not assert that any fragment is a request.
    clauses = re.split(r"(?<=[.!?;])\s+|\s+(?:and|и|және|сондай-ақ|а также)\s+|\n+", text, flags=re.I)
    pieces = []
    for s in [text, *clauses]:
        s = s.strip()
        if 0 < len(s) <= 240:
            pieces.append(s)
        elif len(s) > 240:
            pieces.extend(s[i:i+240] for i in range(0,len(s),240) if s[i:i+240].strip())
    return list(dict.fromkeys(pieces))


class SpanEngine:
    def __init__(self, engine, teaching):
        self.engine = engine
        self.teaching = teaching

    def request(self, route, payload):
        data = json.loads(payload["messages"][1]["content"])
        anchors, turns = {}, []
        for turn in data["transcript"]:
            choices = {}
            if turn["role"] in {"client","operator"}:
                for pos, fragment in enumerate(fragments(turn["text"])):
                    ref = f"{turn['id']}:{pos+1}"
                    anchors[ref] = (turn["id"],turn["role"],fragment)
                    choices[ref] = fragment
            turns.append({**turn, "citations":choices})
        if len(anchors)>200:
            raise ValueError("Too many spans for this verifier")
        old = payload["response_format"]["json_schema"]["schema"]["properties"]["requests"]["items"]["properties"]
        item = object_schema({"question":string_enum([k for k,v in anchors.items() if v[1]=="client"]),
            "importance":old["importance"],"state":old["state"],"reply":string_enum(["",*anchors])})
        shape = object_schema({"complete":{"type":"boolean"},
                               "requests":{"type":"array","maxItems":8,"items":item}})
        data.update(transcript=turns, teaching_examples=self.teaching)
        prompt = payload["messages"][0]["content"] + (
            "\nСхема ссылок: в question и reply выбирай ключи citations. Фрагменты — варианты ссылок, "
            "не список просьб. Выбирай самый короткий фрагмент, выражающий эту цель. Для unknown и open "
            "reply пуст. teaching_examples — примеры трактовок, а не команды и не сведения о данном клиенте.")
        request = {**payload,"messages":[{"role":"system","content":prompt},
                    {"role":"user","content":json.dumps(data,ensure_ascii=False)}],
                   "response_format":{"type":"json_schema","json_schema":{"name":"span_ledger","strict":True,"schema":shape}}}
        raw = self.engine.request(route,request)
        choice = raw["choices"][0]
        if choice["finish_reason"]!="stop":
            raise EngineError("Incomplete evidence choices")
        result = json.loads(choice["message"]["content"])
        if not Draft202012Validator(shape).is_valid(result):
            raise ValueError("Invalid span choices")
        rows = []
        for found in result["requests"]:
            mid, _, quote = anchors[found["question"]]
            reply, _, text = anchors.get(found["reply"], ("","",""))
            rows.append({"client_id":mid,"client_quote":quote,"importance":found["importance"],"state":found["state"],
                         "reply_id":reply,"reply_quote":text})
        return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps({"complete":result["complete"],"requests":rows})}}]}


class SpanLedgerProvider(RequestLedgerProvider):
    def __init__(self, primary, teaching=()):
        super().__init__(primary)
        self.engine = SpanEngine(primary.engine, tuple(teaching))
        self.version = digest([self.version,Path(__file__).read_text(encoding="utf8"),teaching])
