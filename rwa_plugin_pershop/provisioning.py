"""Durable recovery for the external ``POST /pub/users`` workflow.

The paid-order and trial journals establish the model used here: one immutable
request, compare-and-swap stage updates, a five-minute creating lease, raw panel
creation only on the first attempt, and exact-identity adoption afterwards.
RWA's quota counter has no idempotency key, so a counter marked ``charging`` is
never called again after an uncertain interruption; it needs reconciliation.
"""
from __future__ import annotations

import json
import uuid as uuid_module
from datetime import datetime, timezone

from rwa_plugin_pershop.billing import _db_transaction

_STALE_SECONDS = 300


def _decode(raw) -> dict:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _encode(value: dict) -> str:
    from rwa_plugin_pershop import service

    return json.dumps(service._clean_json(value))


def _request(row: dict) -> dict:
    value = _decode(row.get("request"))
    if not value:
        raise RuntimeError("provisioning request requires reconciliation")
    return value


async def _replace(ctx, journal_id: int, stage: dict, expected: str | None) -> str:
    raw = _encode(stage)
    updated = await ctx.db.fetchrow(
        """UPDATE plugin_pershop_api_user_creates
           SET stage=$1,updated_at=NOW()
           WHERE id=$2 AND stage IS NOT DISTINCT FROM $3 RETURNING id""",
        raw, journal_id, expected)
    if not updated:
        raise RuntimeError("provisioning was changed by another worker")
    return raw


async def _mark_reconciliation(ctx, row: dict, expected: str | None,
                               stage: dict, reason: str) -> None:
    failed = dict(stage, state="reconciliation", reason=reason,
                  attempt=None, locked_at=None)
    try:
        await _replace(ctx, row["id"], failed, expected)
    except Exception:
        # The already-durable ``creating``/``charging`` marker still fails
        # closed even when this explanatory update cannot be saved.
        pass


def _active(stage: dict) -> bool:
    if not stage.get("attempt"):
        return False
    try:
        locked = datetime.fromisoformat(str(stage.get("locked_at")))
        locked = locked if locked.tzinfo else locked.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - locked).total_seconds() < _STALE_SECONDS
    except (TypeError, ValueError):
        return True


async def _prepare(ctx, shop: dict, request: dict, key: str) -> dict:
    initial_request = _encode(request)
    initial_stage = _encode({"state": "preparing", "request": request})
    row = await ctx.db.fetchrow(
        """INSERT INTO plugin_pershop_api_user_creates(
               shop_id,idempotency_key,request,stage,provision_username)
           VALUES($1,$2,$3,$4,$5)
           ON CONFLICT(shop_id,idempotency_key) DO NOTHING RETURNING *""",
        shop["id"], key, initial_request, initial_stage, request["username"])
    if not row:
        row = await ctx.db.fetchrow(
            """SELECT * FROM plugin_pershop_api_user_creates
               WHERE shop_id=$1 AND idempotency_key=$2""", shop["id"], key)
    if not row:
        raise RuntimeError("provisioning reservation unavailable")
    row = dict(row)
    if _request(row) != request:
        raise ValueError("idempotency key was already used for another request")
    return row


async def _begin_panel_attempt(ctx, row: dict) -> tuple[dict, str, bool]:
    expected = row["stage"]
    old = _decode(expected)
    state = old.get("state")
    request = _request(row)
    allow_create = state == "preparing"
    if state == "creating":
        if _active(old):
            raise ValueError("provisioning is already in progress")
        allow_create = False
    elif state != "preparing":
        raise RuntimeError("provisioning requires reconciliation")
    stage = {"state": "creating", "request": request,
             "attempt": uuid_module.uuid4().hex,
             "locked_at": datetime.now(timezone.utc)}
    raw = await _replace(ctx, row["id"], stage, expected)
    return stage, raw, allow_create


