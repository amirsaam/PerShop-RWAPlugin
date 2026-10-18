"""Admin-DB + panel_api access ONLY. No Remnawave-core HTTP here."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from rwa_plugin_pershop.naming import allocate_username


async def get_last_mid(ctx, shop_id: int, chat_id: int) -> int | None:
    """Last bot message id we show in this chat (delete-before-send cleanup). Never raises."""
    try:
        mid = await ctx.db.fetchval(
            "SELECT last_mid FROM plugin_pershop_buyers WHERE shop_id=$1 AND tg_id=$2",
            shop_id, int(chat_id))
        return int(mid) if mid else None
    except Exception:
        return None


async def set_last_mid(ctx, shop_id: int, chat_id: int, mid: int | None) -> None:
    """Remember the message id now showing our screen. Never raises."""
    if mid is None:
        return
    try:
        await ctx.db.execute(
            "INSERT INTO plugin_pershop_buyers (shop_id, tg_id, last_mid) VALUES ($1,$2,$3) "
            "ON CONFLICT (shop_id, tg_id) DO UPDATE SET last_mid=$3",
            shop_id, int(chat_id), int(mid))
    except Exception:
        pass


async def list_squads() -> list[dict[str, Any]]:
    """Internal squads via api_client.get_internal_squads().

    Response envelope mirrors the panel's own route:
    {"response": {"internalSquads": [...]}}. Never raises: [] on any
    failure, and callers fall back to manual uuid entry.
    """
    try:
        result = _unwrap(await _panel_client().get_internal_squads())
    except Exception:
        return []
    items = result.get("internalSquads", []) if isinstance(result, dict) else result
    if not isinstance(items, (list, tuple)):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        uuid = item.get("uuid")
        if not uuid:
            continue
        out.append({"uuid": str(uuid), "name": str(item.get("name") or uuid)})
    return out


async def shop_for_owner(ctx, owner: str) -> dict | None:
    return await ctx.db.fetchrow(
        "SELECT * FROM plugin_pershop_shops WHERE owner_admin = $1", owner
    )


async def shop_by_id(ctx, shop_id: int) -> dict | None:
    return await ctx.db.fetchrow("SELECT * FROM plugin_pershop_shops WHERE id = $1", shop_id)


async def shop_for_tg(ctx, shop: dict, tg_id: int | None) -> dict:
    """Shop copy speaking one buyer's own language (My account chooser).
    Used for every buyer-directed send outside the webhook path (reminders,
    admin-panel DMs, withdraw settlements). Never raises."""
    if not tg_id:
        return shop
    try:
        row = await ctx.db.fetchrow(
            "SELECT lang FROM plugin_pershop_buyers WHERE shop_id=$1 AND tg_id=$2",
            shop["id"], tg_id)
    except Exception:
        row = None
    if row and row.get("lang"):
        shop = dict(shop)
        shop["_buyer_lang"] = row["lang"]
    return shop


async def panel_base_url(ctx) -> str:
    """Superadmin-configured public origin of the panel, e.g. https://panel.example.com."""
    base = await ctx.settings.get("public_base_url", default="")
    return (base or "").strip().rstrip("/")


async def register_webhook(ctx, shop: dict) -> dict:
    """Point the shop's own bot at this panel via setWebhook.

    The owner only pastes the BotFather token; the plugin registers
    {base}/api/v2/plugins/pershop/tg/{shop_id} with the shop's secret
    itself. Returns {ok, url} or {ok False, error}. Never raises.
    """
    from rwa_plugin_pershop.crypto import decrypt_token
    from rwa_plugin_pershop.telegram import TgClient

    base = await panel_base_url(ctx)
    if not base:
        return {"ok": False, "error": "panel public URL not set yet (Plugin Settings)"}
    if not base.startswith("https://"):
        return {"ok": False, "error": "public URL must start with https:// (Telegram requirement)"}
    try:
        token = await decrypt_token(ctx, shop["bot_token_enc"])
    except Exception as e:
        return {"ok": False, "error": f"cannot read bot token: {e}"}
    url = f"{base}/api/v2/plugins/pershop/tg/{shop['id']}"
    try:
        data = await TgClient(token).call("setWebhook", {
            "url": url,
            "secret_token": shop.get("webhook_secret") or "",
            "drop_pending_updates": True,
        })
    except Exception as e:
        return {"ok": False, "error": f"Telegram unreachable: {e}"}
    if not data.get("ok"):
        return {"ok": False, "error": str(data.get("description") or data)[:200]}
    try:
        me = await TgClient(token).call("getMe", {})
        uname = ((me.get("result") or {}).get("username") or "").strip().lstrip("@")
        if uname:
            await ctx.db.execute(
                "UPDATE plugin_pershop_shops SET bot_username=$1 WHERE id=$2",
                uname, shop["id"])
    except Exception:
        pass
    return {"ok": True, "url": url}


async def webhook_status(ctx, shop: dict) -> dict:
    """Live setWebhook state via getWebhookInfo. Never raises."""
    from rwa_plugin_pershop.crypto import decrypt_token
    from rwa_plugin_pershop.telegram import TgClient

    try:
        token = await decrypt_token(ctx, shop["bot_token_enc"])
        data = await TgClient(token).call("getWebhookInfo", {})
        info = data.get("result", {}) if data.get("ok") else {}
        try:
            me = await TgClient(token).call("getMe", {})
            uname = ((me.get("result") or {}).get("username") or "").strip().lstrip("@")
            if uname:
                await ctx.db.execute(
                    "UPDATE plugin_pershop_shops SET bot_username=$1 WHERE id=$2",
                    uname, shop["id"])
        except Exception:
            pass
        return {"ok": True, "url": info.get("url") or "",
                "pending": info.get("pending_update_count", 0),
                "last_error": info.get("last_error_message") or ""}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


async def ensure_bot_username(ctx, shop: dict) -> str:
    """Return the shop bot's @username without "@", fetching via getMe and
    caching it on first success when the stored value is empty (e.g. the
    shop was connected while the panel had no Telegram egress, so the
    connect-time getMe failed). Best-effort: "" when unreachable."""
    uname = (shop.get("bot_username") or "").strip().lstrip("@")
    if uname:
        return uname
    try:
        from rwa_plugin_pershop.crypto import decrypt_token
        from rwa_plugin_pershop.telegram import TgClient
        token = await decrypt_token(ctx, shop.get("bot_token_enc") or "")
        if not token:
            return ""
        me = await TgClient(token).call("getMe", {})
        uname = ((me.get("result") or {}).get("username") or "").strip().lstrip("@")
        if uname:
            await ctx.db.execute(
                "UPDATE plugin_pershop_shops SET bot_username=$1 WHERE id=$2",
                uname, shop["id"])
            try:
                shop["bot_username"] = uname
            except Exception:
                pass
            return uname
    except Exception:
        pass
    return ""


async def disconnect_shop(ctx, owner_admin: str) -> dict:
    """Detach the shop's bot: deleteWebhook on Telegram's side (best
    effort), then clear the stored token. All other shop settings stay
    untouched, so the shop can be re-connected later with a new token.
    Never raises; returns {ok, disconnected}."""
    from rwa_plugin_pershop.telegram import TgClient

    shop = await shop_for_owner(ctx, owner_admin)
    if not shop:
        return {"ok": False, "disconnected": False, "error": "no shop yet"}
    had = bool(shop.get("bot_token_enc"))
    if had:
        try:
            from rwa_plugin_pershop.crypto import decrypt_token

            token = await decrypt_token(ctx, shop["bot_token_enc"])
            await TgClient(token).call("deleteWebhook", {"drop_pending_updates": True})
        except Exception:
            pass
        try:
            await ctx.db.execute(
                "UPDATE plugin_pershop_shops SET bot_token_enc=NULL WHERE id=$1", shop["id"])
        except Exception as e:
            return {"ok": False, "disconnected": False, "error": str(e)[:200]}
    return {"ok": True, "disconnected": had}


_PERSHOP_TABLES = [
    "plugin_pershop_events",
    "plugin_pershop_broadcasts",
    "plugin_pershop_referrals",
    "plugin_pershop_test_claims",
    "plugin_pershop_scammers",
    "plugin_pershop_promo_uses",
    "plugin_pershop_promos",
    "plugin_pershop_buyers",
    "plugin_pershop_sessions",
    "plugin_pershop_orders",
    "plugin_pershop_subs",
    "plugin_pershop_packages",
    "plugin_pershop_services",
    "plugin_pershop_tiers",
    "plugin_pershop_service_cats",
    "plugin_pershop_shops",
]

_PERSHOP_SETTING_KEYS = [
    "bot_key",
    "polling_enabled",
    "public_base_url",
    "tutorial_text",
    "tutorial_buttons",
    "test_duration_days",
    "test_traffic_gb",
    "test_squad_uuid",
    "test_hwid",
]


async def reset_all(ctx) -> dict:
    """Superadmin wipe: DROP every PerShop table (schema + rows), clear our
    alembic version rows so migrations re-run clean on next restart, and
    delete every PerShop plugin setting (tokens, offsets, flags). The panel
    itself is untouched. Never raises; returns {ok, dropped, wiped}."""
    try:
        shops = await ctx.db.fetch("SELECT id FROM plugin_pershop_shops")
    except Exception:
        shops = []
    dropped, wiped = [], []
    try:
        await ctx.db.execute(
            "DROP TABLE IF EXISTS " + ", ".join(_PERSHOP_TABLES) + " CASCADE")
        dropped = list(_PERSHOP_TABLES)
    except Exception:
        for t in _PERSHOP_TABLES:
            try:
                await ctx.db.execute(f"DELETE FROM {t}")
                wiped.append(t)
            except Exception:
                pass
    try:
        await ctx.db.execute(
            "DELETE FROM alembic_version WHERE version_num = 'pershop_001'")
    except Exception:
        pass
    try:
        await ctx.db.execute(
            "DELETE FROM alembic_version WHERE version_num = 'pershop_002'")
    except Exception:
        pass
    try:
        await ctx.db.execute(
            "DELETE FROM alembic_version WHERE version_num = 'pershop_003'")
    except Exception:
        pass
    keys = list(_PERSHOP_SETTING_KEYS)
    for s in shops or []:
        try:
            keys.append(f"tg_offset:{s['id']}")
        except Exception:
            pass
    for k in keys:
        try:
            await ctx.settings.delete(k)
        except Exception:
            pass
    return {"ok": True, "dropped": dropped, "wiped": wiped}


async def existing_usernames(ctx, shop_id: int) -> set[str]:
    names: set[str] = set()
    try:
        rows = await ctx.db.fetch("SELECT username FROM plugin_pershop_subs WHERE shop_id = $1", shop_id)
        names.update(r["username"] for r in rows if r["username"])
    except Exception:
        pass
    try:
        rows = await ctx.db.fetch("SELECT username FROM users")
        names.update(r["username"] for r in rows if r and r["username"])
    except Exception:
        pass
    return names


def _panel_client():
    from web.backend.core.plugin_api import panel_api

    return panel_api()


def _create_kwargs(*, username: str, expire_at: datetime,
                   traffic_limit_bytes: int | None, telegram_id: int | None,
                   squad_uuid: str | None, hwid_device_limit: int | None = None,
                   description: str | None = None) -> dict[str, Any]:
    """Exact kwargs for api_client.create_user — the panel validates strictly,
    so no invented keys (no squad_uuid/strategy/created_by)."""
    kw: dict[str, Any] = {
        "username": username,
        "expire_at": expire_at.isoformat(),
        "status": "ACTIVE",
        "traffic_limit_strategy": "MONTH",
    }
    if traffic_limit_bytes:
        kw["traffic_limit_bytes"] = traffic_limit_bytes
    if hwid_device_limit:
        kw["hwid_device_limit"] = hwid_device_limit
    if telegram_id is not None:
        kw["telegram_id"] = telegram_id
    if description:
        kw["description"] = description
    if squad_uuid:
        kw["active_internal_squads"] = [squad_uuid]
    return kw


def _unwrap(result: Any) -> Any:
    """Panel API envelope: {"response": ...} or the payload itself."""
    if isinstance(result, dict):
        return result.get("response", result)
    return result


async def _owner_account_id(ctx, owner_admin: str | None) -> int | None:
    """Panel admin-account id for a shop owner (owns users + quota counters)."""
    try:
        from web.backend.core.admin_accounts import get_admin_account_by_username
        acct = await get_admin_account_by_username(owner_admin or "")
    except Exception:
        return None
    if not acct or acct.get("id") is None:
        return None
    return int(acct["id"])


async def _owner_policy(owner_admin: str | None) -> tuple[Any | None, str]:
    """(role_name, unlimited_traffic_policy) for the owner; default = permissive."""
    try:
        from web.backend.core.admin_accounts import get_admin_account_by_username
        acct = await get_admin_account_by_username(owner_admin or "")
    except Exception:
        return None, "allowed"
    if not acct:
        return None, "allowed"
    return acct.get("role_name"), acct.get("unlimited_traffic_policy", "allowed")


