"""Explicit local administration. Nothing imports a dataset on API startup."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

import httpx
from alembic.util.exc import CommandError
from sqlalchemy.exc import SQLAlchemyError

from .config import BACKEND_ROOT, Settings
from .contracts import ContractError, Contracts, validate_request_semantics
from .importer import DatasetImportError, normalize_dataset
from .storage import EvaluationStore, StorageConflict
from .diagnostics import collect_diagnostics
from .ml_client import MLClient


MAX_INPUT_BYTES = 10 * 1024 * 1024


def read_json(path: Path, *, dataset: bool = False) -> object:
    resolved = path.resolve()
    allowed_root = BACKEND_ROOT / "data" if dataset else BACKEND_ROOT.parent
    if not resolved.is_relative_to(allowed_root.resolve()):
        raise ValueError("Input path is outside the allowed local directory.")
    if resolved.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("Input exceeds the 10 MiB limit.")
    return json.loads(resolved.read_text(encoding="utf-8-sig"))


async def fetch_dataset_from_env(variable: str) -> object:
    """Only an explicit CLI invocation can contact the organizer's chosen API."""
    endpoint = os.getenv(variable, "")
    parsed = urlsplit(endpoint)
    if (parsed.scheme != "https" or not parsed.hostname
            or not parsed.hostname.endswith(".environment.api.powerplatform.com")
            or parsed.username or parsed.password or parsed.fragment
            or parsed.port not in {None, 443}):
        raise ValueError("Dataset URL must be the explicitly configured HTTPS organizer API.")
    async with asyncio.timeout(30):
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=20) as client:
            async with client.stream("GET", endpoint, headers={"Accept": "application/json"}) as response:
                response.raise_for_status()
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(chunks) + len(chunk) > MAX_INPUT_BYTES:
                        raise ValueError("Input exceeds the 10 MiB limit.")
                    chunks.extend(chunk)
    return json.loads(chunks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EVA backend local data commands")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("db-upgrade", help="Apply versioned database migrations")
    commands.add_parser("doctor", help="Check database and ML readiness without changing data")
    commands.add_parser("audit-data", help="Validate stored conversations and checklists; print counts only")
    commands.add_parser("seed-demo", help="Explicitly load the synthetic conversation and checklist")
    checklist = commands.add_parser("seed-checklist", help="Store an immutable checklist version")
    checklist.add_argument("--file", type=Path, help="Local checklist JSON; defaults to canonical example")
    dataset = commands.add_parser("import-dataset", help="Validate and atomically import a complete dataset")
    source = dataset.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="JSON file inside backend/data")
    source.add_argument("--url-env", help="Explicitly fetch the organizer URL from this environment variable")
    dataset.add_argument("--group", help="Require this exact dataset group")
    args = parser.parse_args(argv)

    store = None
    try:
        settings = Settings()
        contracts = Contracts(settings.contracts_dir)
        if args.command == "doctor":
            store = EvaluationStore(settings.effective_database_url)

            async def diagnose():
                async with httpx.AsyncClient(base_url=settings.ml_url, trust_env=False,
                                             follow_redirects=False, timeout=2) as client:
                    return await collect_diagnostics(settings, store, MLClient(client))

            summary = asyncio.run(diagnose())
            print(json.dumps(summary, ensure_ascii=False))
            return 0 if summary["status"] == "ready" else 2
        if args.command == "audit-data":
            store = EvaluationStore(settings.effective_database_url)
            conversations = store.list_conversations()
            checklists = store.list_checklists()
            channels, languages = {}, {}
            messages = audio_links = 0
            for conversation in conversations:
                contracts.validate("conversation", conversation)
                validate_request_semantics({"conversation": conversation, "checklist": {"criteria": []}})
                channels[conversation["channel"]] = channels.get(conversation["channel"], 0) + 1
                languages[conversation["language"]] = languages.get(conversation["language"], 0) + 1
                messages += len(conversation["messages"])
                audio_links += conversation["audio_url"] is not None
            for checklist in checklists:
                contracts.validate("checklist", checklist)
                validate_request_semantics({"conversation": {"messages": []}, "checklist": checklist})
            print(json.dumps({"valid": True, "conversations": len(conversations), "messages": messages,
                              "channels": channels, "languages": languages, "audio_links": audio_links,
                              "checklist_versions": len(checklists)}, ensure_ascii=False))
            return 0
        # Validate before opening a write transaction or applying migrations.
        normalized = None
        checklist_data = None
        if args.command == "import-dataset":
            payload = read_json(args.file, dataset=True) if args.file else asyncio.run(fetch_dataset_from_env(args.url_env))
            normalized = normalize_dataset(payload, contracts, expected_group=args.group)
        elif args.command in {"seed-demo", "seed-checklist"}:
            checklist_data = (read_json(args.file) if args.command == "seed-checklist" and args.file
                              else contracts.example("checklist"))
            contracts.validate("checklist", checklist_data)
            validate_request_semantics({"conversation": {"messages": []}, "checklist": checklist_data})

        store = EvaluationStore(settings.effective_database_url)
        store.initialize()
        if normalized is not None:
            summary = store.import_conversations(normalized.group_id, normalized.conversations,
                                                 raw_records=normalized.raw_records)
        elif checklist_data is not None:
            store.seed_checklist(checklist_data)
            summary = {"checklist_stored": True}
            if args.command == "seed-demo":
                summary.update(store.import_conversations("synthetic-demo", [contracts.example("conversation")]))
        else:
            summary = {"database_upgraded": True}
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except (SQLAlchemyError, StorageConflict, DatasetImportError, ContractError, ValueError,
            OSError, httpx.HTTPError, TimeoutError, RuntimeError, CommandError):
        # Never emit rejected values, SQL parameters, DSNs, signed URLs or traceback.
        print("Operation failed. Check the local database, input format and source configuration.", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
