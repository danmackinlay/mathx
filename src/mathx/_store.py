"""Shared persistence primitives for the file-per-record stores.

Stdlib-only leaf module: both stores (``mathx.jobs``, ``mathx.ledger``) import it and it
imports nothing of mathx. The two stores share every mechanical convention — atomic
tmp-file-then-rename writes, sortable timestamp ids, path-traversal guarding, corrupt-file
tolerance when listing — and this module is the single home for those conventions so the
stores can't drift apart (they had already begun to: ``ledger`` was importing ``jobs``'s
private ``_write_atomic``). Policy stays with the stores: what a record contains, how a
listing is sorted, and what the statuses mean are their business, not this module's.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path


def write_atomic(path: Path, record: dict) -> None:
    """Write *record* as JSON via tmp-file-then-rename, creating parent dirs.

    Rename is atomic on POSIX, so concurrent readers see either the old record or the
    new one — never a torn file (readers still skip-on-corrupt as a second belt)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2))
    tmp.rename(path)


def new_id() -> str:
    """A sortable UTC-timestamp id with a short random suffix (collision insurance —
    callers still loop on ``exists()`` since two submits can share a second)."""
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(2)


def guarded_path(directory: Path, record_id: str, *, label: str) -> Path:
    """The record's file path, refusing ids that could escape *directory*.

    Ids arrive from the CLI and MCP surfaces verbatim, so a hostile
    ``../../etc/passwd`` must die here, not resolve. *label* names the id kind
    in the error ("invalid job id" vs "invalid ledger id")."""
    if "/" in record_id or os.sep in record_id or ".." in record_id:
        raise KeyError(f"invalid {label} id: {record_id!r}")
    return directory / f"{record_id}.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_records(directory: Path, key_field: str) -> list[dict]:
    """All parseable records in *directory*, unsorted (the caller owns ordering).

    Corrupt/partial files and non-record JSON (anything without *key_field*) are
    skipped, not raised: a listing must survive a crashed writer's leavings."""
    records = []
    if directory.is_dir():
        for path in directory.glob("*.json"):
            try:
                record = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(record, dict) and key_field in record:
                records.append(record)
    return records