async def panel_create_user(ctx, *, username: str, expire_at: datetime,
                            traffic_limit_bytes: int | None, telegram_id: int | None,
                            squad_uuid: str | None = None,
                            hwid_device_limit: int | None = None,
                            owner_admin: str | None = None,
                            description: str | None = None) -> dict:
    """Create via panel_api, then mirror the panel's own create route: persist
    the row locally, stamp users.created_by_admin_id so the SHOP OWNER sees the
    account in their users list, and charge their quota counters (rollback the
    core account when the quota gate shuts). Returns
    {'uuid','subscription_url','username'}.
    """
    client = _panel_client()
    account_id = await _owner_account_id(ctx, owner_admin)
    if account_id is not None:
        role_name, policy = await _owner_policy(owner_admin)
        if role_name != "superadmin":
            if policy == "enforced":
                traffic_limit_bytes = None
            elif policy == "disabled" and not traffic_limit_bytes:
                raise RuntimeError("owner traffic policy requires a limit")
    kwargs = _create_kwargs(username=username, expire_at=expire_at,
                            traffic_limit_bytes=traffic_limit_bytes,
                            telegram_id=telegram_id, squad_uuid=squad_uuid,
                            hwid_device_limit=hwid_device_limit,
                            description=description)
    user = _unwrap(await client.create_user(**kwargs))
    if not isinstance(user, dict):
        raise RuntimeError(f"panel_api user-create returned {type(user)}")

    user_uuid = user.get("uuid") or ""
    panel_id = user.get("id")
    sub_url = user.get("subscription_url") or user.get("subscriptionUrl")

    try:
        await ctx.db.upsert_user(user)
        if not user_uuid and panel_id is not None:
            user_uuid = await ctx.db.get_user_uuid_by_panel_id(int(panel_id)) or ""
        if not sub_url and user_uuid:
            row = await ctx.db.get_user_by_uuid(user_uuid)
            if row:
                sub_url = row.get("subscription_url") or row.get("subscriptionUrl")
    except Exception as e:
        ctx.logger.error("shop local user sync failed for %s: %s", username, e)
    if account_id is not None:
        try:
            if user_uuid:
                await ctx.db.execute(
                    "UPDATE users SET created_by_admin_id = $1 WHERE uuid = $2",
                    account_id, user_uuid,
                )
            ok = True
            try:
                from web.backend.core.admin_accounts import increment_usage_counter
                ok = await increment_usage_counter(account_id, "users_created")
                if ok and traffic_limit_bytes:
                    ok = await increment_usage_counter(
                        account_id, "traffic_used_bytes", traffic_limit_bytes)
            except ImportError:
                pass
            if not ok:
                try:
                    await client.delete_user(panel_id)
                except Exception:
                    pass
                raise RuntimeError("owner quota exceeded, core account rolled back")
        except RuntimeError:
            raise
        except Exception as e:
            ctx.logger.error("shop ownership stamping failed for %s: %s", username, e)
    try:
        from web.backend.core.webhook_security import fire_event
        fire_event("user.created", {
            "uuid": str(user_uuid), "username": username, "email": None,
            "telegram_id": telegram_id, "expire_at": expire_at.isoformat(),
            "created_by": owner_admin,
        })
    except Exception:
        pass
    return {"uuid": user_uuid or panel_id or username,
            "subscription_url": sub_url, "username": username}


def _fire_updated(ctx, user_uuid: str, username: str | None,
                  changed_fields: list, updated_by: str | None) -> None:
    """Mirror the panel update route's user.updated event (same keys:
    uuid, username, changed_fields, updated_by). Best-effort: never raises."""
    try:
        from web.backend.core.webhook_security import fire_event
        fire_event("user.updated", {
            "uuid": str(user_uuid), "username": username,
            "changed_fields": sorted(changed_fields),
            "updated_by": updated_by,
        })
    except Exception:
        pass


async def panel_extend_user(ctx, user_uuid: str, extra_days: int,
                            traffic_limit_bytes: int | None) -> datetime:
    """Extend expiry via the core extend action (the panel computes the base:
    expired counts from today, active adds on). Syncs a changed traffic limit
    via update_user. Falls back to an absolute-expiry update on older cores.
    Returns the new expiry. Never writes panel tables directly.
    """
    try:
        from shared.data_access import resolve_panel_user_id
        panel_id = await resolve_panel_user_id(user_uuid)
    except Exception:
        panel_id = user_uuid
    client = _panel_client()
    try:
        await client.extend_user_expiration(panel_id, extra_days)
    except Exception:
        row = None
        try:
            row = await ctx.db.fetchrow("SELECT expire_at FROM users WHERE uuid = $1", user_uuid)
        except Exception:
            pass
        base = row["expire_at"] if row and row["expire_at"] else datetime.now(timezone.utc)
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        new_exp = base + timedelta(days=extra_days)
        await client.update_user(panel_id, expireAt=new_exp.isoformat())
    if traffic_limit_bytes is not None:
        try:
            await client.update_user(panel_id, trafficLimitBytes=traffic_limit_bytes)
        except Exception as e:
            ctx.logger.error("shop traffic sync failed for %s: %s", user_uuid, e)
    row = None
    try:
        row = await ctx.db.fetchrow("SELECT expire_at FROM users WHERE uuid = $1", user_uuid)
    except Exception:
        pass
    if row and row["expire_at"]:
        exp = row["expire_at"]
        return exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) + timedelta(days=extra_days)


async def _panel_set_traffic(ctx, user_uuid: str, traffic_limit_bytes: int | None) -> None:
    """Traffic-only write for top-ups: no expiry touch. Best-effort."""
    if traffic_limit_bytes is None:
        return
    try:
        from shared.data_access import resolve_panel_user_id
        panel_id = await resolve_panel_user_id(user_uuid)
    except Exception:
        panel_id = user_uuid
    try:
        await _panel_client().update_user(panel_id, trafficLimitBytes=traffic_limit_bytes)
    except Exception as e:
        ctx.logger.error("shop traffic top-up failed for %s: %s", user_uuid, e)


async def pending_for_sub(ctx, shop_id: int, username: str):
    """Newest open order against this sub (receipt sent, awaiting admin).
    Pay buttons hide while this exists. withdraw_requested counts as open:
    the money question is unsettled until the owner confirms or rejects."""
    try:
        return await ctx.db.fetchrow(
            """SELECT id FROM plugin_pershop_orders WHERE shop_id=$1
               AND renew_username=$2 AND status IN ('pending','withdraw_requested')
               ORDER BY id DESC LIMIT 1""", shop_id, username)
    except Exception:
        return None


def _extract_token(text: str) -> str:
    """Short-uuid out of a pasted subscription link (or a bare token)."""
    t = (text or "").strip()
    if not t:
        return ""
    if "/" in t:
        t = t.rstrip("/").split("/")[-1]
    t = t.split("?")[0].split("#")[0].strip().split()[0] if t.strip() else ""
    return t


async def _owner_account_row(ctx, owner_admin: str | None) -> tuple[bool, Any | None]:
    """(lookup_ok, row). lookup_ok=False means the lookup itself crashed;
    (True, None) means no account row — legacy/env superadmin, whom the
    panel's own RBAC treats as unrestricted (account_id None = full access)."""
    try:
        from web.backend.core.admin_accounts import get_admin_account_by_username
        return True, await get_admin_account_by_username(owner_admin or "")
    except Exception:
        return False, None


async def _link_allowed(ctx, shop: dict, row: dict) -> bool:
    """Attribution only, no squad check:
    - createdByAdminId == 'superadmin' -> link (any owner);
    - createdByAdminId empty + superadmin-owner (role_name='superadmin') -> link;
    - anything else (numeric admin id different from owner, non-superadmin owner,
      unknown owner, crashed lookup) -> foreign."""
    creator_raw = row.get("createdByAdminId", row.get("created_by_admin_id"))
    # Creator may be numeric admin id, or the literal "superadmin" string.
    creator_str = str(creator_raw).strip() if creator_raw is not None else None
    try:
        creator_num = int(creator_raw) if creator_str else None
    except (TypeError, ValueError):
        creator_num = None
    owner_id = await _owner_account_id(ctx, shop.get("owner_admin"))
    # Linking allowed ONLY for:
    #   - explicit superadmin creator ("superadmin")
    #   - empty creator + superadmin-owned shop
    if creator_str == "superadmin":
        return True
    if creator_str is None:
        ok2, acct2 = await _owner_account_row(ctx, shop.get("owner_admin"))
        is_s = bool(ok2 and (acct2 is None or (acct2 and acct2.get("role_name") == "superadmin")))
    if creator_num is not None:
        return False  # any non-empty, non-superadmin creator = foreign
    ok, acct = await _owner_account_row(ctx, shop.get("owner_admin"))
    is_super = bool(ok and (acct is None or (acct and acct.get("role_name") == "superadmin")))
    return bool(ok and is_super)


async def link_subscription(ctx, shop: dict, token: str, tg_id: int) -> tuple[str, dict]:
    """Attach a pre-existing panel user (found by short-uuid from their sub
    link) to a buyer's Telegram id. Statuses: linked / already_yours /
    not_found / taken / foreign / failed. Never raises."""
    try:
        row = await ctx.db.get_user_by_short_uuid(token)
    except Exception:
        row = None
    if not row:
        return ("not_found", {})
    cur = row.get("telegramId", row.get("telegram_id"))
    try:
        cur = int(cur) if cur is not None else None
    except (TypeError, ValueError):
        cur = None
    username = row.get("username") or token
    # Bot-level link state by user_uuid (not panel users.telegramId):
    bot_sub = await ctx.db.fetchrow(
        "SELECT id, buyer_tg_id FROM plugin_pershop_subs WHERE shop_id=$1 AND user_uuid=$2",
        shop["id"], row.get("uuid"))
    if bot_sub is not None:
        bot_tg = bot_sub.get("buyer_tg_id")
        try:
            bot_tg = int(bot_tg) if bot_tg is not None else None
        except (TypeError, ValueError):
            bot_tg = None
        if bot_tg == tg_id:
            return ("already_yours", {"username": username})
        if bot_tg is not None:
            return ("taken", {"username": username})
    try:
        allowed = await _link_allowed(ctx, shop, row)
    except Exception:
        allowed = False
    if not allowed:
        return ("foreign", {"username": username})
    try:
        from shared.data_access import resolve_panel_user_id
        panel_id = await resolve_panel_user_id(row.get("uuid"))
    except Exception:
        panel_id = row.get("uuid")
    try:
        await _panel_client().update_user(panel_id, telegramId=tg_id)
    except Exception as e:
        ctx.logger.error("shop link failed for %s: %s", username, e)
        return ("failed", {"username": username})
    exp_raw = row.get("expireAt", row.get("expire_at"))
    try:
        from datetime import datetime

        exp = datetime.fromisoformat(str(exp_raw).replace("Z", "+00:00")).replace(tzinfo=None) if exp_raw else None
    except Exception:
        exp = exp_raw  # pass through if parsing fails; DB will reject with clear message
    try:
        existing = await ctx.db.fetchrow(
            "SELECT id FROM plugin_pershop_subs WHERE shop_id=$1 AND username=$2",
            shop["id"], username)
        if existing:
            await ctx.db.execute(
                "UPDATE plugin_pershop_subs SET buyer_tg_id=$1, is_linked=TRUE WHERE id=$2",
                tg_id, existing["id"])
        else:
            await ctx.db.execute(
                """INSERT INTO plugin_pershop_subs(shop_id,buyer_tg_id,user_uuid,username,
                   expire_at,traffic_limit_bytes,is_linked)
                   VALUES($1,$2,$3,$4,$5,$6,TRUE)""",
                shop["id"], tg_id, row.get("uuid"), username, exp,
                row.get("trafficLimitBytes", row.get("traffic_limit_bytes")))
    except Exception as e:
        ctx.logger.error("shop link row failed for %s: %s", username, e)
        return ("failed", {"username": username})
    await log_event(ctx, shop["id"], "linked", tg_id, username)
    return ("linked", {"username": username, "expire_at": exp})


async def _owner_is_superadmin(ctx, shop) -> bool:
    """True when the shop owner has the 'superadmin' role in admin_accounts.
    All admins have a row; we distinguish by role_name, not row presence."""
    try:
        from web.backend.core.admin_accounts import get_admin_account_by_username
        acct = await get_admin_account_by_username(shop.get("owner_admin") or "")
    except Exception:
        return False
    return bool(acct and acct.get("role_name") == "superadmin")


async def unmapped_users(ctx, shop: dict) -> dict:
    """Owner-side plan matching pool: panel users created by this shop's
    owner that have no subs row yet (orphans), plus subs rows with no plan
    (unplanned: linked or legacy subs). One query via IS NOT DISTINCT FROM
    covers both attributed and unattributed creators."""
    owner_id = await _owner_account_id(ctx, shop.get("owner_admin"))
    if owner_id is None and not await _owner_is_superadmin(ctx, shop):
        return {"orphans": [], "unplanned": []}  # fail closed
    orphans = await ctx.db.fetch(
        """SELECT uuid, username, expire_at, traffic_limit_bytes FROM users
           WHERE created_by_admin_id IS NOT DISTINCT FROM $1 AND NOT EXISTS
           (SELECT 1 FROM plugin_pershop_subs s WHERE s.shop_id=$2 AND s.user_uuid=users.uuid::text)
           ORDER BY username""",
        owner_id, shop["id"])
    unplanned = await ctx.db.fetch(
        "SELECT id, user_uuid, username, buyer_tg_id FROM plugin_pershop_subs"
        " WHERE shop_id=$1 AND package_id IS NULL AND COALESCE(is_test,FALSE)=FALSE"
        " ORDER BY id",
        shop["id"])
    return {"orphans": [dict(r) for r in orphans],
            "unplanned": [dict(r) for r in unplanned]}


