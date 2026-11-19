"""Owner-scoped inspection and explicit resolution of provisioning incidents."""
from __future__ import annotations

import json
from datetime import datetime, timezone


def _decode(raw) -> dict:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


async def _journal(ctx, shop_id: int, journal_id: int) -> dict:
    row = await ctx.db.fetchrow(
        """SELECT * FROM plugin_pershop_api_user_creates
           WHERE id=$1 AND shop_id=$2""", journal_id, shop_id)
    if not row:
        raise ValueError("recovery journal not found")
    return dict(row)


async def list_incidents(ctx, shop: dict) -> dict:
    """Public recovery facts only: no keys, SQL errors or internal payloads."""
    journals = await ctx.db.fetch(
        """SELECT id,provision_username,stage,created_at,updated_at
           FROM plugin_pershop_api_user_creates
           WHERE shop_id=$1 AND stage::jsonb->>'state'='reconciliation'
           ORDER BY created_at""", shop["id"])
    updates = await ctx.db.fetch(
        """SELECT id,user_uuid,username,panel_id,request,
                  previous_traffic_limit_bytes,attempted_traffic_limit_bytes,
                  quota_delta_bytes,compensation,reason,created_at
           FROM plugin_pershop_update_incidents
           WHERE shop_id=$1 AND state='reconciliation' ORDER BY created_at""",
        shop["id"])
    creations = []
    for row in journals or []:
        stage = _decode(row["stage"])
        result = stage.get("result") if isinstance(stage.get("result"), dict) else {}
        creations.append({
            "id": int(row["id"]), "kind": "create",
            "username": row["provision_username"], "uuid": result.get("uuid"),
            "reason": stage.get("reason"), "quota_users": stage.get("quota_users"),
            "quota_traffic": stage.get("quota_traffic"),
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        })
    return {"creations": creations,
            "updates": [dict(row, kind="update") for row in (updates or [])]}


async def resume_ownership(ctx, shop: dict, journal_id: int, actor: str) -> dict:
    """Retry only idempotent ownership stamping, unresolved quota untouched."""
    row = await _journal(ctx, shop["id"], journal_id)
    stage = _decode(row["stage"])
    if (stage.get("state") != "reconciliation"
            or stage.get("reason") != "ownership_stamping"):
        raise ValueError("journal is not awaiting ownership recovery")
    resumed = dict(stage, state="created", attempt=None, locked_at=None,
                   recovery_resumed_by=actor,
                   recovery_resumed_at=datetime.now(timezone.utc))
    raw = json.dumps(resumed, default=str)
    updated = await ctx.db.fetchrow(
        """UPDATE plugin_pershop_api_user_creates
           SET stage=$1,updated_at=NOW()
           WHERE id=$2 AND shop_id=$3 AND stage IS NOT DISTINCT FROM $4 RETURNING id""",
        raw, row["id"], shop["id"], row["stage"])
    if not updated:
        raise RuntimeError("recovery was changed by another worker")
    request = _decode(row["request"])
    from rwa_plugin_pershop.provisioning import create_external_user

    return await create_external_user(ctx, shop, request, row["idempotency_key"])


async def resolve_create_quota(ctx, shop: dict, journal_id: int, actor: str,
                               outcomes: dict) -> dict:
    """Record verified outcomes for charging markers; leave others unresolved.

    ``charged`` and ``not_needed`` are operator attestations after inspecting
    RWA. An unmarked following counter is intentionally charged later by the
    ordinary journal flow, exactly once.
    """
    row = await _journal(ctx, shop["id"], journal_id)
    stage = _decode(row["stage"])
    if (stage.get("state") != "reconciliation"
            or stage.get("reason") != "uncertain_quota_accounting"):
        raise ValueError("journal is not awaiting quota reconciliation")
    charged_markers = [name for name in ("quota_users", "quota_traffic")
                       if stage.get(name) == "charging"]
    if not charged_markers:
        raise ValueError("journal has no uncertain quota counter")
    for name in charged_markers:
        if outcomes.get(name) not in ("charged", "not_needed"):
            raise ValueError(f"verified outcome required for {name}")
    for name in ("quota_users", "quota_traffic"):
        if name in charged_markers:
            stage[name] = outcomes[name]
    resumed = dict(stage, state="quota", attempt=None, locked_at=None,
                   quota_resolution={
                       "by": actor, "at": datetime.now(timezone.utc),
                       "outcomes": {name: outcomes[name] for name in charged_markers}})
    raw = json.dumps(resumed, default=str)
    updated = await ctx.db.fetchrow(
        """UPDATE plugin_pershop_api_user_creates
           SET stage=$1,updated_at=NOW()
           WHERE id=$2 AND shop_id=$3 AND stage IS NOT DISTINCT FROM $4 RETURNING id""",
        raw, row["id"], shop["id"], row["stage"])
    if not updated:
        raise RuntimeError("recovery was changed by another worker")
    request = _decode(row["request"])
    from rwa_plugin_pershop.provisioning import create_external_user

    return await create_external_user(ctx, shop, request, row["idempotency_key"])


async def resolve_update_incident(ctx, shop: dict, incident_id: int, actor: str,
                                  note: str) -> dict:
    """Close a manually verified update incident without repeating mutations."""
    cleaned = (note or "").strip()[:500]
    row = await ctx.db.fetchrow(
        """UPDATE plugin_pershop_update_incidents
           SET state='resolved',resolved_at=NOW(),resolved_by=$1,resolution_note=$2
           WHERE id=$3 AND shop_id=$4 AND state='reconciliation'
           RETURNING id,user_uuid,username,state,resolved_at,resolved_by,resolution_note""",
        actor, cleaned, incident_id, shop["id"])
    if not row:
        raise ValueError("update incident not found or already resolved")
    return dict(row)
