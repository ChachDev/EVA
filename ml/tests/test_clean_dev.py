import csv
import json

from benchmarks.clean_dev import compare_record, compare_views, eva_request, template_candidates
from benchmarks.clean_dev_labels import blank_source


def voice_pair():
    original = {
        "channel": "voice call", "language": "RU", "audio_url": "private-trigger",
        "transcript": [
            {"timestamp": "2026-11-01T15:10:10.125+00:00", "speaker": "client", "text": "Запрос."},
            {"timestamp": "2026-11-01T15:10:15.625+00:00", "speaker": "operator", "text": "Ответ."},
        ],
    }
    clean = {
        "conversation_id": "sample", "channel": "voice", "language": "ru", "audio_url": "private-trigger",
        "audio_url_is_signed_trigger": True, "duration_sec": 5.5, "turn_count": 2, "quality_flags": [],
        "resolved_guess": True, "scenario_tags": ["test"], "turns": [
            {"turn_idx": i, "t_sec": i * 5.5, "role": m["speaker"], "text": m["text"]}
            for i, m in enumerate(original["transcript"])
        ],
    }
    return clean, original


def test_voice_preserves_source_time_without_media_offsets_or_answer_hints():
    clean, original = voice_pair()
    assert not compare_record(clean, original)
    checklist = {"version": 1, "criteria": [{"id": "closing", "type": "boolean", "scale": [],
                                             "allow_na": True, "instruction": "Завершение", "na_condition": "Обрыв"}]}
    req = eva_request(clean, original, checklist)
    conv = req["conversation"]
    assert conv["audio_url"] is None
    assert conv["messages"][1] == {"id": "m0002", "speaker": "operator", "text": "Ответ.",
                                   "timestamp": original["transcript"][1]["timestamp"], "offset_seconds": None}
    assert set(conv) == {"id", "channel", "language", "audio_url", "messages"}
    blank = blank_source(req)
    assert blank["annotation"][0]["allowed"] == [True, False, None]
    assert blank["annotation"][0]["human_status"] == "unlabeled"
    assert blank["group"] is None


def test_paired_audit_detects_text_speaker_time_and_index_changes():
    clean, original = voice_pair()
    clean["turns"][1].update(turn_idx=2, text="Иной ответ", role="client", t_sec=9)
    assert compare_record(clean, original) == {
        "text_at_position": 1, "turn_index": 1, "speaker_at_position": 1, "relative_time": 1, "duration": 1}


def test_detects_missing_turn_or_different_metadata_in_sidecar(tmp_path):
    clean, _ = voice_pair()
    row = {k: ";".join(v) if isinstance(v, list) else str(v) for k, v in clean.items()
           if k not in {"turns", "audio_url", "audio_url_is_signed_trigger"}}
    row["resolved_guess"] = "False"
    with (tmp_path / "cqa_conversations.csv").open("w", encoding="utf8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    turn = {**{k: clean[k] for k in ("conversation_id", "channel", "language")}, **clean["turns"][0]}
    (tmp_path / "cqa_turns.jsonl").write_text(json.dumps(turn) + "\n", encoding="utf8")
    assert compare_views(tmp_path, [clean]) == {"csv_differing_records": 1, "jsonl_differing_turns": 1}


def test_shared_timeline_is_a_cross_language_candidate_independent_of_id_suffix():
    clean, _ = voice_pair()
    chat = dict(clean, channel="chat")
    translated = dict(chat, conversation_id="different-123", language="kz")
    shifted = dict(translated, conversation_id="other", turns=[dict(t, t_sec=t["t_sec"] + 1) for t in chat["turns"]])
    assert template_candidates([chat, translated, shifted, clean]) == [["sample", "different-123"]]
