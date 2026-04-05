# draftsx

`draftsx` is a macOS-only CLI and local SQLite FTS index for [Drafts](https://getdrafts.com/).

Drafts is the only source of truth. `draftsx` talks to the real Drafts app through macOS scripting, then keeps a rebuildable local search index on disk so lookups stay fast even when your Drafts library grows.

## What it does

- `sync`: pull drafts from Drafts.app into a local SQLite FTS index
- `refresh`: alias for `sync`
- `status`: show cache freshness
- `search`: full-text search over title, body, and tags
- `list`: filter drafts by folder, tag, kind, and flagged status
- `show`: print a draft from the local index
- `current`: show the draft currently open in Drafts
- `context`: show related drafts, same-domain captures, and recent folder neighbors
- `archive`, `trash`, `flag`: mutate Drafts directly, then refresh the touched cache row
- `tag`: add/remove/set/clear tags directly in Drafts
- `stats`: aggregate folder/kind/domain counts from the cache
- `recent`, `blanks`, `dupes`: cleanup and triage views over the cache
- `open`: open a draft back in Drafts.app
- `create`: create a new draft in Drafts.app and upsert it into the index
- `append`: append text to an existing draft and refresh the index row

## Requirements

- macOS
- Drafts installed at `/Applications/Drafts.app`
- Python 3.12+
- `uv` recommended for local setup

## Install

```bash
cd /Users/yogev/projects/draftsx
uv sync
```

Run directly:

```bash
uv run draftsx sync
python -m draftsx --version
```

Or install as a tool:

```bash
uv tool install .
```

Or install with Homebrew:

```bash
brew install yogevkr/tap/draftsx
```

## Releases

Tag releases are automated with GitHub Actions.

```bash
git tag v0.1.1
git push origin v0.1.1
```

The release workflow:

- verifies the git tag matches `pyproject.toml`
- runs tests and builds `sdist` + wheel artifacts
- publishes a GitHub release in `YogevKr/draftsx`
- updates `YogevKr/homebrew-tap` with the new tarball URL + SHA256

Required repo secret in `YogevKr/draftsx`:

- `HOMEBREW_TAP_SSH_KEY`: private half of a write-enabled deploy key installed on `YogevKr/homebrew-tap`

## Usage

```bash
draftsx sync
draftsx status
draftsx search telemetry budget
draftsx search cloudflare --kind url
draftsx list --folder inbox --kind url
draftsx list --kind long
draftsx stats
draftsx recent --folder inbox
draftsx blanks --limit 20
draftsx dupes --limit 20
draftsx current
draftsx show 4A376C15
draftsx context 4A376C15
draftsx archive 4A376C15
draftsx trash 4A376C15
draftsx flag 4A376C15 --toggle
draftsx tag 4A376C15 --add work,follow-up
draftsx tag 4A376C15 --remove follow-up
draftsx open 4A376C15
printf 'note body\nmore text\n' | draftsx create --tags inbox,ideas
draftsx append 4A376C15 --text $'\n\nfollow-up'
```

By default the index lives at:

```text
~/Library/Application Support/draftsx/index.sqlite3
```

Override with:

```bash
draftsx --home /tmp/draftsx sync
```

## Notes

- Drafts remains the only source of truth. The local SQLite file is just a cache and can always be rebuilt with `draftsx sync`.
- Query commands auto-refresh a stale cache from Drafts unless you pass `--no-refresh`.
- The first `sync` is a full refresh. Later `create`, `append`, `current`, `archive`, `trash`, `flag`, and `tag` calls upsert the touched draft locally.
- Drafts are classified into derived kinds: `url`, `blank`, `note`, and `long`.
- `dupes` groups exact duplicate URLs and larger exact-text repeats. Tiny repeated notes are ignored on purpose to reduce noise.
- Search ranking is text-first with a recency bias, which works better for an inbox-heavy Drafts corpus than raw FTS rank alone.
- `show`, `open`, and `append` accept a full Draft UUID or a unique prefix.
- Search uses SQLite FTS5 token-prefix matching, so `telem` matches `telemetry`.

## Drafts integration

This project relies on Drafts' official macOS scripting support:

- AppleScript automation: <https://docs.getdrafts.com/docs/automation/applescript>
- Draft links / permalinks: <https://docs.getdrafts.com/docs/drafts/cross-linking>