async def assign_plan(ctx, shop: dict, user_uuid: str, package_id: int) -> dict:
    """Owner matches a user they created to a shop package. Same attribution
    rule as linking: creator match, or unattributed user + superadmin owner.
    Creates the subs row (buyer attached later on link) or plans an existing one."""
    pkg = await ctx.db.fetchrow(
        "SELECT * FROM plugin_pershop_packages WHERE id=$1 AND shop_id=$2", package_id, shop["id"])
    if not pkg:
        raise ValueError("package not found")
    if pkg_effect(pkg) != "full":
        raise ValueError("only full plans can be matched (top-ups are not plans)")
    prow = await ctx.db.fetchrow(
        "SELECT uuid, username, expire_at, traffic_limit_bytes, created_by_admin_id FROM users WHERE uuid=$1",
        user_uuid)
    if not prow:
        raise ValueError("user not found")
    owner_id = await _owner_account_id(ctx, shop.get("owner_admin"))
    creator = prow.get("created_by_admin_id")
    if creator is not None:
        if owner_id is None or creator != owner_id:
            raise ValueError("foreign user")
    elif not await _owner_is_superadmin(ctx, shop):
        raise ValueError("foreign user")
    ex = await ctx.db.fetchrow(
        "SELECT id, buyer_tg_id FROM plugin_pershop_subs WHERE shop_id=$1 AND user_uuid=$2",
        shop["id"], user_uuid)
    if ex:
        await ctx.db.execute(
            "UPDATE plugin_pershop_subs SET package_id=$1, updated_at=NOW() WHERE id=$2",
            package_id, ex["id"])
        return {"sub_id": ex["id"], "username": prow["username"], "created": False,
                "buyer_tg_id": ex.get("buyer_tg_id") if isinstance(ex, dict) else ex["buyer_tg_id"]}
    row = await ctx.db.fetchrow(
        """INSERT INTO plugin_pershop_subs(shop_id,buyer_tg_id,user_uuid,username,expire_at,
           traffic_limit_bytes,package_id) VALUES($1,NULL,$2,$3,$4,$5,$6) RETURNING id""",
        shop["id"], user_uuid, prow["username"], prow.get("expire_at"),
        prow.get("traffic_limit_bytes"), package_id)
    return {"sub_id": row["id"], "username": prow["username"], "created": True,
            "buyer_tg_id": None}


