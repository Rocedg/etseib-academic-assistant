"""Ingest the corpus into fragments with metadata.
The prototype reads Markdown with a metadata header so each fragment keeps its source,
validity period, profile, and version."""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass
class Fragment:
    id: str
    text: str
    source: str
    url: str
    section: str
    domain: str
    language: str
    valid_from: date
    valid_until: date | None
    version: str
    profiles: list[str] = field(default_factory=list)

    def is_valid(self, today: date) -> bool:
        return self.valid_from <= today and (self.valid_until is None or today <= self.valid_until)


def _parse_header(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    metadata = dict(l.split(":", 1) for l in m.group(1).splitlines()) if m else {}
    return {k.strip(): v.strip() for k, v in metadata.items()}, text[m.end():] if m else text


def load_corpus(folder: Path) -> list[Fragment]:
    fragments = []
    for f in sorted(folder.glob("*.md")):
        raw = f.read_text(encoding="utf-8")
        metadata, body = _parse_header(raw)
        version = hashlib.sha1(raw.encode()).hexdigest()[:8]
        for i, block in enumerate(re.split(r"\n(?=## )", body)):
            if not block.strip():
                continue
            section = block.splitlines()[0].lstrip("# ").strip()
            fragments.append(Fragment(
                id=f"{f.stem}#{i}", text=block.strip(), source=metadata.get("title", f.stem),
                url=metadata.get("url", ""), section=section, domain=metadata.get("domain", "documental"),
                language=metadata.get("language", "es"),
                valid_from=date.fromisoformat(metadata.get("valid_from", "2000-01-01")),
                valid_until=date.fromisoformat(metadata["valid_until"]) if metadata.get("valid_until") else None,
                version=version, profiles=[p.strip() for p in metadata.get("profiles", "").split(",") if p.strip()]))
    return fragments
