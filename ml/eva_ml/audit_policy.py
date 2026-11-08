"""Resolve a checklist snapshot. Changed instructions never inherit stale Freedom overrides."""
import json
from pathlib import Path
from .issai import digest

CONFIG = json.loads(Path(__file__).with_name("audit_policy.json").read_text(encoding="utf-8"))

def rules_for(checklist, enabled=True):
    result = {}
    for item in checklist["criteria"]:
        original = originals().get(item["id"], {})
        # The first-pass model's fact tables cover a subset of the official checklist.
        official = (checklist["id"] == "freedom-cqa" and item["id"] in CONFIG["criteria"]
                    and all(original.get(k) == item.get(k) for k in
                            ("title","instruction","type","scale","allow_na","na_condition"))
                    and original.get("depends_on",[]) == item.get("depends_on",[]))
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
