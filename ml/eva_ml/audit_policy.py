"""Resolve a checklist snapshot. Changed instructions never inherit stale Freedom overrides."""
import json
from pathlib import Path
from .issai import digest, table_for

CONFIG = json.loads(Path(__file__).with_name("audit_policy.json").read_text(encoding="utf-8"))

def rules_for(checklist, enabled=True):
    result = {}
    for item in checklist["criteria"]:
        official = bool(table_for(item) and item["id"] in CONFIG["criteria"] and
                        table_for(item)["rubric_instruction"] == item["instruction"])
        # Match the stable ID *and* its original rubric: copying another known rubric
        # into this ID must not silently reactivate the previous policy.
        original = originals().get(item["id"], {})
        official = official and all(original.get(k) == item[k] for k in
                                    ("instruction","type","scale","allow_na","na_condition"))
        extra = CONFIG["criteria"][item["id"]] if official and enabled else {}
        rule = {**item, **extra, "official":official, "stage":extra.get("stage","whole"),
                "version":checklist["version"], "source":CONFIG["source"] if official else "Checklist: "+checklist["id"],
                "evidence_requirement":"Точная цитата и роль; при пропуске — проверенный диапазон."}
        rule["hash"] = digest(rule)
        result[item["id"]] = rule
    return result

from functools import lru_cache

@lru_cache(maxsize=1)
def originals():
    path = Path(__file__).resolve().parents[2]/"contracts"/"examples"/"checklist.json"
    return {r["id"]:r for r in json.loads(path.read_text(encoding="utf-8"))["criteria"]}
