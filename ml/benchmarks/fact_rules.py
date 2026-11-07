"""Evaluate configured fact tables without branching on criterion IDs."""
import json
from itertools import product
from pathlib import Path


TABLES = json.loads(Path(__file__).with_name("frozen_checklist.json").read_text(encoding="utf-8"))


def gate(facts):
    if "exclude" not in facts:
        return "apply"
    pair = tuple(facts[key]["answer"] for key in ("exclude", "apply"))
    return {("yes", "no"): "exclude", ("no", "yes"): "apply"}.get(pair, "review")


def matches(expression, answers):
    if expression == "*":
        return True
    return any(all(answers[term.lstrip("!")] != term.startswith("!") for term in group.split())
               for group in expression.split("|"))


def decide(row):
    status = gate(row["facts"])
    if status != "apply":
        return {"exclude": "N/A", "review": "review"}[status]
    keys = list(row["questions"])
    options = {"yes": [True], "no": [False], "unknown": [True, False]}
    values = set()
    for combination in product(*(options[row["facts"][key]["answer"]] for key in keys)):
        answers = dict(zip(keys, combination))
        values.add(next(value for expr, value in row["rules"] if matches(expr, answers)))
    return next(iter(values)) if len(values) == 1 else "review"
