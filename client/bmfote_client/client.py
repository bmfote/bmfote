"""Client + Session — thin wrapper around the bmfote REST API.

Writes go through POST endpoints; reads go through GET endpoints.
Both fail silent with a logger warning on network errors.
"""

from __future__ import annotations

import logging
import os
import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

import httpx

logger = logging.getLogger("bmfote_client")

_CONTENT_CAP = 50_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_fts_markers(text: str) -> str:
    return (text or "").replace(">>>", "").replace("<<<", "")


class Client:
    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = 2.0,
    ):
        self.url = (url or os.environ.get("BMFOTE_URL", "")).rstrip("/")
        if not self.url:
            raise ValueError("bmfote url missing — pass url= or set BMFOTE_URL")
        self.token = token if token is not None else os.environ.get("BMFOTE_TOKEN", "")
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        self._http = httpx.Client(timeout=timeout, headers=headers)

    def session(self, project: str, session_id: Optional[str] = None) -> "Session":
        return Session(self, session_id or str(_uuid.uuid4()), project)

    def close(self) -> None:
        self._http.close()

    # --- internal HTTP helpers ---

    def _post(self, path: str, payload: dict) -> None:
        try:
            r = self._http.post(f"{self.url}{path}", json=payload)
            if r.status_code >= 400:
                logger.warning("bmfote %s returned %d: %s", path, r.status_code, r.text[:200])
        except Exception as e:
            logger.warning("bmfote %s failed: %s", path, e)

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        try:
            r = self._http.get(f"{self.url}{path}", params=params)
            if r.status_code >= 400:
                logger.warning("bmfote %s returned %d: %s", path, r.status_code, r.text[:200])
                return None
            return r.json()
        except Exception as e:
            logger.warning("bmfote %s failed: %s", path, e)
            return None

    # --- read endpoints (mirror engine/mcp_server.py tools) ---

    def search(
        self,
        query: str,
        limit: int = 10,
        type: Optional[str] = None,
    ) -> List[dict]:
        params: dict = {"q": query, "limit": limit}
        if type:
            params["type"] = type
        return self._get("/api/search", params) or []

    def find_error(self, error: str, limit: int = 5) -> List[dict]:
        return self._get("/api/similar-error", {"error": error, "limit": limit}) or []

    def recent(
        self,
        hours: int = 24,
        limit: int = 50,
        session_id: Optional[str] = None,
    ) -> List[dict]:
        params: dict = {"hours": hours, "limit": limit}
        if session_id:
            params["session_id"] = session_id
        return self._get("/api/recent", params) or []

    def get_message(self, uuid: str, context: int = 1) -> Optional[dict]:
        return self._get(f"/api/message/{uuid}", {"context": context})

class Session:
    def __init__(self, client: Client, session_id: str, project: str):
        self.client = client
        self.session_id = session_id
        self.project = project
        self._last_uuid: Optional[str] = None
        self._opened = False

    def _ensure_opened(self) -> None:
        if self._opened:
            return
        self.client._post(
            "/api/sessions",
            {
                "session_id": self.session_id,
                "project": self.project,
                "first_message_at": _now_iso(),
            },
        )
        self._opened = True

    def record_user(self, content: str, timestamp: Optional[str] = None) -> str:
        return self._record("user", content, timestamp)

    def record_assistant(
        self,
        content: str,
        model: Optional[str] = None,
        usage: Optional[dict] = None,
        timestamp: Optional[str] = None,
    ) -> str:
        return self._record("assistant", content, timestamp, model=model, usage=usage)

    def _record(
        self,
        type_: str,
        content: str,
        timestamp: Optional[str],
        model: Optional[str] = None,
        usage: Optional[dict] = None,
    ) -> str:
        self._ensure_opened()
        msg_uuid = str(_uuid.uuid4())
        payload: dict = {
            "session_id": self.session_id,
            "uuid": msg_uuid,
            "parent_uuid": self._last_uuid,
            "type": type_,
            "role": type_,
            "content": (content or "")[:_CONTENT_CAP],
            "timestamp": timestamp or _now_iso(),
        }
        if model:
            payload["model"] = model
        if usage:
            if usage.get("input_tokens") is not None:
                payload["input_tokens"] = usage["input_tokens"]
            if usage.get("output_tokens") is not None:
                payload["output_tokens"] = usage["output_tokens"]
        self.client._post("/api/messages", payload)
        self._last_uuid = msg_uuid
        return msg_uuid

    def close(self) -> None:
        self.client._post(
            "/api/sessions",
            {
                "session_id": self.session_id,
                "project": self.project,
                "last_message_at": _now_iso(),
            },
        )

    def recall(self, query: str, limit: int = 10) -> str:
        """Return a formatted prior-memory block ready to stuff into a system prompt.

        Calls Client.search and formats results as:
            Prior memory for "{query}" (N results):
            - [2026-04-08 user project=foo] snippet...
            - [2026-04-08 assistant project=foo] snippet...

        Snippets have FTS5 highlight markers (>>> <<<) stripped for clean reading.
        Returns an empty-state sentence if nothing matches.
        """
        results = self.client.search(query, limit=limit)
        if not results:
            return f'No prior memory for "{query}".'
        lines = [f'Prior memory for "{query}" ({len(results)} results):', ""]
        for r in results:
            snippet = _strip_fts_markers(r.get("snippet") or r.get("content") or "")
            ts = (r.get("timestamp") or "")[:10]
            proj = r.get("project") or "unknown"
            typ = r.get("type") or "msg"
            lines.append(f"- [{ts} {typ} project={proj}] {snippet}")
        return "\n".join(lines)
