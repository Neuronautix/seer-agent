#!/usr/bin/env python3

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCOPES = ["https://www.googleapis.com/auth/tasks"]
DEFAULT_TOKEN_PATH = Path(__file__).resolve().parent / "google-tasks-token.json"


def _parse_iso_datetime(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_due_datetime(value: str) -> str:
    dt = _parse_iso_datetime(value)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class GoogleTasksClient:
    def __init__(self, token_path: Path | None = None) -> None:
        self.token_path = token_path or DEFAULT_TOKEN_PATH

    def _load_token_info(self) -> dict[str, Any]:
        if not self.token_path.exists():
            raise RuntimeError(
                f"Google Tasks token not found at {self.token_path}. Run deploy/nanobot/gtasks_oauth.py first."
            )
        return json.loads(self.token_path.read_text(encoding="utf-8"))

    def _build_service(self):
        try:
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "Missing Google API dependencies. Install requirements.txt and retry."
            ) from exc

        creds = Credentials.from_authorized_user_info(self._load_token_info(), SCOPES)
        if not creds.valid:
            raise RuntimeError(
                "Google Tasks credentials are invalid or expired. Re-run deploy/nanobot/gtasks_oauth.py."
            )
        return build("tasks", "v1", credentials=creds, cache_discovery=False)

    def list_tasks(
        self,
        tasklist_id: str = "@default",
        show_completed: bool = False,
        max_results: int = 20,
    ) -> dict[str, Any]:
        service = self._build_service()
        response = (
            service.tasks()
            .list(
                tasklist=tasklist_id,
                showCompleted=show_completed,
                showHidden=False,
                maxResults=max_results,
            )
            .execute()
        )
        items = response.get("items", [])
        tasks: list[dict[str, Any]] = []
        for item in items:
            tasks.append(
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "status": item.get("status", "needsAction"),
                    "due": item.get("due"),
                    "updated": item.get("updated"),
                    "notes": item.get("notes", ""),
                }
            )
        return {
            "ok": True,
            "tasklistId": tasklist_id,
            "count": len(tasks),
            "tasks": tasks,
        }

    def create_task(
        self,
        title: str,
        due: str | None = None,
        notes: str | None = None,
        tasklist_id: str = "@default",
    ) -> dict[str, Any]:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("title must not be empty")

        body: dict[str, Any] = {"title": clean_title}
        if due:
            body["due"] = normalize_due_datetime(due)
        if notes:
            body["notes"] = notes.strip()

        service = self._build_service()
        created = service.tasks().insert(tasklist=tasklist_id, body=body).execute()
        return {
            "ok": True,
            "tasklistId": tasklist_id,
            "task": {
                "id": created.get("id"),
                "title": created.get("title"),
                "status": created.get("status", "needsAction"),
                "due": created.get("due"),
                "updated": created.get("updated"),
                "notes": created.get("notes", ""),
            },
        }

    def complete_task(self, task_id: str, tasklist_id: str = "@default") -> dict[str, Any]:
        clean_task_id = task_id.strip()
        if not clean_task_id:
            raise ValueError("task_id must not be empty")

        service = self._build_service()
        existing = service.tasks().get(tasklist=tasklist_id, task=clean_task_id).execute()
        existing["status"] = "completed"
        updated = service.tasks().update(tasklist=tasklist_id, task=clean_task_id, body=existing).execute()
        return {
            "ok": True,
            "tasklistId": tasklist_id,
            "task": {
                "id": updated.get("id"),
                "title": updated.get("title"),
                "status": updated.get("status", "completed"),
                "due": updated.get("due"),
                "updated": updated.get("updated"),
                "notes": updated.get("notes", ""),
            },
        }