def _units(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def format_price(shop: dict, units) -> str:
    """Render units in the shop currency: units × rate + label (e.g. 625,000 تومان)."""
    u = _units(units)
    rate = _units((shop or {}).get("unit_rate"))
    label = ((shop or {}).get("currency_label") or "").strip()
    if rate > 0 and label:
        return f"{u * rate:,.0f} {label}"
    if label:
        return f"{u:g} {label}"
    return f"{u:g} units"


HWID_MODES = ("default", "fixed", "input", "unlimited")


def hwid_cfg(pkg: dict) -> dict:
    """Normalized per-package hwid toggle. 'default' = leave the panel default."""
    pkg = pkg or {}
    mode = (pkg.get("hwid_mode") or "default").strip().lower()
    if mode not in HWID_MODES:
        mode = "default"

    def _num(key, fallback):
        try:
            return int(pkg.get(key, fallback))
        except (TypeError, ValueError):
            return fallback

    fixed = pkg.get("hwid_fixed")
    try:
        fixed = None if fixed is None else int(fixed)
    except (TypeError, ValueError):
        fixed = None
    return {"mode": mode, "min": _num("hwid_min", 1), "default": _num("hwid_default", 1),
            "max": _num("hwid_max", 10), "free": _num("hwid_free", 1),
            "percent": _units(pkg.get("hwid_percent", 50)), "fixed": fixed}


def hwid_extra(base_units: float, cfg: dict, value) -> float:
    """Surcharge for input-mode hwid: % of base per device over free_included."""
    if (cfg or {}).get("mode") != "input":
        return 0.0
    try:
        extra = max(0, int(value or 0) - int(cfg.get("free", 0) or 0))
    except (TypeError, ValueError):
        return 0.0
    return extra * _units(cfg.get("percent")) / 100.0 * _units(base_units)


def order_total(pkg_units: float, pkg: dict | None = None, hwid_value=None) -> float:
    """Package price + hwid surcharge (input mode only)."""
    return _units(pkg_units) + hwid_extra(pkg_units, hwid_cfg(pkg or {}), hwid_value)


def resolve_hwid(pkg: dict, hwid_value=None):
    """Concrete device limit: input -> buyer value, fixed -> admin value,
    default/unlimited -> None (panel default / no cap)."""
    cfg = hwid_cfg(pkg)
    if cfg["mode"] == "fixed":
        return cfg["fixed"]
    if cfg["mode"] == "input":
        try:
            return hwid_value if hwid_value is not None else cfg["default"]
        except (TypeError, ValueError):
            return cfg["default"]
    return None


EFFECTS = ("full", "time_only", "traffic_only")


def pkg_effect(pkg: dict | None) -> str:
    """full = plan buy/renew, time_only = +days offer, traffic_only = +GB offer."""
    eff = (pkg or {}).get("effect") or "full"
    return eff if eff in EFFECTS else "full"


def is_timeless(expire_at) -> bool:
    """Year 2099+ = no expiry (long-duration / lifetime)."""
    try:
        return bool(expire_at) and expire_at.year >= 2099
    except Exception:
        return False


def format_expire(shop_or_lang, expire_at) -> str:
    """Buyer-facing date: 'never expires' for 2999, short date otherwise."""
    from rwa_plugin_pershop.lang import L
    if expire_at is None:
        return L(shop_or_lang, "t_unknown")
    if is_timeless(expire_at):
        return L(shop_or_lang, "t_never")
    try:
        return expire_at.strftime("%Y-%m-%d")
    except Exception:
        return str(expire_at)


def rel_words(shop_or_lang, dt, now=None) -> str:
    """Human distance: 'in 5 days', 'tomorrow', '3 hours ago'. Never raises."""
    from datetime import datetime, timezone

    from rwa_plugin_pershop.lang import L
    try:
        cur = now or datetime.now(timezone.utc)
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if getattr(cur, "tzinfo", None) is None:
            cur = cur.replace(tzinfo=timezone.utc)
        secs = (dt - cur).total_seconds()
    except Exception:
        return ""
    probe = {"lang": shop_or_lang.get("lang") if isinstance(shop_or_lang, dict) else shop_or_lang}
    a = abs(secs)
    days, hours = int(a // 86400), int(a // 3600)
    if secs >= 0:
        if days >= 2:
            return L(probe, "rel_in_days", n=days)
        if days == 1:
            return L(probe, "rel_tomorrow")
        if hours >= 1:
            return L(probe, "rel_in_hours", n=hours)
        return L(probe, "rel_soon")
    if days >= 2:
        return L(probe, "rel_over_days", n=days)
    if days == 1:
        return L(probe, "rel_yesterday")
    if hours >= 1:
        return L(probe, "rel_over_hours", n=hours)
    return L(probe, "rel_now")


def format_when(shop_or_lang, dt, now=None) -> str:
    """Relative + short date: 'in 5 days · 2027-02-22'. Never raises."""
    from rwa_plugin_pershop.lang import L
    if dt is None:
        return L(shop_or_lang, "t_unknown")
    if is_timeless(dt):
        return L(shop_or_lang, "t_never")
    rel = rel_words(shop_or_lang, dt, now)
    try:
        date = dt.strftime("%Y-%m-%d")
    except Exception:
        date = str(dt)
    return f"{rel} ({date})" if rel else date


async def request_withdraw(ctx, order_id: int, buyer_tg_id: int,
                         reason: str, card: str) -> dict:
    """Buyer asks to withdraw their own receipt: order must still be pending
    (not approved/denied). Marks it withdraw_requested and attaches the
    reason + refund card details for the owner. Returns the order + shop +
    plan name for the owner DM. Only pending orders can enter this state."""
    order = await ctx.db.fetchrow(
        "SELECT * FROM plugin_pershop_orders WHERE id=$1", order_id)
    if not order or order.get("buyer_tg_id") != buyer_tg_id:
        raise ValueError("order not found")
    if order.get("status") != "pending":
        raise ValueError("order is no longer pending")
    await ctx.db.execute(
        """UPDATE plugin_pershop_orders SET status='withdraw_requested',
           withdraw_reason=$1, withdraw_card=$2 WHERE id=$3""",
        (reason or "")[:1000], (card or "")[:1000], order_id)
    shop = await shop_by_id(ctx, order["shop_id"])
    pkg = await ctx.db.fetchrow(
        "SELECT name FROM plugin_pershop_packages WHERE id=$1", order["package_id"])
    out = dict(order)
    out["status"] = "withdraw_requested"
    out["withdraw_reason"] = (reason or "")[:1000]
    out["withdraw_card"] = (card or "")[:1000]
    out["shop"] = dict(shop) if shop else {}
    out["package_name"] = (pkg or {}).get("name") or ""
    return out


async def resolve_withdraw(ctx, order_id: int, owner_admin: str, confirm: bool) -> dict:
    """Owner settles a withdraw request. confirm=True -> withdrawn (buyer is
    told the owner will handle the refund); False -> back to pending (the
    receipt returns to the decision queue). Only the owning shop's orders."""
    shop = await shop_for_owner(ctx, owner_admin)
    order = await ctx.db.fetchrow(
        "SELECT * FROM plugin_pershop_orders WHERE id=$1", order_id) if shop else None
    if not order or order.get("shop_id") != (shop or {}).get("id"):
        raise ValueError("order not found")
    if order.get("status") != "withdraw_requested":
        raise ValueError("order is not awaiting withdraw")
    await ctx.db.execute(
        "UPDATE plugin_pershop_orders SET status=$1, decided_at=NOW(), decided_by=$2 WHERE id=$3",
        "withdrawn" if confirm else "pending", owner_admin, order_id)
    out = dict(order)
    out["status"] = "withdrawn" if confirm else "pending"
    out["credit_refunded"] = await _refund_order_credit(ctx, dict(order)) if confirm else 0.0
    out["shop"] = dict(shop)
    return out


async def credit_balance(ctx, shop_id: int, tg_id: int) -> float:
    """Buyer's non-withdrawable credit balance (units). Never raises."""
    try:
        v = await ctx.db.fetchval(
            "SELECT COALESCE(credit_units,0) FROM plugin_pershop_buyers"
            " WHERE shop_id=$1 AND tg_id=$2", shop_id, tg_id)
        return float(v or 0)
    except Exception:
        return 0.0


async def add_credit(ctx, shop_id: int, tg_id: int, units: float) -> float:
    """Add (negative = spend) credit; the balance floors at 0. Returns the
    new balance. Never raises."""
    try:
        row = await ctx.db.fetchrow(
            """INSERT INTO plugin_pershop_buyers(shop_id, tg_id, credit_units)
               VALUES($1,$2,$3)
               ON CONFLICT (shop_id, tg_id) DO UPDATE
               SET credit_units = GREATEST(0, plugin_pershop_buyers.credit_units
                                          + EXCLUDED.credit_units)
               RETURNING credit_units""", shop_id, tg_id, float(units))
        return float((row or {}).get("credit_units") or 0)
    except Exception:
        return await credit_balance(ctx, shop_id, tg_id)


async def _earn_referral_credit(ctx, shop: dict, order: dict, total_units) -> float:
    """Credit the inviter referral_percent of an approved purchase's *cash*
    portion. Credit the buyer already spent (tracked in the order snapshot)
    mints nothing — otherwise balance-funded orders would print credit.
    Runs inside approval but never blocks it; records the earned amount on
    the order for later refunds. Returns the credited amount."""
    try:
        pct = float(shop.get("referral_percent") or 0)
        buyer = int(order.get("buyer_tg_id") or 0)
        if pct <= 0 or not total_units or not buyer:
            return 0.0
        ref = await ctx.db.fetchrow(
            "SELECT referrer_tg_id FROM plugin_pershop_referrals"
            " WHERE shop_id=$1 AND referred_tg_id=$2", shop["id"], buyer)
        if not ref or int(ref.get("referrer_tg_id") or 0) == buyer:
            return 0.0
        cash = max(0.0, float(total_units) - _snapshot_credit(order))
        earned = round(cash * pct / 100.0, 4)
        if earned <= 0:
            return 0.0
        await add_credit(ctx, shop["id"], int(ref["referrer_tg_id"]), earned)
        await ctx.db.execute(
            "UPDATE plugin_pershop_orders SET credit_units=$1 WHERE id=$2",
            earned, order["id"])
        return earned
    except Exception as e:
        try:
            ctx.logger.error("shop referral credit failed: %s", e)
        except Exception:
            pass
        return 0.0


async def _refund_order_credit(ctx, order: dict) -> float:
    """Return the credit a denied/withdrawn order spent to the buyer's
    balance. The spent amount travels in the order snapshot (credit_used);
    orders.credit_units records what the sale *minted* for the referrer.
    Skips orders already pre-funded at withdraw-submit time (the snapshot
    flag), so the credit can never be paid twice. Returns the refunded
    amount. Never raises."""
    try:
        if _snapshot_prefunded(order):
            return 0.0
        spent = _snapshot_credit(order)
        if spent > 0 and order.get("buyer_tg_id"):
            await add_credit(ctx, order["shop_id"], int(order["buyer_tg_id"]), spent)
            return spent
        return 0.0
    except Exception:
        return 0.0


async def prefund_withdraw_credit(ctx, order: dict) -> float:
    """Instantly return the credit a pending order spent to the buyer's
    balance at withdraw-submit time, and stamp the snapshot so the later
    confirm/deny refund path pays nothing more. Mixed-payment withdraws
    work this way: the credit slice comes back now, the owner settles the
    transfer slice manually. Idempotent — a second submit (e.g. after a
    Keep) refunds 0. Never raises."""
    import json
    try:
        raw = (order or {}).get("options_snapshot")
        try:
            data = json.loads(raw) if isinstance(raw, str) else (dict(raw) if raw else {})
        except Exception:
            data = {}
        if (data or {}).get("credit_prefunded"):
            return 0.0
        used = _snapshot_credit(order)
        buyer = int((order or {}).get("buyer_tg_id") or 0)
        if used <= 0 or not buyer:
            return 0.0
        await add_credit(ctx, order["shop_id"], buyer, used)
        data["credit_prefunded"] = True
        await ctx.db.execute(
            "UPDATE plugin_pershop_orders SET options_snapshot=$1 WHERE id=$2",
            json.dumps(data), order["id"])
        return used
    except Exception:
        return 0.0


def _snapshot_prefunded(order: dict) -> bool:
    """True when the order's credit was already returned at withdraw-submit
    time (no further refund is due)."""
    import json

    raw = (order or {}).get("options_snapshot")
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        return False
    try:
        return bool((data or {}).get("credit_prefunded"))
    except (TypeError, ValueError):
        return False


def _snapshot_credit(order: dict) -> float:
    """Credit units the buyer spent on this order (0 when none)."""
    import json

    raw = (order or {}).get("options_snapshot")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return 0.0
    try:
        return float((data or {}).get("credit_used") or 0)
    except (TypeError, ValueError):
        return 0.0


async def has_mixed_payment(ctx, shop_id: int, buyer_tg_id: int, username: str) -> bool:
    """True when the sub's latest approved order mixed credit with a bank
    transfer (partial cover): it can't self-cancel, because the receipt
    slice needs a human refund. Never raises."""
    try:
        row = await ctx.db.fetchrow(
            """SELECT options_snapshot, receipt_kind FROM plugin_pershop_orders
               WHERE shop_id=$1 AND buyer_tg_id=$2 AND renew_username=$3
               AND status='approved' ORDER BY id DESC LIMIT 1""",
            shop_id, int(buyer_tg_id), username)
    except Exception:
        return False
    if not row or (row.get("receipt_kind") or "text") == "credit":
        return False
    return _snapshot_credit(row) > 0


async def cancel_credit_sub(ctx, sub_id: int, buyer_tg_id: int) -> dict:
    """Automatic cancel for a sub bought fully with credit: disables the
    panel account, refunds the originating order's units to the buyer's
    balance, and marks the sub cancelled (renew/reactivate stay hidden
    afterwards). Only full-credit auto-approved orders qualify
    (receipt_kind 'credit', status approved, linked by username).
    Raises ValueError (not_yours / already / no_credit_order); panel
    failures propagate so nothing is refunded or marked on error.
    Returns {order_id, amount, username}."""
    sub = await ctx.db.fetchrow(
        "SELECT * FROM plugin_pershop_subs WHERE id=$1", sub_id)
    if not sub or int(sub.get("buyer_tg_id") or 0) != int(buyer_tg_id):
        raise ValueError("not_yours")
    if sub.get("cancelled"):
        raise ValueError("already")
    order = await ctx.db.fetchrow(
        """SELECT * FROM plugin_pershop_orders WHERE shop_id=$1 AND buyer_tg_id=$2
           AND receipt_kind='credit' AND status='approved'
           AND renew_username=$3 ORDER BY id DESC LIMIT 1""",
        sub["shop_id"], int(buyer_tg_id), sub["username"])
    if not order:
        raise ValueError("no_credit_order")
    amount = float(order.get("total_units") or 0)
    try:
        from shared.data_access import resolve_panel_user_id
        panel_id = await resolve_panel_user_id(sub["user_uuid"])
    except Exception:
        panel_id = sub["user_uuid"]
    await _panel_client().update_user(panel_id, status="DISABLED")
    try:
        _shop = await shop_by_id(ctx, sub["shop_id"])
        _fire_updated(ctx, sub["user_uuid"], sub["username"], ["status"],
                      (_shop or {}).get("owner_admin"))
    except Exception:
        pass
    await add_credit(ctx, sub["shop_id"], int(buyer_tg_id), amount)
    await ctx.db.execute(
        "UPDATE plugin_pershop_subs SET cancelled=TRUE WHERE id=$1", sub_id)
    return {"order_id": order["id"], "amount": amount, "username": sub["username"]}


async def broadcast_audience(ctx, shop_id: int, target: str = "all") -> list[int]:
    """Telegram ids picked per broadcast segment. 'all' = every id that
    ever bought, linked, chose a language, or was referred in this shop;
    the other segments narrow that pool by purchase state. Best-effort
    parts never break the whole list."""
    ids: set[int] = set()
    if target == "buyers":
        # Only people who actually opened the bot's language step.
        try:
            for r in await ctx.db.fetch(
                    "SELECT DISTINCT tg_id FROM plugin_pershop_buyers WHERE shop_id=$1", shop_id):
                if r.get("tg_id"):
                    ids.add(int(r["tg_id"]))
        except Exception:
            pass
        return sorted(ids)
    if target == "subs":
        try:
            for r in await ctx.db.fetch(
                    "SELECT DISTINCT buyer_tg_id FROM plugin_pershop_subs WHERE shop_id=$1",
                    shop_id):
                if r.get("buyer_tg_id"):
                    ids.add(int(r["buyer_tg_id"]))
        except Exception:
            pass
        return sorted(ids)
    if target == "invited":
        try:
            for r in await ctx.db.fetch(
                    "SELECT DISTINCT referred_tg_id FROM plugin_pershop_referrals WHERE shop_id=$1",
                    shop_id):
                if r.get("referred_tg_id"):
                    ids.add(int(r["referred_tg_id"]))
        except Exception:
            pass
        return sorted(ids)
    if target == "expired":
        had = []
        try:
            had = [r for r in await ctx.db.fetch(
                "SELECT buyer_tg_id, expire_at FROM plugin_pershop_subs WHERE shop_id=$1 "
                "AND expire_at IS NOT NULL", shop_id)]
        except Exception:
            had = []
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        for r in had:
            exp = r.get("expire_at")
            if not r.get("buyer_tg_id") or exp is None:
                continue
            if getattr(exp, "tzinfo", None) is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp <= now:
                ids.add(int(r["buyer_tg_id"]))
        return sorted(ids)
    if target == "noorders":
        try:
            for r in await ctx.db.fetch(
                    "SELECT DISTINCT tg_id FROM plugin_pershop_buyers WHERE shop_id=$1", shop_id):
                if r.get("tg_id"):
                    ids.add(int(r["tg_id"]))
        except Exception:
            pass
        try:
            for r in await ctx.db.fetch(
                    "SELECT DISTINCT buyer_tg_id FROM plugin_pershop_orders "
                    "WHERE shop_id=$1 AND status='approved'", shop_id):
                ids.discard(int(r["buyer_tg_id"]))
        except Exception:
            pass
        return sorted(ids)
    try:
        for r in await ctx.db.fetch(
                "SELECT DISTINCT buyer_tg_id FROM plugin_pershop_subs WHERE shop_id=$1", shop_id):
            if r.get("buyer_tg_id"):
                ids.add(int(r["buyer_tg_id"]))
    except Exception:
        pass
    for table, col in (("plugin_pershop_buyers", "tg_id"),
                       ("plugin_pershop_orders", "buyer_tg_id"),
                       ("plugin_pershop_referrals", "referred_tg_id")):
        try:
            q = (f"SELECT DISTINCT {col} FROM {table} WHERE shop_id=$1"
                 if table != "plugin_pershop_buyers" else
                 f"SELECT DISTINCT tg_id FROM {table} WHERE shop_id=$1")
            for r in await ctx.db.fetch(q, shop_id):
                v = r.get(col)
                if v:
                    ids.add(int(v))
        except Exception:
            pass
    return sorted(ids)


async def queue_broadcast(ctx, shop_id: int, html_text: str, photo: bytes | None,
                          target: str = "all") -> dict:
    """Queue a broadcast. The owner writes it in whatever language fits
    their buyers — the bot forwards it verbatim (HTML formatting intact).
    A 30s tick fans it out in small chunks. target picks the segment
    (see broadcast_audience)."""
    audience = await broadcast_audience(ctx, shop_id, target)
    import json

    row = await ctx.db.fetchrow(
        """INSERT INTO plugin_pershop_broadcasts(shop_id,html_text,photo,recipients,total)
           VALUES($1,$2,$3,$4,$5) RETURNING id""",
        shop_id, (html_text or "")[:4000], photo, json.dumps(audience), len(audience))
    await log_event(ctx, shop_id, "broadcast", None,
                    f"target={target} recipients={len(audience)}")
    return {"id": row["id"], "recipients": len(audience), "target": target}


async def log_event(ctx, shop_id: int, kind: str, buyer_tg_id=None, detail: str = "") -> None:
    """Append one activity-feed row. Never raises — the feed is cosmetic
    and must never break a real flow."""
    try:
        await ctx.db.execute(
            "INSERT INTO plugin_pershop_events(shop_id, kind, buyer_tg_id, detail) "
            "VALUES($1,$2,$3,$4)",
            shop_id, kind, buyer_tg_id, (detail or "")[:500])
    except Exception:
        pass


async def list_events(ctx, shop_id: int, limit: int = 120) -> list[dict]:
    """Newest-first activity rows for a shop."""
    try:
        rows = await ctx.db.fetch(
            "SELECT * FROM plugin_pershop_events WHERE shop_id=$1 "
            "ORDER BY id DESC LIMIT $2", shop_id, max(1, min(int(limit), 300)))
        return [dict(r) for r in (rows or [])]
    except Exception:
        return []


async def buyer_detail(ctx, shop: dict, buyer_tg_id: int) -> dict:
    """Everything the shop knows about one buyer, in one payload: language,
    credit, scammer flag, every order and every sub. Never raises so the
    detail page always renders something."""
    import json as _json

    tg = int(buyer_tg_id)
    out = {"tg_id": tg, "lang": None, "credit_units": 0.0,
           "orders": [], "subs": [], "flag": None}
    try:
        b = await ctx.db.fetchrow(
            "SELECT tg_id, lang, credit_units FROM plugin_pershop_buyers"
            " WHERE shop_id=$1 AND tg_id=$2", shop["id"], tg)
        if b:
            out["lang"] = b.get("lang")
            out["credit_units"] = float(b.get("credit_units") or 0)
    except Exception:
        pass
    try:
        orders = await ctx.db.fetch(
            "SELECT id, package_id, service_id, status, total_units, created_at,"
            " renew_username, options_snapshot FROM plugin_pershop_orders"
            " WHERE shop_id=$1 AND buyer_tg_id=$2 ORDER BY id DESC LIMIT 50",
            shop["id"], tg)
        for o in (orders or []):
            row = dict(o)
            try:
                snap = _json.loads(row.pop("options_snapshot")) if isinstance(
                    row.get("options_snapshot"), str) else {}
                row["promo_code"] = snap.get("promo_code")
                row["credit_used"] = snap.get("credit_used") or 0
            except Exception:
                row["promo_code"] = None
                row["credit_used"] = 0
            out["orders"].append(row)
    except Exception:
        pass
    try:
        subs = await ctx.db.fetch(
            "SELECT id, username, expire_at, cancelled, is_test FROM"
            " plugin_pershop_subs WHERE shop_id=$1 AND buyer_tg_id=$2"
            " ORDER BY id DESC", shop["id"], tg)
        out["subs"] = [dict(s) for s in (subs or [])]
    except Exception:
        pass
    try:
        flag = await is_scammer(ctx, tg)
        out["flag"] = flag or None
    except Exception:
        pass
    return out


async def referral_pairs(ctx, shop: dict) -> dict:
    """Referral program page data: every invite with the credit it minted
    (sum of approved orders' referral credit for the invited buyer) plus
    the shop's current referral/campaign percentages."""
    pct = {"referral_percent": float((shop or {}).get("referral_percent") or 0),
           "campaign_percent": float((shop or {}).get("campaign_percent") or 0)}
    try:
        rows = await ctx.db.fetch(
            "SELECT r.referrer_tg_id AS referrer, r.referred_tg_id AS invited,"
            " COALESCE(SUM(o.credit_units), 0) AS earned, COUNT(o.id) AS buys"
            " FROM plugin_pershop_referrals r"
            " LEFT JOIN plugin_pershop_orders o ON o.shop_id = r.shop_id"
            "  AND o.buyer_tg_id = r.referred_tg_id AND o.status = 'approved'"
            " WHERE r.shop_id = $1"
            " GROUP BY r.referrer_tg_id, r.referred_tg_id"
            " ORDER BY earned DESC, r.referred_tg_id", shop["id"])
        pairs = [dict(r) for r in (rows or [])]
    except Exception:
        pairs = []
    return {"pairs": pairs, "invited": len(pairs),
            "earned_units": round(sum(float(p.get("earned") or 0) for p in pairs), 4),
            **pct}


async def shop_health(ctx, shop: dict) -> dict:
    """Cheap probes for the panel's health chips: bot configured, panel API
    reachable, pending orders (total + older than 24h = stuck)."""
    out = {"bot_ok": bool((shop or {}).get("bot_token_enc")),
           "panel_ok": False, "pending": 0, "pending_old": 0}
    try:
        total = await ctx.db.fetchval(
            "SELECT COUNT(*) FROM plugin_pershop_orders "
            "WHERE shop_id=$1 AND status='pending'", shop["id"])
        out["pending"] = int(total or 0)
    except Exception:
        pass
    try:
        old = await ctx.db.fetchval(
            "SELECT COUNT(*) FROM plugin_pershop_orders WHERE shop_id=$1 "
            "AND status='pending' AND created_at < NOW() - INTERVAL '24 hours'",
            shop["id"])
        out["pending_old"] = int(old or 0)
    except Exception:
        pass
    try:
        client = _panel_client()
        await client.get_internal_squads()
        out["panel_ok"] = True
    except Exception:
        pass
    return out


async def resolve_applied(ctx, pkg: dict, hwid_value=None) -> dict:
    """Concrete params for a new user: fixed package traffic/expiry + hwid +
    tier squad. Tier hwid_default applies only in mode=default (no explicit
    owner setting); fixed/input/unlimited always win.
    duration_days 0/blank = timeless package (no expiry, written as 2999)."""
    dur = (pkg or {}).get("duration_days")
    applied = {"traffic_limit_bytes": pkg.get("traffic_limit_bytes"),
               "expire_days": dur or None,
               "unlimited": not dur,
               "hwid": resolve_hwid(pkg, hwid_value),
               "squad_uuid": pkg.get("squad_uuid")}
    if (pkg or {}).get("tier_id"):
        tier = await ctx.db.fetchrow("SELECT * FROM plugin_pershop_tiers WHERE id=$1",
                                     pkg["tier_id"])
        if tier:
            if tier.get("squad_uuid"):
                applied["squad_uuid"] = tier["squad_uuid"]
            if (hwid_cfg(pkg)["mode"] == "default" and applied["hwid"] is None
                    and tier.get("hwid_default") is not None):
                applied["hwid"] = tier["hwid_default"]
    return applied


async def _maybe_move_squad(ctx, sub: dict, new_squad: str | None) -> bool:
    """Move the user to the picked package's tier squad when it differs
    (renew with tier change). Best-effort: failures are logged, the
    approval still stands, and the result reports squad_changed=False."""
    if not new_squad:
        return False
    current: set[str] = set()
    try:
        row = await ctx.db.get_user_by_uuid(sub["user_uuid"])
    except Exception:
        row = None
    row = row or {}
    items = row.get("activeInternalSquads", row.get("active_internal_squads", []))
    if isinstance(items, (list, tuple)):
        for it in items:
            u = it.get("uuid") if isinstance(it, dict) else it
            if u:
                current.add(str(u).lower())
    if current == {str(new_squad).lower()}:
        return False
    try:
        from shared.data_access import resolve_panel_user_id
        panel_id = await resolve_panel_user_id(sub["user_uuid"])
    except Exception:
        panel_id = sub["user_uuid"]
    try:
        await _panel_client().update_user(panel_id, activeInternalSquads=[new_squad])
    except Exception as e:
        ctx.logger.error("shop squad move failed for %s: %s", sub["username"], e)
        return False
    return True


async def _renew_traffic_target(ctx, sub: dict, pkg: dict, applied: dict):
    """Renew traffic target honoring the package reset toggle.

    reset=True  -> cap becomes the package limit (classic renew).
    reset=False -> leftover rolls over: (current limit - used, floored at 0)
                   + package traffic. Unlimited package -> unlimited;
                   unlimited-before -> fresh cap (nothing to roll over).
    Returns (new_limit_bytes_or_None, used_bytes).
    """
    target = applied["traffic_limit_bytes"]
    if target is None:
        return None, 0
    if pkg.get("traffic_reset", True):
        return target, 0
    try:
        row = await ctx.db.get_user_by_uuid(sub["user_uuid"])
    except Exception:
        row = None
    row = row or {}
    cur_limit = row.get("traffic_limit_bytes", row.get("trafficLimitBytes"))
    if cur_limit is None:
        cur_limit = sub.get("traffic_limit_bytes")
    used = row.get("used_traffic_bytes", row.get("usedTrafficBytes", 0)) or 0
    if not cur_limit:
        return target, used
    return max(0, cur_limit - used) + target, used


def _as_aware(dt):
    if dt is None:
        return None
    try:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


async def contract_preview(ctx, shop: dict, sub: dict | None,
                           pkg: dict, hwid_value=None) -> dict:
    """Raw numbers for the contract screen (bot formats them). Never raises:
    on any failure returns blanks the UI renders as 'unknown'.

    Keys: effect, price_units, unlimited_time, traffic_bytes (offer size),
    leftover_bytes, new_cap_bytes, current_expire_at, new_expire_at,
    reset (bool, full renews only).
    """
    out = {"effect": pkg_effect(pkg),
           "price_units": order_total(pkg.get("price_units") or 0, pkg, hwid_value),
           "unlimited_time": False, "traffic_bytes": pkg.get("traffic_limit_bytes"),
           "leftover_bytes": None, "new_cap_bytes": pkg.get("traffic_limit_bytes"),
           "current_expire_at": None, "new_expire_at": None,
           "reset": bool(pkg.get("traffic_reset", True))}
    try:
        applied = await resolve_applied(ctx, pkg, hwid_value)
    except Exception:
        return out
    out["unlimited_time"] = bool(applied["unlimited"])
    if sub is None:
        days = applied["expire_days"] or 30
        if applied["unlimited"]:
            out["new_expire_at"] = datetime(2999, 12, 31, tzinfo=timezone.utc)
        else:
            out["new_expire_at"] = datetime.now(timezone.utc) + timedelta(days=days)
        return out
    row = None
    try:
        row = await ctx.db.get_user_by_uuid(sub["user_uuid"])
    except Exception:
        row = None
    row = row or {}
    cur_exp = row.get("expire_at") or sub.get("expire_at")
    out["current_expire_at"] = cur_exp
    if out["effect"] == "traffic_only":
        out["new_expire_at"] = cur_exp
    elif applied["unlimited"] or is_timeless(cur_exp):
        out["new_expire_at"] = datetime(2999, 12, 31, tzinfo=timezone.utc)
    else:
        days = applied["expire_days"] or 30
        now = datetime.now(timezone.utc)
        b = _as_aware(cur_exp)
        base = now if (b is None or b < now) else b
        out["new_expire_at"] = base + timedelta(days=days)
    cur_limit = row.get("traffic_limit_bytes", row.get("trafficLimitBytes"))
    if cur_limit is None:
        cur_limit = sub.get("traffic_limit_bytes")
    used = row.get("used_traffic_bytes", row.get("usedTrafficBytes", 0)) or 0
    target = applied["traffic_limit_bytes"]
    if out["effect"] in ("full", "traffic_only") and target is not None and cur_limit:
        left = max(0, cur_limit - used)
        out["leftover_bytes"] = left
        if out["effect"] == "traffic_only" or not out["reset"]:
            out["new_cap_bytes"] = left + target
        else:
            out["new_cap_bytes"] = target
    elif out["effect"] == "full":
        out["new_cap_bytes"] = target
    return out


async def approve_order(ctx, order_id: int, decided_by: str) -> dict:
    """Approve a pending order. Renew orders extend; others create a new user.

    Single shared path for panel UI + shop-bot inline buttons.
    """
    order = await ctx.db.fetchrow("SELECT * FROM plugin_pershop_orders WHERE id = $1", order_id)
    if not order:
        raise ValueError("order not found")
    if order["status"] != "pending":
        raise ValueError(f"order already {order['status']}")
    await count_promo_use(ctx, order)
    await log_event(ctx, order["shop_id"], "approved", order.get("buyer_tg_id"),
                    f"#{order_id} {order.get('buyer_username')}")
    if order.get("service_id"):
        # Other-service order: no panel account, no subscription — the
        # buyer's completion text is delivered by the caller.
        svc = await service_by_id(ctx, order["service_id"])
        shop = await shop_by_id(ctx, order["shop_id"])
        if not svc or not shop:
            raise ValueError("service/shop gone")
        await ctx.db.execute(
            "UPDATE plugin_pershop_orders SET status='approved', decided_at=NOW(), decided_by=$1 WHERE id=$2",
            decided_by, order_id)
        earned = await _earn_referral_credit(ctx, shop, order, order.get("total_units"))
        return {"service": True, "renewed": False, "service_name": svc["name"],
                "done_text": (svc.get("done_text") or "").strip() or None,
                "total_units": order.get("total_units"), "referrer_earned": earned}
    pkg = await ctx.db.fetchrow("SELECT * FROM plugin_pershop_packages WHERE id = $1", order["package_id"])
    shop = await shop_by_id(ctx, order["shop_id"])
    if not pkg or not shop:
        raise ValueError("package/shop gone")

    if order.get("renew_username"):
        sub = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_subs WHERE shop_id=$1 AND username=$2",
            shop["id"], order["renew_username"])
        if not sub:
            raise ValueError("subscription to renew not found")
        applied = await resolve_applied(ctx, pkg, _snapshot_hwid(order))
        effect = pkg_effect(pkg)
        squad_changed = False
        if effect == "traffic_only":
            # Top-up: leftover + package traffic, expiry untouched.
            new_limit, _used = await _renew_traffic_target(
                ctx, sub, dict(pkg, traffic_reset=False), applied)
            await _panel_set_traffic(ctx, sub["user_uuid"], new_limit)
            new_exp = sub.get("expire_at")
            try:
                prow = await ctx.db.get_user_by_uuid(sub["user_uuid"])
                if prow and prow.get("expire_at"):
                    new_exp = prow.get("expire_at")
            except Exception:
                pass
            flag_sql = "notified_80=FALSE, notified_out=FALSE"
        else:
            if effect == "time_only":
                # +days offer: date moves, traffic untouched.
                new_limit = sub.get("traffic_limit_bytes")
                traffic_arg = None
                flag_sql = "notified_72h=FALSE, notified_expired=FALSE"
            else:
                new_limit, _used = await _renew_traffic_target(ctx, sub, pkg, applied)
                traffic_arg = new_limit
                flag_sql = ("notified_80=FALSE, notified_72h=FALSE, "
                            "notified_expired=FALSE, notified_out=FALSE")
            cur_exp = sub.get("expire_at")
            if is_timeless(cur_exp) or applied["unlimited"]:
                new_exp = datetime(2999, 12, 31, tzinfo=timezone.utc)
            else:
                new_exp = await panel_extend_user(
                    ctx, sub["user_uuid"], applied["expire_days"] or 30, traffic_arg)
            if effect == "full":
                squad_changed = await _maybe_move_squad(ctx, sub, applied.get("squad_uuid"))
        await ctx.db.execute(
            "UPDATE plugin_pershop_orders SET status='approved', decided_at=NOW(), decided_by=$1 WHERE id=$2",
            decided_by, order_id)
        await ctx.db.execute(
            f"""UPDATE plugin_pershop_subs SET expire_at=$1,
               traffic_limit_bytes=COALESCE($2, traffic_limit_bytes),
               {flag_sql}, updated_at=NOW() WHERE id=$3""",
            new_exp, new_limit, sub["id"])
        if effect != "time_only":
            try:
                from web.backend.core.admin_accounts import increment_usage_counter
                owner_id = await _owner_account_id(ctx, shop.get("owner_admin"))
                if owner_id is not None:
                    delta = (new_limit or 0) - (sub["traffic_limit_bytes"] or 0)
                    if delta:
                        await increment_usage_counter(owner_id, "traffic_used_bytes", delta)
            except Exception as e:
                ctx.logger.error("shop renew counter sync failed: %s", e)
        earned = await _earn_referral_credit(ctx, shop, order, order.get("total_units"))
        changed = ["traffic_limit_bytes"] if effect == "traffic_only" else ["expire_at"]
        if effect == "full":
            changed.append("traffic_limit_bytes")
            if squad_changed:
                changed.append("active_internal_squads")
        _fire_updated(ctx, sub["user_uuid"], sub["username"], changed, shop.get("owner_admin"))
        return {"username": sub["username"], "renewed": True, "expire_at": new_exp,
                "subscription_url": None, "squad_changed": squad_changed,
                "traffic_limit_bytes": new_limit, "effect": effect,
                "referrer_earned": earned}

    if pkg_effect(pkg) != "full":
        raise ValueError("top-up offers can only extend an existing subscription")
    taken = await existing_usernames(ctx, shop["id"])
    username = allocate_username(shop["prefix"], order["buyer_username"], order["buyer_tg_id"], taken)
    applied = await resolve_applied(ctx, pkg, _snapshot_hwid(order))
    if applied["unlimited"]:
        expire_at = datetime(2999, 12, 31, tzinfo=timezone.utc)
    else:
        expire_at = datetime.now(timezone.utc) + timedelta(days=applied["expire_days"] or 30)
    created = await panel_create_user(
        ctx, username=username, expire_at=expire_at,
        traffic_limit_bytes=applied["traffic_limit_bytes"], telegram_id=order["buyer_tg_id"],
        squad_uuid=applied["squad_uuid"], hwid_device_limit=applied["hwid"],
        owner_admin=shop.get("owner_admin"),
        description=f"pershop:{shop['prefix']} order #{order_id}",
    )
    user_uuid = created.get("uuid") or username
    await ctx.db.execute(
        "UPDATE plugin_pershop_orders SET status='approved', decided_at=NOW(), decided_by=$1 WHERE id=$2",
        decided_by, order_id,
    )
    await ctx.db.execute(
        """INSERT INTO plugin_pershop_subs(shop_id, buyer_tg_id, user_uuid, username, expire_at, traffic_limit_bytes,
           package_id)
           VALUES($1,$2,$3,$4,$5,$6,$7)""",
        shop["id"], order["buyer_tg_id"], str(user_uuid), username,
        expire_at, pkg["traffic_limit_bytes"], pkg["id"],
    )
    # Link the order to its sub: powers the mixed-payment note, the
    # full-credit cancel lookup, and later cancels (same as the auto path).
    await ctx.db.execute(
        "UPDATE plugin_pershop_orders SET renew_username=$1 WHERE id=$2",
        username, order_id)
    earned = await _earn_referral_credit(ctx, shop, order, order.get("total_units"))
    return {"username": username, "renewed": False,
            "subscription_url": created.get("subscription_url"), "expire_at": expire_at,
            "referrer_earned": earned}


async def has_services(ctx, shop_id: int) -> bool:
    """Any active sellable service (for the Other-Services menu button)."""
    try:
        n = await ctx.db.fetchval(
            "SELECT COUNT(*) FROM plugin_pershop_services WHERE shop_id=$1 AND active", shop_id)
        return (n or 0) > 0
    except Exception:
        return False


async def service_cats(ctx, shop_id: int) -> list:
    """Active service categories (tiers-equivalent for Other Services)."""
    try:
        rows = await ctx.db.fetch(
            "SELECT * FROM plugin_pershop_service_cats WHERE shop_id=$1 AND active ORDER BY sort, id",
            shop_id)
        return [dict(r) for r in rows or []]
    except Exception:
        return []


async def cat_services(ctx, shop_id: int, cat_id=None) -> list:
    """Active services of one category (None = uncategorized)."""
    try:
        if cat_id is None:
            rows = await ctx.db.fetch(
                "SELECT * FROM plugin_pershop_services WHERE shop_id=$1 AND active AND cat_id IS NULL"
                " ORDER BY sort, id", shop_id)
        else:
            rows = await ctx.db.fetch(
                "SELECT * FROM plugin_pershop_services WHERE shop_id=$1 AND active AND cat_id=$2"
                " ORDER BY sort, id", shop_id, cat_id)
        return [dict(r) for r in rows or []]
    except Exception:
        return []


async def service_by_id(ctx, svc_id) -> dict | None:
    try:
        row = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_services WHERE id=$1", int(svc_id))
        return dict(row) if row else None
    except (TypeError, ValueError):
        return None
    except Exception:
        return None


def _snapshot_hwid(order: dict):
    """Buyer-picked device count stored on the order (input-mode packages)."""
    import json

    raw = (order or {}).get("options_snapshot")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return None
    return (data or {}).get("hwid") if isinstance(data, dict) else None


def snapshot_note(order: dict) -> str:
    """Buyer note attached to an Other-Service order ('' when none)."""
    import json

    raw = (order or {}).get("options_snapshot")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return ""
    note = ((data or {}).get("note") or "") if isinstance(data, dict) else ""
    return str(note)[:500]


async def deny_order(ctx, order_id: int, decided_by: str, reason: str) -> None:
    order = await ctx.db.fetchrow("SELECT * FROM plugin_pershop_orders WHERE id = $1", order_id)
    if not order:
        raise ValueError("order not found")
    if order["status"] not in ("pending", "withdraw_requested"):
        raise ValueError(f"order already {order['status']}")
    await ctx.db.execute(
        "UPDATE plugin_pershop_orders SET status='denied', deny_reason=$1, decided_at=NOW(), decided_by=$2 WHERE id=$3",
        reason, decided_by, order_id,
    )
    await log_event(ctx, order["shop_id"], "denied", order.get("buyer_tg_id"),
                    f"#{order_id} {order.get('buyer_username')}")
    return await _refund_order_credit(ctx, dict(order))


# kind -> shop column holding the admin-editable template.
TEXT_COLUMNS = {
    "thank_you": "thank_you_text",
    "approved": "approved_text",
    "renewed": "approved_text",
    "denied": "denied_text",
}


def format_gb(value) -> str:
    """Bytes -> '12.40 GB' / '850.0 MB' / '∞' for buyer messages. Never raises."""
    try:
        if value is None:
            return "∞"
        v = float(value)
        if v >= 10 ** 9:
            return f"{v / 10 ** 9:.2f} GB"
        return f"{v / 10 ** 6:.1f} MB"
    except Exception:
        return "?"


async def approval_vars(ctx, shop: dict, order: dict, result: dict) -> dict:
    """Template variables for the approved/renewed buyer cards (RWA-bot card
    style): when (relative + date), traffic, plan, price, status. The legacy
    {expire} raw value is kept for custom texts. Never raises."""
    from rwa_plugin_pershop.lang import L
    pkg = None
    try:
        if order.get("package_id"):
            pkg = await ctx.db.fetchrow(
                "SELECT * FROM plugin_pershop_packages WHERE id=$1", order["package_id"])
    except Exception:
        pkg = None
    pkg = pkg or {}
    traffic = result.get("traffic_limit_bytes")
    if traffic is None:
        traffic = pkg.get("traffic_limit_bytes")
    try:
        no_cap = not traffic or float(traffic) <= 0
    except Exception:
        no_cap = True
    traffic_str = L(shop, "unlimited_lbl") if no_cap else format_gb(traffic)
    try:
        total = float(order.get("total_units") or pkg.get("price_units") or 0)
    except Exception:
        total = 0
    exp = result.get("expire_at")
    chosen_hwid = _snapshot_hwid(order)
    if chosen_hwid:
        hwid = str(chosen_hwid)
    elif (pkg.get("hwid_mode") or "default") == "unlimited":
        hwid = "∞"
    elif (pkg.get("hwid_mode") or "default") == "fixed":
        hwid = str(pkg.get("hwid_fixed") or "—")
    else:
        hwid = "—"
    return {
        "when": format_when(shop, exp),
        "traffic": traffic_str,
        "plan": pkg.get("name") or "",
        "price": format_price(shop, total),
        "status": L(shop, "status_active"),
        "dot": "🟢",
        "hwid": hwid,
        "expire": format_when(shop, exp),
        "url": result.get("subscription_url") or L(shop, "url_fallback"),
    }


def tutorial_link_rows(raw: str) -> list:
    """Inline-button rows for the buyer How-to-Use message.

    The superadmin writes one row per line in the plugin settings:
        Label | https://example.com            (one button, own row)
        Label1 | https://a.io || Label2 | https://b.io   (two buttons inline)
    Buttons are wrapped at 8 per row; only http(s) links pass; blank or
    malformed chunks are skipped. Never raises."""
    rows: list[list[dict]] = []
    try:
        for line in (raw or "").splitlines():
            row: list[dict] = []
            for chunk in line.split("||"):
                if "|" not in chunk:
                    continue
                label, _, url = chunk.partition("|")
                label = (label or "").strip()
                url = (url or "").strip()
                if not label or not url.lower().startswith(("http://", "https://")):
                    continue
                if len(row) < 8:
                    row.append({"text": label[:64], "url": url})
            if row:
                rows.append(row)
    except Exception:
        return []
    return rows[:24]


def fmt_safe(text: str, **vars: Any) -> str:
    """Format owner-written text with the given variables, leaving the raw
    text untouched when a placeholder is unknown (never raises)."""
    try:
        return (text or "").format(**vars)
    except Exception:
        return text or ""


def message_text(shop: dict, kind: str, **vars: Any) -> str:
    """Render an admin-editable message template with safe fallback.

    Admin-written text always wins; otherwise the shop language default.
    """
    from rwa_plugin_pershop.lang import L

    default = L(shop, f"tpl_{kind}")
    tpl = (shop.get(TEXT_COLUMNS[kind]) or "").strip() or default
    try:
        return tpl.format(**vars)
    except Exception:
        try:
            return default.format(**vars)
        except Exception:
            return default


async def is_scammer(ctx, tg_id: int):
    """Network-wide scammer flag row, or None. Never raises."""
    try:
        row = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_scammers WHERE tg_id=$1", tg_id)
        return dict(row) if row else None
    except Exception:
        return None


async def list_scammers(ctx) -> list:
    """All network-wide scammer flags, ordered by telegram id. Never raises."""
    try:
        rows = await ctx.db.fetch(
            "SELECT tg_id, marked_by_admin, reason FROM plugin_pershop_scammers ORDER BY tg_id")
        return [dict(r) for r in (rows or [])]
    except Exception:
        return []


async def unmark_scammer(ctx, tg_id: int) -> bool:
    """Remove a network-wide scammer flag (second chances). Returns True
    when a flag existed. Never raises."""
    try:
        row = await ctx.db.fetchrow(
            "SELECT tg_id FROM plugin_pershop_scammers WHERE tg_id=$1", tg_id)
        if not row:
            return False
        await ctx.db.execute(
            "DELETE FROM plugin_pershop_scammers WHERE tg_id=$1", tg_id)
        return True
    except Exception:
        return False


async def mark_scammer(ctx, order_id: int, owner_admin: str) -> dict:
    """Flag an order's buyer as a network-wide scammer.

    Accepts pending orders (settles them as denied, same rule as
    approve/deny) and already-denied orders (flag-only: the report button
    only appears after a deny, so this is the normal path). The flag lives
    in the global table so every shop on the panel can warn about this
    Telegram id. Ownership-checked.
    """
    order = await ctx.db.fetchrow(
        "SELECT * FROM plugin_pershop_orders WHERE id=$1", order_id)
    if not order:
        raise ValueError("order not found")
    shop = await shop_by_id(ctx, order["shop_id"])
    if not shop or shop.get("owner_admin") != owner_admin:
        raise ValueError("not your order")
    if order.get("status") not in ("pending", "denied"):
        raise ValueError("order already decided")
    pkg = await ctx.db.fetchrow(
        "SELECT name FROM plugin_pershop_packages WHERE id=$1",
        order["package_id"])
    reason = f"order #{order_id} ({(pkg or {}).get('name') or '?'})"
    await ctx.db.execute(
        """INSERT INTO plugin_pershop_scammers(tg_id, marked_by_admin, reason)
           VALUES($1,$2,$3)
           ON CONFLICT(tg_id) DO UPDATE SET marked_by_admin=$2, reason=$3""",
        order["buyer_tg_id"], owner_admin, reason)
    await ctx.db.execute(
        "UPDATE plugin_pershop_orders SET status='denied', decided_at=NOW() WHERE id=$1",
        order_id)
    return {"order": dict(order), "shop": dict(shop), "reason": reason}


async def shop_stats(ctx, shop: dict) -> dict:
    """Small overview strip for the owner: counts only, never raises."""
    out = {"subs_active": 0, "orders_pending": 0, "orders_approved": 0,
           "units_sold": 0, "orders_denied": 0, "orders_withdrawn": 0,
           "tests_claimed": 0, "subs_linked": 0, "referral_earned": 0}
    try:
        now = datetime.now(timezone.utc)
        out["subs_active"] = await ctx.db.fetchval(
            "SELECT COUNT(*) FROM plugin_pershop_subs WHERE shop_id=$1 AND expire_at>$2",
            shop["id"], now) or 0
        out["orders_pending"] = await ctx.db.fetchval(
            "SELECT COUNT(*) FROM plugin_pershop_orders WHERE shop_id=$1 AND status IN ('pending','withdraw_requested')",
            shop["id"]) or 0
        appr = await ctx.db.fetchrow(
            "SELECT COUNT(*) AS n, COALESCE(SUM(total_units),0) AS u FROM plugin_pershop_orders WHERE shop_id=$1 AND status='approved'",
            shop["id"]) or {}
        out["orders_approved"] = appr.get("n") or 0
        out["units_sold"] = appr.get("u") or 0
        out["orders_denied"] = await ctx.db.fetchval(
            "SELECT COUNT(*) FROM plugin_pershop_orders WHERE shop_id=$1 AND status='denied'",
            shop["id"]) or 0
        out["orders_withdrawn"] = await ctx.db.fetchval(
            "SELECT COUNT(*) FROM plugin_pershop_orders WHERE shop_id=$1 AND status='withdrawn'",
            shop["id"]) or 0
        out["tests_claimed"] = await ctx.db.fetchval(
            "SELECT COUNT(*) FROM plugin_pershop_subs WHERE shop_id=$1 AND is_test=TRUE",
            shop["id"]) or 0
        out["subs_linked"] = await ctx.db.fetchval(
            "SELECT COUNT(*) FROM plugin_pershop_subs WHERE shop_id=$1 AND is_linked=TRUE",
            shop["id"]) or 0
        out["subs_manual"] = await ctx.db.fetchval(
            """SELECT COUNT(*) FROM plugin_pershop_subs WHERE shop_id=$1 AND package_id IS NULL
               AND COALESCE(is_test,FALSE)=FALSE AND COALESCE(is_linked,FALSE)=FALSE""",
            shop["id"]) or 0
        out["referral_earned"] = await ctx.db.fetchval(
            """SELECT COALESCE(SUM(credit_units),0) FROM plugin_pershop_orders
               WHERE shop_id=$1 AND status='approved'""",
            shop["id"]) or 0
    except Exception as e:
        ctx.logger.warning("shop.stats_failed", extra={"err": str(e)})
    return out


async def test_terms(ctx, shop: dict) -> dict:
    """Superadmin-configured test subscription size. Defaults: 1 day, 1 GB,
    no squad (panel default), panel-default hwid limit."""
    days = await ctx.settings.get("test_duration_days", default=1)
    gb = await ctx.settings.get("test_traffic_gb", default=1)
    try:
        days = max(1, int(days))
    except (TypeError, ValueError):
        days = 1
    try:
        gb = max(0, int(gb))
    except (TypeError, ValueError):
        gb = 1
    squad = (await ctx.settings.get("test_squad_uuid", default="") or "").strip()
    try:
        hwid = await ctx.settings.get("test_hwid", default=None)
        hwid = None if hwid in (None, "") else max(0, int(hwid))
    except (TypeError, ValueError):
        hwid = None
    return {"days": days, "gb": gb, "squad_uuid": squad or None, "hwid": hwid}


async def claim_test_sub(ctx, shop: dict, buyer_tg_id: int, buyer_username: str) -> dict:
    """Create a one-per-person free test account: {prefix}-Test-{N}.

    Panel-global: the claim is recorded in plugin_pershop_test_claims, so
    one Telegram id gets a single test account across ALL shops on this
    panel, no matter which shop hands it out. No admin approval involved.
    Raises ValueError when disabled or already claimed network-wide.
    """
    if not (shop.get("test_enabled") and (shop.get("prefix") or "").strip()):
        raise ValueError("test subscriptions are off")
    prefix = shop["prefix"].strip()
    claimed = await ctx.db.fetchrow(
        """INSERT INTO plugin_pershop_test_claims(tg_id, shop_id)
           VALUES($1,$2) ON CONFLICT DO NOTHING RETURNING tg_id""",
        buyer_tg_id, shop["id"])
    if not claimed:
        raise ValueError("test already claimed")
    terms = await test_terms(ctx, shop)
    number = int(shop.get("test_counter") or 0) + 1
    username = f"{prefix}-Test-{number}"
    expire_at = datetime.now(timezone.utc) + timedelta(days=terms["days"])
    traffic = terms["gb"] * 1024 * 1024 * 1024 if terms["gb"] else None
    created = await panel_create_user(
        ctx, username=username, expire_at=expire_at,
        traffic_limit_bytes=traffic, telegram_id=buyer_tg_id,
        squad_uuid=terms.get("squad_uuid"), hwid_device_limit=terms.get("hwid"),
        owner_admin=shop.get("owner_admin"),
        description=f"pershop:{prefix} test",
    )
    await ctx.db.execute("UPDATE plugin_pershop_shops SET test_counter=$1 WHERE id=$2",
                         number, shop["id"])
    await ctx.db.execute(
        """INSERT INTO plugin_pershop_subs(shop_id, buyer_tg_id, user_uuid, username,
           expire_at, traffic_limit_bytes, is_test)
           VALUES($1,$2,$3,$4,$5,$6,TRUE)""",
        shop["id"], buyer_tg_id, str(created.get("uuid") or username),
        username, expire_at, traffic,
    )
    return {"username": username, "expire_at": expire_at,
            "subscription_url": created.get("subscription_url"), **terms}


async def record_referral(ctx, shop_id: int, referrer_tg_id: int, referred_tg_id: int) -> None:
    """Remember who invited whom. Never raises; duplicates and self-refs ignored."""
    try:
        if not referrer_tg_id or referrer_tg_id == referred_tg_id:
            return
        await ctx.db.execute(
            """INSERT INTO plugin_pershop_referrals(shop_id, referrer_tg_id, referred_tg_id)
               VALUES($1,$2,$3) ON CONFLICT DO NOTHING""",
            shop_id, referrer_tg_id, referred_tg_id)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Promo codes + the invitee first-purchase campaign discount. Promos are
# strictly shop-scoped (UNIQUE(shop_id, code)); every lookup re-filters by
# shop_id so a code minted by one owner can never pay in another shop.

PROMO_KINDS = ("percent", "units")


async def create_promo(ctx, shop_id: int, *, code: str, kind: str, value: float,
                       max_uses: int = 0, valid_until=None,
                       created_by_owner: str = "") -> dict:
    """Insert a promo for one shop. Raises ValueError on bad input/duplicate."""
    clean = "".join(ch for ch in (code or "").upper() if ch.isalnum() or ch in "_-")[:40]
    if not clean:
        raise ValueError("promo code needs letters/digits")
    if kind not in PROMO_KINDS:
        raise ValueError("promo kind must be percent|units")
    value = float(value or 0)
    if value <= 0 or (kind == "percent" and value > 100):
        raise ValueError("promo value out of range")
    try:
        row = await ctx.db.fetchrow(
            """INSERT INTO plugin_pershop_promos(shop_id, code, kind, value, max_uses,
               valid_until, created_by_owner)
               VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING *""",
            shop_id, clean, kind, value, int(max_uses or 0), valid_until,
            created_by_owner)
    except Exception as e:
        if any(w in str(e).lower() for w in ("unique", "duplicate")):
            raise ValueError("this code already exists in the shop")
        raise
    return dict(row or {})


def promo_preview_value(promo: dict, total_units: float) -> float:
    """Units the promo takes off `total` (percent vs flat), capped at total."""
    total = float(total_units or 0)
    value = float((promo or {}).get("value") or 0)
    if (promo or {}).get("kind") == "units":
        return round(min(max(value, 0.0), total), 6)
    return round(min(max(value * total / 100.0, 0.0), total), 6)


async def promo_validate(ctx, shop_id: int, code: str, buyer_tg_id: int) -> dict | None:
    """Active, in-window, barely-used promo for THIS shop + THIS buyer."""
    clean = (code or "").strip().upper()
    if not clean:
        return None
    try:
        row = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_promos WHERE shop_id=$1 AND code=$2 AND active",
            shop_id, clean)
        if not row:
            return None
        promo = dict(row)
        vu = promo.get("valid_until")
        if vu is not None:
            from datetime import datetime, timezone
            if vu.tzinfo is None:
                vu = vu.replace(tzinfo=timezone.utc)
            if vu <= datetime.now(timezone.utc):
                return None
        if int(promo.get("max_uses") or 0) > 0:
            used = await ctx.db.fetchval(
                "SELECT COUNT(*) FROM plugin_pershop_promo_uses WHERE promo_id=$1",
                promo["id"])
            if int(used or 0) >= int(promo["max_uses"]):
                return None
        mine = await ctx.db.fetchrow(
            "SELECT 1 FROM plugin_pershop_promo_uses WHERE promo_id=$1 AND buyer_tg_id=$2",
            promo["id"], buyer_tg_id)
        if mine:
            return None
        return promo
    except Exception:
        return None


