"""Only synthetic organizer envelopes; no network, client data, or audio I/O."""

from copy import deepcopy

import pytest

from app.contracts import ContractError
from app.importer import DatasetImportError, normalize_dataset


def make_record(identifier="synthetic-001", channel="agent chat", language="RU"):
    timestamp = "00:03" if channel == "agent chat" else "2026-11-07T10:00:03+05:00"
    return {
        "conversation_id": identifier,
        "channel": channel,
        "language": language,
        "transcript": [{"timestamp": timestamp, "speaker": "operator", "text": "Synthetic greeting"}],
        "audio_url": None,
    }


def envelope(records=None, error=None):
    records = [make_record()] if records is None else records
    return {
        "error": error,
        "isSuccess": True,
        "data": {"dataset_group_id": "synthetic-group", "total_records": len(records), "records": records},
    }


@pytest.mark.parametrize("error", [None, ""])
@pytest.mark.parametrize("language", ["RU", "KZ", "EN"])
@pytest.mark.parametrize("channel", ["agent chat", "voice call"])
def test_supported_datasets_are_normalized_to_contracts(contracts, channel, language, error):
    payload = envelope([make_record(channel=channel, language=language)], error=error)
    before = deepcopy(payload)
    result = normalize_dataset(payload, contracts, expected_group="synthetic-group")
    assert result.group_id == "synthetic-group"
    conversation = result.conversations[0]
    contracts.validate("conversation", conversation)
    assert conversation["channel"] == {"agent chat": "agent_chat", "voice call": "voice_call"}[channel]
    assert conversation["language"] == language
    assert conversation["messages"][0]["id"] == "m0001"
    assert conversation["messages"][0]["offset_seconds"] == (3 if channel == "agent chat" else None)
    assert payload == before
    assert result.raw_records == before["data"]["records"]
    result.raw_records[0]["transcript"][0]["text"] = "Changed copy"
    result.conversations[0]["messages"][0]["text"] = "Changed normalized copy"
    assert payload == before


def test_message_ids_use_source_order_not_text_or_time(contracts):
    record = make_record()
    record["transcript"] = [
        {"timestamp": "123:59", "speaker": "client", "text": "  Repeated text  "},
        {"timestamp": "00:00", "speaker": "bot", "text": "  Repeated text  "},
        {"timestamp": "00:00", "speaker": "system", "text": "Synthetic context"},
    ]
    messages = normalize_dataset(envelope([record]), contracts).conversations[0]["messages"]
    assert [message["id"] for message in messages] == ["m0001", "m0002", "m0003"]
    assert [message["offset_seconds"] for message in messages] == [7439, 0, 0]
    assert [message["timestamp"] for message in messages] == ["123:59", "00:00", "00:00"]
    assert messages[0]["text"] == "  Repeated text  "


def test_voice_chronology_compares_instants_without_guessing_audio_origin(contracts):
    record = make_record(channel="voice call")
    record["audio_url"] = "https://audio.example.invalid/synthetic.mp3?token=synthetic-value"
    record["transcript"] = [
        {"timestamp": "2026-11-07T10:00:00+05:00", "speaker": "client", "text": "Synthetic request"},
        {"timestamp": "2026-11-07T05:00:00Z", "speaker": "operator", "text": "Synthetic reply"},
        {"timestamp": "2026-11-07T05:00:00.125+00:00", "speaker": "operator", "text": "Synthetic follow-up"},
    ]
    conversation = normalize_dataset(envelope([record]), contracts).conversations[0]
    assert [message["timestamp"] for message in conversation["messages"]] == [
        message["timestamp"] for message in record["transcript"]
    ]
    assert all(message["offset_seconds"] is None for message in conversation["messages"])
    assert conversation["audio_url"] == record["audio_url"]


def test_empty_complete_dataset_is_valid(contracts):
    result = normalize_dataset(envelope([]), contracts)
    assert result.conversations == result.raw_records == []


