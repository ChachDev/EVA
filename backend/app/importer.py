"""Normalize an explicitly supplied organizer dataset without external I/O."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import re
from urllib.parse import urlsplit

from app.contracts import ContractError, Contracts


class DatasetImportError(ValueError):
    """An import error safe to display without source data or identifiers."""


@dataclass(frozen=True)
class NormalizedDataset:
    group_id: str
    conversations: list[dict]
    raw_records: list[dict]


_ENVELOPE_FIELDS = {"error", "isSuccess", "data"}
_DATA_FIELDS = {"dataset_group_id", "total_records", "records"}
_RECORD_FIELDS = {"conversation_id", "channel", "language", "transcript", "audio_url"}
_MESSAGE_FIELDS = {"timestamp", "speaker", "text"}
_CHANNELS = {"agent chat": "agent_chat", "voice call": "voice_call"}
_LANGUAGES = {"RU", "KZ", "EN"}
_SPEAKERS = {"client", "operator", "bot", "system"}
_CHAT_TIMESTAMP = re.compile(r"[0-9]{2,}:[0-5][0-9]")
_VOICE_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}"
    r"(?::[0-9]{2}(?:[.,][0-9]+)?)?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):?[0-5][0-9])"
)


def _object(value: object, fields: set[str], label: str) -> dict:
    if type(value) is not dict or set(value) != fields:
        raise DatasetImportError(f"Invalid {label} fields.")
    return value


def _nonempty_string(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise DatasetImportError(f"Invalid {label}.")
    return value


def _audio_url(value: object) -> str | None:
    if value is None:
        return None
    url = _nonempty_string(value, "audio URL")
    if any(character.isspace() for character in url):
        raise DatasetImportError("Invalid audio URL.")
    try:
        parts = urlsplit(url)
        valid = (
            parts.scheme in {"http", "https"}
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
        )
        # Accessing port also rejects invalid or out-of-range numeric ports.
        parts.port
    except ValueError:
        raise DatasetImportError("Invalid audio URL.") from None
    if not valid:
        raise DatasetImportError("Invalid audio URL.")
    return url


def _chat_offset(timestamp: str) -> int:
    if not _CHAT_TIMESTAMP.fullmatch(timestamp):
        raise DatasetImportError("Invalid chat timestamp.")
    try:
        minutes, seconds = (int(part) for part in timestamp.split(":"))
    except ValueError:
        raise DatasetImportError("Invalid chat timestamp.") from None
    return minutes * 60 + seconds


def _voice_instant(timestamp: str) -> datetime:
    if not _VOICE_TIMESTAMP.fullmatch(timestamp):
        raise DatasetImportError("Invalid voice timestamp.")
    try:
        instant = datetime.fromisoformat(timestamp.replace("z", "+00:00").replace("Z", "+00:00"))
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError
    except ValueError:
        raise DatasetImportError("Invalid voice timestamp.") from None
    return instant


def _conversation(record: object, contracts: Contracts) -> dict:
    source = _object(record, _RECORD_FIELDS, "conversation record")
    identifier = _nonempty_string(source["conversation_id"], "conversation identifier")
    channel = source["channel"]
    language = source["language"]
    if type(channel) is not str or channel not in _CHANNELS:
        raise DatasetImportError("Unsupported conversation channel.")
    if type(language) is not str or language not in _LANGUAGES:
        raise DatasetImportError("Unsupported conversation language.")
    transcript = source["transcript"]
    if type(transcript) is not list or not transcript:
        raise DatasetImportError("A conversation must contain a nonempty transcript.")

    messages: list[dict] = []
    previous_instant: datetime | None = None
    for index, value in enumerate(transcript, start=1):
        message = _object(value, _MESSAGE_FIELDS, "transcript message")
        timestamp = _nonempty_string(message["timestamp"], "message timestamp")
        text = _nonempty_string(message["text"], "message text")
        speaker = message["speaker"]
        if type(speaker) is not str or speaker not in _SPEAKERS:
            raise DatasetImportError("Unsupported transcript speaker.")
        offset: int | None = None
        if channel == "agent chat":
            offset = _chat_offset(timestamp)
        else:
            instant = _voice_instant(timestamp)
            if previous_instant is not None and instant < previous_instant:
                raise DatasetImportError("Voice timestamps must be in chronological order.")
            previous_instant = instant
            # No audio-origin timestamp exists in the organizer contract. The
            # first transcript message must not be treated as the recording start.
        messages.append({
            "id": f"m{index:04d}",
            "speaker": speaker,
            "text": text,
            "timestamp": timestamp,
            "offset_seconds": offset,
        })

    conversation = {
        "id": identifier,
        "channel": _CHANNELS[channel],
        "language": language,
        "messages": messages,
        "audio_url": _audio_url(source["audio_url"]),
    }
    try:
        contracts.validate("conversation", conversation)
    except ContractError:
        raise DatasetImportError("Normalized conversation violates its contract.") from None
    # Generated IDs reflect source order, including identical messages.
    if len({message["id"] for message in messages}) != len(messages):
        raise DatasetImportError("Normalized message identifiers must be unique.")
    return conversation


def normalize_dataset(
    payload: object,
    contracts: Contracts,
    expected_group: str | None = None,
) -> NormalizedDataset:
    """Validate the whole envelope and return independent raw/normalized copies.

    A single invalid record rejects the dataset. Partial/paginated envelopes are
    rejected because this adapter has no organizer pagination contract.
    """
    envelope = _object(payload, _ENVELOPE_FIELDS, "dataset envelope")
    if envelope["isSuccess"] is not True:
        raise DatasetImportError("Organizer dataset request was unsuccessful.")
    error = envelope["error"]
    if error is not None and (type(error) is not str or error != ""):
        raise DatasetImportError("Organizer dataset envelope contains an error.")
    data = _object(envelope["data"], _DATA_FIELDS, "dataset data")
    group_id = _nonempty_string(data["dataset_group_id"], "dataset group identifier")
    if expected_group is not None:
        _nonempty_string(expected_group, "expected dataset group")
        if group_id != expected_group:
            raise DatasetImportError("Dataset group does not match the requested group.")
    records = data["records"]
    total = data["total_records"]
    if type(records) is not list:
        raise DatasetImportError("Dataset records must be an array.")
    if type(total) is not int or total < 0 or total != len(records):
        raise DatasetImportError("Dataset must contain exactly its declared total records.")

    conversations: list[dict] = []
    identifiers: set[str] = set()
    for record in records:
        conversation = _conversation(record, contracts)
        if conversation["id"] in identifiers:
            raise DatasetImportError("Dataset conversation identifiers must be unique.")
        identifiers.add(conversation["id"])
        conversations.append(conversation)
    return NormalizedDataset(group_id, conversations, deepcopy(records))