def _matches(row: dict, request: dict) -> bool:
    username = row.get("username")
    if (not isinstance(username, str)
            or username.casefold() != str(request["username"]).casefold()
            or row.get("description") != request.get("description")):
        return False
    try:
        expected_tg = request.get("telegram_id")
        actual_tg = row.get("telegram_id", row.get("telegramId"))
        if expected_tg is None:
            if actual_tg is not None:
                return False
        elif int(actual_tg) != int(expected_tg):
            return False
    except (TypeError, ValueError):
        return False
    return bool(row.get("uuid", row.get("id")))


def _result(row: dict, request: dict) -> dict:
    return {
        "uuid": str(row.get("uuid") or row.get("id")),
        "panel_id": row.get("panel_id", row.get("id")),
        "username": request["username"],
        "subscription_url": row.get("subscription_url", row.get("subscriptionUrl")),
        "expire_at": request["expire_at"],
    }


async def _materialize(ctx, row: dict, request: dict, result: dict) -> dict:
    payload = {
        "uuid": result.get("uuid"), "id": result.get("panel_id"),
        "username": request["username"],
        "subscriptionUrl": result.get("subscription_url"),
        "expireAt": request["expire_at"],
        "trafficLimitBytes": request.get("traffic_limit_bytes"),
        "telegramId": request.get("telegram_id"),
        "description": request["description"],
    }
    try:
        mirror_uuid = await ctx.db.upsert_user(payload)
        user_uuid = str(mirror_uuid or result.get("uuid") or "")
        stored = await ctx.db.fetchrow("SELECT * FROM users WHERE uuid=$1", user_uuid)
    except Exception as exc:
        raise RuntimeError("provisioning local sync requires reconciliation") from exc
    stored = dict(stored) if stored else {}
    if not stored or not _matches(stored, request):
        raise RuntimeError("provisioning identity requires reconciliation")
    if result.get("uuid") and str(stored.get("uuid")) != str(result["uuid"]):
        raise RuntimeError("provisioning identity requires reconciliation")
    return _result(stored, request)


async def _panel_create(ctx, request: dict) -> dict:
    from rwa_plugin_pershop import service

    client = service._panel_client()
    kwargs = service._create_kwargs(
        username=request["username"],
        expire_at=datetime.fromisoformat(request["expire_at"]),
        traffic_limit_bytes=request.get("traffic_limit_bytes"),
        telegram_id=request.get("telegram_id"),
        squad_uuid=request.get("squad_uuid"),
        hwid_device_limit=request.get("hwid_device_limit"),
        description=request["description"])
    try:
        created = service._unwrap(await client.create_user(**kwargs))
    except Exception as exc:
        raise RuntimeError("panel user creation failed") from exc
    if not isinstance(created, dict):
        raise RuntimeError("panel user-create returned an invalid response")
    user_uuid = created.get("uuid")
    panel_id = created.get("id")
    if not user_uuid and panel_id is None:
        raise RuntimeError("panel user-create returned an invalid response")
    return {
        "uuid": str(user_uuid) if user_uuid else None,
        "panel_id": panel_id,
        "username": request["username"],
        "subscription_url": (created.get("subscription_url")
                             or created.get("subscriptionUrl")),
        "expire_at": request["expire_at"],
    }


async def _run_panel(ctx, row: dict, stage: dict, expected: str,
                     *, allow_create: bool) -> tuple[dict, str]:
    request = _request(row)
    try:
        stored = await ctx.db.fetchrow(
            "SELECT * FROM users WHERE lower(username)=lower($1)",
            request["username"])
        if stored:
            stored = dict(stored)
            if not _matches(stored, request):
                raise RuntimeError("provisioning identity requires reconciliation")
            result = _result(stored, request)
        elif allow_create:
            result = await _panel_create(ctx, request)
        else:
            raise RuntimeError("provisioning outcome requires reconciliation")
        result = await _materialize(ctx, row, request, result)
        saved = {"state": "created", "request": request, "result": result}
        return result, await _replace(ctx, row["id"], saved, expected)
    except Exception as exc:
        released = dict(stage, attempt=None, locked_at=None)
        try:
            await _replace(ctx, row["id"], released, expected)
        except Exception:
            pass
        if isinstance(exc, (ValueError, RuntimeError)):
            raise
        raise RuntimeError("provisioning requires reconciliation") from exc


