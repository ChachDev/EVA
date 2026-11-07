"""EVA Trace. Source character offsets are Python Unicode code points, end exclusive."""
from dataclasses import dataclass
from .issai import digest


@dataclass
class Trace:
    conversation: dict

    def __post_init__(self):
        source = self.conversation.get("source", {})
        self.target = source.get("target_operator_id")
        self.complete_start = source.get("complete_start")
        self.complete_end = source.get("complete_end")
        self.gaps = source.get("gaps", False)
        self.records = {}
        named = {m.get("speaker_id") for m in self.conversation["messages"] if m["speaker"] == "operator"}
        self.ambiguous = len(named) > 1 and self.target is None
        self.unknown = False
        for order, m in enumerate(self.conversation["messages"]):
            role = m["speaker"]
            if role == "operator" and self.target is not None and m.get("speaker_id") != self.target:
                role = "other_operator" if m.get("speaker_id") else "unknown"
            self.unknown |= role == "unknown"
            self.records[m["id"]] = dict(utterance_id=m["id"], speaker_id=m.get("speaker_id"), role=role,
                text=m["text"], order=order, timestamp=m.get("timestamp"), language=m.get("language",self.conversation["language"]),
                start=m.get("offset_seconds"), end=m.get("end_seconds"), quality=m.get("quality"))
        self.hash = digest(self.conversation)

    def scope_problem(self, stage):
        if self.ambiguous:
            return "Не указан оцениваемый оператор при нескольких операторах."
        if self.unknown:
            return "Неизвестная роль может изменить вывод об отсутствии действия."
        if self.gaps or (stage in ("start","whole") and self.complete_start is False) or (
                stage in ("end","whole") and self.complete_end is False):
            return "Проверяемый участок исходного разговора неполон."
        return None

    def verify(self, proof):
        row = self.records.get(proof["id"])
        quote = proof["quote"]
        if row is None or not quote.strip():
            raise ValueError("Неизвестная реплика или пустая цитата.")
        start = row["text"].find(quote)
        if start < 0:
            raise ValueError("Цитата не совпадает с исходной репликой.")
        if row["end"] is not None and (row["start"] is None or row["end"] < row["start"]):
            raise ValueError("Конец фрагмента не соответствует исходному началу.")
        return {**{k:row[k] for k in ("utterance_id","role","speaker_id","order","timestamp","start","end")},
                "quote":quote,"chars":[start,start+len(quote)]}

    def span(self, stage):
        rows = list(self.records)
        return {"first":rows[0],"last":rows[-1],"stage":stage,
                "complete_start":self.complete_start,"complete_end":self.complete_end,"gaps":self.gaps}
