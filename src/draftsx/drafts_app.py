from __future__ import annotations

import json
import subprocess
from typing import Any

from .model import Draft


class DraftsAppError(RuntimeError):
    pass


FETCH_SHARED_JXA = r"""
function textValue(value) {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value);
}

function dateValue(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  if (value instanceof Date) {
    return value.toISOString();
  }
  return String(value);
}

function listValue(value) {
  if (value === null || value === undefined) {
    return [];
  }
  return Array.from(value).map(String);
}

function serializeProperties(props) {
  return {
    id: textValue(props.id),
    title: textValue(props.title),
    content: textValue(props.content),
    tags: listValue(props.tagList),
    flagged: Boolean(props.flagged),
    folder: textValue(props.folder),
    createdAt: dateValue(props.creationDate),
    modifiedAt: dateValue(props.modificationDate),
    permalink: textValue(props.permalink)
  };
}
"""


FETCH_ALL_JXA = (
    FETCH_SHARED_JXA
    + r"""
function run(argv) {
  const app = Application("Drafts");
  return JSON.stringify(app.drafts().map(draft => serializeProperties(draft.properties())));
}
"""
)


FETCH_ONE_JXA = (
    FETCH_SHARED_JXA
    + r"""
function run(argv) {
  const app = Application("Drafts");
  const draft = app.drafts.byId(argv[0]);
  return JSON.stringify(serializeProperties(draft.properties()));
}
"""
)


FETCH_CURRENT_JXA = (
    FETCH_SHARED_JXA
    + r"""
function run(argv) {
  const app = Application("Drafts");
  return JSON.stringify(serializeProperties(app.currentDraft().properties()));
}
"""
)


CREATE_APPLESCRIPT = r"""
on run argv
	set theContent to item 1 of argv
	set tagsArg to item 2 of argv
	set flaggedArg to item 3 of argv

	set isFlagged to false
	if flaggedArg is "true" then set isFlagged to true

	set theTags to {}
	if tagsArg is not "" then
		set oldDelimiters to AppleScript's text item delimiters
		set AppleScript's text item delimiters to linefeed
		set theTags to text items of tagsArg
		set AppleScript's text item delimiters to oldDelimiters
	end if

	tell application "Drafts"
		set newDraft to make new draft with properties {content:theContent, flagged:isFlagged, tag list:theTags}
		return id of newDraft as string
	end tell
end run
"""


APPEND_APPLESCRIPT = r"""
on run argv
	set draftId to item 1 of argv
	set extraText to item 2 of argv

	tell application "Drafts"
		set targetDraft to draft id draftId
		set content of targetDraft to (content of targetDraft as string) & extraText
		return draftId
	end tell
end run
"""


SET_FOLDER_APPLESCRIPT = r"""
on run argv
	set draftId to item 1 of argv
	set folderName to item 2 of argv

	tell application "Drafts"
		set targetDraft to draft id draftId
		if folderName is "archive" then
			set folder of targetDraft to archive
		else if folderName is "trash" then
			set folder of targetDraft to trash
		else if folderName is "inbox" then
			set folder of targetDraft to inbox
		else
			error "unsupported folder: " & folderName
		end if
		return draftId
	end tell
end run
"""


SET_FLAG_APPLESCRIPT = r"""
on run argv
	set draftId to item 1 of argv
	set modeArg to item 2 of argv

	tell application "Drafts"
		set targetDraft to draft id draftId
		if modeArg is "toggle" then
			set flagged of targetDraft to not (flagged of targetDraft)
		else if modeArg is "true" then
			set flagged of targetDraft to true
		else if modeArg is "false" then
			set flagged of targetDraft to false
		else
			error "unsupported flag mode: " & modeArg
		end if
		return draftId
	end tell
end run
"""


SET_TAGS_APPLESCRIPT = r"""
on run argv
	set draftId to item 1 of argv
	set tagsArg to item 2 of argv

	set theTags to {}
	if tagsArg is not "" then
		set oldDelimiters to AppleScript's text item delimiters
		set AppleScript's text item delimiters to linefeed
		set theTags to text items of tagsArg
		set AppleScript's text item delimiters to oldDelimiters
	end if

	tell application "Drafts"
		set targetDraft to draft id draftId
		set tag list of targetDraft to theTags
		return draftId
	end tell
end run
"""


def fetch_all_drafts() -> list[Draft]:
    payload = _run_jxa(FETCH_ALL_JXA)
    raw_items = json.loads(payload)
    return [Draft.from_mapping(item) for item in raw_items]


def fetch_draft(draft_id: str) -> Draft:
    payload = _run_jxa(FETCH_ONE_JXA, draft_id)
    data = json.loads(payload)
    draft = Draft.from_mapping(data)
    if not draft.id:
        raise DraftsAppError(f"draft not found: {draft_id}")
    return draft


def fetch_current_draft() -> Draft:
    payload = _run_jxa(FETCH_CURRENT_JXA)
    data = json.loads(payload)
    draft = Draft.from_mapping(data)
    if not draft.id:
        raise DraftsAppError("current draft not found")
    return draft


def create_draft(content: str, tags: list[str] | None = None, flagged: bool = False) -> Draft:
    draft_id = _run_applescript(CREATE_APPLESCRIPT, content, "\n".join(tags or []), _flag(flagged)).strip()
    return fetch_draft(draft_id)


def append_to_draft(draft_id: str, text: str) -> Draft:
    _run_applescript(APPEND_APPLESCRIPT, draft_id, text)
    return fetch_draft(draft_id)


def move_draft_to_folder(draft_id: str, folder: str) -> Draft:
    _run_applescript(SET_FOLDER_APPLESCRIPT, draft_id, folder)
    return fetch_draft(draft_id)


def set_draft_flag(draft_id: str, *, flagged: bool | None = None, toggle: bool = False) -> Draft:
    if toggle:
        mode = "toggle"
    elif flagged is True:
        mode = "true"
    elif flagged is False:
        mode = "false"
    else:
        raise ValueError("flag mutation requires flagged=True/False or toggle=True")
    _run_applescript(SET_FLAG_APPLESCRIPT, draft_id, mode)
    return fetch_draft(draft_id)


def set_draft_tags(draft_id: str, tags: list[str]) -> Draft:
    _run_applescript(SET_TAGS_APPLESCRIPT, draft_id, "\n".join(tags))
    return fetch_draft(draft_id)


def _flag(value: bool) -> str:
    return "true" if value else "false"


def _run_jxa(script: str, *args: str) -> str:
    return _run_osascript(["-l", "JavaScript", *args], script)


def _run_applescript(script: str, *args: str) -> str:
    return _run_osascript([*args], script)


def _run_osascript(args: list[str], script: str) -> str:
    command = ["osascript"]
    if args and args[0] == "-l":
        command.extend(args[:2])
        argv = args[2:]
    else:
        argv = args
    command.extend(["-"] + argv)

    result = subprocess.run(
        command,
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "unknown osascript failure"
        raise DraftsAppError(message)
    return result.stdout.strip()