async def _stamp(ctx, row: dict):  # -> (request, result, raw_stage)
    request = _request(row)
    stage = _decode(row["stage"])
    result = dict(stage.get("result") or {})
    expected = row["stage"]
    owner_id = request.get("owner_id")
    try:
        if owner_id is not None:
            stamped = await ctx.db.fetchrow(
                """UPDATE users SET created_by_admin_id=$1
                   WHERE uuid=$2 AND (created_by_admin_id IS NULL
                                      OR created_by_admin_id=$1)
                   RETURNING uuid,username,description,telegram_id,created_by_admin_id""",
                int(owner_id), result["uuid"])
            if (not stamped
                    or int(stamped.get("created_by_admin_id")) != int(owner_id)
                    or not _matches(stamped, request)):
                raise RuntimeError("provisioning ownership requires reconciliation")
        next_stage = dict(stage, state="stamped", attempt=None, locked_at=None)
        raw = await _replace(ctx, row["id"], next_stage, expected)
        return request, result, raw
    except Exception as exc:
        await _mark_reconciliation(
            ctx, row, expected, stage, "ownership_stamping")
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError("provisioning ownership requires reconciliation") from exc


async def _charge_one(ctx, row: dict, request: dict, stage: dict,
                      counter: str, amount: int | None):
    field = "users" if counter == "users_created" else "traffic"
    marker = f"quota_{field}"
    status = stage.get(marker)
    if status == "charged" or status == "not_needed":
        return row["stage"]
    expected = row["stage"]
    if status == "charging":
        await _mark_reconciliation(
            ctx, row, expected, stage, "uncertain_quota_accounting")
        raise RuntimeError("provisioning quota requires reconciliation")
    if not amount:
        advanced = dict(stage, **{marker: "not_needed"})
        return await _replace(ctx, row["id"], advanced, expected)
    charging = dict(stage, state="quota", **{marker: "charging"})
    expected = await _replace(ctx, row["id"], charging, expected)
    try:
        from web.backend.core.admin_accounts import increment_usage_counter
        charged = bool(await increment_usage_counter(
            int(request["owner_id"]), counter, *((amount,) if counter != "users_created" else ())))
    except Exception as exc:
        await _mark_reconciliation(
            ctx, row, expected, charging, "uncertain_quota_accounting")
        raise RuntimeError("provisioning quota requires reconciliation") from exc
    if not charged:
        await _mark_reconciliation(
            ctx, row, expected, charging, "quota_refused_after_create")
        raise RuntimeError("provisioning quota requires reconciliation")
    done = dict(charging, **{marker: "charged"})
    # If this CAS fails, the durable charging marker prevents a blind second
    # call on the next request.
    return await _replace(ctx, row["id"], done, expected)


async def _account_quota(ctx, row: dict, request: dict, result: dict,
                         stage: dict, expected: str):
    current = dict(row, stage=expected)
    if request.get("owner_id") is None:
        ready = dict(stage, state="ready", quota_users="not_needed",
                     quota_traffic="not_needed")
        return await _replace(ctx, row["id"], ready, expected)
    raw = await _charge_one(ctx, current, request, stage, "users_created", 1)
    stage = _decode(raw)
    current = dict(row, stage=raw)
    raw = await _charge_one(ctx, current, request, stage, "traffic_used_bytes",
                            request.get("traffic_limit_bytes"))
    stage = _decode(raw)
    current = dict(row, stage=raw)
    ready = dict(stage, state="ready")
    return await _replace(ctx, current["id"], ready, raw)