async def promo_find_by_code(ctx, shop_id: int, code: str) -> dict | None:
    clean = (code or "").strip().upper()
    if not clean:
        return None
    try:
        row = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_promos WHERE shop_id=$1 AND code=$2",
            shop_id, clean)
        return dict(row) if row else None
    except Exception:
        return None


async def campaign_units_for(ctx, shop, buyer_tg_id: int, total_units: float) -> float:
    """First-purchase discount for a referral *invitee* — the Ad-campaign
    lever. Active only when the owner wrote a campaign_percent > 0; a buyer
    who joined via an invite link and has NOT yet completed any approved
    order in this shop gets that percent off this checkout."""
    pct = float((shop or {}).get("campaign_percent") or 0)
    if pct <= 0 or not buyer_tg_id or float(total_units or 0) <= 0:
        return 0.0
    try:
        invited = await ctx.db.fetchrow(
            "SELECT 1 FROM plugin_pershop_referrals WHERE shop_id=$1 AND referred_tg_id=$2",
            shop.get("id"), buyer_tg_id)
        if not invited:
            return 0.0
        prior = await ctx.db.fetchrow(
            "SELECT 1 FROM plugin_pershop_orders WHERE shop_id=$1 AND buyer_tg_id=$2 "
            "AND status='approved' LIMIT 1",
            shop.get("id"), buyer_tg_id)
        if prior:
            return 0.0
    except Exception:
        return 0.0
    return round(min(max(float(total_units) * pct / 100.0, 0.0), float(total_units)), 6)


