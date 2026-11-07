"""A truthful placeholder: no inference, score calculation or external I/O."""

from typing import Any


def evaluate(request: dict[str, Any]) -> dict[str, Any]:
    # An uncertain reference locates the conversation; it is not proof of a score.
    first_message_id = request["conversation"]["messages"][0]["id"]
    warnings = ["STUB_PROVIDER: модель не подключена; результаты не являются оценками качества."]
    if request["conversation"]["audio_url"]:
        warnings.append("AUDIO_NOT_PROCESSED: аудио не скачивается и не анализируется; используется только переданный текст.")
    return {
        "schema_version": request["schema_version"],
        "conversation_id": request["conversation"]["id"],
        "checklist_id": request["checklist"]["id"],
        "checklist_version": request["checklist"]["version"],
        "provider": "stub",
        "model": "unconfigured",
        "criteria": [
            {
                "criterion_id": criterion["id"],
                "status": "needs_review",
                "value": None,
                "reason": "Модель ещё не подключена. Критерий не оценён; нужна ручная проверка.",
                "evidence": [{"kind": "uncertain", "message_ids": [first_message_id]}],
            }
            for criterion in request["checklist"]["criteria"]
        ],
        "warnings": warnings,
    }
