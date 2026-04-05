from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from .drafts_app import (
    DraftsAppError,
    append_to_draft,
    create_draft,
    fetch_all_drafts,
    fetch_current_draft,
    move_draft_to_folder,
    set_draft_flag,
    set_draft_tags,
)
from .index import DraftIndex, DraftIndexError, KIND_VALUES
from .model import Draft
from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="draftsx", description="CLI and local index for Drafts.app")
    parser.add_argument("--home", type=Path, help="directory for the local SQLite index")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync", help="refresh the local index from Drafts.app")
    sync_parser.add_argument("--json", action="store_true", help="emit JSON")
    sync_parser.set_defaults(handler=cmd_sync)

    refresh_parser = subparsers.add_parser("refresh", help="alias for sync")
    refresh_parser.add_argument("--json", action="store_true", help="emit JSON")
    refresh_parser.set_defaults(handler=cmd_sync)

    status_parser = subparsers.add_parser("status", help="show cache freshness and sync state")
    status_parser.add_argument("--json", action="store_true", help="emit JSON")
    status_parser.set_defaults(handler=cmd_status)

    search_parser = subparsers.add_parser("search", help="search the local Drafts index")
    search_parser.add_argument("query", help="search terms")
    search_parser.add_argument("--kind", choices=KIND_VALUES, help="filter by derived draft kind")
    search_parser.add_argument("--limit", type=int, default=10, help="maximum results")
    search_parser.add_argument("--json", action="store_true", help="emit JSON")
    search_parser.add_argument("--no-refresh", action="store_true", help="do not auto-refresh a stale cache from Drafts")
    search_parser.set_defaults(handler=cmd_search)

    list_parser = subparsers.add_parser("list", help="list drafts from the local cache")
    list_parser.add_argument("--folder", choices=["inbox", "archive", "trash"], help="filter by folder")
    list_parser.add_argument("--tag", help="filter by exact tag name")
    list_parser.add_argument("--kind", choices=KIND_VALUES, help="filter by derived draft kind")
    list_parser.add_argument("--flagged", action="store_true", help="only flagged drafts")
    list_parser.add_argument("--limit", type=int, default=30, help="maximum results")
    list_parser.add_argument("--offset", type=int, default=0, help="result offset")
    list_parser.add_argument("--sort", choices=["modified", "created", "title"], default="modified", help="sort order")
    list_parser.add_argument("--json", action="store_true", help="emit JSON")
    list_parser.add_argument("--no-refresh", action="store_true", help="do not auto-refresh a stale cache from Drafts")
    list_parser.set_defaults(handler=cmd_list)

    show_parser = subparsers.add_parser("show", help="print a draft from the local index")
    show_parser.add_argument("draft_id", help="full draft UUID or a unique prefix")
    show_parser.add_argument("--json", action="store_true", help="emit JSON")
    show_parser.add_argument("--no-refresh", action="store_true", help="do not auto-refresh a stale cache from Drafts")
    show_parser.set_defaults(handler=cmd_show)

    open_parser = subparsers.add_parser("open", help="open a draft back in Drafts.app")
    open_parser.add_argument("draft_id", help="full draft UUID or a unique prefix")
    open_parser.add_argument("--no-refresh", action="store_true", help="do not auto-refresh a stale cache from Drafts")
    open_parser.set_defaults(handler=cmd_open)

    current_parser = subparsers.add_parser("current", help="show the draft currently open in Drafts")
    current_parser.add_argument("--json", action="store_true", help="emit JSON")
    current_parser.set_defaults(handler=cmd_current)

    archive_parser = subparsers.add_parser("archive", help="move a draft to archive in Drafts")
    archive_parser.add_argument("draft_id", help="full draft UUID or a unique prefix")
    archive_parser.add_argument("--json", action="store_true", help="emit JSON")
    archive_parser.add_argument("--no-refresh", action="store_true", help="do not auto-refresh a stale cache from Drafts")
    archive_parser.set_defaults(handler=cmd_archive)

    trash_parser = subparsers.add_parser("trash", help="move a draft to trash in Drafts")
    trash_parser.add_argument("draft_id", help="full draft UUID or a unique prefix")
    trash_parser.add_argument("--json", action="store_true", help="emit JSON")
    trash_parser.add_argument("--no-refresh", action="store_true", help="do not auto-refresh a stale cache from Drafts")
    trash_parser.set_defaults(handler=cmd_trash)

    flag_parser = subparsers.add_parser("flag", help="set or toggle a draft's flagged state in Drafts")
    flag_parser.add_argument("draft_id", help="full draft UUID or a unique prefix")
    flag_group = flag_parser.add_mutually_exclusive_group()
    flag_group.add_argument("--on", action="store_true", help="set flagged=true")
    flag_group.add_argument("--off", action="store_true", help="set flagged=false")
    flag_group.add_argument("--toggle", action="store_true", help="toggle the flagged state")
    flag_parser.add_argument("--json", action="store_true", help="emit JSON")
    flag_parser.add_argument("--no-refresh", action="store_true", help="do not auto-refresh a stale cache from Drafts")
    flag_parser.set_defaults(handler=cmd_flag)

    tag_parser = subparsers.add_parser("tag", help="modify a draft's tag list in Drafts")
    tag_parser.add_argument("draft_id", help="full draft UUID or a unique prefix")
    tag_parser.add_argument("--add", default="", help="comma-separated tags to add")
    tag_parser.add_argument("--remove", default="", help="comma-separated tags to remove")
    tag_group = tag_parser.add_mutually_exclusive_group()
    tag_group.add_argument("--set", dest="set_tags", default="", help="comma-separated replacement tags")
    tag_group.add_argument("--clear", action="store_true", help="remove all tags")
    tag_parser.add_argument("--json", action="store_true", help="emit JSON")
    tag_parser.add_argument("--no-refresh", action="store_true", help="do not auto-refresh a stale cache from Drafts")
    tag_parser.set_defaults(handler=cmd_tag)

    context_parser = subparsers.add_parser("context", help="show related drafts and corpus context for one draft")
    context_parser.add_argument("draft_id", help="full draft UUID or a unique prefix")
    context_parser.add_argument("--limit", type=int, default=5, help="maximum related results per section")
    context_parser.add_argument("--json", action="store_true", help="emit JSON")
    context_parser.add_argument("--no-refresh", action="store_true", help="do not auto-refresh a stale cache from Drafts")
    context_parser.set_defaults(handler=cmd_context)

    recent_parser = subparsers.add_parser("recent", help="show recently modified drafts")
    recent_parser.add_argument("--folder", choices=["inbox", "archive", "trash"], help="filter by folder")
    recent_parser.add_argument("--kind", choices=KIND_VALUES, help="filter by derived draft kind")
    recent_parser.add_argument("--limit", type=int, default=20, help="maximum results")
    recent_parser.add_argument("--json", action="store_true", help="emit JSON")
    recent_parser.add_argument("--no-refresh", action="store_true", help="do not auto-refresh a stale cache from Drafts")
    recent_parser.set_defaults(handler=cmd_recent)

    blanks_parser = subparsers.add_parser("blanks", help="show recent blank drafts")
    blanks_parser.add_argument("--folder", choices=["inbox", "archive", "trash"], help="filter by folder")
    blanks_parser.add_argument("--limit", type=int, default=20, help="maximum results")
    blanks_parser.add_argument("--json", action="store_true", help="emit JSON")
    blanks_parser.add_argument("--no-refresh", action="store_true", help="do not auto-refresh a stale cache from Drafts")
    blanks_parser.set_defaults(handler=cmd_blanks)

    dupes_parser = subparsers.add_parser("dupes", help="show duplicate URL/text groups")
    dupes_parser.add_argument("--limit", type=int, default=20, help="maximum duplicate groups")
    dupes_parser.add_argument("--sample-size", type=int, default=5, help="sample drafts per group")
    dupes_parser.add_argument("--json", action="store_true", help="emit JSON")
    dupes_parser.add_argument("--no-refresh", action="store_true", help="do not auto-refresh a stale cache from Drafts")
    dupes_parser.set_defaults(handler=cmd_dupes)

    stats_parser = subparsers.add_parser("stats", help="show aggregate corpus stats from the local cache")
    stats_parser.add_argument("--json", action="store_true", help="emit JSON")
    stats_parser.add_argument("--no-refresh", action="store_true", help="do not auto-refresh a stale cache from Drafts")
    stats_parser.set_defaults(handler=cmd_stats)

    create_parser = subparsers.add_parser("create", help="create a new draft in Drafts.app")
    create_parser.add_argument("content", nargs="?", help="draft content; stdin is used when omitted and piped")
    create_parser.add_argument("--file", type=Path, help="read content from a file")
    create_parser.add_argument("--tags", default="", help="comma-separated tags")
    create_parser.add_argument("--flagged", action="store_true", help="mark the draft as flagged")
    create_parser.add_argument("--open", action="store_true", help="open the created draft in Drafts.app")
    create_parser.set_defaults(handler=cmd_create)

    append_parser = subparsers.add_parser("append", help="append text to an existing draft")
    append_parser.add_argument("draft_id", help="full draft UUID or a unique prefix")
    append_parser.add_argument("text", nargs="?", help="text to append; stdin is used when omitted and piped")
    append_parser.add_argument("--file", type=Path, help="read appended text from a file")
    append_parser.add_argument("--open", action="store_true", help="open the updated draft in Drafts.app")
    append_parser.add_argument("--no-refresh", action="store_true", help="do not auto-refresh a stale cache from Drafts")
    append_parser.set_defaults(handler=cmd_append)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    index = DraftIndex(args.home)

    try:
        return int(args.handler(args, index) or 0)
    except (DraftIndexError, DraftsAppError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def sync_index(index: DraftIndex) -> dict[str, object]:
    drafts = fetch_all_drafts()
    index.replace_all(drafts)
    status = index.get_status()
    return {"count": len(drafts), "db_path": str(index.db_path), "status": status}


def ensure_index(index: DraftIndex, auto_refresh: bool = True) -> dict[str, object]:
    status = index.get_status()
    if auto_refresh and status["needs_refresh"]:
        sync_index(index)
        return index.get_status()
    return status


def cmd_sync(args: argparse.Namespace, index: DraftIndex) -> int:
    result = sync_index(index)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    print(f"synced {result['count']} drafts into {result['db_path']}")
    return 0


def cmd_status(args: argparse.Namespace, index: DraftIndex) -> int:
    status = index.get_status()
    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False))
        return 0
    print(f"index: {status['db_path']}")
    print(f"docs: {status['doc_count']}")
    print(f"last sync: {status['last_sync_at'] or '-'}")
    print(f"fresh: {status['fresh']}")
    print(f"stale after: {status['stale_after_seconds']}s")
    if status["reasons"]:
        print("reasons:")
        for reason in status["reasons"]:
            print(f"  - {reason}")
    return 0