async def checkout_discount(ctx, shop, buyer_tg_id: int, total_units: float,
                            promo: dict | None = None) -> dict:
    """Combined checkout discount: promos + the campaign first-order cut.
    Both apply, never below zero. Returns units + metadata for the order
    snapshot. Never raises."""
    total = float(total_units or 0)
    res = {"promo": None, "promo_units": 0.0, "campaign_units": 0.0,
           "campaign_pct": float((shop or {}).get("campaign_percent") or 0),
           "discount_units": 0.0}
    if total <= 0:
        return res
    if promo:
        res["promo"] = dict(promo)
        res["promo_units"] = promo_preview_value(promo, total)
    res["campaign_units"] = min(
        await campaign_units_for(ctx, shop, buyer_tg_id, total), total - res["promo_units"])
    res["discount_units"] = round(res["promo_units"] + res["campaign_units"], 6)
    return res


async def count_promo_use(ctx, order: dict | None) -> None:
    """Burn one promo use at approval time (never-raises accounting)."""
    try:
        raw = (order or {}).get("options_snapshot")
        snap = _snap_dict(raw)
        code = str(snap.get("promo_code") or "").strip().upper()
        if not code:
            return
        shop_id = (order or {}).get("shop_id")
        promo = await ctx.db.fetchrow(
            "SELECT id FROM plugin_pershop_promos WHERE shop_id=$1 AND code=$2",
            shop_id, code)
        if not promo:
            return
        await ctx.db.execute(
            """INSERT INTO plugin_pershop_promo_uses(promo_id, shop_id, order_id, buyer_tg_id)
               VALUES($1,$2,$3,$4) ON CONFLICT (promo_id, buyer_tg_id) DO NOTHING""",
            promo["id"], shop_id, (order or {}).get("id"),
            (order or {}).get("buyer_tg_id"))
        await log_event(ctx, shop_id, "promo", (order or {}).get("buyer_tg_id"),
                        f"{code} on #{(order or {}).get('id')}")
    except Exception:
        pass


