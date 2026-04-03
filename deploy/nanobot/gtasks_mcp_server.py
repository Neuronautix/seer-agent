#!/usr/bin/env python3

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from google_chat_sync import post_chat_message
from gtasks_client import GoogleTasksClient

SERVER = FastMCP("google-tasks-tools")
CLIENT = GoogleTasksClient()


def _json_success(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _json_error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, separators=(",", ":"))


@SERVER.tool()
def list_tasks(
    limit: int = 20,
    include_completed: bool = False,
    tasklist: str = "@default",
) -> str:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if limit > 100:
        raise ValueError("limit must be <= 100")

    try:
        return _json_success(
            CLIENT.list_tasks(tasklist_id=tasklist.strip() or "@default", show_completed=include_completed, max_results=limit)
        )
    except Exception as exc:
        return _json_error(str(exc))


@SERVER.tool()
def create_task(
    title: str,
    due: str | None = None,
    notes: str | None = None,
    tasklist: str = "@default",
) -> str:
    try:
        result = CLIENT.create_task(
            title=title,
            due=due,
            notes=notes,
            tasklist_id=tasklist.strip() or "@default",
        )
    except Exception as exc:
        return _json_error(str(exc))

    task = result.get("task", {})
    chat_status = post_chat_message(
        f"Task created: {task.get('title', 'untitled')} ({task.get('id', 'no-id')})"
    )
    result["googleChat"] = chat_status
    return _json_success(result)


@SERVER.tool()
def complete_task(task_id: str, tasklist: str = "@default") -> str:
    try:
        result = CLIENT.complete_task(task_id=task_id, tasklist_id=tasklist.strip() or "@default")
    except Exception as exc:
        return _json_error(str(exc))

    task = result.get("task", {})
    chat_status = post_chat_message(
        f"Task completed: {task.get('title', 'untitled')} ({task.get('id', 'no-id')})"
    )
    result["googleChat"] = chat_status
    return _json_success(result)


if __name__ == "__main__":
    SERVER.run(transport="stdio")
