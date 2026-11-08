"""Immutable local audio and durable, leased transcription jobs."""

from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4
import wave

from sqlalchemy import Column, Float, ForeignKey, Integer, Table, Text, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from .contracts import Contracts
from .models import conversations, metadata
from .storage import BACKEND_ROOT, StorageConflict
from .transcription_client import TranscriptionInvalidResponse, validate_transcription


class MediaInvalid(ValueError):
    pass


class MediaNotFound(ValueError):
    pass


class MediaConflict(StorageConflict):
    pass


class MediaStorageError(RuntimeError):
    pass


class MediaBacklogFull(RuntimeError):
    pass


media_assets = Table(
    "media_assets", metadata,
    Column("id", Text, primary_key=True),
    Column("operation_id", Text, nullable=False, unique=True),
    Column("content_type", Text, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("sha256", Text, nullable=False),
    Column("duration_seconds", Float),
    Column("created_at", Text, nullable=False),
    Column("relative_path", Text, nullable=False, unique=True),
    Column("display_name", Text),
)

transcription_jobs = Table(
    "transcription_jobs", metadata,
    Column("id", Text, primary_key=True),
    Column("asset_id", Text, ForeignKey("media_assets.id"), nullable=False),
    Column("operation_id", Text, nullable=False, unique=True),
    Column("language", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("lease_token", Text),
    Column("lease_expires_at", Text),
    Column("result_payload", Text),
    Column("conversation_id", Text, ForeignKey("conversations.id")),
    Column("error_code", Text),
)

transcription_reviews = Table(
    "transcription_reviews", metadata,
    Column("operation_id", Text, primary_key=True),
    Column("job_id", Text, ForeignKey("transcription_jobs.id"), nullable=False, unique=True),
    Column("request_hash", Text, nullable=False),
    Column("roles_payload", Text, nullable=False),
    Column("reviewer_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)

_FORMATS = {"audio/wav": "wav", "audio/mpeg": "mp3", "audio/flac": "flac", "audio/ogg": "ogg"}
_ALIASES = {"audio/x-wav": "audio/wav", "audio/wave": "audio/wav", "audio/mp3": "audio/mpeg", "audio/x-flac": "audio/flac"}
_ASSET_FIELDS = ("id", "content_type", "size_bytes", "sha256", "duration_seconds", "created_at")
_ERROR_CODES = {"transcription_failed", "transcription_unavailable", "invalid_transcription", "interrupted",
                "audio_missing", "asr_unavailable", "invalid_asr_response", "invalid_transcript", "transcription_timeout"}


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _uuid(value):
    if type(value) is not str:
        raise MediaInvalid("An operation or resource identifier must be a UUID.")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        raise MediaInvalid("An operation or resource identifier must be a UUID.") from None
    if str(parsed) != value.lower():
        raise MediaInvalid("An operation or resource identifier must be a canonical UUID.")
    return str(parsed)


def _document(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _asset(row):
    return {key: row[key] for key in _ASSET_FIELDS}


def _job(row):
    return {**{key: row[key] for key in (
        "id", "asset_id", "language", "status", "created_at", "updated_at", "conversation_id", "error_code",
    )}, "result": json.loads(row["result_payload"]) if row["result_payload"] is not None else None}


def segment_messages(segments, roles=None):
    messages = []
    for i, segment in enumerate(segments):
        seconds = math.floor(segment["start_seconds"])
        messages.append({"speaker": roles[i] if roles is not None else segment["speaker"], "text": segment["text"],
                         "timestamp": f"{seconds // 60:02d}:{seconds % 60:02d}",
                         "offset_seconds": segment["start_seconds"], "end_seconds": segment["end_seconds"]})
    return messages


def _content_type(content_type):
    if type(content_type) is not str:
        raise MediaInvalid("Audio Content-Type is required.")
    content_type = content_type.split(";", 1)[0].strip().lower()
    content_type = _ALIASES.get(content_type, content_type)
    if content_type not in _FORMATS:
        raise MediaInvalid("Unsupported audio Content-Type.")
    return content_type


def _audio(data: bytes, content_type: str) -> tuple[str, float | None]:
    content_type = _content_type(content_type)
    duration = None
    if content_type == "audio/wav":
        if (len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE"
                or int.from_bytes(data[4:8], "little") != len(data) - 8):
            raise MediaInvalid("Invalid WAV structure.")
        try:
            with wave.open(io.BytesIO(data), "rb") as audio:
                frames, rate = audio.getnframes(), audio.getframerate()
                if frames <= 0 or rate <= 0 or audio.getcomptype() != "NONE":
                    raise ValueError()
                duration = frames / rate
                if duration > 3600:
                    raise MediaInvalid("Audio duration exceeds one hour.")
                if len(audio.readframes(frames + 1)) != frames * audio.getnchannels() * audio.getsampwidth():
                    raise ValueError()
        except (wave.Error, EOFError, ValueError):
            raise MediaInvalid("Invalid or incomplete WAV audio.") from None
    elif content_type == "audio/flac":
        # A native FLAC begins with its mandatory 34-byte STREAMINFO block.
        if len(data) < 42 or data[:4] != b"fLaC" or data[4] & 0x7F != 0 or data[5:8] != b"\x00\x00\x22":
            raise MediaInvalid("Invalid FLAC signature.")
    elif content_type == "audio/ogg":
        if len(data) < 28 or data[:4] != b"OggS" or data[4] != 0:
            raise MediaInvalid("Invalid Ogg signature.")
        segment_count = data[26]
        if not segment_count or len(data) < 27 + segment_count + sum(data[27:27 + segment_count]):
            raise MediaInvalid("Incomplete Ogg page.")
    else:
        offset = 0
        if data.startswith(b"ID3"):
            if len(data) < 10 or data[3] not in {2, 3, 4} or any(byte & 0x80 for byte in data[6:10]):
                raise MediaInvalid("Invalid MP3 tag signature.")
            offset = 10 + sum(byte << shift for byte, shift in zip(data[6:10], (21, 14, 7, 0)))
            if data[5] & 0x10 and data[3] == 4:
                offset += 10
        if len(data) < offset + 4:
            raise MediaInvalid("Invalid MP3 frame signature.")
        header = int.from_bytes(data[offset:offset + 4], "big")
        version, layer = (header >> 19) & 3, (header >> 17) & 3
        bitrate, rate = (header >> 12) & 15, (header >> 10) & 3
        if (header >> 21 != 0x7FF or version == 1 or layer == 0
                or bitrate in {0, 15} or rate == 3):
            raise MediaInvalid("Invalid MP3 frame signature.")
    return content_type, duration


class MediaService:
    def __init__(self, store, media_root: Path, *, max_upload_bytes: int = 25 * 1024 * 1024,
                 max_pending_jobs: int = 100, media_base_url: str | None = None):
        for value in (max_upload_bytes, max_pending_jobs):
            if type(value) is not int or value <= 0:
                raise ValueError("Media limits must be positive integers.")
        self.root = Path(media_root).resolve()
        if not self.root.is_relative_to(BACKEND_ROOT):
            raise ValueError("Media storage must stay inside backend.")
        if media_base_url not in (None, "/"):
            parts = urlsplit(media_base_url)
            if (parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or parts.username or parts.password or parts.query or parts.fragment or parts.path not in {"", "/"}):
                raise ValueError("Media base URL must be a plain HTTP loopback origin.")
        self.base_url = media_base_url.rstrip("/") if media_base_url else None
        self.store = store
        self.max_upload_bytes = max_upload_bytes
        self.max_pending_jobs = max_pending_jobs
        self.contracts = Contracts(BACKEND_ROOT.parent / "contracts")

    def _locked(self, connection):
        # SQLite's store write connection already uses BEGIN IMMEDIATE. The
        # shared PostgreSQL transaction lock also makes the backlog bound exact.
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(630487203)")

    def upload(self, data: bytes, content_type: str, operation_id: str, display_name=None) -> dict:
        operation_id = _uuid(operation_id)
        if type(data) is not bytes or not data:
            raise MediaInvalid("Audio upload must contain bytes.")
        if len(data) > self.max_upload_bytes:
            raise MediaInvalid("Audio upload exceeds its size limit.")
        content_type = _content_type(content_type)
        digest = hashlib.sha256(data).hexdigest()
        with self.store.engine.connect() as connection:
            existing = connection.execute(select(media_assets).where(media_assets.c.operation_id == operation_id)).mappings().one_or_none()
        if existing is not None:
            if existing["sha256"] != digest or existing["content_type"] != content_type:
                raise MediaConflict("The upload operation already belongs to different audio.")
            self.asset_path(existing["id"])
            return _asset(existing)
        content_type, duration = _audio(data, content_type)
        identifier = str(uuid4())
        relative = identifier + "." + _FORMATS[content_type]
        final = self.root / relative
        temporary = self.root / (".tmp-" + str(uuid4()))
        name = (display_name or "").replace("\\", "/").split("/")[-1]
        name = "".join(c for c in name if c.isprintable()).strip()[:180] or None
        item = {"id": identifier, "operation_id": operation_id, "content_type": content_type, "display_name": name,
                "size_bytes": len(data), "sha256": digest, "duration_seconds": duration,
                "created_at": _stamp(_now()), "relative_path": relative}
        committed = False
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with temporary.open("xb") as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, final)
            with self.store._write_connection() as connection:
                connection.execute(insert(media_assets).values(**item))
            committed = True
            return _asset(item)
        except IntegrityError:
            with self.store.engine.connect() as connection:
                existing = connection.execute(select(media_assets).where(media_assets.c.operation_id == operation_id)).mappings().one_or_none()
            if existing is not None and existing["sha256"] == digest and existing["content_type"] == content_type:
                return _asset(existing)
            raise MediaConflict("The upload operation conflicts with another request.") from None
        except OSError:
            raise MediaStorageError("Audio could not be saved locally.") from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
                if not committed:
                    final.unlink(missing_ok=True)
            except OSError:
                # A storage fault must not overwrite the original safe error.
                pass

    def get_asset(self, asset_id: str) -> dict | None:
        asset_id = _uuid(asset_id)
        with self.store.engine.connect() as connection:
            row = connection.execute(select(media_assets).where(media_assets.c.id == asset_id)).mappings().one_or_none()
        return _asset(row) if row is not None else None

    def asset_path(self, asset_id: str) -> Path:
        asset_id = _uuid(asset_id)
        with self.store.engine.connect() as connection:
            row = connection.execute(select(media_assets).where(media_assets.c.id == asset_id)).mappings().one_or_none()
        if row is None:
            raise MediaNotFound("Audio asset was not found.")
        expected = asset_id + "." + _FORMATS[row["content_type"]]
        original = self.root / expected
        try:
            if original.is_symlink():
                raise MediaStorageError("Stored audio failed its integrity check.")
            path = original.resolve()
            if row["relative_path"] != expected or not path.is_relative_to(self.root) or not path.is_file():
                raise MediaStorageError("Stored audio is missing or its path is invalid.")
            if path.stat().st_size != row["size_bytes"]:
                raise MediaStorageError("Stored audio failed its integrity check.")
            digest, size = hashlib.sha256(), 0
            with path.open("rb") as source:
                while block := source.read(64 * 1024):
                    size += len(block)
                    if size > row["size_bytes"]:
                        raise MediaStorageError("Stored audio failed its integrity check.")
                    digest.update(block)
            if size != row["size_bytes"] or digest.hexdigest() != row["sha256"]:
                raise MediaStorageError("Stored audio failed its integrity check.")
        except OSError:
            raise MediaStorageError("Stored audio could not be verified.") from None
        return path

    def create_job(self, asset_id: str, language: str, operation_id: str) -> dict:
        asset_id, operation_id = _uuid(asset_id), _uuid(operation_id)
        if type(language) is not str or language not in {"RU", "KZ", "EN"}:
            raise MediaInvalid("Unsupported transcription language.")
        now = _stamp(_now())
        item = {"id": str(uuid4()), "asset_id": asset_id, "operation_id": operation_id, "language": language,
                "status": "queued", "created_at": now, "updated_at": now,
                "lease_token": None, "lease_expires_at": None, "result_payload": None,
                "conversation_id": None, "error_code": None}
        try:
            with self.store._write_connection() as connection:
                self._locked(connection)
                previous = connection.execute(select(transcription_jobs).where(
                    transcription_jobs.c.operation_id == operation_id)).mappings().one_or_none()
                if previous is not None:
                    if previous["asset_id"] != asset_id or previous["language"] != language:
                        raise MediaConflict("The transcription operation already has different input.")
                    return _job(previous)
                if connection.execute(select(media_assets.c.id).where(media_assets.c.id == asset_id)).scalar_one_or_none() is None:
                    raise MediaNotFound("Audio asset was not found.")
                pending = connection.execute(select(func.count()).select_from(transcription_jobs).where(
                    transcription_jobs.c.status.in_(("queued", "running")))).scalar_one()
                if pending >= self.max_pending_jobs:
                    raise MediaBacklogFull("The local transcription backlog is full.")
                connection.execute(insert(transcription_jobs).values(**item))
        except IntegrityError:
            raise MediaConflict("The transcription operation conflicts with another request.") from None
        return _job(item)

    def get_job(self, job_id: str) -> dict | None:
        job_id = _uuid(job_id)
        with self.store.engine.connect() as connection:
            row = connection.execute(select(transcription_jobs).where(transcription_jobs.c.id == job_id)).mappings().one_or_none()
        return _job(row) if row is not None else None

    def claim_job(self, job_id: str | None = None, lease_seconds: int = 600) -> dict | None:
        if job_id is not None:
            job_id = _uuid(job_id)
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 86400:
            raise MediaInvalid("A job lease must last between one second and one day.")
        now = _now()
        with self.store._write_connection() as connection:
            self._locked(connection)
            query = select(transcription_jobs).where(transcription_jobs.c.status == "queued")
            if job_id is not None:
                query = query.where(transcription_jobs.c.id == job_id)
            row = connection.execute(query.order_by(transcription_jobs.c.created_at, transcription_jobs.c.id)
                                     .limit(1).with_for_update(skip_locked=True)).mappings().one_or_none()
            if row is None:
                return None
            item = dict(row)
            item.update(status="running", updated_at=_stamp(now), lease_token=str(uuid4()),
                        lease_expires_at=_stamp(now + timedelta(seconds=lease_seconds)))
            connection.execute(update(transcription_jobs).where(transcription_jobs.c.id == row["id"]).values(
                status=item["status"], updated_at=item["updated_at"], lease_token=item["lease_token"],
                lease_expires_at=item["lease_expires_at"]))
        return {**_job(item), "lease_token": item["lease_token"]}

    def _leased(self, connection, job_id, lease_token):
        row = connection.execute(select(transcription_jobs).where(
            transcription_jobs.c.id == _uuid(job_id)).with_for_update()).mappings().one_or_none()
        if row is None:
            raise MediaNotFound("Transcription job was not found.")
        if row["lease_token"] != _uuid(lease_token):
            raise MediaConflict("The transcription lease does not own this job.")
        if row["status"] == "running" and row["lease_expires_at"] <= _stamp(_now()):
            raise MediaConflict("The transcription lease has expired.")
        return row

    def _create_conversation(self, connection, row, result, roles=None):
        identifier = "audio-" + row["id"]
        messages = [dict(m, id=f"m{i:04d}") for i, m in enumerate(segment_messages(result["segments"], roles), 1)]
        audio_url = (self.base_url + f"/api/v1/audio-assets/{row['asset_id']}/content") if self.base_url is not None else None
        conversation = {"id": identifier, "channel": "voice_call", "language": row["language"],
                        "messages": messages, "audio_url": audio_url}
        self.contracts.validate("conversation", conversation)
        document = _document(conversation)
        connection.execute(insert(conversations).values(
            id=identifier, group_id="audio-transcriptions", payload=document,
            raw_payload=_document(result), content_hash=hashlib.sha256(document.encode()).hexdigest()))
        return identifier

    def history(self, limit, offset):
        jobs = transcription_jobs.c
        stmt = select(jobs.id, jobs.asset_id, jobs.status, jobs.created_at, jobs.language, jobs.conversation_id,
                      media_assets.c.display_name).join(media_assets, media_assets.c.id == jobs.asset_id).order_by(
                          jobs.created_at.desc(), jobs.id.desc()).offset(offset).limit(limit + 1)
        with self.store.engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return {"items": [dict(r) for r in rows[:limit]], "has_more": len(rows) > limit}

    def export_transcript(self, job_id):
        with self.store.engine.connect() as conn:
            row = conn.execute(select(transcription_jobs).where(transcription_jobs.c.id == _uuid(job_id))).mappings().one_or_none()
            if row is None:
                raise MediaNotFound()
            if row["result_payload"] is None:
                raise MediaConflict()
            roles = conn.execute(select(transcription_reviews.c.roles_payload).where(
                transcription_reviews.c.job_id == row["id"])).scalar_one_or_none()
            segments = json.loads(row["result_payload"])["segments"]
            return {"channel": "voice_call", "language": row["language"],
                    "messages": segment_messages(segments, json.loads(roles) if roles else None)}

    def complete_job(self, job_id: str, lease_token: str, result: dict) -> dict:
        try:
            with self.store._write_connection() as connection:
                self._locked(connection)
                row = self._leased(connection, job_id, lease_token)
                asset = connection.execute(select(media_assets).where(media_assets.c.id == row["asset_id"])).mappings().one()
                try:
                    checked = validate_transcription(result, asset["sha256"], row["language"], asset["duration_seconds"])
                except TranscriptionInvalidResponse:
                    raise MediaInvalid("Transcription result is invalid for this audio.") from None
                payload = _document(checked)
                if row["status"] in {"completed", "needs_review"} and row["result_payload"] == payload:
                    return _job(row)
                if row["status"] != "running":
                    raise MediaConflict("The transcription job is not running.")
                unknown = any(segment["speaker"] == "unknown" for segment in checked["segments"])
                conversation_id = None
                if not unknown:
                    conversation_id = self._create_conversation(connection, row, checked)
                item = dict(row)
                item.update(status="needs_review" if unknown else "completed", updated_at=_stamp(_now()),
                            conversation_id=conversation_id, result_payload=payload, error_code=None, lease_expires_at=None)
                connection.execute(update(transcription_jobs).where(transcription_jobs.c.id == row["id"]).values(
                    **{key: item[key] for key in ("status", "updated_at", "conversation_id", "result_payload", "error_code", "lease_expires_at")}))
                return _job(item)
        except IntegrityError:
            raise MediaConflict("The transcription result conflicts with stored content.") from None

    def review_job(self, job_id: str, roles: list[str], operation_id: str,
                   reviewer_id: str = "local-reviewer") -> dict:
        job_id, operation_id = _uuid(job_id), _uuid(operation_id)
        if (type(roles) is not list or not roles
                or any(type(role) is not str or role not in {"client", "operator"} for role in roles)
                or type(reviewer_id) is not str or not reviewer_id.strip() or len(reviewer_id) > 128):
            raise MediaInvalid("A review requires known roles and a reviewer identity.")
        request_hash = hashlib.sha256(_document({"job_id": job_id, "roles": roles, "reviewer_id": reviewer_id}).encode()).hexdigest()
        try:
            with self.store._write_connection() as connection:
                self._locked(connection)
                previous = connection.execute(select(transcription_reviews).where(
                    transcription_reviews.c.operation_id == operation_id)).mappings().one_or_none()
                if previous is not None:
                    if previous["job_id"] != job_id or previous["request_hash"] != request_hash:
                        raise MediaConflict("The review operation already belongs to another decision.")
                    row = connection.execute(select(transcription_jobs).where(transcription_jobs.c.id == job_id)).mappings().one()
                    return _job(row)
                row = connection.execute(select(transcription_jobs).where(
                    transcription_jobs.c.id == job_id).with_for_update()).mappings().one_or_none()
                if row is None:
                    raise MediaNotFound("Transcription job was not found.")
                if row["status"] != "needs_review" or row["result_payload"] is None:
                    raise MediaConflict("Only an unresolved transcription can receive a role review.")
                original = json.loads(row["result_payload"])
                if len(roles) != len(original["segments"]):
                    raise MediaInvalid("Review roles must cover every transcript segment in order.")
                conversation_id = self._create_conversation(connection, row, original, roles)
                now = _stamp(_now())
                connection.execute(insert(transcription_reviews).values(
                    operation_id=operation_id, job_id=job_id, request_hash=request_hash,
                    roles_payload=_document(roles), reviewer_id=reviewer_id, created_at=now))
                connection.execute(update(transcription_jobs).where(transcription_jobs.c.id == job_id).values(
                    status="completed", conversation_id=conversation_id, updated_at=now))
                return _job({**dict(row), "status": "completed", "conversation_id": conversation_id, "updated_at": now})
        except IntegrityError:
            raise MediaConflict("The transcription review conflicts with stored content.") from None

    def fail_job(self, job_id: str, lease_token: str, error_code: str = "transcription_failed") -> dict:
        if type(error_code) is not str or error_code not in _ERROR_CODES:
            error_code = "transcription_failed"
        with self.store._write_connection() as connection:
            self._locked(connection)
            row = self._leased(connection, job_id, lease_token)
            if row["status"] == "failed":
                return _job(row)
            if row["status"] != "running":
                raise MediaConflict("The transcription job is not running.")
            item = dict(row)
            item.update(status="failed", error_code=error_code, updated_at=_stamp(_now()), lease_expires_at=None)
            connection.execute(update(transcription_jobs).where(transcription_jobs.c.id == row["id"]).values(
                status=item["status"], error_code=item["error_code"], updated_at=item["updated_at"], lease_expires_at=None))
            return _job(item)

    def recover_expired_jobs(self) -> int:
        with self.store._write_connection() as connection:
            self._locked(connection)
            result = connection.execute(update(transcription_jobs).where(
                transcription_jobs.c.status == "running", transcription_jobs.c.lease_expires_at <= _stamp(_now()),
            ).values(status="failed", error_code="interrupted", updated_at=_stamp(_now()), lease_expires_at=None))
            return result.rowcount