@pytest.mark.parametrize("payload", [None, [], "synthetic", 1, True, {}, {"isSuccess": True}])
def test_rejects_invalid_envelope_shape(contracts, payload):
    with pytest.raises(DatasetImportError):
        normalize_dataset(payload, contracts)


@pytest.mark.parametrize("success", [False, None, 1, 0, "true", [], {}])
def test_success_is_a_strict_boolean(contracts, success):
    payload = envelope()
    payload["isSuccess"] = success
    with pytest.raises(DatasetImportError):
        normalize_dataset(payload, contracts)


@pytest.mark.parametrize("error", ["synthetic failure", " ", False, True, 0, [], {}])
def test_success_envelope_must_not_contain_error(contracts, error):
    with pytest.raises(DatasetImportError):
        normalize_dataset(envelope(error=error), contracts)


@pytest.mark.parametrize("total", [True, False, 1.0, "1", None, -1, 0, 2])
def test_rejects_invalid_or_partial_dataset_totals(contracts, total):
    payload = envelope()
    payload["data"]["total_records"] = total
    with pytest.raises(DatasetImportError):
        normalize_dataset(payload, contracts)


@pytest.mark.parametrize("records", [None, {}, "synthetic", True])
def test_records_must_be_an_array(contracts, records):
    payload = envelope()
    payload["data"]["records"] = records
    with pytest.raises(DatasetImportError):
        normalize_dataset(payload, contracts)


@pytest.mark.parametrize("group", [None, True, 1, "", " \t"])
def test_dataset_group_must_be_a_nonempty_string(contracts, group):
    payload = envelope()
    payload["data"]["dataset_group_id"] = group
    with pytest.raises(DatasetImportError):
        normalize_dataset(payload, contracts)


@pytest.mark.parametrize("expected", [True, 1, "", " ", "another-synthetic-group"])
def test_expected_group_is_checked_without_echoing_it(contracts, expected):
    with pytest.raises(DatasetImportError):
        normalize_dataset(envelope(), contracts, expected_group=expected)


@pytest.mark.parametrize("level", ["envelope", "data", "record", "message"])
@pytest.mark.parametrize("operation", ["missing", "extra"])
def test_requires_known_fields_at_each_level(contracts, level, operation):
    payload = envelope()
    objects = {
        "envelope": payload,
        "data": payload["data"],
        "record": payload["data"]["records"][0],
        "message": payload["data"]["records"][0]["transcript"][0],
    }
    target = objects[level]
    if operation == "missing":
        del target[next(iter(target))]
    else:
        target["synthetic-extra"] = "synthetic-value"
    with pytest.raises(DatasetImportError):
        normalize_dataset(payload, contracts)


@pytest.mark.parametrize("field,value", [
    ("conversation_id", None), ("conversation_id", True), ("conversation_id", ""), ("conversation_id", " \n"),
    ("channel", "agent_chat"), ("channel", "voice_call"), ("channel", "Agent chat"), ("channel", []),
    ("language", "ru"), ("language", "RU "), ("language", {}),
    ("transcript", []), ("transcript", None), ("transcript", {}), ("transcript", [None]),
])
def test_rejects_invalid_record_values(contracts, field, value):
    record = make_record()
    record[field] = value
    with pytest.raises(DatasetImportError):
        normalize_dataset(envelope([record]), contracts)


@pytest.mark.parametrize("field,value", [
    ("speaker", "agent"), ("speaker", "Operator"), ("speaker", []),
    ("text", None), ("text", ""), ("text", " \t\n"), ("text", False),
    ("timestamp", None), ("timestamp", False), ("timestamp", ""),
])
def test_rejects_invalid_message_values(contracts, field, value):
    record = make_record()
    record["transcript"][0][field] = value
    with pytest.raises(DatasetImportError):
        normalize_dataset(envelope([record]), contracts)


