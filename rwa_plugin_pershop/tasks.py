"""Background ticks. Exceptions are contained per-shop/per-sub; pool connections via ctx.db."""
from __future__ import annotations

from datetime import datetime, timezone

from rwa_plugin_pershop.lang import L

HOURS_72 = 72 * 3600


def _as_aware(value) -> datetime | None:
    if not value:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def notifier_tick(ctx) -> None:
    try:
        shops = await ctx.db.fetch("SELECT * FROM plugin_pershop_shops")
    except Exception as e:
        ctx.logger.warning("shop.notifier_no_shops", extra={"err": str(e)})
        return
    for s in shops:
        try:
            await _notify_shop(ctx, dict(s))
        except Exception as e:
            ctx.logger.warning("shop.notifier_shop_failed", extra={"shop": s["id"], "err": str(e)})


async def _notify_shop(ctx, shop: dict) -> None:
    from rwa_plugin_pershop.crypto import decrypt_token
    from rwa_plugin_pershop.telegram import TgClient

    subs = await ctx.db.fetch("SELECT * FROM plugin_pershop_subs WHERE shop_id=$1 AND NOT cancelled", shop["id"])
    if not subs:
        return
    try:
        tg = TgClient(await decrypt_token(ctx, shop["bot_token_enc"]))
    except Exception as e:
        ctx.logger.warning("shop.decrypt_failed", extra={"err": str(e)})
        return
    now = datetime.now(timezone.utc)
    for sub in subs:
        try:
            await _notify_sub(ctx, tg, shop, sub, now)
        except Exception as e:
            ctx.logger.warning("shop.notify_sub_failed", extra={"sub": sub["id"], "err": str(e)})
    try:
        await _nag_pending(ctx, tg, shop, now)
    except Exception as e:
        ctx.logger.warning("shop.nag_failed", extra={"shop": shop["id"], "err": str(e)})


async def _nag_pending(ctx, tg, shop: dict, now: datetime) -> None:
    """Remind the owner about receipts waiting past their SLA.

    Per-shop `pending_nag_hours` (default 6, 0 = off). One friendly
    summary per window ("you have N orders waiting") — order details are
    NOT re-sent; they live in the original decision DMs and the Orders tab.
    """
    from rwa_plugin_pershop.lang import L

    hours = shop.get("pending_nag_hours") or 0
    admin = shop.get("admin_tg_id")
    if not admin or hours <= 0:
        return
    try:
        rows = await ctx.db.fetch(
            "SELECT o.* FROM plugin_pershop_orders o WHERE o.shop_id=$1"
            " AND o.status IN ('pending','withdraw_requested') ORDER BY o.id", shop["id"])
    except Exception:
        return
    window = hours * 3600
    due = []
    for o in rows or []:
        try:
            created = _as_aware(o.get("created_at"))
            nudged = _as_aware(o.get("owner_nudged_at"))
        except Exception:
            continue
        if not created:
            continue
        if ((now - created).total_seconds() >= window
                and (not nudged or (now - nudged).total_seconds() >= window)):
            due.append(o)
    if not due:
        return
    await tg.send_message(admin, L(shop, "nag_header", n=len(due), h=hours))
    for o in due:
        try:
            await ctx.db.execute(
                "UPDATE plugin_pershop_orders SET owner_nudged_at=NOW() WHERE id=$1", o["id"])
        except Exception:
            pass