def cmd_search(args: argparse.Namespace, index: DraftIndex) -> int:
    ensure_index(index, auto_refresh=not args.no_refresh)
    results = index.search(args.query, limit=args.limit, kind=args.kind)
    if args.json:
        payload = [
            {
                "id": result.draft.id,
                "kind": result.draft.kind,
                "title": result.draft.title,
                "tags": result.draft.tags,
                "folder": result.draft.folder,
                "flagged": result.draft.flagged,
                "snippet": result.snippet,
                "score": result.score,
            }
            for result in results
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if not results:
        print("no matches")
        return 0

    for result in results:
        tags = ",".join(result.draft.tags)
        print(f"{short_id(result.draft.id)}  {result.draft.title}")
        print(f"  kind={result.draft.kind} folder={result.draft.folder} flagged={result.draft.flagged} tags={tags or '-'}")
        if result.snippet:
            print(f"  {result.snippet}")
    return 0


def cmd_list(args: argparse.Namespace, index: DraftIndex) -> int:
    ensure_index(index, auto_refresh=not args.no_refresh)
    results = index.list_drafts(
        folder=args.folder,
        tag=args.tag,
        kind=args.kind,
        flagged=True if args.flagged else None,
        limit=args.limit,
        offset=args.offset,
        sort=args.sort,
    )
    if args.json:
        print(json.dumps([draft_to_dict(draft) for draft in results], indent=2, ensure_ascii=False))
        return 0

    if not results:
        print("no drafts")
        return 0

    for draft in results:
        tags = ",".join(draft.tags)
        stamp = draft.modified_at or draft.created_at or "-"
        print(f"{short_id(draft.id)}  {draft.title}")
        print(f"  kind={draft.kind} modified={stamp} folder={draft.folder} flagged={draft.flagged} tags={tags or '-'}")
    return 0


def cmd_show(args: argparse.Namespace, index: DraftIndex) -> int:
    draft = get_indexed_draft(index, args.draft_id, auto_refresh=not args.no_refresh)
    if args.json:
        print(json.dumps(draft_to_dict(draft), indent=2, ensure_ascii=False))
        return 0

    print(f"id: {draft.id}")
    print(f"title: {draft.title}")
    print(f"kind: {draft.kind}")
    print(f"folder: {draft.folder}")
    print(f"flagged: {draft.flagged}")
    print(f"tags: {', '.join(draft.tags) or '-'}")
    print(f"domain: {draft.domain or '-'}")
    print(f"primary url: {draft.primary_url or '-'}")
    print(f"created: {draft.created_at or '-'}")
    print(f"modified: {draft.modified_at or '-'}")
    print(f"permalink: {draft.permalink or '-'}")
    print()
    print(draft.content)
    return 0


def cmd_open(args: argparse.Namespace, index: DraftIndex) -> int:
    draft = get_indexed_draft(index, args.draft_id, auto_refresh=not args.no_refresh)
    if not draft.permalink:
        raise DraftIndexError(f"draft {draft.id} does not have a permalink")
    subprocess.run(["open", draft.permalink], check=True)
    print(f"opened {draft.id}")
    return 0


def cmd_current(args: argparse.Namespace, index: DraftIndex) -> int:
    draft = fetch_current_draft()
    index.upsert(draft)
    if args.json:
        print(json.dumps(draft_to_dict(draft), indent=2, ensure_ascii=False))
        return 0
    return cmd_show(argparse.Namespace(draft_id=draft.id, json=False, no_refresh=True), index)


def cmd_archive(args: argparse.Namespace, index: DraftIndex) -> int:
    draft = mutate_draft(index, args.draft_id, auto_refresh=not args.no_refresh, action=lambda draft_id: move_draft_to_folder(draft_id, "archive"))
    return print_mutated_draft("archived", draft, as_json=args.json)


def cmd_trash(args: argparse.Namespace, index: DraftIndex) -> int:
    draft = mutate_draft(index, args.draft_id, auto_refresh=not args.no_refresh, action=lambda draft_id: move_draft_to_folder(draft_id, "trash"))
    return print_mutated_draft("trashed", draft, as_json=args.json)


def cmd_flag(args: argparse.Namespace, index: DraftIndex) -> int:
    toggle = args.toggle or (not args.on and not args.off)
    draft = mutate_draft(
        index,
        args.draft_id,
        auto_refresh=not args.no_refresh,
        action=lambda draft_id: set_draft_flag(draft_id, flagged=True if args.on else False if args.off else None, toggle=toggle),
    )
    return print_mutated_draft("flagged" if draft.flagged else "unflagged", draft, as_json=args.json)


def cmd_tag(args: argparse.Namespace, index: DraftIndex) -> int:
    add_tags = parse_tags(args.add)
    remove_tags = set(parse_tags(args.remove))
    set_tags = parse_tags(args.set_tags)
    if not add_tags and not remove_tags and not set_tags and not args.clear:
        raise ValueError("tag mutation requires --add, --remove, --set, or --clear")

    base_draft = get_indexed_draft(index, args.draft_id, auto_refresh=not args.no_refresh)
    if args.clear:
        next_tags: list[str] = []
    elif set_tags:
        next_tags = dedupe_tags(set_tags)
    else:
        next_tags = dedupe_tags(base_draft.tags)

    if not args.clear and not set_tags:
        next_tags = [tag for tag in next_tags if tag not in remove_tags]
        next_tags.extend(tag for tag in add_tags if tag not in next_tags)
    elif remove_tags:
        next_tags = [tag for tag in next_tags if tag not in remove_tags]
        next_tags.extend(tag for tag in add_tags if tag not in next_tags)

    draft = mutate_draft(index, base_draft.id, auto_refresh=False, action=lambda draft_id: set_draft_tags(draft_id, next_tags))
    return print_mutated_draft("tagged", draft, as_json=args.json)


def cmd_context(args: argparse.Namespace, index: DraftIndex) -> int:
    ensure_index(index, auto_refresh=not args.no_refresh)
    anchor = get_indexed_draft(index, args.draft_id, auto_refresh=False)
    result = index.context(anchor.id, limit=args.limit)
    if args.json:
        print(json.dumps(context_to_dict(result), indent=2, ensure_ascii=False))
        return 0

    print(f"anchor: {anchor.title}")
    print(f"id: {anchor.id}")
    print(f"kind: {anchor.kind}")
    print(f"folder: {anchor.folder}")
    print(f"domain: {anchor.domain or '-'}")
    print(f"query: {result.query or '-'}")
    print()
    print("related:")
    if not result.related:
        print("  none")
    else:
        for item in result.related:
            print(f"  {short_id(item.draft.id)}  {item.draft.title}")
            if item.snippet:
                print(f"    {item.snippet}")
    print()
    print("same domain:")
    if not result.same_domain:
        print("  none")
    else:
        for draft in result.same_domain:
            print(f"  {short_id(draft.id)}  {draft.title}")
    print()
    print("recent in folder:")
    if not result.recent_in_folder:
        print("  none")
    else:
        for draft in result.recent_in_folder:
            print(f"  {short_id(draft.id)}  {draft.title}")
    return 0


def cmd_recent(args: argparse.Namespace, index: DraftIndex) -> int:
    ensure_index(index, auto_refresh=not args.no_refresh)
    results = index.recent_drafts(folder=args.folder, kind=args.kind, limit=args.limit)
    if args.json:
        print(json.dumps([draft_to_dict(draft) for draft in results], indent=2, ensure_ascii=False))
        return 0
    return render_draft_list(results)


def cmd_blanks(args: argparse.Namespace, index: DraftIndex) -> int:
    ensure_index(index, auto_refresh=not args.no_refresh)
    results = index.blank_drafts(folder=args.folder, limit=args.limit)
    if args.json:
        print(json.dumps([draft_to_dict(draft) for draft in results], indent=2, ensure_ascii=False))
        return 0
    return render_draft_list(results)


def cmd_dupes(args: argparse.Namespace, index: DraftIndex) -> int:
    ensure_index(index, auto_refresh=not args.no_refresh)
    groups = index.find_duplicate_groups(limit=args.limit, sample_size=args.sample_size)
    if args.json:
        print(json.dumps([duplicate_group_to_dict(group) for group in groups], indent=2, ensure_ascii=False))
        return 0
    if not groups:
        print("no duplicate groups")
        return 0
    for group in groups:
        key_preview = group.key[4:] if group.key.startswith(("url:", "text:")) else group.key
        print(f"{group.duplicate_type} duplicates ({group.count})  {key_preview[:120]}")
        for draft in group.sample_drafts:
            print(f"  {short_id(draft.id)}  {draft.title}")
    return 0


def cmd_stats(args: argparse.Namespace, index: DraftIndex) -> int:
    ensure_index(index, auto_refresh=not args.no_refresh)
    stats = index.collect_stats()
    if args.json:
        print(json.dumps(asdict(stats), indent=2, ensure_ascii=False))
        return 0

    print(f"drafts: {stats.total_drafts}")
    print(f"flagged: {stats.flagged_count}")
    print(f"tagged: {stats.tagged_count}")
    print(f"latest modified: {stats.latest_modified_at or '-'}")
    print()
    print("folders:")
    for item in stats.folder_counts:
        print(f"  {item['folder']}: {item['count']}")
    print()
    print("kinds:")
    for item in stats.kind_counts:
        print(f"  {item['kind']}: {item['count']}")
    print()
    print("top domains:")
    if not stats.top_domains:
        print("  none")
    else:
        for item in stats.top_domains:
            print(f"  {item['domain']}: {item['count']}")
    return 0


def cmd_create(args: argparse.Namespace, index: DraftIndex) -> int:
    content = read_text_input(args.content, args.file)
    tags = parse_tags(args.tags)
    draft = create_draft(content=content, tags=tags, flagged=args.flagged)
    index.upsert(draft)
    print(f"created {draft.id}")
    if args.open and draft.permalink:
        subprocess.run(["open", draft.permalink], check=True)
    return 0


def cmd_append(args: argparse.Namespace, index: DraftIndex) -> int:
    ensure_index(index, auto_refresh=not args.no_refresh)
    draft_id = index.resolve_id(args.draft_id)
    text = read_text_input(args.text, args.file)
    draft = append_to_draft(draft_id, text)
    index.upsert(draft)
    print(f"updated {draft.id}")
    if args.open and draft.permalink:
        subprocess.run(["open", draft.permalink], check=True)
    return 0


def get_indexed_draft(index: DraftIndex, value: str, *, auto_refresh: bool = True) -> Draft:
    ensure_index(index, auto_refresh=auto_refresh)
    resolved = index.resolve_id(value)
    draft = index.get(resolved)
    if draft is None:
        raise DraftIndexError(f"draft {resolved} is not indexed")
    return draft


def mutate_draft(index: DraftIndex, value: str, *, auto_refresh: bool, action: Callable[[str], Draft]) -> Draft:
    draft = get_indexed_draft(index, value, auto_refresh=auto_refresh)
    updated = action(draft.id)
    index.upsert(updated)
    return updated


def print_mutated_draft(verb: str, draft: Draft, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"action": verb, "draft": draft_to_dict(draft)}, indent=2, ensure_ascii=False))
        return 0
    print(f"{verb} {draft.id}")
    print(f"  title={draft.title}")
    print(f"  folder={draft.folder} flagged={draft.flagged}")
    print(f"  tags={', '.join(draft.tags) or '-'}")
    return 0


