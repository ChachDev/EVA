"""Serve explicitly reviewed training examples, scoped to the rubric and scenario family."""
from hashlib import sha256
import json
from pathlib import Path

from .audit_policy import rules_for
from .issai import digest
from .ledger_spans import SpanLedgerProvider


def read_catalog(path):
    raw = Path(path).read_bytes()
    if len(raw) > 1_000_000:
        raise ValueError("Calibration catalog exceeds its size limit")
    catalog = json.loads(raw.decode("utf-8-sig"))
    if catalog["format"] != 1:
        raise ValueError("Unknown calibration catalog format")
    examples, chats = catalog["examples"], catalog["chats"]
    if not isinstance(chats,dict) or not isinstance(examples,list):
        raise ValueError("Invalid calibration manifest")
    for chat in chats.values():
        if chat["split"] not in {"development","evaluation"} or not isinstance(chat["family"],str) or not chat["family"].strip():
            raise ValueError("Unknown split or family")
    seen = set()
    for item in examples:
        source = chats.get(item["source_chat"])
        if (not source or source["split"]!="development" or item["id"] in seen or
                not all(isinstance(item.get(k),str) and item[k].strip() for k in
                        ("id","human_reviewer","approval_reference","fragment","reason","rubric_hash")) or
                not 1<=len(item["fragment"])<=700 or not 1<=len(item["reason"])<=320 or
                type(item["value"]) is not int or item["value"] not in {0,5,10,15,20}):
            raise ValueError("Invalid or unapproved training example")
        seen.add(item["id"])
    return catalog, sha256(raw).hexdigest()


def examples_for(catalog, req):
    cid = req["conversation"]["id"]
    destination = catalog["chats"].get(cid)
    if not destination:
        return ()
    rule = rules_for(req["checklist"]).get("consultation")
    if not rule or not rule["official"]:
        return ()
    chosen = []
    for item in catalog["examples"]:
        source = catalog["chats"][item["source_chat"]]
        if source["family"] == destination["family"] or item["rubric_hash"] != rule["hash"]:
            continue
        chosen.append({k:item[k] for k in ("id","fragment","value","reason")})
        if len(chosen)==3:
            break
    return tuple(chosen)


class CalibratedProvider:
    def __init__(self, primary, path):
        self.primary = primary
        self.name, self.model = primary.name, primary.model
        self.catalog, self.catalog_hash = read_catalog(path)

    def evaluate(self, req):
        chosen = examples_for(self.catalog, req)
        result = SpanLedgerProvider(self.primary, chosen).evaluate(req)
        result["warnings"].append(f"Калибровка {self.catalog_hash}, использовано примеров: {len(chosen)}, "
                                  f"SHA-256 набора: {digest(chosen)}.")
        return result
