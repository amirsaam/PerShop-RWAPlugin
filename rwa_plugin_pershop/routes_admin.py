"""Admin CRUD + approve/deny + /app bundle. Every route requires shop permission."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import Request
from pydantic import BaseModel, Field


class ShopIn(BaseModel):
    bot_token: str | None = None
    admin_tg_id: int
    prefix: str
    lang: str = "en"
    card_text: str = ""
    welcome_text: str = ""
    thank_you_text: str = ""
    approved_text: str = ""
    denied_text: str = ""
    currency_label: str = ""
    unit_rate: float | None = Field(default=None, gt=0)
    test_enabled: bool = False
    referral_text: str = ""
    # % of each invited buyer's purchase credited to the inviter (0 = off).
    referral_percent: float = Field(default=0, ge=0, le=100)
    support_text: str = ""
    # Hours a pending receipt may wait before the owner gets a Telegram
    # nudge (0 = off). Re-nudged every window until decided.
    pending_nag_hours: int = Field(default=6, ge=0, le=168)
    # True = tiers stay an internal grouping/squad tool; buyers see one flat
    # package list after pressing Buy instead of tier buttons.
    tiers_internal: bool = False


class PluginSettingsIn(BaseModel):
    polling_enabled: bool = False
    tutorial_text: str = ""
    test_duration_days: int = Field(default=1, ge=1, le=365)
    test_traffic_gb: int = Field(default=1, ge=0, le=100000)
    test_squad_uuid: str = ""
    test_hwid: int | None = Field(default=None, ge=0, le=100000)
    public_base_url: str = ""


class PkgIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # Every package lives in a tier (tier = internal squad mapping; a squad-less
    # sub gets no connection, so tier-less packages are rejected).
    tier_id: int
    # Show order in the bot (ORDER BY sort, id).
    sort: int = Field(default=0, ge=0, le=1000000)
    traffic_limit_bytes: int | None = Field(default=None, ge=0)
    # 0/blank = timeless package (no expiry, subs written as year 2999).
    duration_days: int = Field(default=30, ge=0, le=3650)
    price_units: float = Field(default=0, ge=0)
    squad_uuid: str | None = None
    # effect: full = plan buy/renew, time_only = +days top-up offer,
    # traffic_only = +GB top-up offer (top-ups only extend existing subs).
    # hwid toggle: default (panel default) | fixed (admin value) |
    # input (buyer picks in bot) | unlimited (no cap)
    hwid_mode: str = "default"
    hwid_min: int = Field(default=1, ge=0)
    hwid_default: int = Field(default=1, ge=0)
    hwid_max: int = Field(default=10, ge=0)
    hwid_free: int = Field(default=1, ge=0)
    hwid_percent: float = Field(default=50, ge=0)
    hwid_fixed: int | None = Field(default=None, ge=0)
    # renew traffic math: True = cap resets to the package limit,
    # False = leftover rolls over, package traffic is added on top.
    traffic_reset: bool = True
    effect: str = "full"
    # Scope for top-up offers: NULL = offered for every tier's subs,
    # else only for subs matched to that tier's plan.
    scope_tier_id: int | None = None
    active: bool = True


class TierIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    squad_uuid: str | None = None
    hwid_default: int | None = Field(default=None, ge=0)
    sort: int = 0
    active: bool = True


class SvcCatIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    sort: int = 0
    active: bool = True


class SvcIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    cat_id: int | None = None
    price_units: float = Field(default=0, ge=0)
    description: str = Field(default="", max_length=2000)
    done_text: str = Field(default="", max_length=2000)
    sort: int = 0
    active: bool = True


class DenyIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


class PubUserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    expire_at: datetime
    traffic_limit_bytes: int | None = Field(default=None, ge=0)
    hwid_device_limit: int | None = Field(default=None, ge=0)
    telegram_id: int | None = None
    squad_uuid: str | None = None


class PubUserUpdate(BaseModel):
    expire_at: datetime | None = None
    traffic_limit_bytes: int | None = Field(default=None, ge=0)
    hwid_device_limit: int | None = Field(default=None, ge=0)
    telegram_id: int | None = None
    squad_uuid: str | None = None
    status: str | None = None


class AssignIn(BaseModel):
    user_uuid: str = Field(min_length=1)
    package_id: int


def build_admin_router(ctx):
    from datetime import timezone

    from fastapi import APIRouter, Depends, HTTPException, Response
    from web.backend.core.plugin_api import auth_deps

    from rwa_plugin_pershop.crypto import decrypt_token, encrypt_token
    from rwa_plugin_pershop.naming import sanitize_fragment
    from rwa_plugin_pershop.service import api_create_user, api_get_user, api_list_users, api_update_user, approve_order, assign_plan, deny_order, new_public_key, public_catalog_url, queue_broadcast, shop_by_id, shop_by_public_key, shop_catalog, shop_for_owner, shop_for_tg, unmapped_users, verify_public_key

    AdminUser, require_permission = auth_deps()
    router = APIRouter()

    def _check_hwid(payload: PkgIn) -> None:
        from rwa_plugin_pershop.service import EFFECTS, HWID_MODES

        if payload.hwid_mode not in HWID_MODES:
            raise HTTPException(400, "hwid_mode must be default|fixed|input|unlimited")
        if payload.hwid_mode == "fixed" and payload.hwid_fixed is None:
            raise HTTPException(400, "fixed hwid needs hwid_fixed")
        if payload.effect not in EFFECTS:
            raise HTTPException(400, "effect must be full|time_only|traffic_only")
        if payload.effect == "time_only" and payload.duration_days <= 0:
            raise HTTPException(400, "time top-up needs duration_days > 0")
        if (payload.effect == "traffic_only"
                and not (payload.traffic_limit_bytes or 0) > 0):
            raise HTTPException(400, "traffic top-up needs a traffic limit")

    def owner(admin) -> str:
        return getattr(admin, "username", None) or getattr(admin, "login", None) or str(getattr(admin, "id", "admin"))

    def _clean_prefix(raw: str) -> str:
        clean = sanitize_fragment(raw)[:24]
        if not clean or clean == "user":
            raise HTTPException(400, "prefix must contain letters/digits")
        return clean

    @router.get("/shops/mine")
    async def shops_mine(_a: AdminUser = Depends(require_permission("pershop", "view"))):
        me = owner(_a)
        row = await shop_for_owner(ctx, me)
        if not row:
            return {"shop": None}
        row = dict(row)
        has_token = row.get("bot_token_enc") is not None
        row.pop("bot_token_enc", None)
        row["has_token"] = has_token
        row["webhook_configured"] = bool(row.get("webhook_secret")) and has_token
        return {"shop": row}

    @router.get("/shops/default-texts")
    async def shop_default_texts(_a: AdminUser = Depends(require_permission("pershop", "view"))):
        """Builtin defaults for every owner-editable bot text, in the shop's
        language (or ?lang= override). Powers the ↺ Default buttons."""
        from rwa_plugin_pershop.lang import L

        me = owner(_a)
        shop = await shop_for_owner(ctx, me) or {}
        lang = (shop.get("lang") or "en") if isinstance(shop, dict) else "en"
        probe = {"lang": lang}
        return {"defaults": {
            "welcome": L(probe, "menu_title"),
            "card": "",
            "thank_you": L(probe, "tpl_thank_you"),
            "approved": L(probe, "tpl_approved"),
            "denied": L(probe, "tpl_denied"),
            "referral": "",
            "support": "",
        }}

    @router.put("/shops/mine")
    async def shops_upsert(payload: ShopIn, _a: AdminUser = Depends(require_permission("pershop", "edit"))):
        import secrets

        from rwa_plugin_pershop.service import register_webhook

        me = owner(_a)
        prefix = _clean_prefix(payload.prefix)
        from rwa_plugin_pershop.lang import normalize_lang

        lang = normalize_lang(payload.lang)
        cur = await shop_for_owner(ctx, me)
        token_given = bool(payload.bot_token and payload.bot_token.strip())
        # Token is optional: the shop (texts, currency, catalog…) can be
        # fully configured before any bot is connected, or after a
        # disconnect. Webhook registration only runs when a token is given.
        token_enc = await encrypt_token(ctx, payload.bot_token) if token_given else (cur["bot_token_enc"] if cur else None)
        secret = (cur and cur.get("webhook_secret")) or secrets.token_urlsafe(24)
        try:
            if cur:
                await ctx.db.execute(
                    """UPDATE plugin_pershop_shops SET bot_token_enc=$1, admin_tg_id=$2, prefix=$3,
                       card_text=$4, welcome_text=$5, webhook_secret=$6,
                       thank_you_text=$7, approved_text=$8, denied_text=$9, lang=$10,
                       currency_label=$11, unit_rate=$12, test_enabled=$13,
                       referral_text=$14, support_text=$15, pending_nag_hours=$16,
                       referral_percent=$17, tiers_internal=$18 WHERE id=$19""",
                    token_enc, payload.admin_tg_id, prefix,
                    payload.card_text, payload.welcome_text, secret,
                    payload.thank_you_text, payload.approved_text, payload.denied_text, lang,
                    payload.currency_label.strip(), payload.unit_rate,
                    payload.test_enabled, payload.referral_text, payload.support_text,
                    payload.pending_nag_hours, payload.referral_percent,
                    payload.tiers_internal, cur["id"],
                )
                shop_id = cur["id"]
            else:
                row = await ctx.db.fetchrow(
                    """INSERT INTO plugin_pershop_shops(owner_admin, bot_token_enc, admin_tg_id, prefix, card_text,
                       welcome_text, webhook_secret, thank_you_text, approved_text, denied_text, lang,
                       currency_label, unit_rate, test_enabled, referral_text, support_text, pending_nag_hours,
                       referral_percent, tiers_internal)
                       VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19) RETURNING id""",
                    me, token_enc, payload.admin_tg_id, prefix, payload.card_text, payload.welcome_text, secret,
                    payload.thank_you_text, payload.approved_text, payload.denied_text, lang,
                    payload.currency_label.strip(), payload.unit_rate,
                    payload.test_enabled, payload.referral_text, payload.support_text,
                    payload.pending_nag_hours, payload.referral_percent, payload.tiers_internal,
                )
                shop_id = row["id"]
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                raise HTTPException(409, "prefix already taken")
            raise
        # One shop per admin, token in hand: point the bot at this panel ourselves.
        # Never fails the save — the owner can retry from the Connect button.
        webhook = {"ok": None, "skipped": True}
        if token_given:
            fresh = await shop_by_id(ctx, shop_id)
            if fresh:
                webhook = await register_webhook(ctx, dict(fresh))
        return {"id": shop_id, "webhook_secret": secret, "webhook": webhook}

    async def _pub_shop(request: Request) -> dict:
        """Shop resolved from its external API key (X-Shop-Key header or
        ?key=). 401 when missing/unknown. No admin auth by design."""
        import hashlib

        provided = (request.headers.get("x-shop-key")
                    or request.query_params.get("key") or "")
        digest = hashlib.sha256(provided.encode()).hexdigest() if provided else ""
        shop = await shop_by_public_key(ctx, digest)
        if not shop or not await verify_public_key(ctx, shop, provided):
            raise HTTPException(401, "invalid key")
        return dict(shop)

    def _aware(dt: datetime) -> datetime:
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    @router.get("/pub/catalog")
    async def pub_catalog(request: Request):
        """Public per-shop catalog: exactly what buyers see in the bot
        (tiers, packages, services, converted prices). Auth is the shop's own
        external API key: X-Shop-Key header or ?key= query. No admin auth."""
        return await shop_catalog(ctx, await _pub_shop(request))

    @router.get("/pub/users")
    async def pub_users(request: Request, limit: int = 50,
                        offset: int = 0, search: str = ""):
        """Panel users created by this shop's owner (owner RBAC visibility
        applied). Key auth only."""
        try:
            return {"users": await api_list_users(
                ctx, await _pub_shop(request),
                limit=limit, offset=offset, search=search)}
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.post("/pub/users", status_code=201)
    async def pub_users_create(payload: PubUserCreate, request: Request):
        """Create a panel user with the owner's hands: prefix-enforced
        username, owner traffic policy + quota gates, owner attribution, and
        a plan-less plugin row (stats/matching/notifier pick it up)."""
        shop = await _pub_shop(request)
        try:
            return await api_create_user(
                ctx, shop, username=payload.username,
                expire_at=_aware(payload.expire_at),
                traffic_limit_bytes=payload.traffic_limit_bytes,
                telegram_id=payload.telegram_id,
                squad_uuid=(payload.squad_uuid or "").strip() or None,
                hwid_device_limit=payload.hwid_device_limit)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except RuntimeError as e:
            raise HTTPException(422, str(e))

    @router.get("/pub/users/{user_uuid}")
    async def pub_user_get(user_uuid: str, request: Request):
        shop = await _pub_shop(request)
        try:
            return {"user": await api_get_user(ctx, shop, user_uuid)}
        except ValueError:
            raise HTTPException(404, "user not found")

    @router.patch("/pub/users/{user_uuid}")
    async def pub_user_patch(user_uuid: str, payload: PubUserUpdate,
                             request: Request):
        """Patch an owner-created user. Only provided non-null fields change;
        squad moves re-check the owner scope, traffic increases charge quota."""
        shop = await _pub_shop(request)
        given = payload.model_fields_set
        kwargs: dict = {}
        if "expire_at" in given and payload.expire_at is not None:
            kwargs["expire_at"] = _aware(payload.expire_at)
        for key in ("traffic_limit_bytes", "hwid_device_limit", "telegram_id"):
            if key in given and getattr(payload, key) is not None:
                kwargs[key] = getattr(payload, key)
        if "squad_uuid" in given and payload.squad_uuid is not None:
            kwargs["squad_uuid"] = payload.squad_uuid
        if "status" in given and payload.status is not None:
            kwargs["status"] = payload.status
        if not kwargs:
            raise HTTPException(400, "nothing to update")
        try:
            return await api_update_user(ctx, shop, user_uuid, **kwargs)
        except ValueError as e:
            msg = str(e)
            if msg in ("user not found", "foreign user"):
                raise HTTPException(404, "user not found")
            raise HTTPException(400, msg)
        except RuntimeError as e:
            raise HTTPException(422, str(e))

    @router.get("/shops/mine/apikey")
    async def shops_apikey(_a: AdminUser = Depends(require_permission("pershop", "view"))):
        """Show this shop's external API key (owner-only) + endpoint URL."""
        from rwa_plugin_pershop.crypto import decrypt_token

        shop = await shop_for_owner(ctx, owner(_a))
        if not shop:
            raise HTTPException(404, "no shop yet")
        key = None
        if shop.get("public_key_enc"):
            try:
                key = await decrypt_token(ctx, shop["public_key_enc"])
            except Exception:
                key = None
        return {"enabled": bool(key), "key": key, "url": await public_catalog_url(ctx)}

    @router.post("/shops/mine/apikey/rotate")
    async def shops_apikey_rotate(_a: AdminUser = Depends(require_permission("pershop", "edit"))):
        """(Re)generate this shop's external API key. Old key stops working."""
        shop = await shop_for_owner(ctx, owner(_a))
        if not shop:
            raise HTTPException(404, "no shop yet")
        fresh = await new_public_key(ctx)
        await ctx.db.execute(
            "UPDATE plugin_pershop_shops SET public_key_hash=$1, public_key_enc=$2 WHERE id=$3",
            fresh["key_hash"], fresh["key_enc"], shop["id"])
        return {"key": fresh["key"], "url": await public_catalog_url(ctx)}

    @router.delete("/shops/mine/apikey")
    async def shops_apikey_revoke(_a: AdminUser = Depends(require_permission("pershop", "edit"))):
        """Revoke this shop's external API key (endpoint goes 401)."""
        shop = await shop_for_owner(ctx, owner(_a))
        if not shop:
            raise HTTPException(404, "no shop yet")
        await ctx.db.execute(
            "UPDATE plugin_pershop_shops SET public_key_hash=NULL, public_key_enc=NULL WHERE id=$1",
            shop["id"])
        return {"enabled": False}

    @router.post("/shops/mine/rotate-secret")
    async def shops_rotate_secret(_a: AdminUser = Depends(require_permission("pershop", "edit"))):
        import secrets

        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "create shop first")
        secret = secrets.token_urlsafe(24)
        await ctx.db.execute("UPDATE plugin_pershop_shops SET webhook_secret=$1 WHERE id=$2", secret, shop["id"])
        return {"webhook_secret": secret}

    @router.post("/shops/mine/webhook")
    async def shops_connect(_a: AdminUser = Depends(require_permission("pershop", "edit"))):
        """(Re)point the owner's bot at this panel + report live Telegram state."""
        from rwa_plugin_pershop.service import register_webhook, webhook_status

        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "create shop first")
        result = await register_webhook(ctx, dict(shop))
        status = await webhook_status(ctx, dict(shop)) if result.get("ok") else {"ok": False}
        return {"webhook": result, "status": status}

    @router.post("/shops/mine/disconnect")
    async def shops_disconnect(_a: AdminUser = Depends(require_permission("pershop", "edit"))):
        """Detach the shop's bot (deleteWebhook + clear stored token).
        Texts, currency, catalog and buyers stay untouched."""
        from rwa_plugin_pershop.service import disconnect_shop

        return await disconnect_shop(ctx, owner(_a))

    @router.get("/packages")
    async def pkgs(_a: AdminUser = Depends(require_permission("pershop", "view"))):
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            return {"packages": []}
        rows = await ctx.db.fetch("SELECT * FROM plugin_pershop_packages WHERE shop_id=$1 ORDER BY sort, id", shop["id"])
        return {"packages": [dict(r) for r in rows]}

    @router.post("/packages")
    async def pkg_create(payload: PkgIn, _a: AdminUser = Depends(require_permission("pershop", "edit"))):
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "create shop first")
        if not payload.tier_id:
            raise HTTPException(400, "tier required")
        tier = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_tiers WHERE id=$1 AND shop_id=$2", payload.tier_id, shop["id"])
        if not tier:
            raise HTTPException(400, "tier not found")
        _check_hwid(payload)
        if payload.scope_tier_id:
            scope = await ctx.db.fetchrow(
                "SELECT * FROM plugin_pershop_tiers WHERE id=$1 AND shop_id=$2",
                payload.scope_tier_id, shop["id"])
            if not scope:
                raise HTTPException(400, "scope tier not found")
        row = await ctx.db.fetchrow(
            """INSERT INTO plugin_pershop_packages(shop_id,tier_id,name,traffic_limit_bytes,duration_days,
               price_units,squad_uuid,active,hwid_mode,hwid_min,hwid_default,hwid_max,
               hwid_free,hwid_percent,hwid_fixed,traffic_reset,effect,scope_tier_id,sort)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19) RETURNING id""",
            shop["id"], payload.tier_id, payload.name.strip(), payload.traffic_limit_bytes,
            payload.duration_days, payload.price_units, payload.squad_uuid, payload.active,
            payload.hwid_mode, payload.hwid_min, payload.hwid_default, payload.hwid_max,
            payload.hwid_free, payload.hwid_percent, payload.hwid_fixed, payload.traffic_reset,
            payload.effect, payload.scope_tier_id, payload.sort,
        )
        return {"id": row["id"]}

    @router.put("/packages/{pkg_id}")
    async def pkg_update(pkg_id: int, payload: PkgIn, _a: AdminUser = Depends(require_permission("pershop", "edit"))):
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "no shop")
        if not payload.tier_id:
            raise HTTPException(400, "tier required")
        tier = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_tiers WHERE id=$1 AND shop_id=$2", payload.tier_id, shop["id"])
        if not tier:
            raise HTTPException(400, "tier not found")
        _check_hwid(payload)
        if payload.scope_tier_id:
            scope = await ctx.db.fetchrow(
                "SELECT * FROM plugin_pershop_tiers WHERE id=$1 AND shop_id=$2",
                payload.scope_tier_id, shop["id"])
            if not scope:
                raise HTTPException(400, "scope tier not found")
        await ctx.db.execute(
            """UPDATE plugin_pershop_packages SET tier_id=$1, name=$2, traffic_limit_bytes=$3, duration_days=$4,
               price_units=$5, squad_uuid=$6, active=$7, hwid_mode=$8, hwid_min=$9, hwid_default=$10,
               hwid_max=$11, hwid_free=$12, hwid_percent=$13, hwid_fixed=$14, traffic_reset=$15,
               effect=$16, scope_tier_id=$17, sort=$18
               WHERE id=$19 AND shop_id=$20""",
            payload.tier_id, payload.name.strip(), payload.traffic_limit_bytes, payload.duration_days,
            payload.price_units, payload.squad_uuid, payload.active, payload.hwid_mode,
            payload.hwid_min, payload.hwid_default, payload.hwid_max, payload.hwid_free,
            payload.hwid_percent, payload.hwid_fixed, payload.traffic_reset, payload.effect,
            payload.scope_tier_id, payload.sort, pkg_id, shop["id"],
        )
        return {"ok": True}

    @router.delete("/packages/{pkg_id}")
    async def pkg_delete(pkg_id: int, _a: AdminUser = Depends(require_permission("pershop", "edit"))):
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "no shop")
        used = await ctx.db.fetchrow(
            "SELECT id FROM plugin_pershop_orders WHERE package_id=$1 LIMIT 1", pkg_id)
        if used:
            raise HTTPException(409, "package has orders")
        planned = await ctx.db.fetchrow(
            "SELECT id FROM plugin_pershop_subs WHERE package_id=$1 LIMIT 1", pkg_id)
        if planned:
            raise HTTPException(409, "package is mapped to subscriptions")
        await ctx.db.execute("DELETE FROM plugin_pershop_packages WHERE id=$1 AND shop_id=$2", pkg_id, shop["id"])
        return {"ok": True}

    @router.get("/unmapped")
    async def unmapped(_a: AdminUser = Depends(require_permission("pershop", "edit"))):
        """Owner-side plan matching pool: panel users created by this owner
        with no subs row yet, plus subs rows with no plan."""
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "no shop")
        return await unmapped_users(ctx, shop)

    @router.post("/subs/assign-plan")
    async def subs_assign(payload: AssignIn, _a: AdminUser = Depends(require_permission("pershop", "edit"))):
        """Owner matches one of their users to a shop package (quick-renew plan)."""
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "no shop")
        try:
            res = await assign_plan(ctx, shop, payload.user_uuid.strip(), payload.package_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        try:  # Buyer opted into renewal: tell them the moment it's unlocked.
            if res.get("buyer_tg_id"):
                from rwa_plugin_pershop.crypto import decrypt_token
                from rwa_plugin_pershop.lang import L
                from rwa_plugin_pershop.telegram import TgClient

                btg = TgClient(await decrypt_token(ctx, shop["bot_token_enc"]))
                bshop = await shop_for_tg(ctx, shop, res["buyer_tg_id"])
                await btg.send_message(res["buyer_tg_id"],
                    L(bshop, "plan_unlocked", username=res["username"]))
        except Exception as e:
            ctx.logger.warning("shop.unlock_dm_failed", extra={"err": str(e)})
        return res

    async def _owned_tier(shop_id: int, tier_id: int):
        tier = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_tiers WHERE id=$1 AND shop_id=$2", tier_id, shop_id)
        if not tier:
            raise HTTPException(404, "tier not found")
        return tier

    async def _owned_scat(shop_id: int, cat_id: int):
        cat = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_service_cats WHERE id=$1 AND shop_id=$2", cat_id, shop_id)
        if not cat:
            raise HTTPException(404, "service category not found")
        return cat

    async def _owned_svc(shop_id: int, svc_id: int):
        svc = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_services WHERE id=$1 AND shop_id=$2", svc_id, shop_id)
        if not svc:
            raise HTTPException(404, "service not found")
        return svc

    @router.get("/squads")
    async def squads(_a: AdminUser = Depends(require_permission("pershop", "view"))):
        from rwa_plugin_pershop.service import list_squads

        squads = await list_squads()
        try:
            from web.backend.core.rbac import get_scope
            scope = await get_scope(_a, "squad", "view")
        except Exception:
            scope = None
        if scope is not None:
            squads = [s for s in squads if str(s.get("uuid", "")).lower() in scope]
        return {"squads": squads}

    @router.get("/tiers")
    async def tiers(_a: AdminUser = Depends(require_permission("pershop", "view"))):
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            return {"tiers": []}
        rows = await ctx.db.fetch(
            "SELECT * FROM plugin_pershop_tiers WHERE shop_id=$1 ORDER BY sort, id", shop["id"])
        return {"tiers": [dict(r) for r in rows]}

    @router.post("/tiers")
    async def tier_create(payload: TierIn, _a: AdminUser = Depends(require_permission("pershop", "edit"))):
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "create shop first")
        try:
            row = await ctx.db.fetchrow(
                """INSERT INTO plugin_pershop_tiers(shop_id,name,squad_uuid,hwid_default,sort,active)
                   VALUES($1,$2,$3,$4,$5,$6) RETURNING id""",
                shop["id"], payload.name.strip(), (payload.squad_uuid or "").strip(),
                payload.hwid_default, payload.sort, payload.active)
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                raise HTTPException(409, "tier name already used")
            raise
        return {"id": row["id"]}

    @router.put("/tiers/{tier_id}")
    async def tier_update(tier_id: int, payload: TierIn,
                          _a: AdminUser = Depends(require_permission("pershop", "edit"))):
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "no shop")
        await _owned_tier(shop["id"], tier_id)
        try:
            await ctx.db.execute(
                "UPDATE plugin_pershop_tiers SET name=$1, squad_uuid=$2, hwid_default=$3, sort=$4, active=$5 WHERE id=$6",
                payload.name.strip(), (payload.squad_uuid or "").strip(),
                payload.hwid_default, payload.sort, payload.active, tier_id)
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                raise HTTPException(409, "tier name already used")
            raise
        return {"ok": True}

    @router.delete("/tiers/{tier_id}")
    async def tier_delete(tier_id: int, _a: AdminUser = Depends(require_permission("pershop", "edit"))):
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "no shop")
        await _owned_tier(shop["id"], tier_id)
        used = await ctx.db.fetch(
            "SELECT id FROM plugin_pershop_packages WHERE tier_id=$1 LIMIT 1", tier_id)
        if used:
            raise HTTPException(409, "tier still has packages")
        await ctx.db.execute("DELETE FROM plugin_pershop_tiers WHERE id=$1", tier_id)
        return {"ok": True}

    @router.get("/service-cats")
    async def service_cats(_a: AdminUser = Depends(require_permission("pershop", "view"))):
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            return {"service_cats": []}
        rows = await ctx.db.fetch(
            "SELECT * FROM plugin_pershop_service_cats WHERE shop_id=$1 ORDER BY sort, id", shop["id"])
        return {"service_cats": [dict(r) for r in rows]}

    @router.post("/service-cats")
    async def scat_create(payload: SvcCatIn, _a: AdminUser = Depends(require_permission("pershop", "edit"))):
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "create shop first")
        try:
            row = await ctx.db.fetchrow(
                """INSERT INTO plugin_pershop_service_cats(shop_id,name,sort,active)
                   VALUES($1,$2,$3,$4) RETURNING id""",
                shop["id"], payload.name.strip(), payload.sort, payload.active)
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                raise HTTPException(409, "category name already used")
            raise
        return {"id": row["id"]}

    @router.put("/service-cats/{cat_id}")
    async def scat_update(cat_id: int, payload: SvcCatIn,
                         _a: AdminUser = Depends(require_permission("pershop", "edit"))):
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "no shop")
        await _owned_scat(shop["id"], cat_id)
        try:
            await ctx.db.execute(
                "UPDATE plugin_pershop_service_cats SET name=$1, sort=$2, active=$3 WHERE id=$4",
                payload.name.strip(), payload.sort, payload.active, cat_id)
        except Exception as e:
            if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                raise HTTPException(409, "category name already used")
            raise
        return {"ok": True}

    @router.delete("/service-cats/{cat_id}")
    async def scat_delete(cat_id: int, _a: AdminUser = Depends(require_permission("pershop", "edit"))):
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "no shop")
        await _owned_scat(shop["id"], cat_id)
        # Items survive as uncategorized (FK is ON DELETE SET NULL).
        await ctx.db.execute("DELETE FROM plugin_pershop_service_cats WHERE id=$1", cat_id)
        return {"ok": True}

    @router.get("/services")
    async def services(_a: AdminUser = Depends(require_permission("pershop", "view"))):
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            return {"services": []}
        rows = await ctx.db.fetch(
            "SELECT * FROM plugin_pershop_services WHERE shop_id=$1 ORDER BY sort, id", shop["id"])
        return {"services": [dict(r) for r in rows]}

    @router.post("/services")
    async def svc_create(payload: SvcIn, _a: AdminUser = Depends(require_permission("pershop", "edit"))):
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "create shop first")
        if payload.cat_id is not None:
            await _owned_scat(shop["id"], payload.cat_id)
        row = await ctx.db.fetchrow(
            """INSERT INTO plugin_pershop_services(shop_id,cat_id,name,price_units,description,done_text,sort,active)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id""",
            shop["id"], payload.cat_id, payload.name.strip(), payload.price_units,
            (payload.description or "").strip(), (payload.done_text or "").strip(),
            payload.sort, payload.active)
        return {"id": row["id"]}

    @router.put("/services/{svc_id}")
    async def svc_update(svc_id: int, payload: SvcIn,
                        _a: AdminUser = Depends(require_permission("pershop", "edit"))):
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "no shop")
        await _owned_svc(shop["id"], svc_id)
        if payload.cat_id is not None:
            await _owned_scat(shop["id"], payload.cat_id)
        await ctx.db.execute(
            """UPDATE plugin_pershop_services
               SET cat_id=$1, name=$2, price_units=$3, description=$4, done_text=$5, sort=$6, active=$7
               WHERE id=$8""",
            payload.cat_id, payload.name.strip(), payload.price_units,
            (payload.description or "").strip(), (payload.done_text or "").strip(),
            payload.sort, payload.active, svc_id)
        return {"ok": True}

    @router.delete("/services/{svc_id}")
    async def svc_delete(svc_id: int, _a: AdminUser = Depends(require_permission("pershop", "edit"))):
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "no shop")
        await _owned_svc(shop["id"], svc_id)
        used = await ctx.db.fetch(
            "SELECT id FROM plugin_pershop_orders WHERE service_id=$1 LIMIT 1", svc_id)
        if used:
            raise HTTPException(409, "service already has orders")
        await ctx.db.execute("DELETE FROM plugin_pershop_services WHERE id=$1", svc_id)
        return {"ok": True}

    @router.get("/orders")
    async def orders(status: str = "pending", _a: AdminUser = Depends(require_permission("pershop", "moderate"))):
        if status not in ("pending", "approved", "denied", "cancelled", "withdrawn", "all"):
            raise HTTPException(400, "status must be pending|approved|denied|cancelled|withdrawn|all")
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            return {"orders": []}
        if status == "all":
            rows = await ctx.db.fetch(
                """SELECT o.*, COALESCE(p.name, v.name) AS package_name,
                          COALESCE(p.price_units, v.price_units) AS package_units,
                          s.tg_id IS NOT NULL AS buyer_flagged
                   FROM plugin_pershop_orders o
                   LEFT JOIN plugin_pershop_packages p ON p.id=o.package_id
                   LEFT JOIN plugin_pershop_services v ON v.id=o.service_id
                   LEFT JOIN plugin_pershop_scammers s ON s.tg_id=o.buyer_tg_id
                   WHERE o.shop_id=$1 ORDER BY o.id DESC LIMIT 200""", shop["id"])
        else:
            filt = ("o.status IN ('pending','withdraw_requested')" if status == "pending"
                    else "o.status='withdrawn'" if status == "withdrawn" else "o.status=$2")
            rows = await ctx.db.fetch(
                f"""SELECT o.*, COALESCE(p.name, v.name) AS package_name,
                            COALESCE(p.price_units, v.price_units) AS package_units,
                            s.tg_id IS NOT NULL AS buyer_flagged
                     FROM plugin_pershop_orders o
                     LEFT JOIN plugin_pershop_packages p ON p.id=o.package_id
                     LEFT JOIN plugin_pershop_services v ON v.id=o.service_id
                     LEFT JOIN plugin_pershop_scammers s ON s.tg_id=o.buyer_tg_id
                     WHERE o.shop_id=$1 AND {filt} ORDER BY o.id DESC LIMIT 200""",
                shop["id"], status)
        return {"orders": [dict(r) for r in rows]}

    @router.get("/stats")
    async def stats(_a: AdminUser = Depends(require_permission("pershop", "view"))):
        from rwa_plugin_pershop.service import shop_stats

        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            return {"stats": {}}
        return {"stats": await shop_stats(ctx, dict(shop))}

    @router.get("/buyers")
    async def buyers(search: str = "", _a: AdminUser = Depends(require_permission("pershop", "view"))):
        """Buyer lookup: credit balance + language + sub count (+ scammer
        flag). Settles "I had credit!" disputes without touching the DB."""
        from rwa_plugin_pershop.service import is_scammer

        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            return {"buyers": []}
        subs = await ctx.db.fetch(
            "SELECT id, buyer_tg_id FROM plugin_pershop_subs WHERE shop_id=$1", shop["id"])
        ords = await ctx.db.fetch(
            "SELECT buyer_tg_id FROM plugin_pershop_orders WHERE shop_id=$1", shop["id"])
        counts: dict = {}
        for s in subs or []:
            if s.get("buyer_tg_id"):
                counts[int(s["buyer_tg_id"])] = counts.get(int(s["buyer_tg_id"]), 0) + 1
        tgs = set(counts)
        for o in ords or []:
            if o.get("buyer_tg_id"):
                tgs.add(int(o["buyer_tg_id"]))
        prefs = await ctx.db.fetch(
            "SELECT tg_id, lang, credit_units FROM plugin_pershop_buyers WHERE shop_id=$1",
            shop["id"])
        pref = {int(b["tg_id"]): b for b in (prefs or [])}
        needle = (search or "").strip()
        out = []
        for tg in sorted(tgs):
            if needle and needle not in str(tg):
                continue
            p = pref.get(tg, {})
            flag = await is_scammer(ctx, tg)
            out.append({"tg_id": tg, "lang": p.get("lang") or shop.get("lang") or "en",
                        "credit_units": float(p.get("credit_units") or 0),
                        "subs": counts.get(tg, 0),
                        "flagged": bool(flag),
                        "flag_reason": (flag or {}).get("reason") or ""})
            if len(out) >= 50:
                break
        return {"buyers": out}

    @router.get("/scammers")
    async def scammers_list(_a: AdminUser = Depends(require_permission("pershop", "view"))):
        """Network-wide scammer flags (all shops share the list)."""
        from rwa_plugin_pershop.service import list_scammers

        return {"scammers": await list_scammers(ctx)}

    @router.delete("/scammers/{tg_id}")
    async def scammer_unmark(tg_id: int, _a: AdminUser = Depends(require_permission("pershop", "moderate"))):
        """Remove a network-wide scammer flag (second chances)."""
        from rwa_plugin_pershop.service import unmark_scammer

        return {"removed": await unmark_scammer(ctx, tg_id)}

    @router.post("/orders/{order_id}/approve")
    async def order_approve(order_id: int, _a: AdminUser = Depends(require_permission("pershop", "moderate"))):
        from rwa_plugin_pershop.telegram import TgClient

        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "no shop")
        # Ownership check: order must belong to this admin's shop.
        order = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_orders WHERE id=$1 AND shop_id=$2", order_id, shop["id"])
        if not order:
            raise HTTPException(404, "order not found")
        try:
            result = await approve_order(ctx, order_id, me)
        except ValueError as e:
            raise HTTPException(409, str(e))
        from rwa_plugin_pershop.lang import L
        from rwa_plugin_pershop.service import format_price, message_text
        from rwa_plugin_pershop.service import approval_vars as _av2
        from rwa_plugin_pershop.service import fmt_safe as _fs2

        try:
            tg = TgClient(await decrypt_token(ctx, shop["bot_token_enc"]))
            bshop = await shop_for_tg(ctx, shop, order["buyer_tg_id"])
            _card2 = await _av2(ctx, shop, order, result)
            if result.get("service"):
                await tg.send_message(order["buyer_tg_id"],
                    _fs2(result.get("done_text") or L(bshop, "service_done_default"),
                         username=order.get("buyer_username") or ""))
            elif result.get("renewed"):
                await tg.send_message(order["buyer_tg_id"],
                    message_text(bshop, "renewed", username=result["username"], **_card2))
            else:
                url = result.get("subscription_url") or L(bshop, "url_fallback")
                await tg.send_message(order["buyer_tg_id"],
                    message_text(bshop, "approved", username=result["username"],
                                 url=url, **_card2))
        except Exception as e:
            ctx.logger.warning("shop.notify_buyer_failed", extra={"err": str(e)})
        try:
            earned = float(result.get("referrer_earned") or 0)
        except (TypeError, ValueError):
            earned = 0
        if earned > 0:
            # The buyer came through a referral link: pay the inviter.
            try:
                ref = await ctx.db.fetchrow(
                    "SELECT referrer_tg_id FROM plugin_pershop_referrals"
                    " WHERE shop_id=$1 AND referred_tg_id=$2",
                    shop["id"], order["buyer_tg_id"])
                if ref:
                    rshop = await shop_for_tg(ctx, shop, int(ref["referrer_tg_id"]))
                    from rwa_plugin_pershop.service import format_price as _fp5

                    await tg.send_message(int(ref["referrer_tg_id"]),
                        L(rshop, "earned_credit",
                          buyer=order.get("buyer_username") or str(order.get("buyer_tg_id")),
                          plan=result.get("service_name") or result.get("username"),
                          value=_fp5(rshop, earned)))
            except Exception as e:
                ctx.logger.warning("shop.referral_dm_failed", extra={"err": str(e)})
        return result

    @router.post("/orders/{order_id}/deny")
    async def order_deny(order_id: int, payload: DenyIn, _a: AdminUser = Depends(require_permission("pershop", "moderate"))):
        from rwa_plugin_pershop.telegram import TgClient

        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "no shop")
        order = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_orders WHERE id=$1 AND shop_id=$2", order_id, shop["id"])
        if not order:
            raise HTTPException(404, "order not found")
        try:
            refunded = await deny_order(ctx, order_id, me, payload.reason)
        except ValueError as e:
            raise HTTPException(409, str(e))
        from rwa_plugin_pershop.lang import L
        from rwa_plugin_pershop.service import format_price, message_text

        try:
            tg = TgClient(await decrypt_token(ctx, shop["bot_token_enc"]))
            bshop = await shop_for_tg(ctx, shop, order["buyer_tg_id"])
            _dplan = ""
            try:
                if order.get("package_id"):
                    _dpkg = await ctx.db.fetchrow(
                        "SELECT name FROM plugin_pershop_packages WHERE id=$1", order["package_id"])
                    _dplan = (_dpkg or {}).get("name") or ""
                elif order.get("service_id"):
                    _dsvc = await ctx.db.fetchrow(
                        "SELECT name FROM plugin_pershop_services WHERE id=$1", order["service_id"])
                    _dsvc = (_dsvc or {}).get("name") or ""
                    _dplan = _dsvc
            except Exception:
                pass
            await tg.send_message(order["buyer_tg_id"],
                message_text(bshop, "denied", reason=payload.reason,
                             username=order.get("buyer_username") or "", plan=_dplan))
            if (refunded or 0) > 0:
                await tg.send_message(order["buyer_tg_id"],
                    L(bshop, "credit_refunded", value=format_price(bshop, float(refunded))))
        except Exception as e:
            ctx.logger.warning("shop.notify_buyer_failed", extra={"err": str(e)})
        try:
            tg2 = TgClient(await decrypt_token(ctx, shop["bot_token_enc"]))
            await tg2.send_message(shop["admin_tg_id"],
                L(shop, "scam_offer", id=order_id, reason=payload.reason),
                reply_markup={"inline_keyboard": [
                    [{"text": L(shop, "scam_btn"),
                      "callback_data": f"scam:{order_id}"}]]})
        except Exception as e:
            ctx.logger.warning("shop.scam_offer_failed", extra={"err": str(e)})
        return {"ok": True}

    @router.get("/shops/all")
    async def shops_all(_a: AdminUser = Depends(require_permission("pershop_system", "manage"))):
        """Superadmin view: every shop (tokens never leave the server)."""
        rows = await ctx.db.fetch("SELECT * FROM plugin_pershop_shops ORDER BY id")
        out = []
        for r in rows:
            d = dict(r)
            has_token = d.get("bot_token_enc") is not None
            d.pop("bot_token_enc", None)
            d["has_token"] = has_token
            d["webhook_configured"] = bool(d.get("webhook_secret")) and has_token
            out.append(d)
        return {"shops": out}

    @router.get("/plugin-settings")
    async def plugin_settings_get(_a: AdminUser = Depends(require_permission("pershop_system", "manage"))):
        from rwa_plugin_pershop import _own_version

        polling = await ctx.settings.get("polling_enabled", default=False)
        tutorial = await ctx.settings.get("tutorial_text", default="")
        test_days = await ctx.settings.get("test_duration_days", default=1)
        test_gb = await ctx.settings.get("test_traffic_gb", default=1)
        test_squad = await ctx.settings.get("test_squad_uuid", default="")
        test_hwid = await ctx.settings.get("test_hwid", default=None)
        base = await ctx.settings.get("public_base_url", default="")
        n_shops = await ctx.db.fetchval("SELECT COUNT(*) FROM plugin_pershop_shops")
        n_pending = await ctx.db.fetchval("SELECT COUNT(*) FROM plugin_pershop_orders WHERE status IN ('pending','withdraw_requested')")
        n_subs = await ctx.db.fetchval("SELECT COUNT(*) FROM plugin_pershop_subs")
        return {"version": _own_version(), "polling_enabled": bool(polling),
                "tutorial_text": tutorial or "", "test_duration_days": test_days,
                "test_traffic_gb": test_gb, "test_squad_uuid": test_squad or "",
                "test_hwid": test_hwid, "public_base_url": base or "",
                "counts": {"shops": n_shops, "pending_orders": n_pending, "subs": n_subs}}

    @router.put("/plugin-settings")
    async def plugin_settings_put(payload: PluginSettingsIn,
                                  _a: AdminUser = Depends(require_permission("pershop_system", "manage"))):
        await ctx.settings.set("polling_enabled", payload.polling_enabled)
        await ctx.settings.set("tutorial_text", payload.tutorial_text)
        await ctx.settings.set("test_duration_days", payload.test_duration_days)
        await ctx.settings.set("test_traffic_gb", payload.test_traffic_gb)
        await ctx.settings.set("test_squad_uuid", (payload.test_squad_uuid or "").strip())
        await ctx.settings.set("test_hwid", payload.test_hwid)
        base = (payload.public_base_url or "").strip().rstrip("/")
        await ctx.settings.set("public_base_url", base)
        return {"polling_enabled": payload.polling_enabled,
                "tutorial_text": payload.tutorial_text,
                "test_duration_days": payload.test_duration_days,
                "test_traffic_gb": payload.test_traffic_gb,
                "test_squad_uuid": (payload.test_squad_uuid or "").strip(),
                "test_hwid": payload.test_hwid,
                "public_base_url": base}

    @router.delete("/admin/reset")
    async def admin_reset(_a: AdminUser = Depends(require_permission("pershop_system", "manage"))):
        """Superadmin wipe: every PerShop table is dropped (schema + rows),
        our migration revisions are unstamped so they re-run on next restart,
        and every plugin setting is deleted. IRREVERSIBLE."""
        from rwa_plugin_pershop.service import reset_all

        return await reset_all(ctx)

    @router.post("/broadcast")
    async def broadcast(request: Request, _a: AdminUser = Depends(require_permission("pershop", "edit"))):
        """Send-to-all: HTML text + optional photo (<=5MB). Queued, a 30s
        tick fans it out ~25 recipients at a time. The owner writes it in
        whatever language fits their buyers — forwarded verbatim."""
        form = await request.form()
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            raise HTTPException(400, "no shop")
        html_text = str(form.get("text") or "")
        if len(html_text.encode("utf-8")) > 4000:
            raise HTTPException(400, "text too long (4000 bytes max)")
        photo = None
        upload = form.get("photo")
        if upload is not None and getattr(upload, "filename", ""):
            photo = await upload.read()
            if len(photo) > 5 * 1024 * 1024:
                raise HTTPException(413, "photo must be under 5MB")
            if not photo:
                photo = None
        if not html_text.strip() and not photo:
            raise HTTPException(400, "empty broadcast")
        return await queue_broadcast(ctx, shop["id"], html_text, photo)

    @router.get("/broadcasts")
    async def broadcasts(_a: AdminUser = Depends(require_permission("pershop", "view"))):
        """Last broadcasts with fan-out progress."""
        me = owner(_a)
        shop = await shop_for_owner(ctx, me)
        if not shop:
            return {"broadcasts": []}
        rows = await ctx.db.fetch(
            """SELECT id, total, done, failed, status, created_at
               FROM plugin_pershop_broadcasts WHERE shop_id=$1
               ORDER BY id DESC LIMIT 10""", shop["id"])
        return {"broadcasts": [dict(r) for r in rows]}

    @router.get("/app")
    async def app_bundle(_a: AdminUser = Depends(require_permission("pershop", "view"))):
        bundle = (Path(__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        return Response(bundle, media_type="application/javascript")

    return router
