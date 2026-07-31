import os
from datetime import datetime, timezone
from typing import Any

import httpx


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SupabaseStorage:
    def __init__(self) -> None:
        self.base_url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
        self.key = (
            os.getenv("SUPABASE_SECRET_KEY")
            or os.getenv("SUPABASE_SERVICE_KEY")
            or ""
        )
        if not self.key:
            raise RuntimeError(
                "Falta SUPABASE_SECRET_KEY (o SUPABASE_SERVICE_KEY para proyectos antiguos)."
            )

        self.headers = {
            "apikey": self.key,
            "Content-Type": "application/json",
        }
        # Las claves legacy service_role son JWT y sí pueden enviarse como Bearer.
        # Las claves modernas sb_secret_ deben enviarse solamente como apikey.
        if not self.key.startswith("sb_secret_"):
            self.headers["Authorization"] = f"Bearer {self.key}"

    def _request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        json: Any | None = None,
        prefer: str | None = None,
    ) -> Any:
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        response = httpx.request(
            method,
            f"{self.base_url}/{table}",
            headers=headers,
            params=params,
            json=json,
            timeout=20.0,
        )
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    def create_order(self, order: dict) -> dict:
        rows = self._request(
            "POST",
            "label_orders",
            json=order,
            prefer="return=representation",
        )
        return rows[0]

    def get_order(self, order_id: str) -> dict | None:
        rows = self._request(
            "GET",
            "label_orders",
            params={"id": f"eq.{order_id}", "limit": "1"},
        )
        return rows[0] if rows else None

    def list_user_orders(self, user_id: int, limit: int = 20) -> list[dict]:
        return self._request(
            "GET",
            "label_orders",
            params={
                "telegram_user_id": f"eq.{user_id}",
                "order": "created_at.desc",
                "limit": str(limit),
            },
        ) or []

    def list_orders(self, limit: int = 100) -> list[dict]:
        return self._request(
            "GET",
            "label_orders",
            params={"order": "created_at.desc", "limit": str(limit)},
        ) or []

    def update_order(self, order_id: str, changes: dict) -> dict | None:
        changes = dict(changes)
        changes["updated_at"] = now_iso()
        rows = self._request(
            "PATCH",
            "label_orders",
            params={"id": f"eq.{order_id}"},
            json=changes,
            prefer="return=representation",
        )
        return rows[0] if rows else None

    def create_ticket(self, ticket: dict) -> dict:
        rows = self._request(
            "POST",
            "label_tickets",
            json=ticket,
            prefer="return=representation",
        )
        return rows[0]

    def get_ticket(self, ticket_id: str) -> dict | None:
        rows = self._request(
            "GET",
            "label_tickets",
            params={"id": f"eq.{ticket_id}", "limit": "1"},
        )
        return rows[0] if rows else None

    def list_tickets(self, *, status: str | None = None, limit: int = 100) -> list[dict]:
        params = {"order": "created_at.desc", "limit": str(limit)}
        if status:
            params["status"] = f"eq.{status}"
        return self._request("GET", "label_tickets", params=params) or []

    def update_ticket(self, ticket_id: str, changes: dict) -> dict | None:
        changes = dict(changes)
        changes["updated_at"] = now_iso()
        rows = self._request(
            "PATCH",
            "label_tickets",
            params={"id": f"eq.{ticket_id}"},
            json=changes,
            prefer="return=representation",
        )
        return rows[0] if rows else None
