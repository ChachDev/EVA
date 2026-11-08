"""Fixed synthetic privacy contrasts; compare the same Qwen first pass with and without the second read."""
from argparse import ArgumentParser
from copy import deepcopy
import json
from pathlib import Path
from time import perf_counter

from benchmarks.clean_dev import PRIVATE, ROOT, read_json
from benchmarks.share_report import same, scores
from eva_ml.consultation_v11 import ConsultationV11Provider
from eva_ml.contracts import Contracts
from eva_ml.disclosure_review import DisclosureReviewer
from eva_ml.engine import DEFAULT_WEIGHTS, LocalEngine


def cases(holdout=False, transfer=False):
    check = read_json(ROOT.parent / "contracts/examples/checklist.json")
    check["criteria"].append(dict(check["criteria"][-1], id="monitor", title="Сообщение баланса",
        instruction="Отметить любое сообщение конкретного баланса оператором, включая ответ владельцу после идентификации."))
    development = [
        ("own-before", "RU", [("client", "Здравствуйте. Какой остаток у меня?"), ("system", "Идентификация пройдена."),
          ("operator", "Меня зовут Ирина. У вас на счёте сорок два рубля.")],
         {"gross_errors": False, "identity": True, "monitor": True, "contact": False, "closing": False}),
        ("own-after", "RU", [("client", "Здравствуйте. Какой остаток у меня?"),
          ("operator", "Меня зовут Ирина. У вас на счёте сорок два рубля."), ("system", "Идентификация пройдена.")],
         {"gross_errors": True, "identity": False, "monitor": True, "contact": False}),
        ("third-given", "EN", [("client", "What is my neighbour's available balance?"), ("system", "Customer identity verified."),
          ("operator", "Her checking account has forty-one dollars left.")], {"gross_errors": True, "monitor": True}),
        ("third-refusal", "EN", [("client", "What is my neighbour's available balance?"), ("system", "Customer identity verified."),
          ("operator", "I can only discuss your own account. I can't give you her figure.")], {"gross_errors": False, "monitor": False}),
        ("mixed-third", "KZ", [("client", "Ағамның шотында қанша бар?"), ("system", "Клиенттің жеке басы расталды."),
          ("operator", "На счёте вашего брата осталось восемь тысяч теңге.")], {"gross_errors": True, "monitor": True}),
        ("mixed-refusal", "KZ", [("client", "Ағамның шотында қанша бар?"), ("system", "Клиенттің жеке басы расталды."),
          ("operator", "Ағаңыздың шоты туралы бұл мәліметті бере алмаймын.")], {"gross_errors": False, "monitor": False}),
        ("real-greeting", "EN", [("client", "How much do I have in my account?"), ("system", "Customer identity verified."),
          ("operator", "Good morning! My name is Dana. Your balance is ninety pounds. Can I help further? Goodbye.")],
         {"contact": True, "closing": True, "gross_errors": False, "monitor": True}),
    ]
    independent = [
        ("en-hypothetical", "EN", [("client", "Can you tell me my aunt's balance?"),
          ("operator", "For illustration only: if her account held 67 pounds, that would be an example. I cannot disclose her balance.")],
         {"gross_errors": False, "monitor": False}),
        ("kz-order", "KZ", [("client", "Менің картамдағы қалдық қанша?"),
          ("operator", "Сіздің шотыңызда 380 теңге бар."), ("system", "Клиенттің жеке басы расталды.")],
         {"gross_errors": True, "identity": False, "monitor": True}),
        ("ru-family", "RU", [("client", "Можно узнать остаток у дяди?"), ("system", "Личность клиента подтверждена."),
          ("operator", "На карте вашего дяди 815 рублей.")], {"gross_errors": True, "monitor": True}),
        ("ru-proper", "RU", [("client", "Сколько денег на моей карте?"), ("system", "Личность клиента подтверждена."),
          ("operator", "Добрый вечер! Меня зовут Олег. На вашем счёте 26 рублей. Могу ещё чем-нибудь помочь? До свидания!")],
         {"gross_errors": False, "monitor": True, "contact": True, "closing": True}),
    ]
    transfer_cases = [
        ("kz-verified-owner", "KZ", [("client", "Картамда неше теңге бар?"), ("system", "Клиенттің жеке басы расталды."),
          ("operator", "Менің есімім Меруерт. Шотыңызда 516 теңге бар.")],
         {"identity": True, "monitor": True, "gross_errors": False, "contact": False, "closing": False}),
        ("en-verified-third", "EN", [("client", "Tell me the balance on my partner's card."), ("system", "Identity verified."),
          ("operator", "Your partner has 903 dollars on that card.")], {"monitor": True, "gross_errors": True}),
        ("ru-refusal", "RU", [("client", "Покажи остаток по карте моей знакомой."),
          ("operator", "Сведения о карте вашей знакомой я предоставить не могу.")], {"monitor": False, "gross_errors": False}),
        ("en-late", "EN", [("client", "What funds can I use from my current account?"),
          ("operator", "Your available balance is 602 euros."), ("system", "Customer identity verified.")],
         {"monitor": True, "identity": False, "gross_errors": True}),
    ]
    subset = transfer_cases if transfer else independent if holdout else development
    for label, lang, messages, expected in subset:
        conv = {"id": "contrast-" + label, "channel": "agent_chat", "language": lang, "audio_url": None,
                "source": {"complete_start": True, "complete_end": True, "target_operator_id": "A"},
                "messages": [dict({"id": f"m{i:04d}", "speaker": role, "text": text, "timestamp": None, "offset_seconds": None},
                                  **({"speaker_id": "A"} if role == "operator" else {}))
                             for i, (role, text) in enumerate(messages, 1)]}
        yield {"schema_version": "1.0.0", "conversation": conv, "checklist": check}, expected


def run(port, output, snapshot, holdout=False, transfer=False):
    if not output.resolve().is_relative_to(PRIVATE.resolve()):
        raise ValueError("Keep individual results in ml/data")
    engine = LocalEngine(port, Path(DEFAULT_WEIGHTS).stem)
    primary, reviewer, contracts = ConsultationV11Provider(engine), DisclosureReviewer(engine), Contracts()
    samples = list(cases(holdout, transfer))
    if snapshot:
        req = read_json(snapshot)
        samples.insert(0, (req, {"contact": False, "closing": False, "identity": True, "gross_errors": False}))
    with output.open("x", encoding="utf8") as f:
        for req, reference in samples:
            contracts.validate_request(req)
            base = primary.evaluate(req)
            start = perf_counter()
            revised = reviewer.refine(req, deepcopy(base))
            contracts.validate_response(req, revised)
            b, r = scores(base), scores(revised)
            counts = [sum(same(values[k], v) for k, v in reference.items()) for values in (b, r)]
            item = {"id": req["conversation"]["id"], "reference": reference, "baseline": base, "review": revised,
                    "seconds_review": round(perf_counter()-start, 2), "correct": counts}
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            f.flush()
            print(item["id"], counts, "/", len(reference), "review seconds", item["seconds_review"], flush=True)


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--snapshot", type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--holdout", action="store_true")
    group.add_argument("--transfer", action="store_true")
    args = parser.parse_args()
    run(args.port, args.output, args.snapshot, args.holdout, args.transfer)