def _snap_dict(raw) -> dict:
    """options_snapshot as a dict, best-effort."""
    import json

    try:
        data = json.loads(raw) if isinstance(raw, str) else (dict(raw) if raw else {})
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def list_promos_with_uses(ctx, shop_id: int) -> list[dict]:
    """All promos of a shop + how many times each was redeemed so far."""
    try:
        rows = await ctx.db.fetch(
            """SELECT p.*, COALESCE(u.cnt, 0) AS uses FROM plugin_pershop_promos p
               LEFT JOIN (SELECT promo_id, COUNT(*) AS cnt FROM plugin_pershop_promo_uses
                          GROUP BY promo_id) u ON u.promo_id=p.id
               WHERE p.shop_id=$1 ORDER BY p.id""",
            shop_id)
        return [dict(r) for r in (rows or [])]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Panel-side sub-card actions from the shop bot: HWID device reset and the
# full subscription reissue (panel "revoke" — regenerate the subscription
# credentials: the old URL dies, every device must re-add the link).

def _resolve_panel_uid(user_uuid: str) -> object:
    try:
        from shared.data_access import resolve_panel_user_id
        return resolve_panel_user_id(user_uuid or "")
    except Exception:
        return user_uuid or ""


async def panel_reset_devices(ctx, user_uuid: str) -> dict:
    """Unbind every HWID device of a panel user. Rows are marked removed so
    the panel keeps its detection history. Never raises."""
    try:
        client = _panel_client()
        await client.delete_all_user_hwid_devices(_resolve_panel_uid(user_uuid))
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    try:
        # Mirror rows live in the admin DB; marking them removed makes the
        # active count in the bot refresh instantly instead of waiting for sync.
        await ctx.db.execute(
            "UPDATE user_hwid_devices SET removed_at=NOW() "
            "WHERE user_uuid=$1 AND removed_at IS NULL", user_uuid)
    except Exception:
        pass
    return {"ok": True}


async def panel_reissue_subscription(ctx, user_uuid: str) -> dict:
    """Panel revoke (the 'Full reissue' button on the edit-user page): new
    credentials, new subscription link. Returns the fresh URL best-effort:
    from the revoke response, then the local mirror. Never raises."""
    url = None
    try:
        client = _panel_client()
        res = client.revoke_user_subscription(_resolve_panel_uid(user_uuid), None, False)
        res = _unwrap(res)
        if isinstance(res, dict):
            cand = (res.get("user") if isinstance(res.get("user"), dict) else res) or {}
            for key in ("subscription_url", "subscriptionUrl", "subscription_page_url",
                        "subscriptionPageUrl"):
                v = cand.get(key) or (res.get(key) if isinstance(res, dict) else None)
                if v:
                    url = str(v)
                    break
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "url": None}
    if not url:
        try:
            row = await ctx.db.get_user_by_uuid(user_uuid)
            if row:
                url = row.get("subscription_url") or row.get("subscriptionUrl")
        except Exception:
            url = None
    return {"ok": True, "url": str(url) if url else None}


# ---------------------------------------------------------------------------
# Public catalog API: per-shop read-only key so owners can reuse exactly what
# buyers see in Telegram (tiers, packages, services, prices) on their own
# website/widget. Key lookup is by SHA-256 hash; the raw key is stored
# encrypted (same Fernet as bot tokens) so the owner can view/copy it.

PUBLIC_CATALOG_PATH = "/api/v2/plugins/pershop/pub/catalog"


async def new_public_key(ctx) -> dict:
    """Generate a fresh external API key. Returns {key, key_hash, key_enc}."""
    import hashlib
    import secrets

    from rwa_plugin_pershop.crypto import encrypt_token

    key = secrets.token_urlsafe(32)
    return {"key": key,
            "key_hash": hashlib.sha256(key.encode()).hexdigest(),
            "key_enc": await encrypt_token(ctx, key)}


async def shop_by_public_key(ctx, key_hash: str) -> dict | None:
    if not key_hash:
        return None
    return await ctx.db.fetchrow(
        "SELECT * FROM plugin_pershop_shops WHERE public_key_hash = $1", key_hash)


async def verify_public_key(ctx, shop: dict, provided: str) -> bool:
    """Constant-time check of the raw key against the stored copy. Never raises."""
    import hmac

    try:
        if not shop or not shop.get("public_key_enc") or not provided:
            return False
        from rwa_plugin_pershop.crypto import decrypt_token

        return hmac.compare_digest(await decrypt_token(ctx, shop["public_key_enc"]), provided)
    except Exception:
        return False


async def public_catalog_url(ctx) -> str:
    try:
        base = (await ctx.settings.get("public_base_url", default="") or "").rstrip("/")
    except Exception:
        base = ""
    return f"{base}{PUBLIC_CATALOG_PATH}" if base else PUBLIC_CATALOG_PATH