async def _notify_sub(ctx, tg, shop: dict, sub: dict, now: datetime) -> None:
    from rwa_plugin_pershop.service import is_timeless, pending_for_sub, shop_for_tg

    if not sub.get("buyer_tg_id"):
        return  # Owner-matched, buyer hasn't linked yet: nobody to notify.
    if sub.get("cancelled"):
        return  # Self-cancelled: the panel account is disabled, stay silent.
    shop = await shop_for_tg(ctx, shop, sub.get("buyer_tg_id"))
    # Admin DB (users table) is the source of truth — re-sync local copy first.
    panel_user = None
    try:
        panel_user = await ctx.db.fetchrow(
            "SELECT expire_at, used_traffic_bytes, traffic_limit_bytes FROM users WHERE uuid=$1",
            sub["user_uuid"])
    except Exception:
        panel_user = None

    exp = _as_aware((panel_user and panel_user["expire_at"]) or sub["expire_at"])
    if panel_user and panel_user["expire_at"] and panel_user["expire_at"] != sub["expire_at"]:
        try:
            await ctx.db.execute(
                "UPDATE plugin_pershop_subs SET expire_at=$1, updated_at=NOW() WHERE id=$2",
                _as_aware(panel_user["expire_at"]), sub["id"])
        except Exception:
            pass

    timeless = is_timeless(exp)
    try:
        pending = await pending_for_sub(ctx, shop["id"], sub["username"])
    except Exception:
        pending = None
    if pending:
        return  # Paid and waiting: never nag a buyer mid-approval.

    if not timeless:
        await _notify_expiry(ctx, tg, shop, sub, exp, now)

    # Traffic warnings: 80% heads-up, then a 100% "out of traffic" notice
    # just like the expired notice. Each fires once per cap; both re-arm.
    limit = (panel_user and panel_user["traffic_limit_bytes"]) or sub["traffic_limit_bytes"]
    used = panel_user and panel_user["used_traffic_bytes"]
    if limit and used is not None:
        ratio = used / limit
        if ratio >= 1.0 and not sub.get("notified_out"):
            await _notify_out_of_traffic(ctx, tg, shop, sub)
        elif ratio >= 0.8 and not sub["notified_80"]:
            await _notify_traffic(ctx, tg, shop, sub, ratio)
        if ratio < 1.0 and sub.get("notified_out"):
            await ctx.db.execute(
                "UPDATE plugin_pershop_subs SET notified_out=FALSE WHERE id=$1", sub["id"])
        if ratio < 0.8 and sub["notified_80"]:
            await ctx.db.execute(
                "UPDATE plugin_pershop_subs SET notified_80=FALSE WHERE id=$1", sub["id"])


async def _traffic_offers(ctx, shop: dict) -> bool:
    """Does this shop sell +GB top-ups? (drives the fix button)."""
    try:
        rows = await ctx.db.fetch(
            "SELECT id FROM plugin_pershop_packages WHERE shop_id=$1 AND active"
            " AND effect='traffic_only' LIMIT 1", shop["id"])
        return bool(rows)
    except Exception:
        return False


async def _notify_expiry(ctx, tg, shop: dict, sub: dict, exp, now: datetime) -> None:
    """Expired (once) + 72h warning (once, re-arms) — each with its fix button."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    # Expired (once).
    if exp and exp <= now:
        if not sub["notified_expired"]:
            await tg.send_message(sub["buyer_tg_id"],
                L(shop, "expired_notice", username=sub["username"]), reply_markup=kb(
                [[{"text": L(shop, "reactivate_btn"),
                   "callback_data": f"qrenew:{sub['id']}"}]]))
            await ctx.db.execute(
                "UPDATE plugin_pershop_subs SET notified_expired=TRUE WHERE id=$1", sub["id"])
        return

    # 72h pre-expiry warning (once; re-arms if expiry moves away).
    if exp:
        remaining = (exp - now).total_seconds()
        if 0 < remaining <= HOURS_72 and not sub["notified_72h"]:
            await tg.send_message(sub["buyer_tg_id"],
                L(shop, "expiring_notice", username=sub["username"],
                  expire=exp.strftime("%Y-%m-%d %H:%M")), reply_markup=kb(
                [[{"text": L(shop, "renew_keep_btn"),
                   "callback_data": f"qrenew:{sub['id']}"}]]))
            await ctx.db.execute(
                "UPDATE plugin_pershop_subs SET notified_72h=TRUE WHERE id=$1", sub["id"])
        elif remaining > HOURS_72 and sub["notified_72h"]:
            await ctx.db.execute(
                "UPDATE plugin_pershop_subs SET notified_72h=FALSE WHERE id=$1", sub["id"])


async def _notify_traffic(ctx, tg, shop: dict, sub: dict, ratio: float) -> None:
    """80% warning with an Add-traffic button when the shop sells top-ups."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    markup = (kb([[{"text": L(shop, "add_traffic_btn"),
                            "callback_data": f"addtraffic:{sub['id']}"}]])
              if await _traffic_offers(ctx, shop) else None)
    await tg.send_message(sub["buyer_tg_id"],
        L(shop, "traffic_notice", username=sub["username"], percent=f"{ratio:.0%}"),
        reply_markup=markup)
    await ctx.db.execute(
        "UPDATE plugin_pershop_subs SET notified_80=TRUE WHERE id=$1", sub["id"])