@pytest.mark.parametrize("timestamp", [
    "0:01", "00:60", "00:-1", "-01:00", "01:00:00", "00:01.5", " 00:01", "00:01\n", "٠٠:٠١",
    "2026-11-07T00:00:00Z",
])
def test_rejects_invalid_chat_timestamps(contracts, timestamp):
    record = make_record()
    record["transcript"][0]["timestamp"] = timestamp
    with pytest.raises(DatasetImportError):
        normalize_dataset(envelope([record]), contracts)


@pytest.mark.parametrize("timestamp", [
    "00:01", "2026-11-07", "2026-11-07T10:00:00", "2026-11-07 10:00:00+05:00",
    "2026-02-29T10:00:00Z", "2026-13-01T10:00:00Z", "2026-11-07T25:00:00Z",
    "2026-11-07T10:60:00Z", "2026-11-07T10:00:60Z", "2026-11-07T10:00:00+24:00",
    "2026-11-07T10:00:00+05:99", "2026-11-07T10:00:00-0160",
])
def test_rejects_invalid_or_naive_voice_timestamps(contracts, timestamp):
    record = make_record(channel="voice call")
    record["transcript"][0]["timestamp"] = timestamp
    with pytest.raises(DatasetImportError):
        normalize_dataset(envelope([record]), contracts)


def test_rejects_reversed_voice_instants(contracts):
    record = make_record(channel="voice call")
    record["transcript"].append({
        "timestamp": "2026-11-07T05:00:02Z", "speaker": "client", "text": "Synthetic reversed reply",
    })
    with pytest.raises(DatasetImportError):
        normalize_dataset(envelope([record]), contracts)


@pytest.mark.parametrize("url", [
    "", " ", False, 1, "file:///synthetic.mp3", "//audio.example.invalid/a.mp3", "https:///missing-host",
    "https://user:secret@audio.example.invalid/a.mp3", "https://audio.example.invalid:invalid/a.mp3",
    "https://audio.example.invalid:99999/a.mp3", "https://audio.example.invalid/a\n.mp3",
])
def test_rejects_invalid_audio_urls(contracts, url):
    record = make_record(channel="voice call")
    record["audio_url"] = url
    with pytest.raises(DatasetImportError):
        normalize_dataset(envelope([record]), contracts)


@pytest.mark.parametrize("url", [None, "http://audio.example.invalid/a.mp3", "https://audio.example.invalid/a.mp3"])
def test_audio_urls_are_only_preserved_not_downloaded(contracts, monkeypatch, url):
    import socket
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("Importer attempted network I/O"))
    record = make_record(channel="voice call")
    record["audio_url"] = url
    result = normalize_dataset(envelope([record]), contracts)
    assert result.conversations[0]["audio_url"] == url


def test_duplicate_conversation_ids_reject_whole_dataset(contracts):
    payload = envelope([make_record(), make_record()])
    before = deepcopy(payload)
    with pytest.raises(DatasetImportError):
        normalize_dataset(payload, contracts)
    assert payload == before


def test_invalid_later_record_is_atomic_and_errors_are_sanitized(contracts):
    secret = "SYNTHETIC_PRIVATE_DATA_DO_NOT_ECHO"
    bad_record = make_record(identifier=secret)
    bad_record["transcript"][0].update(timestamp=secret, text=secret)
    payload = envelope([make_record(), bad_record])
    before = deepcopy(payload)
    with pytest.raises(DatasetImportError) as captured:
        normalize_dataset(payload, contracts)
    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)
    assert payload == before


def test_normalized_contract_validation_failure_is_sanitized(contracts, monkeypatch):
    def reject(_name, _value):
        raise ContractError("SYNTHETIC_PRIVATE_CONTRACT_DETAIL")
    monkeypatch.setattr(contracts, "validate", reject)
    with pytest.raises(DatasetImportError) as captured:
        normalize_dataset(envelope(), contracts)
    assert "SYNTHETIC_PRIVATE_CONTRACT_DETAIL" not in str(captured.value)
    assert captured.value.__suppress_context__ is True
