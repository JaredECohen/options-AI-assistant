"""Options-class context (T8), OptionsCoach portion only.

Materials are loaded only from COURSE_CONTEXT_DIR and only when a manifest
declares, per file, its source, attribution and rights status "cleared". No
materials or rights were available on 2026-11-08, so this is BLOCKED in
practice: with no cleared manifest the loader returns nothing and every caller
gets course_context_unavailable. Nothing is uploaded wholesale to a model:
retrieval returns at most MAX_SNIPPETS short, attributed snippets per query,
and their text is marked untrusted (never instructions).

manifest.json:
  {"version": "2026-11-01", "files": [{"path": "week1.md", "source": "...",
    "attribution": "...", "rights": "cleared"}]}
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

MAX_SNIPPETS = 3
SNIPPET_CHARS = 1200
MAX_FILE_BYTES = 512 * 1024
WORD = re.compile(r"[a-z0-9]+")


class CourseContextUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Passage:
    file: str
    attribution: str
    source: str
    text: str
    sha256: str


def load(directory: str) -> tuple[str, list[Passage]]:
    if not directory:
        raise CourseContextUnavailable("COURSE_CONTEXT_DIR is not set")
    root = Path(directory)
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise CourseContextUnavailable("no manifest.json with rights declarations")
    manifest = json.loads(manifest_path.read_text())
    passages = []
    for entry in manifest.get("files", []):
        if entry.get("rights") != "cleared" or not entry.get("attribution") or not entry.get("source"):
            continue
        path = (root / entry["path"]).resolve()
        if root.resolve() not in path.parents or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        raw = path.read_text(errors="replace")
        digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
        for chunk in re.split(r"\n\s*\n", raw):
            chunk = chunk.strip()
            if len(chunk) >= 40:
                passages.append(Passage(entry["path"], entry["attribution"], entry["source"], chunk[:SNIPPET_CHARS], digest))
    if not passages:
        raise CourseContextUnavailable("manifest lists no rights-cleared files")
    return str(manifest.get("version", "")), passages


def retrieve(directory: str, query: str, k: int = MAX_SNIPPETS) -> dict:
    version, passages = load(directory)
    q = set(WORD.findall(query.lower()))
    scored = sorted(((len(q & set(WORD.findall(p.text.lower()))), i, p) for i, p in enumerate(passages)), reverse=True)
    hits = [p for score, _i, p in scored if score > 0][: min(k, MAX_SNIPPETS)]
    return {"contract": "oc.course_context.v1", "materials_version": version, "untrusted_text": True,
            "snippets": [{"text": p.text, "file": p.file, "attribution": p.attribution, "source": p.source,
                          "content_sha256": p.sha256} for p in hits]}
