"""ClickUp — closed Tasks, for the day's record.

Mirrors LocalLLMAgent's agent/tools/clickup.py — just the slice
`closed_tasks` needs (`_team_id`, `_spaces`, `_fetch_tasks`,
`_ms_to_local_date`). Everything else in that module (the chat tools:
list/read/add/move/comment, digests, tag watchers) is not journaling and
stays there.

Key resolution order: config/.env file > CLICKUP_API_TOKEN env var
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

from scribejay.core.dates import local_timezone
from scribejay.core.http import http_error, load_env, missing_key_error, resolve_key

load_env()

API_ROOT = "https://api.clickup.com/api/v2"

# ClickUp's personal token is sent raw, with no "Bearer" prefix.
TIMEOUT_S = 15

# 100 tasks per page. The ceiling bounds the walk so an unexpectedly large
# workspace can't spin the loop.
_PAGE_SIZE = 100
_MAX_PAGES = 10


class _ClickUpError(Exception):
    """A configuration- or lookup-shaped failure with a message meant to be
    read: no workspace, an unknown space."""


def _get(path: str, token: str, **params) -> dict:
    resp = requests.get(
        f"{API_ROOT}{path}",
        headers={"Authorization": token},
        params=params or None,
        timeout=TIMEOUT_S,
    )
    resp.raise_for_status()
    return resp.json()


def _team_id(token: str) -> str:
    teams = _get("/team", token).get("teams", [])
    if not teams:
        raise _ClickUpError("this ClickUp token has no workspaces")
    if len(teams) > 1:
        names = ", ".join(t.get("name", t["id"]) for t in teams)
        raise _ClickUpError(
            f"this token sees {len(teams)} ClickUp workspaces ({names}); "
            "the tools assume one and would silently pick the first"
        )
    return teams[0]["id"]


def _spaces(token: str, team_id: str) -> list:
    """Every Space on the workspace, with its id, name, and the statuses it
    defines."""
    spaces = _get(f"/team/{team_id}/space", token, archived="false").get("spaces", [])
    return [
        {
            "name": s.get("name", ""),
            "id": s["id"],
            "statuses": [
                {"status": st.get("status", ""), "type": st.get("type", "")}
                for st in s.get("statuses", [])
            ],
        }
        for s in spaces
        if s.get("id")
    ]


def _fetch_tasks(token: str, team_id: str, space_ids: list, include_done: bool,
                 updated_after_ms: int = None, logger=None) -> list:
    """Every task in the given Spaces, paged. ClickUp excludes its Closed
    status group by default, so include_done is required to see shipped work."""
    tasks, page = [], 0
    while page < _MAX_PAGES:
        params = {"page": page, "space_ids[]": space_ids}
        if include_done:
            params["include_closed"] = "true"
        if updated_after_ms is not None:
            params["date_updated_gt"] = int(updated_after_ms)
        body = _get(f"/team/{team_id}/task", token, **params)
        batch = body.get("tasks", [])
        tasks.extend(batch)
        if body.get("last_page") or len(batch) < _PAGE_SIZE:
            return tasks
        page += 1

    # Reaching here means the cap stopped the walk, not the API: there are more
    # tasks in the window than _MAX_PAGES * _PAGE_SIZE. The day then reads as
    # quieter than it was, and a task that produces *less* pushes no alert while
    # a failing one does — so say so.
    if logger:
        logger.warning(
            f"ClickUp paging stopped at the {_MAX_PAGES}-page cap after "
            f"{len(tasks)} task(s); the day's closed Tasks may be incomplete")
    return tasks


def _ms_to_local_date(ms) -> str | None:
    """ClickUp timestamps are Unix milliseconds (sometimes a string, sometimes
    an int) and are UTC; the day we report is the local one."""
    if ms in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, ZoneInfo(local_timezone())).date().isoformat()
    except (ValueError, TypeError, OSError):
        return None


def _client(api_key: str | None):
    token = resolve_key("CLICKUP_API_TOKEN", api_key)
    if not token:
        return None, missing_key_error("CLICKUP_API_TOKEN")
    return token, None


def closed_tasks(day: date, api_key: str = None, logger=None) -> dict:
    """Every Task that reached a Done status on `day` (a LOCAL date), for
    scribejay/daily_commits.py.

    This is the record of work that leaves no commit behind. A Task in a code
    Space mostly duplicates git, but a contract advanced in another Space
    touches no repository at all, so without this those days read as empty
    ones.

    **Closed on `day` means `date_closed` falls on `day`, never
    `date_updated`.** Editing a Task months after shipping it bumps
    date_updated, which would report old work as today's.

    Rows carry the Space and the status name because both differ across
    Spaces and both are what makes a line readable. No description: nothing
    renders one.

    `logger` is optional so the settings screen's Test button can call this
    with nothing to log to; the scheduled job passes its own, which is where
    the page-cap warning has to land."""
    token, err = _client(api_key)
    if err:
        return err

    tz = ZoneInfo(local_timezone())
    start_ms = int(datetime.combine(day, datetime.min.time(),
                                    tzinfo=tz).timestamp() * 1000)
    try:
        team_id = _team_id(token)
        spaces = _spaces(token, team_id)
        if not spaces:
            return {"items": []}
        space_by_id = {a["id"]: a["name"] for a in spaces}
        tasks = _fetch_tasks(token, team_id, list(space_by_id), include_done=True,
                             updated_after_ms=start_ms, logger=logger)
    except _ClickUpError as e:
        return {"error": str(e)}
    except Exception as e:
        return http_error(e)

    wanted = day.isoformat()
    items = [{
        "title": task.get("name", "(no title)"),
        "space": space_by_id.get((task.get("space") or {}).get("id"), ""),
        "status": (task.get("status") or {}).get("status", ""),
    } for task in tasks
        if (task.get("status") or {}).get("type") == "closed"
        and _ms_to_local_date(task.get("date_closed")) == wanted]
    return {"items": items}
