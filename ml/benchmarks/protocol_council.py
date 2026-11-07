"""Review the experimental protocol with two local models, using no pilot data."""
import argparse
import json
from pathlib import Path

from evidence_first import OUTPUT, SERVER, WEIGHTS
from local_compare import engine, local_request, write_json


QUESTION = (
    "A team will audit multilingual operator transcripts using a configurable rubric. "
    "A local judge first extracts evidence and decides applicability with three states, then separately grades applicable criteria only. "
    "The first phase and its evidence are frozen, and the second sees the full transcript to retain context. "
    "Briefly identify one failure mode, a concrete safeguard and a paired metric for detecting false improvement. "
    "No examples from any real pilot are supplied. Answer in Russian."
)
ADVISORS = {"issai": WEIGHTS, "qwen": Path("D:/EVA-ML/models/Qwen3.5-9B-Q5_K_M.gguf")}


def consult(name):
    question = QUESTION
    if name == "issai":
        peer = json.loads((OUTPUT / "council-qwen.json").read_text(encoding="utf-8"))
        question += " Another advisor proposed: " + json.dumps(peer, ensure_ascii=False)
        question += ". Challenge or refine their advice with a specific check."
    fields = {key: {"type": "string", "minLength": 1, "maxLength": 220}
              for key in ("risk", "safeguard", "metric")}
    schema = {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}
    payload = {
        "model": "eva-local",
        "messages": [
            {"role": "system", "content": "Ты независимый технический рецензент. Оцени проект эксперимента."},
            {"role": "user", "content": question},
        ],
        "temperature": 0, "seed": 42, "max_tokens": 900,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": {"name": "advice", "strict": True, "schema": schema}},
    }
    reply = local_request("/v1/chat/completions", payload)["choices"][0]
    if reply["finish_reason"] != "stop":
        raise ValueError("Incomplete advice")
    advice = json.loads(reply["message"]["content"])
    if set(advice) != set(fields):
        raise ValueError("Missing advice")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_json(OUTPUT / f"council-{name}.json", advice)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("advisor", choices=ADVISORS)
    args = parser.parse_args()
    with engine(SERVER, ADVISORS[args.advisor]):
        consult(args.advisor)
