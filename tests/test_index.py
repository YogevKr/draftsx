from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from draftsx.index import DEFAULT_STALE_AFTER_SECONDS, DraftIndex, DraftIndexError, build_match_query
from draftsx.model import Draft, classify_draft_kind


class DraftIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.index = DraftIndex(Path(self.temp_dir.name))

    def test_search_matches_title_content_and_tags(self) -> None:
        self.index.replace_all(
            [
                Draft(
                    id="ABCDEF01-1111-1111-1111-111111111111",
                    title="Telemetry budget",
                    content="Cut ingest cost and keep full trace retention.",
                    tags=["infra", "cost"],
                    flagged=False,
                    folder="inbox",
                    created_at=None,
                    modified_at=None,
                    permalink="drafts://open?uuid=ABCDEF01-1111-1111-1111-111111111111",
                ),
                Draft(
                    id="ABCDEF02-2222-2222-2222-222222222222",
                    title="Weekend notes",
                    content="Pick up groceries.",
                    tags=["personal"],
                    flagged=False,
                    folder="archive",
                    created_at=None,
                    modified_at=None,
                    permalink="drafts://open?uuid=ABCDEF02-2222-2222-2222-222222222222",
                ),
            ]
        )

        results = self.index.search("telem cost", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].draft.id, "ABCDEF01-1111-1111-1111-111111111111")

    def test_search_prefers_more_recent_results_when_text_match_is_similar(self) -> None:
        self.index.replace_all(
            [
                Draft(
                    id="ABCDEF01-1111-1111-1111-111111111111",
                    title="Planner notes old",
                    content="Planner ranking notes",
                    tags=[],
                    flagged=False,
                    folder="archive",
                    created_at="2025-01-01T00:00:00Z",
                    modified_at="2025-01-01T00:00:00Z",
                    permalink=None,
                ),
                Draft(
                    id="ABCDEF02-2222-2222-2222-222222222222",
                    title="Planner notes new",
                    content="Planner ranking notes",
                    tags=[],
                    flagged=False,
                    folder="inbox",
                    created_at="2026-04-05T00:00:00Z",
                    modified_at="2026-04-05T00:00:00Z",
                    permalink=None,
                ),
            ]
        )

        results = self.index.search("planner notes", limit=2)

        self.assertEqual(results[0].draft.id, "ABCDEF02-2222-2222-2222-222222222222")

    def test_resolve_id_supports_unique_prefix(self) -> None:
        draft = Draft(
            id="ABCDEF01-1111-1111-1111-111111111111",
            title="Note",
            content="Hello",
            tags=[],
            flagged=False,
            folder="inbox",
            created_at=None,
            modified_at=None,
            permalink=None,
        )
        self.index.upsert(draft)

        self.assertEqual(self.index.resolve_id("ABCDEF01"), draft.id)

    def test_resolve_id_rejects_ambiguous_prefix(self) -> None:
        self.index.replace_all(
            [
                Draft(
                    id="ABCDEF01-1111-1111-1111-111111111111",
                    title="One",
                    content="Alpha",
                    tags=[],
                    flagged=False,
                    folder="inbox",
                    created_at=None,
                    modified_at=None,
                    permalink=None,
                ),
                Draft(
                    id="ABCDEF02-2222-2222-2222-222222222222",
                    title="Two",
                    content="Beta",
                    tags=[],
                    flagged=False,
                    folder="inbox",
                    created_at=None,
                    modified_at=None,
                    permalink=None,
                ),
            ]
        )

        with self.assertRaises(DraftIndexError):
            self.index.resolve_id("ABCDEF")

    def test_list_drafts_filters_and_sorts(self) -> None:
        self.index.replace_all(
            [
                Draft(
                    id="ABCDEF01-1111-1111-1111-111111111111",
                    title="Alpha",
                    content="Hello",
                    tags=["infra"],
                    flagged=False,
                    folder="archive",
                    created_at="2026-04-01T10:00:00Z",
                    modified_at="2026-04-01T11:00:00Z",
                    permalink=None,
                ),
                Draft(
                    id="ABCDEF02-2222-2222-2222-222222222222",
                    title="Bravo",
                    content="World",
                    tags=["ops", "infra"],
                    flagged=True,
                    folder="inbox",
                    created_at="2026-04-03T10:00:00Z",
                    modified_at="2026-04-03T11:00:00Z",
                    permalink=None,
                ),
                Draft(
                    id="ABCDEF03-3333-3333-3333-333333333333",
                    title="Charlie",
                    content="Else",
                    tags=["personal"],
                    flagged=True,
                    folder="inbox",
                    created_at="2026-04-02T10:00:00Z",
                    modified_at="2026-04-02T11:00:00Z",
                    permalink=None,
                ),
            ]
        )

        results = self.index.list_drafts(folder="inbox", tag="infra", flagged=True, sort="modified")

        self.assertEqual([draft.id for draft in results], ["ABCDEF02-2222-2222-2222-222222222222"])

    def test_list_drafts_filters_by_kind(self) -> None:
        self.index.replace_all(
            [
                Draft(
                    id="ABCDEF01-1111-1111-1111-111111111111",
                    title="https://example.com",
                    content="https://example.com",
                    tags=[],
                    flagged=False,
                    folder="inbox",
                    created_at=None,
                    modified_at=None,
                    permalink=None,
                ),
                Draft(
                    id="ABCDEF02-2222-2222-2222-222222222222",
                    title="Short note",
                    content="Hello world",
                    tags=[],
                    flagged=False,
                    folder="inbox",
                    created_at=None,
                    modified_at=None,
                    permalink=None,
                ),
            ]
        )

        results = self.index.list_drafts(kind="url")

        self.assertEqual([draft.id for draft in results], ["ABCDEF01-1111-1111-1111-111111111111"])

    def test_context_returns_related_and_same_domain(self) -> None:
        self.index.replace_all(
            [
                Draft(
                    id="AAA00001-1111-1111-1111-111111111111",
                    title="https://developers.cloudflare.com/durable-objects/examples/websocket-hibernation-server/",
                    content="https://developers.cloudflare.com/durable-objects/examples/websocket-hibernation-server/",
                    tags=[],
                    flagged=False,
                    folder="inbox",
                    created_at="2026-04-05T00:00:00Z",
                    modified_at="2026-04-05T00:00:00Z",
                    permalink=None,
                ),
                Draft(
                    id="BBB00002-2222-2222-2222-222222222222",
                    title="https://blog.cloudflare.com/how-cloudflare-runs-prometheus-at-scale",
                    content="https://blog.cloudflare.com/how-cloudflare-runs-prometheus-at-scale",
                    tags=[],
                    flagged=False,
                    folder="inbox",
                    created_at="2026-04-04T00:00:00Z",
                    modified_at="2026-04-04T00:00:00Z",
                    permalink=None,
                ),
                Draft(
                    id="CCC00003-3333-3333-3333-333333333333",
                    title="Cloudflare websocket notes",
                    content="Cloudflare websocket notes and durable objects setup",
                    tags=[],
                    flagged=False,
                    folder="archive",
                    created_at="2026-04-03T00:00:00Z",
                    modified_at="2026-04-03T00:00:00Z",
                    permalink=None,
                ),
            ]
        )

        result = self.index.context("AAA00001-1111-1111-1111-111111111111", limit=3)

        self.assertTrue(result.query)
        self.assertEqual(result.same_domain[0].id, "BBB00002-2222-2222-2222-222222222222")
        self.assertEqual(result.related[0].draft.id, "CCC00003-3333-3333-3333-333333333333")

    def test_status_tracks_sync_freshness(self) -> None:
        self.index.replace_all([])

        status = self.index.get_status()

        self.assertEqual(status["doc_count"], 0)
        self.assertEqual(status["source_count"], 0)
        self.assertFalse(status["needs_refresh"])
        self.assertIsNotNone(status["last_sync_at"])

        with self.index._connect() as conn:
            self.index._set_meta(conn, "last_sync_at", "2000-01-01T00:00:00Z")

        stale = self.index.get_status(max_age_seconds=DEFAULT_STALE_AFTER_SECONDS)
        self.assertTrue(stale["needs_refresh"])
        self.assertFalse(stale["fresh"])

    def test_collect_stats_summarizes_corpus_shape(self) -> None:
        self.index.replace_all(
            [
                Draft(
                    id="AAA00001-1111-1111-1111-111111111111",
                    title="https://example.com/a",
                    content="https://example.com/a",
                    tags=[],
                    flagged=False,
                    folder="inbox",
                    created_at="2026-04-01T00:00:00Z",
                    modified_at="2026-04-01T00:00:00Z",
                    permalink=None,
                ),
                Draft(
                    id="BBB00002-2222-2222-2222-222222222222",
                    title="Long note",
                    content="x" * 600,
                    tags=["work"],
                    flagged=True,
                    folder="archive",
                    created_at="2026-04-02T00:00:00Z",
                    modified_at="2026-04-03T00:00:00Z",
                    permalink=None,
                ),
            ]
        )

        stats = self.index.collect_stats(domain_limit=5)

        self.assertEqual(stats.total_drafts, 2)
        self.assertEqual(stats.flagged_count, 1)
        self.assertEqual(stats.tagged_count, 1)
        self.assertEqual(stats.folder_counts[0]["count"], 1)
        self.assertEqual({item["kind"] for item in stats.kind_counts}, {"url", "long"})
        self.assertEqual(stats.top_domains[0]["domain"], "example.com")

    def test_find_duplicate_groups_for_url_and_text(self) -> None:
        self.index.replace_all(
            [
                Draft(
                    id="AAA00001-1111-1111-1111-111111111111",
                    title="https://example.com/a",
                    content="https://example.com/a",
                    tags=[],
                    flagged=False,
                    folder="inbox",
                    created_at=None,
                    modified_at="2026-04-05T00:00:00Z",
                    permalink=None,
                ),
                Draft(
                    id="BBB00002-2222-2222-2222-222222222222",
                    title="https://example.com/a",
                    content="https://example.com/a",
                    tags=[],
                    flagged=False,
                    folder="archive",
                    created_at=None,
                    modified_at="2026-04-04T00:00:00Z",
                    permalink=None,
                ),
                Draft(
                    id="CCC00003-3333-3333-3333-333333333333",
                    title="Long note",
                    content="This is a repeated long note body with enough text to count as a real duplicate entry.",
                    tags=[],
                    flagged=False,
                    folder="inbox",
                    created_at=None,
                    modified_at="2026-04-03T00:00:00Z",
                    permalink=None,
                ),
                Draft(
                    id="DDD00004-4444-4444-4444-444444444444",
                    title="Another title",
                    content="This is a repeated long note body with enough text to count as a real duplicate entry.",
                    tags=[],
                    flagged=False,
                    folder="archive",
                    created_at=None,
                    modified_at="2026-04-02T00:00:00Z",
                    permalink=None,
                ),
            ]
        )

        groups = self.index.find_duplicate_groups(limit=10, sample_size=3)

        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0].duplicate_type, "text")
        self.assertEqual(groups[0].count, 2)
        self.assertEqual(groups[1].duplicate_type, "url")
        self.assertEqual(groups[1].count, 2)


class MatchQueryTests(unittest.TestCase):
    def test_build_match_query_uses_prefix_terms(self) -> None:
        self.assertEqual(build_match_query("telemetry budget"), '"telemetry"* AND "budget"*')

    def test_build_match_query_requires_tokens(self) -> None:
        with self.assertRaises(DraftIndexError):
            build_match_query("!!!")


class DraftKindTests(unittest.TestCase):
    def test_classify_draft_kind(self) -> None:
        self.assertEqual(classify_draft_kind("", ""), "blank")
        self.assertEqual(classify_draft_kind("https://example.com", "https://example.com"), "url")
        self.assertEqual(classify_draft_kind("Note", "x" * 501), "long")
        self.assertEqual(classify_draft_kind("Note", "hello"), "note")


if __name__ == "__main__":
    unittest.main()
