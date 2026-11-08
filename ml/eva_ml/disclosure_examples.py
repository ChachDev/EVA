"""Locally fitted character n-gram nearest neighbours for choosing balanced in-context demonstrations."""
from collections import Counter
from functools import lru_cache
from math import log, sqrt
import re
import unicodedata

# Synthetic training turns. Labels describe what the operator said, not the final Freedom flag.
EXAMPLES = (
    ("На вашем текущем счёте осталось двенадцать рублей.", "own"),
    ("Остаток по вашему договору составляет 2800 тенге.", "own"),
    ("Сіздің шотыңызда тоқсан теңге қалды.", "own"),
    ("Картаңыздағы қаражат — бес мың теңге.", "own"),
    ("Your available funds are seventy pounds.", "own"),
    ("There is $82 in your checking account.", "own"),
    ("У другого вкладчика на депозите пять тысяч.", "third"),
    ("У вашей сестры на карте 730 рублей.", "third"),
    ("Әкеңіздің шотында үш жүз теңге бар.", "third"),
    ("Көршіңіздің қалдығы — 2400 теңге.", "third"),
    ("Your brother has $350 in his account.", "third"),
    ("The other account holder has twelve euros left.", "third"),
    ("Не могу назвать остаток по чужому счёту.", "no_value"),
    ("Вы процитировали пример: «У клиента 400 рублей».", "no_value"),
    ("Сумму на вашем счёте уточнить пока не удалось.", "no_value"),
    ("Өзге адамның шотын қарауға рұқсат жоқ.", "no_value"),
    ("Мысалы, шотта мың теңге болса, комиссия осылай саналады.", "no_value"),
    ("Қалдықты қолданбадан көре аласыз.", "no_value"),
    ("I cannot share another person's assets.", "no_value"),
    ("If you had ten dollars in the account, this fee would apply.", "no_value"),
    ("The fee for a statement is five dollars.", "no_value"),
)


def grams(text):
    words = re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())
    padded = ("^" + word + "$" for word in words)
    return {word[i:i+3] for word in padded for i in range(len(word)-2)}


class ExampleIndex:
    def __init__(self, examples):
        self.samples = [(text, label, grams(text)) for text, label in examples]
        counts = Counter(g for _, _, features in self.samples for g in features)
        self.weights = {g: (1 + log((1 + len(examples)) / (1 + n))) ** 2 for g, n in counts.items()}

    def examples_for(self, text):
        query = grams(text)
        best = {}
        for example, label, features in self.samples:
            common = sum(self.weights[g] for g in sorted(query & features))
            norm = sqrt(sum(self.weights[g] for g in sorted(features)))
            score = common / norm if norm else 0
            if label not in best or score > best[label][0]:
                best[label] = score, example
        return [{"operator_text": example, "action": label} for label, (_, example) in best.items()]


@lru_cache(maxsize=1)
def trained_index():
    return ExampleIndex(EXAMPLES)
