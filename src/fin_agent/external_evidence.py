from __future__ import annotations

import csv
import json
from pathlib import Path

from .io_utils import sha256_file
from .models import DocumentMeta, ExternalEvidence


SUPPORTED_SUFFIXES = {".json", ".md", ".txt", ".csv"}
REFERENCE_FILE_NAMES = {"readme.md", "example.schema.json"}


def is_external_evidence_file(path: Path) -> bool:
    """Return True only for user-supplied evidence, not bundled instructions/templates."""
    return (
        path.is_file()
        and path.suffix.lower() in SUPPORTED_SUFFIXES
        and path.name.lower() not in REFERENCE_FILE_NAMES
        and not path.name.lower().endswith(".schema.json")
    )


def iter_external_evidence_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return [path for path in sorted(directory.rglob("*")) if is_external_evidence_file(path)]


def _text_from_file(path: Path) -> tuple[str, str, list[str], str | None]:
    if path.suffix.lower() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            title = str(payload.get("title") or path.stem)
            content = str(payload.get("content") or payload.get("text") or json.dumps(payload, ensure_ascii=False))
            tags = [str(item) for item in payload.get("tags", [])] if isinstance(payload.get("tags", []), list) else []
            as_of_date = str(payload["as_of_date"]) if payload.get("as_of_date") else None
            return title, content, tags, as_of_date
        return path.stem, json.dumps(payload, ensure_ascii=False), [], None
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        content = "\n".join(" | ".join(cell for cell in row) for row in rows)
        return path.stem, content, ["tabular"], None
    return path.stem, path.read_text(encoding="utf-8-sig"), [], None


def load_external_evidence(directory: Path, document: DocumentMeta) -> list[ExternalEvidence]:
    if not directory.exists():
        return []
    company_tokens = {
        token.lower()
        for token in (document.company_name, document.security_code)
        if token
    }
    evidence: list[ExternalEvidence] = []
    for path in iter_external_evidence_files(directory):
        title, content, tags, as_of_date = _text_from_file(path)
        searchable = f"{path.stem}\n{title}\n{content[:2000]}".lower()
        if company_tokens and not any(token in searchable for token in company_tokens):
            continue
        digest = sha256_file(path)
        evidence.append(
            ExternalEvidence(
                evidence_id=digest[:16],
                title=title,
                content=content[:6000],
                source_path=str(path.resolve()),
                sha256=digest,
                tags=tags,
                as_of_date=as_of_date,
            )
        )
    return evidence
