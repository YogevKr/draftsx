from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
import re


URL_PREFIXES = ("http://", "https://")


@dataclass(slots=True)
class Draft:
    id: str
    title: str
    content: str
    tags: list[str]
    flagged: bool
    folder: str
    created_at: str | None
    modified_at: str | None
    permalink: str | None

    @property
    def tags_text(self) -> str:
        return " ".join(self.tags)

    @property
    def stripped_content(self) -> str:
        return self.content.strip()

    @property
    def kind(self) -> str:
        return classify_draft_kind(self.title, self.content)

    @property
    def primary_url(self) -> str | None:
        return extract_primary_url(self.title, self.content)

    @property
    def domain(self) -> str | None:
        url = self.primary_url
        if not url:
            return None
        host = urlparse(url).netloc.lower().strip()
        return root_domain(host)

    @property
    def duplicate_key(self) -> str | None:
        return build_duplicate_key(self.title, self.content)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "Draft":
        return cls(
            id=str(payload.get("id", "")),
            title=_text(payload.get("title")),
            content=_text(payload.get("content")),
            tags=[str(tag) for tag in payload.get("tags", []) or []],
            flagged=bool(payload.get("flagged", False)),
            folder=_text(payload.get("folder")),
            created_at=_optional_text(payload.get("createdAt") or payload.get("created_at")),
            modified_at=_optional_text(payload.get("modifiedAt") or payload.get("modified_at")),
            permalink=_optional_text(payload.get("permalink")),
        )


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def classify_draft_kind(title: str, content: str) -> str:
    stripped = content.strip()
    if not stripped:
        return "blank"
    if starts_like_url(title.strip()) or starts_like_url(first_nonempty_line(stripped)):
        return "url"
    if len(stripped) > 500:
        return "long"
    return "note"


def extract_primary_url(title: str, content: str) -> str | None:
    title_value = title.strip()
    if starts_like_url(title_value):
        return title_value

    for line in content.splitlines():
        candidate = line.strip()
        if starts_like_url(candidate):
            return candidate
    return None


def first_nonempty_line(value: str) -> str:
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def starts_like_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith(URL_PREFIXES)


def root_domain(host: str) -> str | None:
    if not host:
        return None
    labels = [label for label in host.split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels) or None
    if len(labels[-1]) == 2 and labels[-2] in {"co", "org", "net", "ac", "gov"}:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def build_duplicate_key(title: str, content: str) -> str | None:
    stripped = content.strip()
    if not stripped:
        return None

    primary_url = extract_primary_url(title, content)
    if primary_url:
        return f"url:{primary_url.lower()}"

    normalized = normalize_duplicate_text(stripped)
    if len(normalized) < 20:
        return None
    return f"text:{normalized}"


def normalize_duplicate_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()