def _same_sub(row: dict, shop_id: int, request: dict, result: dict) -> bool:
    try:
        actual_tg = row.get("buyer_tg_id")
        expected_tg = request.get("telegram_id")
        tg_matches = (actual_tg is None if expected_tg is None
                      else actual_tg is not None and int(actual_tg) == int(expected_tg))
        return (int(row["shop_id"]) == int(shop_id)
                and str(row["user_uuid"]) == str(result["uuid"])
                and str(row["username"]).casefold() == request["username"].casefold()
                and tg_matches and row.get("package_id") is None
                and not row.get("is_test") and not row.get("is_linked")
                and not row.get("cancelled"))
    except (KeyError, TypeError, ValueError):
        return False


async def _complete(ctx, row: dict, request: dict, result: dict,
                    expected: str) -> dict:
    stage = _decode(expected)
    response = {"uuid": result["uuid"], "username": request["username"],
                "subscription_url": result.get("subscription_url")}
    try:
        async with _db_transaction(ctx) as db:
            inserted = await db.fetchrow(
                """INSERT INTO plugin_pershop_subs(
                       shop_id,buyer_tg_id,user_uuid,username,expire_at,
                       traffic_limit_bytes,package_id)
                   VALUES($1,$2,$3,$4,$5,$6,NULL)
                   ON CONFLICT(username) DO NOTHING RETURNING id""",
                row["shop_id"], request.get("telegram_id"), result["uuid"],
                request["username"], datetime.fromisoformat(request["expire_at"]),
                request.get("traffic_limit_bytes"))
            sub = await db.fetchrow(
                "SELECT * FROM plugin_pershop_subs WHERE username=$1",
                request["username"])
            if not sub or not _same_sub(dict(sub), row["shop_id"], request, result):
                raise RuntimeError("subscription conflicts with durable request")
            sub_id = int(sub["id"])
            response["sub_id"] = sub_id
            final = dict(stage, state="completed", request=request,
                         result=result, response=response)
            raw = _encode(final)
            updated = await db.fetchrow(
                """UPDATE plugin_pershop_api_user_creates
                   SET stage=$1,updated_at=NOW()
                   WHERE id=$2 AND stage IS NOT DISTINCT FROM $3 RETURNING id""",
                raw, row["id"], expected)
            if not updated:
                raise RuntimeError("provisioning was changed by another worker")
        return response
    except Exception as exc:
        # The ready stage already proves ownership and quota. Keep it so a
        # lost commit can retry only subscription insertion + response save.
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError("provisioning finalization requires reconciliation") from exc


async def create_external_user(ctx, shop: dict, request: dict,
                               idempotency_key: str) -> dict:
    """Reserve, provision and return one exact external user create request."""
    row = await _prepare(ctx, shop, request, idempotency_key)
    while True:
        stage = _decode(row["stage"])
        state = stage.get("state")
        if state == "completed":
            response = stage.get("response")
            if not isinstance(response, dict):
                raise RuntimeError("provisioning response requires reconciliation")
            return dict(response)
        if state == "reconciliation":
            raise RuntimeError("provisioning requires reconciliation")
        if state in ("preparing", "creating"):
            creating, expected, allow_create = await _begin_panel_attempt(ctx, row)
            result, raw = await _run_panel(
                ctx, row, creating, expected, allow_create=allow_create)
        elif state == "created":
            result = dict(stage.get("result") or {})
            raw = row["stage"]
        else:
            result = dict(stage.get("result") or {})
            raw = row["stage"]
        if not result.get("uuid"):
            raise RuntimeError("provisioning identity requires reconciliation")
        row = dict(row, stage=raw)
        if state in ("preparing", "creating", "created"):
            request, result, raw = await _stamp(ctx, row)
            row = dict(row, stage=raw)
            stage = _decode(raw)
        request = _request(row)
        if stage.get("state") == "stamped":
            raw = await _account_quota(ctx, row, request, result, stage, raw)
            row = dict(row, stage=raw)
            stage = _decode(raw)
        if stage.get("state") == "quota":
            raw = await _account_quota(ctx, row, request, result, stage, raw)
            row = dict(row, stage=raw)
            stage = _decode(raw)
        if stage.get("state") == "ready":
            return await _complete(ctx, row, request, result, raw)
        raise RuntimeError("provisioning requires reconciliation")
