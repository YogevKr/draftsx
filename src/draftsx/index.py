from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from .model import Draft, build_duplicate_key, classify_draft_kind, extract_primary_url, root_domain


SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    tags_text TEXT NOT NULL,
    flagged INTEGER NOT NULL,
    folder TEXT NOT NULL,
    created_at TEXT,
    modified_at TEXT,
    permalink TEXT,
    kind TEXT NOT NULL,
    primary_url TEXT,
    domain TEXT,
    duplicate_key TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS drafts_fts USING fts5(
    id UNINDEXED,
    title,
    content,
    tags_text,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULT_STALE_AFTER_SECONDS = 300
RECENCY_AGE_PENALTY = 0.35
KIND_VALUES = ("url", "blank", "note", "long")
CONTEXT_STOPWORDS = {
    "http",
    "https",
    "www",
    "com",
    "org",
    "net",
    "html",
    "pdf",
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
}


@dataclass(slots=True)
class SearchResult:
    draft: Draft
    snippet: str
    score: float


@dataclass(slots=True)
class ContextResult:
    anchor: Draft
    query: str
    related: list[SearchResult]
    same_domain: list[Draft]
    recent_in_folder: list[Draft]


@dataclass(slots=True)
class StatsResult:
    total_drafts: int
    flagged_count: int
    tagged_count: int
    folder_counts: list[dict[str, object]]
    kind_counts: list[dict[str, object]]
    top_domains: list[dict[str, object]]
    latest_modified_at: str | None


@dataclass(slots=True)
class DuplicateGroup:
    key: str
    duplicate_type: str
    count: int
    sample_drafts: list[Draft]


class DraftIndexError(RuntimeError):
    pass


class DraftIndex:
    def __init__(self, home: Path | None = None) -> None:
        self.home = home or default_home()
        self.home.mkdir(parents=True, exist_ok=True)
        self.db_path = self.home / "index.sqlite3"
        self._init_db()

    def replace_all(self, drafts: list[Draft]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM drafts")
            conn.execute("DELETE FROM drafts_fts")
            for draft in drafts:
                self._upsert(conn, draft)
            self._set_meta(conn, "last_sync_at", utc_now())
            self._set_meta(conn, "source_count", str(len(drafts)))

    def upsert(self, draft: Draft) -> None:
        with self._connect() as conn:
            self._upsert(conn, draft)

    def get(self, draft_id: str) -> Draft | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_draft(row)

    def resolve_id(self, value: str) -> str:
        with self._connect() as conn:
            exact = conn.execute("SELECT id FROM drafts WHERE id = ?", (value,)).fetchone()
            if exact is not None:
                return exact["id"]
            rows = conn.execute(
                "SELECT id FROM drafts WHERE id LIKE ? ORDER BY id LIMIT 2",
                (f"{value}%",),
            ).fetchall()
        if not rows:
            raise DraftIndexError(f"no indexed draft matches '{value}'")
        if len(rows) > 1:
            raise DraftIndexError(f"draft id prefix '{value}' is ambiguous")
        return rows[0]["id"]

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM drafts").fetchone()
        return int(row["count"])

    def list_drafts(
        self,
        *,
        folder: str | None = None,
        tag: str | None = None,
        kind: str | None = None,
        flagged: bool | None = None,
        limit: int = 30,
        offset: int = 0,
        sort: str = "modified",
        exclude_ids: set[str] | None = None,
    ) -> list[Draft]:
        order_by = {
            "modified": "COALESCE(modified_at, '') DESC, title COLLATE NOCASE ASC",
            "created": "COALESCE(created_at, '') DESC, title COLLATE NOCASE ASC",
            "title": "title COLLATE NOCASE ASC, COALESCE(modified_at, '') DESC",
        }.get(sort)
        if order_by is None:
            raise DraftIndexError(f"unsupported sort: {sort}")

        clauses: list[str] = []
        params: list[object] = []

        if folder:
            clauses.append("folder = ?")
            params.append(folder)
        if flagged is not None:
            clauses.append("flagged = ?")
            params.append(int(flagged))
        if tag:
            clauses.append("LOWER(tags_json) LIKE ?")
            params.append(f'%"{tag.lower()}"%')
        if kind:
            require_kind(kind)
            clauses.append("kind = ?")
            params.append(kind)
        if exclude_ids:
            placeholders = ", ".join("?" for _ in exclude_ids)
            clauses.append(f"id NOT IN ({placeholders})")
            params.extend(sorted(exclude_ids))

        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM drafts
                {where_clause}
                ORDER BY {order_by}
                LIMIT ?
                OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
        return [self._row_to_draft(row) for row in rows]

    def search(
        self,
        query: str,
        limit: int = 10,
        *,
        kind: str | None = None,
        exclude_ids: set[str] | None = None,
        match_mode: str = "all",
    ) -> list[SearchResult]:
        match_query = build_match_query(query, mode=match_mode)
        clauses = ["drafts_fts MATCH ?"]
        params: list[object] = [match_query]

        if kind:
            require_kind(kind)
            clauses.append("d.kind = ?")
            params.append(kind)
        if exclude_ids:
            placeholders = ", ".join("?" for _ in exclude_ids)
            clauses.append(f"d.id NOT IN ({placeholders})")
            params.extend(sorted(exclude_ids))

        where_clause = " AND ".join(clauses)
        fetch_limit = max(limit * 8, 50)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    d.*,
                    snippet(drafts_fts, 2, '[', ']', ' … ', 18) AS snippet,
                    bm25(drafts_fts, 4.0, 1.5, 1.0) AS bm25_score
                FROM drafts_fts
                JOIN drafts AS d USING (id)
                WHERE {where_clause}
                ORDER BY bm25_score
                LIMIT ?
                """,
                (*params, fetch_limit),
            ).fetchall()
        return self._rerank_search_rows(rows, limit)

    def context(self, draft_id: str, limit: int = 5) -> ContextResult:
        anchor = self.get(draft_id)
        if anchor is None:
            raise DraftIndexError(f"draft {draft_id} is not indexed")

        query = build_context_query(anchor)
        related = self.search(query, limit=limit, exclude_ids={anchor.id}, match_mode="any") if query else []
        same_domain: list[Draft] = []
        if anchor.domain:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM drafts
                    WHERE domain = ? AND id != ?
                    ORDER BY COALESCE(modified_at, created_at, '') DESC
                    LIMIT ?
                    """,
                    (anchor.domain, anchor.id, limit),
                ).fetchall()
            same_domain = [self._row_to_draft(row) for row in rows]

        recent_in_folder = self.list_drafts(
            folder=anchor.folder,
            limit=limit,
            sort="modified",
            exclude_ids={anchor.id},
        )
        return ContextResult(
            anchor=anchor,
            query=query,
            related=related,
            same_domain=same_domain,
            recent_in_folder=recent_in_folder,
        )

    def get_status(self, max_age_seconds: int = DEFAULT_STALE_AFTER_SECONDS) -> dict[str, object]:
        last_sync_at = self._get_meta("last_sync_at")
        source_count = self._get_meta("source_count")
        doc_count = self.count()
        reasons: list[str] = []
        age_seconds: int | None = None

        if not last_sync_at:
            reasons.append("index has never been synced from Drafts")
        else:
            synced_at = parse_timestamp(last_sync_at)
            age_seconds = max(0, int((datetime.now(UTC) - synced_at).total_seconds()))
            if age_seconds > max_age_seconds:
                reasons.append(f"index older than {max_age_seconds}s")

        needs_refresh = bool(reasons)
        return {
            "db_path": str(self.db_path),
            "doc_count": doc_count,
            "source_count": int(source_count) if source_count is not None else None,
            "last_sync_at": last_sync_at,
            "age_seconds": age_seconds,
            "stale_after_seconds": max_age_seconds,
            "fresh": not needs_refresh,
            "needs_refresh": needs_refresh,
            "reasons": reasons,
        }

    def needs_refresh(self, max_age_seconds: int = DEFAULT_STALE_AFTER_SECONDS) -> bool:
        return bool(self.get_status(max_age_seconds=max_age_seconds)["needs_refresh"])

    def collect_stats(self, domain_limit: int = 10) -> StatsResult:
        with self._connect() as conn:
            total_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_drafts,
                    SUM(flagged) AS flagged_count,
                    SUM(CASE WHEN tags_json != '[]' THEN 1 ELSE 0 END) AS tagged_count,
                    MAX(modified_at) AS latest_modified_at
                FROM drafts
                """
            ).fetchone()
            folder_rows = conn.execute(
                "SELECT folder, COUNT(*) AS count FROM drafts GROUP BY folder ORDER BY count DESC, folder ASC"
            ).fetchall()
            kind_rows = conn.execute(
                "SELECT kind, COUNT(*) AS count FROM drafts GROUP BY kind ORDER BY count DESC, kind ASC"
            ).fetchall()
            domain_rows = conn.execute(
                """
                SELECT domain, COUNT(*) AS count
                FROM drafts
                WHERE domain IS NOT NULL AND domain != ''
                GROUP BY domain
                ORDER BY count DESC, domain ASC
                LIMIT ?
                """,
                (domain_limit,),
            ).fetchall()

        return StatsResult(
            total_drafts=int(total_row["total_drafts"] or 0),
            flagged_count=int(total_row["flagged_count"] or 0),
            tagged_count=int(total_row["tagged_count"] or 0),
            folder_counts=[{"folder": row["folder"], "count": int(row["count"])} for row in folder_rows],
            kind_counts=[{"kind": row["kind"], "count": int(row["count"])} for row in kind_rows],
            top_domains=[{"domain": row["domain"], "count": int(row["count"])} for row in domain_rows],
            latest_modified_at=total_row["latest_modified_at"],
        )

    def recent_drafts(self, *, folder: str | None = None, kind: str | None = None, limit: int = 20) -> list[Draft]:
        return self.list_drafts(folder=folder, kind=kind, limit=limit, sort="modified")

    def blank_drafts(self, *, folder: str | None = None, limit: int = 20) -> list[Draft]:
        return self.list_drafts(folder=folder, kind="blank", limit=limit, sort="modified")

    def find_duplicate_groups(self, *, limit: int = 20, sample_size: int = 5) -> list[DuplicateGroup]:
        with self._connect() as conn:
            groups = conn.execute(
                """
                SELECT duplicate_key, COUNT(*) AS count
                FROM drafts
                WHERE duplicate_key IS NOT NULL AND duplicate_key != ''
                GROUP BY duplicate_key
                HAVING COUNT(*) > 1
                ORDER BY count DESC, duplicate_key ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

            results: list[DuplicateGroup] = []
            for row in groups:
                key = str(row["duplicate_key"])
                sample_rows = conn.execute(
                    """
                    SELECT *
                    FROM drafts
                    WHERE duplicate_key = ?
                    ORDER BY COALESCE(modified_at, created_at, '') DESC, id ASC
                    LIMIT ?
                    """,
                    (key, sample_size),
                ).fetchall()
                results.append(
                    DuplicateGroup(
                        key=key,
                        duplicate_type="url" if key.startswith("url:") else "text",
                        count=int(row["count"]),
                        sample_drafts=[self._row_to_draft(sample_row) for sample_row in sample_rows],
                    )
                )
        return results

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            self._ensure_column(conn, "drafts", "kind", "TEXT NOT NULL DEFAULT 'note'")
            self._ensure_column(conn, "drafts", "primary_url", "TEXT")
            self._ensure_column(conn, "drafts", "domain", "TEXT")
            self._ensure_column(conn, "drafts", "duplicate_key", "TEXT")
            self._backfill_derived_fields(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS drafts_kind_idx ON drafts(kind)")
            conn.execute("CREATE INDEX IF NOT EXISTS drafts_domain_idx ON drafts(domain)")
            conn.execute("CREATE INDEX IF NOT EXISTS drafts_modified_idx ON drafts(modified_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS drafts_duplicate_idx ON drafts(duplicate_key)")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _upsert(self, conn: sqlite3.Connection, draft: Draft) -> None:
        conn.execute(
            """
            INSERT INTO drafts (
                id,
                title,
                content,
                tags_json,
                tags_text,
                flagged,
                folder,
                created_at,
                modified_at,
                permalink,
                kind,
                primary_url,
                domain,
                duplicate_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                content = excluded.content,
                tags_json = excluded.tags_json,
                tags_text = excluded.tags_text,
                flagged = excluded.flagged,
                folder = excluded.folder,
                created_at = excluded.created_at,
                modified_at = excluded.modified_at,
                permalink = excluded.permalink,
                kind = excluded.kind,
                primary_url = excluded.primary_url,
                domain = excluded.domain,
                duplicate_key = excluded.duplicate_key
            """,
            (
                draft.id,
                draft.title,
                draft.content,
                json.dumps(draft.tags),
                draft.tags_text,
                int(draft.flagged),
                draft.folder,
                draft.created_at,
                draft.modified_at,
                draft.permalink,
                draft.kind,
                draft.primary_url,
                draft.domain,
                draft.duplicate_key,
            ),
        )
        conn.execute("DELETE FROM drafts_fts WHERE id = ?", (draft.id,))
        conn.execute(
            "INSERT INTO drafts_fts (id, title, content, tags_text) VALUES (?, ?, ?, ?)",
            (draft.id, draft.title, draft.content, draft.tags_text),
        )

    def _rerank_search_rows(self, rows: list[sqlite3.Row], limit: int) -> list[SearchResult]:
        if not rows:
            return []

        ranked: list[tuple[float, sqlite3.Row]] = []
        for index, row in enumerate(rows, start=1):
            age_days = age_in_days(row["modified_at"] or row["created_at"])
            combined_rank = index + (RECENCY_AGE_PENALTY * math.log1p(age_days))
            ranked.append((combined_rank, row))

        ranked.sort(key=lambda item: (item[0], float(item[1]["bm25_score"])))
        return [
            SearchResult(
                draft=self._row_to_draft(row),
                snippet=row["snippet"] or "",
                score=1.0 / combined_rank,
            )
            for combined_rank, row in ranked[:limit]
        ]

    def _set_meta(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """
            INSERT INTO meta (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def _get_meta(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _backfill_derived_fields(self, conn: sqlite3.Connection) -> None:
        rows = conn.execute("SELECT id, title, content FROM drafts").fetchall()
        updates = [
            (
                classify_draft_kind(row["title"], row["content"]),
                extract_primary_url(row["title"], row["content"]),
                extract_domain(extract_primary_url(row["title"], row["content"])),
                build_duplicate_key(row["title"], row["content"]),
                row["id"],
            )
            for row in rows
        ]
        if updates:
            conn.executemany(
                """
                UPDATE drafts
                SET kind = ?, primary_url = ?, domain = ?, duplicate_key = ?
                WHERE id = ?
                """,
                updates,
            )

    def _row_to_draft(self, row: sqlite3.Row) -> Draft:
        return Draft(
            id=row["id"],
            title=row["title"],
            content=row["content"],
            tags=list(json.loads(row["tags_json"])),
            flagged=bool(row["flagged"]),
            folder=row["folder"],
            created_at=row["created_at"],
            modified_at=row["modified_at"],
            permalink=row["permalink"],
        )


def default_home() -> Path:
    return Path.home() / "Library" / "Application Support" / "draftsx"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(UTC)


def require_kind(value: str) -> str:
    if value not in KIND_VALUES:
        allowed = ", ".join(KIND_VALUES)
        raise DraftIndexError(f"unsupported kind '{value}', expected one of: {allowed}")
    return value


def extract_domain(url: str | None) -> str | None:
    if not url:
        return None
    match = re.match(r"https?://([^/]+)", url, flags=re.IGNORECASE)
    if not match:
        return None
    return root_domain(match.group(1).lower())


def build_context_query(draft: Draft, max_terms: int = 3) -> str:
    tokens = []
    seen: set[str] = set()
    candidates: list[str] = []
    if draft.primary_url:
        candidates.extend(tokens_from_url(draft.primary_url))
    candidates.extend(tokens_from_text(f"{draft.title}\n{draft.content[:1200]}"))

    for token in candidates:
        lowered = token.lower()
        if lowered in seen or lowered in CONTEXT_STOPWORDS:
            continue
        if len(lowered) < 2 or lowered.isdigit():
            continue
        seen.add(lowered)
        tokens.append(token)
        if len(tokens) >= max_terms:
            break
    return " ".join(tokens)


def build_match_query(query: str, mode: str = "all") -> str:
    tokens = re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", query, flags=re.UNICODE)
    if not tokens:
        raise DraftIndexError("search query must contain at least one searchable token")
    operator = {"all": " AND ", "any": " OR "}.get(mode)
    if operator is None:
        raise DraftIndexError(f"unsupported match mode: {mode}")
    return operator.join(f'"{escape_fts_token(token)}"*' for token in tokens)


def age_in_days(value: str | None) -> float:
    if not value:
        return 3650.0
    return max(0.0, (datetime.now(UTC) - parse_timestamp(value)).total_seconds() / 86400.0)


def escape_fts_token(value: str) -> str:
    return value.replace('"', '""')


def tokens_from_text(value: str) -> list[str]:
    tokens: list[str] = []
    for raw in re.findall(r"[^\W_]+(?:[-'][^\W_]+)*", value, flags=re.UNICODE):
        parts = raw.replace("-", " ").split()
        tokens.extend(parts or [raw])
    return tokens


def tokens_from_url(value: str) -> list[str]:
    parsed = urlparse(value)
    tokens: list[str] = []
    root = extract_domain(value)
    if root:
        tokens.extend([label for label in root.split(".") if label not in {"com", "org", "net", "co", "il"}])
    path_parts = [part for part in parsed.path.split("/") if part]
    for part in path_parts:
        tokens.extend(tokens_from_text(part))
    return tokens