async def shop_catalog(ctx, shop: dict) -> dict:
    """Allowlisted public snapshot: exactly what buyers see in the bot.
    Never exposes tokens, secrets, admin ids, buyers, orders or subs."""
    shop = shop or {}
    sid = shop.get("id")

    async def _safe_fetch(query: str):
        try:
            return await ctx.db.fetch(query, sid)
        except Exception:
            return []

    tiers = await _safe_fetch("SELECT * FROM plugin_pershop_tiers WHERE shop_id=$1 AND active")
    pkgs = await _safe_fetch("SELECT * FROM plugin_pershop_packages WHERE shop_id=$1 AND active")
    cats = await _safe_fetch("SELECT * FROM plugin_pershop_service_cats WHERE shop_id=$1 AND active")
    svcs = await _safe_fetch("SELECT * FROM plugin_pershop_services WHERE shop_id=$1 AND active")
    try:
        tutorial = await ctx.settings.get("tutorial_text", default="") or ""
        test_days = await ctx.settings.get("test_duration_days", default=1) or 1
        test_gb = await ctx.settings.get("test_traffic_gb", default=1) or 0
    except Exception:
        tutorial, test_days, test_gb = "", 1, 0

    tier_names = {t["id"]: t.get("name") for t in tiers}
    out_pkgs = []
    for p in sorted(pkgs, key=lambda r: (r.get("tier_id") or 0, r.get("id") or 0)):
        dur = p.get("duration_days") or 0
        tb = p.get("traffic_limit_bytes") or 0
        scope = p.get("scope_tier_id")
        out_pkgs.append({
            "id": p["id"], "name": p.get("name"),
            "tier_id": p.get("tier_id"), "tier_name": tier_names.get(p.get("tier_id")),
            "duration_days": dur, "timeless": not dur,
            "traffic_limit_bytes": tb or None,
            "traffic_limit_gb": round(tb / 1e9, 2) if tb else None,
            "hwid_mode": (p.get("hwid_mode") or "default"),
            "hwid_fixed": p.get("hwid_fixed"),
            "effect": (p.get("effect") or "full"),
            "traffic_reset": bool(p.get("traffic_reset", True)),
            "scope_tier_id": scope, "scope_tier_name": tier_names.get(scope),
            "price_units": _units(p.get("price_units")),
            "price_display": format_price(shop, p.get("price_units")),
            "currency_label": (shop.get("currency_label") or ""),
        })
    cat_names = {c["id"]: c.get("name") for c in cats}
    out_svcs = [
        {"id": s["id"], "name": s.get("name"),
         "cat_id": s.get("cat_id"), "cat_name": cat_names.get(s.get("cat_id")),
         "description": s.get("description") or "",
         "price_units": _units(s.get("price_units")),
         "price_display": format_price(shop, s.get("price_units")),
         "currency_label": (shop.get("currency_label") or "")}
        for s in sorted(svcs, key=lambda r: (r.get("cat_id") or 0, r.get("id") or 0))
    ]
    return {
        "shop": {"id": sid, "prefix": shop.get("prefix"),
                 "bot_username": shop.get("bot_username"), "lang": shop.get("lang") or "en",
                 "currency_label": shop.get("currency_label") or "",
                 "unit_rate": _units(shop.get("unit_rate")),
                 "welcome_text": shop.get("welcome_text") or "",
                 "referral_text": shop.get("referral_text") or "",
                 "support_text": shop.get("support_text") or ""},
        "trial": {"enabled": bool(shop.get("test_enabled")),
                  "duration_days": test_days, "traffic_gb": test_gb},
        "tutorial_text": tutorial,
        "tiers": [{"id": t["id"], "name": t.get("name")} for t in tiers],
        "packages": out_pkgs,
        "service_categories": [{"id": c["id"], "name": c.get("name")} for c in cats],
        "services": out_svcs,
    }


# ---------------------------------------------------------------------------
# External admin user API: a trusted integration acts with the shop owner's
# hands — create/read/update panel users. Everything is bounded by the
# owner's RBAC (squad view scope, traffic policy, quota counters) and every
# created user is stamped users.created_by_admin_id, so the owner sees them
# in their users list and the plugin stats count them (subs_manual).
# Raises ValueError on bad input / foreign users, RuntimeError when the
# panel or a quota gate refuses.

_API_USER_FIELDS = ("uuid", "username", "status", "expire_at",
                    "traffic_limit_bytes", "used_traffic_bytes",
                    "hwid_device_limit", "telegram_id", "created_by_admin_id")

_UNSET = object()


def _api_public_user(row: dict) -> dict:
    r = row or {}
    return {
        "uuid": r.get("uuid"),
        "username": r.get("username"),
        "status": r.get("status"),
        "expire_at": r.get("expire_at", r.get("expireAt")),
        "traffic_limit_bytes": r.get("traffic_limit_bytes", r.get("trafficLimitBytes")),
        "used_traffic_bytes": r.get("used_traffic_bytes", r.get("usedTrafficBytes")) or 0,
        "hwid_device_limit": r.get("hwid_device_limit", r.get("hwidDeviceLimit")),
        "telegram_id": r.get("telegram_id", r.get("telegramId")),
        "created_by_admin_id": r.get("created_by_admin_id", r.get("createdByAdminId")),
    }


async def _api_admin_ns(ctx, shop):
    """Namespace mirroring the shop owner for RBAC calls, or None for a
    row-less owner (the panel treats account-less admins as unrestricted).
    Raises ValueError when the lookup itself crashes — fail closed."""
    ok, row = await _owner_account_row(ctx, shop.get("owner_admin"))
    if not ok:
        raise ValueError("owner scope unavailable")
    if row is None:
        return None
    import types

    return types.SimpleNamespace(account_id=row.get("id"),
                                 role_id=row.get("role_id"),
                                 role=row.get("role_name"))


async def _api_assert_owner_user(ctx, shop, user_uuid: str):
    """Users row + owner namespace, enforcing attribution: the creator must
    be the owner's account, or the user creator-less with a row-less owner.
    Raises ValueError (not found / foreign / scope unavailable)."""
    ns = await _api_admin_ns(ctx, shop)
    owner_id = getattr(ns, "account_id", None) if ns is not None else None
    row = await ctx.db.fetchrow("SELECT * FROM users WHERE uuid=$1", user_uuid)
    if not row:
        raise ValueError("user not found")
    creator = row.get("created_by_admin_id", row.get("createdByAdminId"))
    try:
        creator = int(creator) if creator is not None else None
    except (TypeError, ValueError):
        creator = None
    if creator is not None:
        if owner_id is None or creator != int(owner_id):
            raise ValueError("foreign user")
    elif ns is not None:
        raise ValueError("foreign user")
    return row, ns


async def _api_check_squad(ctx, ns, squad_uuid: str | None) -> None:
    """Squad assignment must sit inside the owner's RBAC view scope.
    Permissive when the RBAC layer is unreachable (same default as _owner_policy)."""
    if not squad_uuid:
        return
    try:
        from web.backend.core.rbac import check_access
    except Exception:
        return
    try:
        ok = await check_access(ns, "squad", str(squad_uuid), "view")
    except Exception:
        return
    if not ok:
        raise ValueError("squad outside owner scope")


async def _charge_traffic(ctx, account_id: int, delta_bytes: int) -> bool:
    try:
        from web.backend.core.admin_accounts import increment_usage_counter
    except ImportError:
        return True
    try:
        return bool(await increment_usage_counter(account_id, "traffic_used_bytes", delta_bytes))
    except Exception as e:
        ctx.logger.error("shop traffic quota charge failed: %s", e)
        return True


def _as_aware_api(exp) -> datetime:
    if not isinstance(exp, datetime):
        raise ValueError("expire_at must be an ISO datetime")
    return exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)


async def api_list_users(ctx, shop: dict, *, limit: int = 50,
                         offset: int = 0, search: str = "") -> list[dict]:
    """Panel users created by this shop's owner (newest RBAC visibility
    applied on top). Raises ValueError when the owner scope is unavailable."""
    ns = await _api_admin_ns(ctx, shop)
    owner_id = getattr(ns, "account_id", None) if ns is not None else None
    try:
        limit = max(1, min(int(limit or 50), 200))
    except (TypeError, ValueError):
        limit = 50
    try:
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        offset = 0
    rows = await ctx.db.fetch(
        """SELECT * FROM users WHERE created_by_admin_id IS NOT DISTINCT FROM $1
           AND username ILIKE $2 ORDER BY username LIMIT $3 OFFSET $4""",
        owner_id, f"%{search or ''}%", limit, offset)
    try:
        from web.backend.core.rbac import get_visible_user_uuids
        visible = await get_visible_user_uuids(ns)
    except Exception:
        visible = None
    if visible is not None:
        rows = [r for r in rows if str(r.get("uuid")) in visible]
    return [_api_public_user(r) for r in rows]


async def api_get_user(ctx, shop: dict, user_uuid: str) -> dict:
    row, _ns = await _api_assert_owner_user(ctx, shop, user_uuid)
    return _api_public_user(row)


async def api_create_user(ctx, shop: dict, *, username: str, expire_at: datetime,
                          traffic_limit_bytes: int | None = None,
                          telegram_id: int | None = None,
                          squad_uuid: str | None = None,
                          hwid_device_limit: int | None = None) -> dict:
    """Free-form user (no package needed) created with the owner's hands:
    prefix-enforced username, owner traffic policy + quota gates, owner
    attribution stamp, plus a plan-less subs row so stats/matching/notifier
    pick it up. Raises ValueError / RuntimeError."""
    prefix = (shop.get("prefix") or "").strip()
    uname = (username or "").strip()
    if not prefix or not uname.lower().startswith(prefix.lower() + "-"):
        raise ValueError("username must start with '<prefix>-'")
    exp = _as_aware_api(expire_at)
    ns = await _api_admin_ns(ctx, shop)
    await _api_check_squad(ctx, ns, squad_uuid)
    created = await panel_create_user(
        ctx, username=uname, expire_at=exp,
        traffic_limit_bytes=traffic_limit_bytes, telegram_id=telegram_id,
        squad_uuid=(squad_uuid or "").strip() or None,
        hwid_device_limit=hwid_device_limit,
        owner_admin=shop.get("owner_admin"),
        description=f"pershop:{prefix} api")
    uuid = str(created.get("uuid") or "")
    sub_id = None
    try:
        row = await ctx.db.fetchrow(
            """INSERT INTO plugin_pershop_subs(shop_id,buyer_tg_id,user_uuid,username,expire_at,
               traffic_limit_bytes,package_id) VALUES($1,$2,$3,$4,$5,$6,$7) RETURNING id""",
            shop["id"], telegram_id, uuid, uname, exp, traffic_limit_bytes, None)
        sub_id = row["id"] if row else None
    except Exception as e:
        ctx.logger.error("shop api-create subs row failed for %s: %s", uname, e)
    return {"uuid": uuid, "username": uname,
            "subscription_url": created.get("subscription_url"), "sub_id": sub_id}


async def api_update_user(ctx, shop: dict, user_uuid: str, *,
                          expire_at=_UNSET,
                          traffic_limit_bytes=_UNSET,
                          hwid_device_limit=_UNSET,
                          telegram_id=_UNSET,
                          squad_uuid=_UNSET,
                          status=_UNSET) -> dict:
    """Patch an owner-created user. Omitted fields are untouched; explicit
    null traffic/hwid/telegram means 'leave it' (the core drops nulls).
    Traffic increases charge the owner's quota (reverted + refused when the
    gate shuts). Squad moves re-check the owner scope. Raises
    ValueError / RuntimeError."""
    prow, ns = await _api_assert_owner_user(ctx, shop, user_uuid)
    try:
        from shared.data_access import resolve_panel_user_id
        panel_id = await resolve_panel_user_id(user_uuid)
    except Exception:
        panel_id = user_uuid
    owner_id = getattr(ns, "account_id", None) if ns is not None else None
    fields: dict = {}
    new_expire = prow.get("expire_at")
    new_traffic = prow.get("traffic_limit_bytes")
    if expire_at is not _UNSET:
        new_expire = _as_aware_api(expire_at)
        fields["expireAt"] = new_expire.isoformat()
    if traffic_limit_bytes is not _UNSET and traffic_limit_bytes is not None:
        role_name, policy = await _owner_policy(shop.get("owner_admin"))
        if role_name != "superadmin":
            if policy == "enforced":
                raise ValueError("owner traffic policy forbids limits")
        try:
            new_traffic = int(traffic_limit_bytes)
        except (TypeError, ValueError):
            raise ValueError("traffic_limit_bytes must be a number")
        if new_traffic < 0:
            raise ValueError("traffic_limit_bytes must be >= 0")
        fields["trafficLimitBytes"] = new_traffic
    elif traffic_limit_bytes is not _UNSET and traffic_limit_bytes is None:
        role_name, policy = await _owner_policy(shop.get("owner_admin"))
        if role_name != "superadmin" and policy == "disabled":
            raise ValueError("owner traffic policy requires a limit")
    if hwid_device_limit is not _UNSET and hwid_device_limit is not None:
        try:
            fields["hwidDeviceLimit"] = int(hwid_device_limit)
        except (TypeError, ValueError):
            raise ValueError("hwid_device_limit must be a number")
    if telegram_id is not _UNSET and telegram_id is not None:
        fields["telegramId"] = int(telegram_id)
    if squad_uuid is not _UNSET:
        su = (squad_uuid or "").strip()
        if su:
            await _api_check_squad(ctx, ns, su)
            fields["activeInternalSquads"] = [su]
        else:
            fields["activeInternalSquads"] = []
    if status is not _UNSET and status is not None:
        s = str(status).strip().lower()
        if s not in ("active", "disabled"):
            raise ValueError("status must be active|disabled")
        fields["status"] = s.upper()
    if not fields:
        raise ValueError("nothing to update")
    await _panel_client().update_user(panel_id, **fields)
    if "trafficLimitBytes" in fields:
        old = prow.get("traffic_limit_bytes") or 0
        delta = (fields["trafficLimitBytes"] or 0) - old
        if delta > 0 and owner_id is not None:
            if not await _charge_traffic(ctx, int(owner_id), delta):
                try:
                    if old:
                        await _panel_client().update_user(panel_id, trafficLimitBytes=old)
                except Exception:
                    pass
                raise RuntimeError("owner quota exceeded")
    try:
        await ctx.db.execute(
            "UPDATE plugin_pershop_subs SET expire_at=$1, traffic_limit_bytes=$2 WHERE shop_id=$3 AND user_uuid=$4",
            new_expire, new_traffic, shop["id"], user_uuid)
    except Exception as e:
        ctx.logger.error("shop api-update subs sync failed for %s: %s", user_uuid, e)
    return {"uuid": user_uuid, "username": prow.get("username"),
            "updated": sorted(fields)}