async def _notify_out_of_traffic(ctx, tg, shop: dict, sub: dict) -> None:
    """100% exhausted — the traffic twin of the expired notice, with a fix button."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    if await _traffic_offers(ctx, shop):
        btn = {"text": L(shop, "add_traffic_btn"),
               "callback_data": f"addtraffic:{sub['id']}"}
    else:
        btn = {"text": L(shop, "renew_keep_btn"),
               "callback_data": f"qrenew:{sub['id']}"}
    await tg.send_message(sub["buyer_tg_id"],
        L(shop, "out_of_traffic_notice", username=sub["username"]),
        reply_markup=kb([[btn]]))
    await ctx.db.execute(
        "UPDATE plugin_pershop_subs SET notified_out=TRUE WHERE id=$1", sub["id"])


BROADCAST_CHUNK = 25


async def broadcast_tick(ctx) -> None:
    """Fan out queued send-to-all broadcasts, ~25 recipients per 30s tick.

    The owner writes the text once (HTML formatting intact, optional photo);
    each buyer send is contained so one blocked bot never stops the blast.
    """
    import json

    from rwa_plugin_pershop.crypto import decrypt_token
    from rwa_plugin_pershop.telegram import TgClient

    try:
        rows = await ctx.db.fetch(
            "SELECT * FROM plugin_pershop_broadcasts WHERE status IN ('queued','sending')"
            " ORDER BY id LIMIT 3")
    except Exception:
        return
    for b in rows or []:
        try:
            await _broadcast_step(ctx, b, json, decrypt_token, TgClient)
        except Exception as e:
            ctx.logger.warning("shop.broadcast_failed",
                               extra={"bc": b["id"], "err": str(e)})


async def _broadcast_step(ctx, b: dict, json, decrypt_token, TgClient) -> None:
    try:
        all_ids = [int(x) for x in (json.loads(b.get("recipients") or "[]"))]
    except Exception:
        all_ids = []
    pos = int(b.get("pos") or 0)
    if pos == 0:
        try:
            await ctx.db.execute(
                "UPDATE plugin_pershop_broadcasts SET status='sending' WHERE id=$1", b["id"])
        except Exception:
            pass
    if pos >= len(all_ids):
        try:
            await ctx.db.execute(
                "UPDATE plugin_pershop_broadcasts SET status='done' WHERE id=$1", b["id"])
        except Exception:
            pass
        return
    try:
        shop = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_shops WHERE id=$1", b["shop_id"])
        tg = TgClient(await decrypt_token(ctx, shop["bot_token_enc"]))
    except Exception:
        return
    chunk = all_ids[pos:pos + BROADCAST_CHUNK]
    done, failed = int(b.get("done") or 0), int(b.get("failed") or 0)
    photo = b.get("photo")
    if isinstance(photo, memoryview):
        photo = photo.tobytes()
    for tg_id in chunk:
        try:
            if photo:
                ok = await tg.send_photo(tg_id, photo, caption=b.get("html_text") or "")
                if ok is None:
                    raise RuntimeError("photo failed")
            else:
                await tg.send_message(tg_id, b.get("html_text") or "")
            done += 1
        except Exception:
            failed += 1
    try:
        await ctx.db.execute(
            "UPDATE plugin_pershop_broadcasts SET pos=$1, done=$2, failed=$3,"
            " status=CASE WHEN $1 >= $4 THEN 'done' ELSE 'sending' END WHERE id=$5",
            pos + len(chunk), done, failed, len(all_ids), b["id"])
    except Exception:
        pass


async def poller_tick(ctx) -> None:
    """Fallback long-polling when operator enables it (works behind NAT)."""
    """Fallback long-polling when operator enables it (works behind NAT).

    Disabled unless settings polling_enabled=true. Webhook is preferred.
    """
    try:
        enabled = await ctx.settings.get("polling_enabled", default=False)
    except Exception:
        return
    if not enabled:
        return
    try:
        shops = await ctx.db.fetch("SELECT * FROM plugin_pershop_shops")
    except Exception:
        return
    from rwa_plugin_pershop.crypto import decrypt_token
    from rwa_plugin_pershop.routes_telegram import handle_update
    from rwa_plugin_pershop.telegram import TgClient

    for s in shops:
        try:
            shop = dict(s)
            tg = TgClient(await decrypt_token(ctx, shop["bot_token_enc"]), timeout=10)
            key = f"tg_offset:{shop['id']}"
            offset = await ctx.settings.get(key, default=None)
            updates = await tg.get_updates(offset, timeout_s=5)
            for u in updates:
                try:
                    await handle_update(ctx, shop, u)
                except Exception as e:
                    ctx.logger.warning("shop.poll_update_failed",
                                       extra={"shop": shop["id"], "err": str(e)})
                offset = max(offset or 0, u.get("update_id", 0) + 1)
            if updates:
                await ctx.settings.set(key, offset)
        except Exception as e:
            ctx.logger.warning("shop.poll_failed", extra={"shop": s["id"], "err": str(e)})