def render_draft_list(results: list[Draft]) -> int:
    if not results:
        print("no drafts")
        return 0
    for draft in results:
        tags = ",".join(draft.tags)
        stamp = draft.modified_at or draft.created_at or "-"
        print(f"{short_id(draft.id)}  {draft.title}")
        print(f"  kind={draft.kind} modified={stamp} folder={draft.folder} flagged={draft.flagged} tags={tags or '-'}")
    return 0


def parse_tags(raw: str) -> list[str]:
    return [tag.strip() for tag in raw.split(",") if tag.strip()]


def dedupe_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        normalized = tag.strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result


def read_text_input(inline_value: str | None, file_path: Path | None) -> str:
    if file_path is not None:
        return file_path.read_text()
    if inline_value is not None:
        return inline_value
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data:
            return data
    raise ValueError("content required: pass text, --file, or pipe stdin")


def draft_to_dict(draft: Draft) -> dict[str, object]:
    return {
        "id": draft.id,
        "kind": draft.kind,
        "title": draft.title,
        "content": draft.content,
        "tags": draft.tags,
        "flagged": draft.flagged,
        "folder": draft.folder,
        "domain": draft.domain,
        "primary_url": draft.primary_url,
        "created_at": draft.created_at,
        "modified_at": draft.modified_at,
        "permalink": draft.permalink,
    }


def context_to_dict(result: object) -> dict[str, object]:
    return {
        "anchor": draft_to_dict(result.anchor),
        "query": result.query,
        "related": [
            {
                "draft": draft_to_dict(item.draft),
                "snippet": item.snippet,
                "score": item.score,
            }
            for item in result.related
        ],
        "same_domain": [draft_to_dict(draft) for draft in result.same_domain],
        "recent_in_folder": [draft_to_dict(draft) for draft in result.recent_in_folder],
    }


def duplicate_group_to_dict(group: object) -> dict[str, object]:
    return {
        "key": group.key,
        "duplicate_type": group.duplicate_type,
        "count": group.count,
        "sample_drafts": [draft_to_dict(draft) for draft in group.sample_drafts],
    }


def short_id(value: str) -> str:
    return value[:8]


if __name__ == "__main__":
    raise SystemExit(main())
