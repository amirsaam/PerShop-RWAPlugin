"""Telegram webhook (open route, Telegram calls it) + shared update handler.

Auth: optional X-Telegram-Bot-Api-Secret-Token compared to shops.webhook_secret.
This is the ONLY open route in the plugin; everything else requires RBAC.
"""
from __future__ import annotations

from fastapi import APIRouter, Header, Request


async def handle_update(ctx, shop: dict, update: dict) -> None:
    from rwa_plugin_pershop.crypto import decrypt_token
    from rwa_plugin_pershop.telegram import TgClient

    msg = update.get("message") or update.get("edited_message") or {}
    cb = update.get("callback_query") or {}
    sender = ((cb.get("from") or {}).get("id")
              if cb else (msg.get("from") or {}).get("id"))
    what = (cb.get("data") or "") if cb else (msg.get("text") or "")[:60]
    try:
        ctx.logger.info(
            "shop.update_in shop=%s sender=%s kind=%s what=%r"
            % ((shop or {}).get("prefix"), sender, "cb" if cb else "msg", what))
    except Exception:
        pass
    try:
        tg = TgClient(await decrypt_token(ctx, shop["bot_token_enc"]))
        if cb:
            sender = (cb.get("from") or {}).get("id")
            if sender and sender != shop.get("admin_tg_id"):
                shop = await _with_buyer_lang(ctx, shop, sender)
            await _on_callback(ctx, shop, tg, cb)
            return
        if not msg:
            return
        from_id = (msg.get("from") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if not from_id:
            return
        if from_id == shop["admin_tg_id"]:
            await _on_admin_message(ctx, shop, tg, msg, text)
        else:
            shop = await _with_buyer_lang(ctx, shop, from_id)
            await _on_buyer_message(ctx, shop, tg, msg, text)
    except Exception as e:
        try:
            import traceback as _tb

            _err = f"{type(e).__name__}: {e}"
            ctx.logger.error(
                "shop.update_failed shop=%s sender=%s kind=%s what=%r err=%s\n%s"
                % ((shop or {}).get("prefix"), sender, "cb" if cb else "msg",
                   what, _err, _tb.format_exc(limit=8)))
        except Exception:
            pass


async def _with_buyer_lang(ctx, shop: dict, tg_id: int) -> dict:
    """Shop copy speaking the buyer's own language (My account chooser).
    Falls back to the shop default; never raises."""
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


def _owner_shop(shop: dict) -> dict:
    """Shop copy in the owner's language for owner-directed messages.
    Buyer flows run in the buyer's language; the owner DM stays in the
    shop default the owner configured."""
    if isinstance(shop, dict) and "_buyer_lang" in shop:
        shop = dict(shop)
        shop.pop("_buyer_lang", None)
    return shop


def _card_block(shop: dict) -> str:
    """Payment/card details as a tappable Telegram code block.

    The owner enters a plain number in the panel; the bot wraps it in
    <code> automatically so buyers can tap-to-copy. Text that already
    carries its own formatting is passed through untouched."""
    import html as _html
    card = (shop.get("card_text") or "").strip()
    if not card:
        return ""
    if "<code" in card:
        return card
    return "<code>" + _html.escape(card) + "</code>"


async def _get_session(ctx, shop_id: int, tg_id: int, role: str) -> dict | None:
    return await ctx.db.fetchrow(
        "SELECT * FROM plugin_pershop_sessions WHERE shop_id=$1 AND tg_id=$2 AND role=$3",
        shop_id, tg_id, role,
    )


async def _set_session(ctx, shop_id: int, tg_id: int, role: str, state: str, data: dict | None = None) -> None:
    import json

    await ctx.db.execute(
        """INSERT INTO plugin_pershop_sessions(shop_id,tg_id,role,state,data,updated_at)
           VALUES($1,$2,$3,$4,$5,NOW())
           ON CONFLICT(shop_id,tg_id,role) DO UPDATE SET state=$4, data=$5, updated_at=NOW()""",
        shop_id, tg_id, role, state, json.dumps(data or {}),
    )


async def _clear_session(ctx, shop_id: int, tg_id: int, role: str) -> None:
    await ctx.db.execute("DELETE FROM plugin_pershop_sessions WHERE shop_id=$1 AND tg_id=$2 AND role=$3",
                         shop_id, tg_id, role)


async def _show(ctx, shop, tg, chat_id: int, mid: int | None, text: str,
                reply_markup: dict | None = None) -> int | None:
    """Render a button-driven screen, RWA-bot style: edit the tapped message
    in place when there is one; otherwise delete our previous bot message in
    this chat and send fresh, so the DM never fills up. Only our own tracked
    messages are ever deleted — buyer messages (receipts included) stay as
    anchors. Returns the message id now showing the screen (or None). Never raises."""
    from rwa_plugin_pershop.service import get_last_mid, set_last_mid

    # Transport-level guarantee: Telegram rejects empty message text, and a
    # rejected send surfaces as total bot silence. An empty screen can never
    # be intentional, so fall back to the menu title instead of sending "".
    if not (text or "").strip():
        try:
            ctx.logger.warning(
                "shop.empty_screen shop=%s chat=%s"
                % (shop.get("prefix") if isinstance(shop, dict) else None, chat_id))
        except Exception:
            pass
        try:
            from rwa_plugin_pershop.lang import L as _LG

            text = _LG(shop, "menu_title") or "Main menu"
        except Exception:
            text = "Main menu"

    sid = shop.get("id") if isinstance(shop, dict) else None
    if mid:
        try:
            if await tg.edit_text(chat_id, mid, text, reply_markup):
                await set_last_mid(ctx, sid, chat_id, mid)
                try:
                    _shop = shop.get("prefix") if isinstance(shop, dict) else None
                    ctx.logger.info(
                        "shop.screen_edited shop=%s chat=%s mid=%s len=%d"
                        % (_shop, chat_id, mid, len(text or "")))
                except Exception:
                    pass
                return mid
        except Exception:
            pass
    if sid is not None:
        prev = await get_last_mid(ctx, sid, chat_id)
        if prev and prev != mid:
            try:
                if await tg.edit_text(chat_id, prev, text, reply_markup):
                    try:
                        _shop = shop.get("prefix") if isinstance(shop, dict) else None
                        ctx.logger.info(
                            "shop.screen_edited shop=%s chat=%s mid=%s len=%d"
                            % (_shop, chat_id, prev, len(text or "")))
                    except Exception:
                        pass
                    return prev
            except Exception:
                pass
            try:
                await tg.delete_message(chat_id, prev)
            except Exception:
                pass
    try:
        new_mid = await tg.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        try:
            _shop = shop.get("prefix") if isinstance(shop, dict) else None
            ctx.logger.error(
                "shop.send_failed shop=%s chat=%s len=%d preview=%r err=%s"
                % (_shop, chat_id, len(text or ""), (text or "")[:160],
                   f"{type(e).__name__}: {e}"))
        except Exception:
            pass
        return None
    if not new_mid:
        # Telegram answered HTTP 200 with ok:false (no exception raised), so
        # there is nothing to show and nothing was logged until now.
        try:
            _shop = shop.get("prefix") if isinstance(shop, dict) else None
            ctx.logger.error(
                "shop.send_empty_result shop=%s chat=%s len=%d preview=%r"
                % (_shop, chat_id, len(text or ""), (text or "")[:160]))
        except Exception:
            pass
        return None
    try:
        _shop = shop.get("prefix") if isinstance(shop, dict) else None
        ctx.logger.info(
            "shop.screen_sent shop=%s chat=%s mid=%s len=%d"
            % (_shop, chat_id, new_mid, len(text or "")))
    except Exception:
        pass
    await set_last_mid(ctx, sid, chat_id, new_mid)
    return new_mid


async def _packages(ctx, shop_id: int) -> list:
    rows = await ctx.db.fetch(
        "SELECT * FROM plugin_pershop_packages WHERE shop_id=$1 AND active ORDER BY sort, id", shop_id)
    return [r for r in rows if ((r.get("effect") if isinstance(r, dict) else r["effect"]) or "full") == "full"]


def _full_only(pkgs) -> list:
    """Top-up offers never appear in buy/renew pickers (own pickers exist)."""
    out = []
    for p in pkgs or []:
        try:
            eff = (p.get("effect") if isinstance(p, dict) else p["effect"]) or "full"
        except Exception:
            eff = "full"
        if eff == "full":
            out.append(p)
    return out


def _extract_receipt(msg: dict, text: str) -> tuple[str | None, str, int | None]:
    """Return (file_id_or_text, kind, message_id). Accepts photo/doc/any-text."""
    photo = msg.get("photo") or []
    doc = msg.get("document") or {}
    if photo:
        return photo[-1].get("file_id"), "photo", msg.get("message_id")
    if doc.get("file_id"):
        return doc.get("file_id"), "doc", msg.get("message_id")
    text = (text or "")[:2000]
    return (text or None), "text", msg.get("message_id")


def _cancel_row(shop) -> list:
    from rwa_plugin_pershop.lang import L

    return [[{"text": L(shop, "cancel_btn"), "callback_data": "cancel"}]]


def _back_row(shop) -> list:
    from rwa_plugin_pershop.lang import L

    return [[{"text": L(shop, "back_btn"), "callback_data": "menu"}]]


def _pkg_label(shop, pkg) -> str:
    from rwa_plugin_pershop.service import format_price

    price = format_price(shop, pkg.get("price_units")) if pkg.get("price_units") else (pkg.get("price_text") or "")
    return f"{pkg['name']} — {price}" if price else pkg["name"]


async def _send_buyer_menu(ctx, shop, tg, chat_id, edit=None) -> None:
    """Main menu as a persistent bottom panel (reply keyboard).

    Same entries, order and visibility rules as the old inline menu, two
    per row. Test shows only when the owner enabled it; referral/support
    only when the owner configured a text; how-to only when superadmin set
    one. My account is always last. Everything below the menu stays inline.
    """
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import reply_kb

    labels = [L(shop, "buy_subs_btn")]
    if shop.get("test_enabled"):
        labels.append(L(shop, "test_sub_btn"))
    labels.append(L(shop, "my_subs_btn"))
    try:
        from rwa_plugin_pershop.service import has_services

        if await has_services(ctx, shop["id"]):
            labels.append(L(shop, "services_btn"))
    except Exception:
        pass
    try:
        _ref_pct = float(shop.get("referral_percent") or 0)
    except Exception:
        _ref_pct = 0
    if (shop.get("referral_text") or "").strip() or _ref_pct > 0:
        labels.append(L(shop, "referral_btn"))
    tutorial = await ctx.settings.get("tutorial_text", default="")
    if (tutorial or "").strip():
        labels.append(L(shop, "tutorial_btn"))
    if (shop.get("support_text") or "").strip():
        labels.append(L(shop, "support_btn"))
    labels.append(L(shop, "account_btn"))
    rows = [labels[i:i + 2] for i in range(0, len(labels), 2)]
    from rwa_plugin_pershop.service import fmt_safe as _fsw
    text = _fsw((shop["welcome_text"] or "").strip(), shop=shop.get("prefix") or "") or L(shop, "menu_title")
    if edit:
        # A reply keyboard cannot ride on an edited message; the persistent
        # panel is already up, so only the text is refreshed in place.
        await _show(ctx, shop, tg, chat_id, edit, text)
    else:
        menu_mid = await tg.send_message(chat_id, text, reply_markup=reply_kb(rows))
        # Track it: without this, later taps keep editing a stale message
        # from a previous session while this fresh menu sits unchanged.
        try:
            from rwa_plugin_pershop.service import set_last_mid as _slm

            await _slm(ctx, shop.get("id"), chat_id, menu_mid)
        except Exception:
            pass


async def _send_test_offer(ctx, shop, tg, sender, edit=None) -> None:
    """Trial terms + Get-test button. Shared by inline tap and panel tap."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import test_terms
    from rwa_plugin_pershop.telegram import kb

    if not shop.get("test_enabled"):
        await _show(ctx, shop, tg, sender, edit, L(shop, "test_off"))
        return
    terms = await test_terms(ctx, shop)
    await _show(
        ctx, shop, tg, sender, edit, L(shop, "test_confirm", days=terms["days"], gb=terms["gb"]),
        kb([[{"text": L(shop, "test_confirm_btn"), "callback_data": "testconfirm"}]]
           + _back_row(shop)))


async def _send_referral(ctx, shop, tg, chat_id, tg_id, edit=None) -> None:
    """Referral screen: owner text (with {percent}/{link} filled in) plus the
    copyable invite link. Self-heals a missing bot username via getMe."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import ensure_bot_username, fmt_safe

    bot_un = await ensure_bot_username(ctx, shop)
    try:
        pct = ("%g" % float(shop.get("referral_percent") or 0))
    except Exception:
        pct = "0"
    text = (shop.get("referral_text") or "").strip() or L(shop, "referral_default", percent=pct)
    link = f"https://t.me/{bot_un}?start=r_{tg_id}" if bot_un else ""
    text = fmt_safe(text, percent=pct, link=link or f"r_{tg_id}")
    if bot_un:
        await _show(ctx, shop, tg, chat_id, edit,
                    L(shop, "referral_body", text=text, link=link))
    else:
        await _show(ctx, shop, tg, chat_id, edit,
                    L(shop, "referral_nocode", text=text, id=tg_id))


async def _on_menu_action(ctx, shop, tg, chat_id, buyer_id, buyer_un, action) -> None:
    """Persistent-panel tap: same destinations as the old inline menu."""
    from rwa_plugin_pershop.lang import L

    if action == "pershop:buy":
        await _send_buy_list(ctx, shop, tg, chat_id)
    elif action == "pershop:test":
        await _send_test_offer(ctx, shop, tg, buyer_id)
    elif action == "my_subs":
        await _send_subs_list(ctx, shop, tg, buyer_id)
    elif action == "pershop:services":
        await _send_svc_list(ctx, shop, tg, chat_id)
    elif action == "pershop:ref":
        await _send_referral(ctx, shop, tg, buyer_id, buyer_id)
    elif action == "pershop:how":
        tutorial = (await ctx.settings.get("tutorial_text", default="") or "").strip()
        if tutorial:
            await _show(ctx, shop, tg, buyer_id, None, tutorial)
    elif action == "pershop:help":
        support = (shop.get("support_text") or "").strip()
        if support:
            await _show(ctx, shop, tg, buyer_id, None, support)
    elif action == "account":
        await _send_account(ctx, shop, tg, buyer_id)


async def _send_buy_list(ctx, shop, tg, chat_id, edit=None) -> None:
    """Buy entry: tier buttons, a flat package list when tiers are internal,
    or the legacy direct list when the shop has no tiers yet."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    tiers = await ctx.db.fetch(
        "SELECT * FROM plugin_pershop_tiers WHERE shop_id=$1 AND active ORDER BY sort, id", shop["id"])
    if tiers and not shop.get("tiers_internal"):
        rows = [[{"text": t["name"], "callback_data": f"tiers:{t['id']}"}] for t in tiers]
        rows += _back_row(shop)
        await _show(ctx, shop, tg, chat_id, edit, L(shop, "choose_tier"), kb(rows))
        return
    pkgs = await _packages(ctx, shop["id"])
    rows = [[{"text": _pkg_label(shop, p), "callback_data": f"buy:{p['id']}"}] for p in pkgs]
    rows += _back_row(shop)
    await _show(ctx, shop, tg, chat_id, edit, L(shop, "welcome_fallback"),
                kb(rows) if rows else None)


async def _send_tier_packages(ctx, shop, tg, sender, tier, edit=None) -> None:
    """Package list of one tier + Back to menu."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    pkgs = await ctx.db.fetch(
        "SELECT * FROM plugin_pershop_packages WHERE tier_id=$1 AND active ORDER BY sort, id", tier["id"])
    rows = [[{"text": _pkg_label(shop, p), "callback_data": f"buy:{p['id']}"}] for p in _full_only(pkgs)]
    rows += _back_row(shop)
    await _show(ctx, shop, tg, sender, edit, L(shop, "tier_pkg_title"), kb(rows))


async def _send_svc_list(ctx, shop, tg, chat_id, edit=None) -> None:
    """Other-Services entry: category buttons (or direct items) + Back."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import cat_services, service_cats
    from rwa_plugin_pershop.telegram import kb

    cats = await service_cats(ctx, shop["id"])
    if cats:
        rows = [[{"text": c["name"], "callback_data": f"svccat:{c['id']}"}] for c in cats]
        if await cat_services(ctx, shop["id"], None):
            rows.append([{"text": L(shop, "scat_other"), "callback_data": "svcother:"}])
        rows += _back_row(shop)
        await _show(ctx, shop, tg, chat_id, edit, L(shop, "choose_scat"), kb(rows))
        return
    await _send_svc_items(ctx, shop, tg, chat_id,
                          await cat_services(ctx, shop["id"], None), edit=edit)


async def _send_svc_items(ctx, shop, tg, chat_id, svcs, title=None, edit=None) -> None:
    """Item buttons of one category + Back to the service list."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import format_price
    from rwa_plugin_pershop.telegram import kb

    rows = [[{"text": f"{s['name']} — {format_price(shop, s.get('price_units') or 0)}",
              "callback_data": f"svc:{s['id']}"}] for s in svcs or []]
    rows.append([{"text": L(shop, "back_btn"), "callback_data": "pershop:services"}])
    await _show(ctx, shop, tg, chat_id, edit, title or L(shop, "choose_scat"), kb(rows))


async def _send_svc_detail(ctx, shop, tg, chat_id, svc, back="pershop:services", edit=None) -> None:
    """One service: description + converted price + Buy + Back."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import format_price
    from rwa_plugin_pershop.telegram import kb

    text = f"<b>{svc['name']}</b>"
    if (svc.get("description") or "").strip():
        from rwa_plugin_pershop.service import fmt_safe as _fsd, format_price as _fpd
        text += f"\n{_fsd((svc['description'] or '').strip(), price=_fpd(shop, svc.get('price_units') or 0))}"
    text += f"\n{L(shop, 'svc_price', price=format_price(shop, svc.get('price_units') or 0))}"
    await _show(ctx, shop, tg, chat_id, edit, text, kb(
        [[{"text": L(shop, "contract_pay_btn"), "callback_data": f"svcbuy:{svc['id']}"}],
         [{"text": L(shop, "back_btn"), "callback_data": back}]]))


async def _send_svc_note_prompt(ctx, shop, tg, chat_id, svc, back, edit=None) -> None:
    """Step 1 of a service order: optional buyer note (or Skip)."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    mid = await _show(ctx, shop, tg, chat_id, edit, L(shop, "svc_note_prompt"), kb(
        [[{"text": L(shop, "svc_skip_btn"), "callback_data": f"svcskip:{svc['id']}"}],
         [{"text": L(shop, "back_btn"), "callback_data": f"svc:{svc['id']}"}]]))
    await _set_session(ctx, shop["id"], chat_id, "buyer",
                       f"await_svc_note:{svc['id']}",
                       {"service_id": svc["id"], "back": back, "pm": mid})


async def _send_svc_contract(ctx, shop, tg, sender, svc, note, back,
                             credit_applied=False, edit=None) -> dict:
    """Contract screen for a service: item + converted price + note, then Pay."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import credit_balance, format_price
    from rwa_plugin_pershop.telegram import kb

    price = float(svc.get("price_units") or 0)
    lines = [L(shop, "contract_title"),
             L(shop, "contract_plan", plan=svc["name"], price=format_price(shop, price))]
    if (note or "").strip():
        lines.append(L(shop, "note_line", note=note.strip()).strip())
    bal = await credit_balance(ctx, shop["id"], sender)
    credit_row = []
    use_credit = bool(credit_applied) and bal > 0
    if bal > 0 and not use_credit:
        lines.append(L(shop, "credit_contract_line",
                         value=format_price(shop, bal)))
        credit_row = [[{"text": L(shop, "credit_use_btn"), "callback_data": "credituse"}]]
    elif use_credit:
        used = min(bal, price)
        due = price - used
        lines.append(L(shop, "credit_due", used=format_price(shop, used),
                         due=format_price(shop, due)) if due > 0
                     else L(shop, "credit_full"))
    lines.append(L(shop, "contract_hint"))
    saved = {"service_id": svc["id"], "note": (note or "").strip()[:500],
             "total_units": price, "back": back, "credit": bool(use_credit)}
    await _set_session(ctx, shop["id"], sender, "buyer",
                       f"await_summary:svc:{svc['id']}", saved)
    await _show(ctx, shop, tg, sender, edit, "\n\n".join(lines), kb(
        [[{"text": L(shop, "contract_pay_btn"), "callback_data": "stconfirm"}]]
        + credit_row +
        [[{"text": L(shop, "back_btn"), "callback_data": back}]]))
    return saved


async def _load_svc_draft(ctx, shop, sender):
    """Buyer's in-progress service draft: (svc, note, state, data) or None."""
    import json

    from rwa_plugin_pershop.service import service_by_id

    ses = await _get_session(ctx, shop["id"], sender, "buyer")
    if not ses:
        return None
    try:
        data = json.loads(ses["data"] or "{}")
    except Exception:
        data = {}
    if not data.get("service_id"):
        return None
    svc = await service_by_id(ctx, data["service_id"])
    if not svc:
        return None
    return svc, data.get("note") or "", ses["state"] or "", data


async def _submit_svc_receipt(ctx, shop, tg, msg, text, buyer_id, buyer_un, chat_id, data) -> None:
    """Receipt submit for an Other-Service order: same pipeline as packages
    (forward + decide buttons + strip storage), but no renew/hwid math and
    the buyer note rides along in the header."""
    import json as _json

    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import format_price, service_by_id
    from rwa_plugin_pershop.telegram import kb

    receipt_ref, kind, message_id = _extract_receipt(msg, text)
    if not receipt_ref:
        await _show(ctx, shop, tg, chat_id, data.get("pm"), L(shop, "need_receipt"))
        return
    svc = await service_by_id(ctx, data.get("service_id"))
    if not svc:
        await _clear_session(ctx, shop["id"], buyer_id, "buyer")
        await _show(ctx, shop, tg, chat_id, data.get("pm"), L(shop, "svc_unavailable"))
        return
    note = (data.get("note") or "")[:500]
    total = float(svc.get("price_units") or 0)
    credit_used = 0.0
    if data.get("credit"):
        from rwa_plugin_pershop.service import add_credit as _ac2
        from rwa_plugin_pershop.service import credit_balance as _cb3

        _bal = await _cb3(ctx, shop["id"], buyer_id)
        credit_used = min(_bal, total)
        if credit_used > 0:
            await _ac2(ctx, shop["id"], buyer_id, -credit_used)
    snapshot = _json.dumps({"service": svc["id"], "note": note,
                            "credit_used": credit_used})
    row = await ctx.db.fetchrow(
        """INSERT INTO plugin_pershop_orders(shop_id,service_id,buyer_tg_id,buyer_username,
           receipt_file_id,receipt_kind,total_units,options_snapshot)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id""",
        shop["id"], svc["id"], buyer_id, buyer_un, receipt_ref, kind, total, snapshot)
    await _clear_session(ctx, shop["id"], buyer_id, "buyer")
    await _maybe_scam_warn(ctx, shop, tg, buyer_id, buyer_un)
    osh = _owner_shop(shop)
    header = L(osh, "order_header", id=row["id"], action=L(osh, "action_service"),
               buyer=buyer_un, buyer_id=buyer_id, pkg=svc["name"],
               price=format_price(shop, total))
    if credit_used > 0:
        header += "\n" + L(osh, "credit_owner_line",
                          used=format_price(shop, credit_used),
                          due=format_price(shop, total - credit_used))
    if note.strip():
        header += L(osh, "note_line", note=note.strip())
    try:
        if kind in ("photo", "doc") and message_id:
            try:
                await tg.forward_message(shop["admin_tg_id"], chat_id, message_id)
            except Exception:
                pass
        if kind == "text":
            header += L(osh, "order_receipt_line", ref=receipt_ref)
        await tg.send_message(shop["admin_tg_id"], header)
        decide_id = await tg.send_message(
            shop["admin_tg_id"], L(osh, "decide_order", id=row["id"]),
            reply_markup=kb([[{"text": L(osh, "approve_btn"), "callback_data": f"approve:{row['id']}"},
                              {"text": L(osh, "deny_btn"), "callback_data": f"deny:{row['id']}"}]]))
        try:
            await ctx.db.execute(
                "UPDATE plugin_pershop_orders SET owner_chat_id=$1, owner_msg_id=$2 WHERE id=$3",
                shop["admin_tg_id"], decide_id, row["id"])
        except Exception:
            pass
    except Exception as e:
        ctx.logger.warning("shop.forward_failed", extra={"err": str(e)})
    from rwa_plugin_pershop.service import message_text

    # Receipt step: the buyer's receipt stays, the bot's prompt is removed,
    # and the confirmation arrives as one fresh message after the receipt.
    try:
        if data.get("pm"):
            await tg.delete_message(chat_id, data["pm"])
    except Exception:
        pass
    confirm_id = None
    try:
        confirm_id = await tg.send_message(chat_id, message_text(shop, "thank_you", order=row["id"]))
    except Exception:
        pass
    try:
        if confirm_id:
            await ctx.db.execute(
                "UPDATE plugin_pershop_orders SET buyer_msg_id=$1 WHERE id=$2",
                confirm_id, row["id"])
    except Exception:
        pass


async def _subs_state(ctx, shop, sub) -> dict:
    """Live panel read + derived buyer state. Never raises; missing pieces
    stay None and the UI degrades to plain rows instead of crashing."""
    from datetime import datetime, timezone

    from rwa_plugin_pershop.service import is_timeless, pending_for_sub

    st = {"status": "", "exp": None, "expired": False, "days": None,
          "has_expiry": False, "has_cap": False, "limit": sub.get("traffic_limit_bytes"),
          "used": None, "left": None, "pending": None, "plan": None, "pkg": None,
          "hwid": None,
          "timeless": False, "topups_time": [], "topups_traffic": []}
    try:
        row = await ctx.db.fetchrow(
            "SELECT status, expire_at, used_traffic_bytes, traffic_limit_bytes,"
            " hwid_device_limit FROM users WHERE uuid=$1",
            sub["user_uuid"])
    except Exception:
        row = None
    row = dict(row) if row else {}
    st["status"] = str(row.get("status") or "").upper()
    exp = row.get("expire_at") or sub.get("expire_at")
    try:
        if exp is not None and getattr(exp, "tzinfo", None) is None:
            exp = exp.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    st["exp"] = exp
    now = datetime.now(timezone.utc)
    st["timeless"] = is_timeless(exp)
    st["has_expiry"] = bool(exp) and not st["timeless"]
    if exp and not st["timeless"]:
        try:
            st["expired"] = exp <= now
            delta = (now - exp) if st["expired"] else (exp - now)
            st["days"] = delta.days
        except Exception:
            pass
    limit = row.get("traffic_limit_bytes")
    if limit is None:
        limit = sub.get("traffic_limit_bytes")
    used = row.get("used_traffic_bytes")
    st["limit"], st["used"] = limit, used
    try:
        st["hwid"] = row.get("hwid_device_limit")
    except Exception:
        st["hwid"] = None
    st["has_cap"] = bool(limit)
    if limit and used is not None:
        try:
            st["left"] = limit - used
        except Exception:
            pass
    try:
        st["pending"] = await pending_for_sub(ctx, shop["id"], sub["username"])
    except Exception:
        st["pending"] = None
    if sub.get("package_id"):
        try:
            pkg = await ctx.db.fetchrow(
                "SELECT * FROM plugin_pershop_packages WHERE id=$1", sub["package_id"])
        except Exception:
            pkg = None
        if pkg:
            st["pkg"] = dict(pkg)
            plan = pkg["name"]
            if pkg.get("tier_id"):
                try:
                    tier = await ctx.db.fetchrow(
                        "SELECT name FROM plugin_pershop_tiers WHERE id=$1", pkg["tier_id"])
                except Exception:
                    tier = None
                if tier:
                    plan = f"{tier['name']} · {plan}"
            st["plan"] = plan
    try:
        offers = await ctx.db.fetch(
            "SELECT * FROM plugin_pershop_packages WHERE shop_id=$1 AND active AND effect != 'full'"
            " ORDER BY price_units, id", shop["id"])
    except Exception:
        offers = []
    for o in offers or []:
        o = dict(o)
        # Package-scoped top-ups (like hwid limits): an offer bound to one
        # tier shows only for subs matched to that tier's plan.
        scope = o.get("scope_tier_id")
        tier = (st["pkg"] or {}).get("tier_id")
        if scope is not None and (tier is None or int(scope) != int(tier)):
            continue
        if o.get("effect") == "time_only":
            st["topups_time"].append(o)
        elif o.get("effect") == "traffic_only":
            st["topups_traffic"].append(o)
    return st


async def _send_subs_list(ctx, shop, tg, sender, edit=None) -> None:
    """My-subs dashboard: one status-rich row per subscription + link + Back."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    rows = await ctx.db.fetch("SELECT * FROM plugin_pershop_subs WHERE shop_id=$1 AND buyer_tg_id=$2",
                              shop["id"], sender)
    link_row = [[{"text": L(shop, "link_sub_btn"), "callback_data": "linksub"}]]
    if not rows:
        await _show(ctx, shop, tg, sender, edit, L(shop, "no_subs"),
                    kb(link_row + _back_row(shop)))
        return
    btns = []
    for r in rows:
        st = await _subs_state(ctx, shop, r)
        if st["pending"]:
            dot = "⏳"
        elif st["expired"]:
            dot = "🔴"
        elif st["timeless"] and not st["has_cap"]:
            dot = "♾️"
        else:
            out = st["left"] is not None and st["left"] <= 0
            urgent = (st["days"] is not None and st["days"] <= 3) or bool(out)
            dot = "🟡" if urgent else "🟢"
        btns.append([{"text": f"{dot} {r['username']}",
                      "callback_data": f"sub:{r['id']}"}])
    btns += link_row + _back_row(shop)
    await _show(ctx, shop, tg, sender, edit, L(shop, "pick_sub"), kb(btns))


def _fmt_gb(value) -> str:
    """Bytes -> '12.40 GB' / '850.0 MB' / '∞'; never raises."""
    try:
        if value is None:
            return "∞"
        v = float(value)
        if v >= 10 ** 9:
            return f"{v / 10 ** 9:.2f} GB"
        return f"{v / 10 ** 6:.1f} MB"
    except Exception:
        return "?"


async def _send_sub_detail(ctx, shop, tg, sender, sub, edit=None) -> None:
    """Sub-detail dashboard: live numbers + exactly the buttons that make
    sense for this sub's state. Pending hides every pay button."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import format_when
    from rwa_plugin_pershop.telegram import kb

    st = await _subs_state(ctx, shop, sub)
    if st["status"] == "ACTIVE":
        status = L(shop, "status_active")
    elif st["status"]:
        status = L(shop, "status_off")
    else:
        status = "—"
    if not st["has_cap"]:
        traffic = L(shop, "unlimited_lbl")
    elif st["used"] is None:
        traffic = _fmt_gb(st["limit"])
    else:
        traffic = f"{_fmt_gb(st['used'])} / {_fmt_gb(st['limit'])}"
    try:
        hw = st.get("hwid")
        hwid = L(shop, "unlimited_lbl") if hw is None or int(hw) <= 0 else str(int(hw))
    except Exception:
        hwid = str(st.get("hwid"))
    dot = "🟢" if st["status"] == "ACTIVE" else "🔴"
    plan = st["plan"] or "—"
    text = L(shop, "sub_info", username=sub["username"], status=status,
             expire=format_when(shop, st["exp"]), traffic=traffic, plan=plan,
             dot=dot, hwid=hwid)
    home = [[{"text": L(shop, "back_btn"), "callback_data": "back:subs"},
             {"text": L(shop, "main_menu_btn"), "callback_data": "menu"}]]
    if st["pending"]:
        status = L(shop, "pending_badge")
        text = L(shop, "sub_info", username=sub["username"], status=status,
                 expire=format_when(shop, st["exp"]), traffic=traffic, plan=plan,
                 dot="⏳", hwid=hwid)
        text += "\n\n" + L(shop, "pending_note")
        pid = (st["pending"] or {}).get("id") if isinstance(st["pending"], dict) else None
        btns = ([[{"text": L(shop, "cancel_receipt_btn"),
                   "callback_data": f"cancelorder:{pid}"}]] if pid else []) + home
        await _show(ctx, shop, tg, sender, edit, text, kb(btns))
        return
    if sub.get("cancelled"):
        text += "\n\n" + L(shop, "cancelled_lbl")
        await _show(ctx, shop, tg, sender, edit, text, kb(home))
        return
    if sub.get("is_test"):
        # Trial accounts are info-only: no renew, no top-ups. A full plan
        # is bought through the Buy menu like any new subscription.
        text += "\n\n" + L(shop, "test_no_renew")
        await _show(ctx, shop, tg, sender, edit, text, kb(home))
        return
    offer = None
    if sub.get("buyer_tg_id"):
        # A sub bought fully with credit can be cancelled automatically:
        # disable + refund-to-balance, no owner decision.
        try:
            offer = await ctx.db.fetchrow(
                """SELECT id, total_units FROM plugin_pershop_orders WHERE shop_id=$1
                   AND buyer_tg_id=$2 AND receipt_kind='credit' AND status='approved'
                   AND renew_username=$3 ORDER BY id DESC LIMIT 1""",
                shop["id"], int(sub["buyer_tg_id"]), sub["username"])
        except Exception:
            offer = None
    cancel_row = ([[{"text": L(shop, "credit_cancel_btn"),
                    "callback_data": f"cfcancel:{sub['id']}"}]] if offer else [])
    if not offer and sub.get("buyer_tg_id"):
        # Paid partly with credit + partly by transfer: no self-cancel, but
        # say why instead of silently hiding the button.
        from rwa_plugin_pershop.service import has_mixed_payment
        try:
            if await has_mixed_payment(ctx, shop["id"], int(sub["buyer_tg_id"]), sub["username"]):
                text += "\n\n" + L(shop, "mixed_note")
        except Exception:
            pass
    if st["timeless"] and not st["has_cap"]:
        text += "\n\n" + L(shop, "lifetime_ok")
        await _show(ctx, shop, tg, sender, edit, text, kb(cancel_row + home))
        return
    if not st["pkg"]:
        # Linked / pre-shop sub with no shop package: it is shown as-is
        # from the system. The only renewal path is picking a package
        # from this shop (change-plan chain) — no quick renew, no owner
        # ping, no package talk.
        btns = [[{"text": L(shop, "renew_btn", username=sub["username"]),
                  "callback_data": f"renew:{sub['id']}"}]]
        btns += cancel_row + home
        await _show(ctx, shop, tg, sender, edit, text, kb(btns))
        return
    if st["expired"]:
        quick = L(shop, "reactivate_btn")
    elif st["pkg"]:
        quick = L(shop, "extend_early_btn")
    else:
        quick = L(shop, "quick_renew_btn")
    btns = [[{"text": quick, "callback_data": f"qrenew:{sub['id']}"},
             {"text": L(shop, "change_plan_btn"), "callback_data": f"renew:{sub['id']}"}]]
    extra = []
    if st["has_cap"] and st["topups_traffic"]:
        extra.append({"text": L(shop, "add_traffic_btn"),
                      "callback_data": f"addtraffic:{sub['id']}"})
    if st["has_expiry"] and st["topups_time"]:
        extra.append({"text": L(shop, "add_days_btn"),
                      "callback_data": f"adddays:{sub['id']}"})
    if extra:
        btns.append(extra)
    btns += cancel_row + home
    await _show(ctx, shop, tg, sender, edit, text, kb(btns))


async def _send_account(ctx, shop, tg, sender, edit=None) -> None:
    """My account hub: Credit Balance / Purchase History / Language buttons."""
    from rwa_plugin_pershop.lang import LANGS, LANG_FLAGS, L, normalize_lang
    from rwa_plugin_pershop.service import credit_balance, format_price
    from rwa_plugin_pershop.telegram import kb

    bal = await credit_balance(ctx, shop["id"], sender)
    try:
        n = await ctx.db.fetchval(
            "SELECT COUNT(*) FROM plugin_pershop_orders WHERE shop_id=$1 AND buyer_tg_id=$2",
            shop["id"], sender) or 0
    except Exception:
        n = 0
    cur = normalize_lang(shop.get("_buyer_lang") or shop.get("lang"))
    rows = [
        [{"text": L(shop, "acc_balance_btn", value=format_price(shop, bal)),
          "callback_data": "acc:balance"}],
        [{"text": L(shop, "acc_history_btn", n=n), "callback_data": "acc:history"}],
        [{"text": L(shop, "acc_lang_btn",
                   lang=f"{LANG_FLAGS.get(cur, '')} {cur}"),
          "callback_data": "langmenu"}],
    ]
    text = L(shop, "account_title") + "\n\n" + L(shop, "acc_hub_hint")
    await _show(ctx, shop, tg, sender, edit, text, kb(rows + _back_row(shop)))


async def _send_balance(ctx, shop, tg, sender, edit=None) -> None:
    """Credit balance detail: the invitees whose purchases earned credit."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import credit_balance, format_price
    from rwa_plugin_pershop.telegram import kb

    try:
        rows = await ctx.db.fetch(
            """SELECT r.referred_tg_id, COALESCE(SUM(o.credit_units), 0) AS earned,
                      MAX(o.buyer_username) AS uname
               FROM plugin_pershop_referrals r LEFT JOIN plugin_pershop_orders o
                 ON o.shop_id=r.shop_id AND o.buyer_tg_id=r.referred_tg_id
                AND o.status='approved'
               WHERE r.shop_id=$1 AND r.referrer_tg_id=$2
               GROUP BY r.referred_tg_id ORDER BY earned DESC""",
            shop["id"], sender)
    except Exception:
        rows = []
    contrib = [r for r in (rows or [])
               if float(r.get("earned") or 0) > 0]
    bal = await credit_balance(ctx, shop["id"], sender)
    head = L(shop, "credit_balance", value=format_price(shop, bal))
    if not contrib:
        text = head + "\n\n" + L(shop, "acc_no_contrib")
    else:
        lines = []
        for r in contrib:
            name = r.get("uname") or str(r.get("referred_tg_id"))
            lines.append(f"@{name} · {format_price(shop, r.get('earned') or 0)}")
        text = head + "\n\n" + L(shop, "acc_contrib_title") + "\n\n" + "\n".join(lines)
    await _show(ctx, shop, tg, sender, edit, text, kb(
        [[{"text": L(shop, "back_btn"), "callback_data": "account"}]]))


async def _send_history(ctx, shop, tg, sender, edit=None) -> None:
    """Purchase history: every order ever placed (all statuses)."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import format_price, format_when
    from rwa_plugin_pershop.telegram import kb

    rows = await ctx.db.fetch(
        "SELECT o.*, p.name AS package_name, p.price_units AS package_units,"
        " s.name AS service_name"
        " FROM plugin_pershop_orders o LEFT JOIN plugin_pershop_packages p ON p.id=o.package_id"
        " LEFT JOIN plugin_pershop_services s ON s.id=o.service_id"
        " WHERE o.shop_id=$1 AND o.buyer_tg_id=$2 ORDER BY o.id DESC LIMIT 10",
        shop["id"], sender)
    status_word = {"pending": L(shop, "ord_pending"), "approved": L(shop, "ord_approved"),
                   "denied": L(shop, "ord_denied"), "cancelled": L(shop, "ord_cancelled"),
                   "withdraw_requested": L(shop, "pending_badge")}
    if not rows:
        text = L(shop, "account_title") + "\n\n" + L(shop, "history_empty")
    else:
        lines = []
        for o in rows:
            units = o.get("total_units")
            if units is None:
                units = o.get("package_units") or 0
            lines.append(f"{format_when(shop, o.get('created_at'))} · "
                         f"{o.get('package_name') or o.get('service_name') or ''} · "
                         f"{format_price(shop, units)} · "
                         f"{status_word.get(o.get('status'), o.get('status') or '')}")
        text = L(shop, "account_title") + "\n\n" + "\n".join(lines)
    await _show(ctx, shop, tg, sender, edit, text, kb(
        [[{"text": L(shop, "back_btn"), "callback_data": "account"}]]))


async def _send_lang_menu(ctx, shop, tg, sender, edit=None) -> None:
    """Buyer language picker (overrides the shop default for this buyer)."""
    from rwa_plugin_pershop.lang import LANGS, LANG_FLAGS, L, normalize_lang
    from rwa_plugin_pershop.telegram import kb

    cur = normalize_lang(shop.get("_buyer_lang") or shop.get("lang"))
    rows = [[{"text": (f"{LANG_FLAGS.get(code, '')} {name} ✅" if code == cur
                       else f"{LANG_FLAGS.get(code, '')} {name}"),
              "callback_data": f"langpick:{code}"}]
            for code, name in LANGS.items()]
    rows.append([{"text": L(shop, "back_btn"), "callback_data": "account"}])
    await _show(ctx, shop, tg, sender, edit, L(shop, "lang_title"), kb(rows))


async def _cancel_back_target(ctx, shop, order) -> str:
    """Back target for the cancel screens: the sub detail when the order is
    tied to a sub, else the subs list."""
    try:
        sub = await ctx.db.fetchrow(
            "SELECT id FROM plugin_pershop_subs WHERE shop_id=$1 AND username=$2",
            shop["id"], order.get("renew_username") or "")
    except Exception:
        sub = None
    return f"sub:{sub['id']}" if sub else "my_subs"


async def _send_withdraw_reason(ctx, shop, tg, sender, order_id: str, edit=None) -> None:
    """Withdraw step 1: only a still-pending order can enter the flow.
    Anything already decided answers 'too late' instead of a dead button."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    try:
        order = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_orders WHERE id=$1", int(order_id))
    except (TypeError, ValueError):
        order = None
    if not order or order.get("buyer_tg_id") != sender:
        await _send_subs_list(ctx, shop, tg, sender, edit=edit)
        return
    if order.get("status") != "pending":
        mid = await _show(ctx, shop, tg, sender, edit, L(shop, "withdraw_too_late"))
        await _send_subs_list(ctx, shop, tg, sender, edit=mid)
        return
    back = await _cancel_back_target(ctx, shop, order)
    mid = await _show(
        ctx, shop, tg, sender, edit, L(shop, "withdraw_reason_prompt", id=order["id"]),
        kb([[{"text": L(shop, "back_btn"), "callback_data": back}]]))
    await _set_session(ctx, shop["id"], sender, "buyer",
                       f"await_wd_reason:{order['id']}", {"back": back, "pm": mid})


async def _finalize_withdraw(ctx, shop, tg, sender, chat_id, order_id: int,
                             reason: str, card: str) -> None:
    """Withdraw step 2 done: mark the order, strip the old Approve/Deny
    buttons on the owner's decide message, and post the withdraw card with
    Confirm/Keep buttons. The buyer gets a confirmation; nothing is
    delivered for this receipt either way."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import format_price as _fp12
    from rwa_plugin_pershop.service import prefund_withdraw_credit, request_withdraw
    from rwa_plugin_pershop.telegram import kb

    try:
        order = await request_withdraw(ctx, order_id, sender, reason, card)
    except ValueError:
        await _clear_session(ctx, shop["id"], sender, "buyer")
        await tg.send_message(sender, L(shop, "withdraw_too_late"))
        await _send_subs_list(ctx, shop, tg, sender)
        return
    await _clear_session(ctx, shop["id"], sender, "buyer")
    # Mixed payment: the credit slice comes back instantly; the owner only
    # settles the transfer slice manually. Pure-transfer orders refund 0
    # here and behave exactly as before.
    prefunded = await prefund_withdraw_credit(ctx, order)
    if prefunded > 0:
        await tg.send_message(chat_id, L(shop, "withdraw_sent_mixed", id=order["id"],
                                        value=_fp12(shop, prefunded)))
    else:
        await tg.send_message(chat_id, L(shop, "withdraw_sent", id=order["id"]))
    osh = _owner_shop(shop)
    try:
        if order.get("owner_chat_id") and order.get("owner_msg_id"):
            await tg.edit_reply_markup(order["owner_chat_id"], order["owner_msg_id"])
    except Exception:
        pass
    try:
        card_text = L(osh, "owner_withdraw_request", id=order["id"],
                      username=order.get("buyer_username") or order.get("renew_username") or "",
                      plan=order.get("package_name") or "",
                      reason=order.get("withdraw_reason") or "",
                      card=order.get("withdraw_card") or "")
        if prefunded > 0:
            card_text += "\n" + L(osh, "owner_credit_returned",
                                 value=_fp12(osh, prefunded))
        _wd_card_id = await _show(
            ctx, shop, tg, shop["admin_tg_id"], order.get("owner_msg_id"), card_text,
            reply_markup=kb(
                [[{"text": L(osh, "wd_process_btn"),
                   "callback_data": f"wdprocess:{order['id']}"}]]))
        if _wd_card_id:
            try:
                await ctx.db.execute(
                    "UPDATE plugin_pershop_orders SET owner_msg_id=$1 WHERE id=$2",
                    _wd_card_id, order["id"])
            except Exception:
                pass
    except Exception as e:
        ctx.logger.warning("shop.withdraw_ping_failed", extra={"err": str(e)})
    try:
        sub = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_subs WHERE shop_id=$1 AND username=$2",
            shop["id"], order.get("renew_username") or "")
    except Exception:
        sub = None
    if sub:
        await _send_sub_detail(ctx, shop, tg, sender, sub)
    else:
        await _send_subs_list(ctx, shop, tg, sender)


async def _wd_open_order(ctx, shop, order_id: int):
    """The withdraw request being processed, or None when it already left
    the withdraw_requested state (approved/denied/withdrawn since)."""
    try:
        order = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_orders WHERE id=$1 AND shop_id=$2",
            order_id, shop["id"])
    except Exception:
        return None
    if not order or order.get("status") != "withdraw_requested":
        return None
    return order


async def _process_withdraw_cb(ctx, shop, tg, order_id: int, edit=None) -> None:
    """Owner opens a cancellation request (admin-only callback). One
    Process button -> Approve cancellation / Deny as fake receipt. There
    is deliberately no keep option: holding a buyer's cancel request
    hostage is not allowed."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    order = await _wd_open_order(ctx, shop, order_id)
    if not order:
        await _show(ctx, shop, tg, shop["admin_tg_id"], edit, L(shop, "wd_stale"))
        return
    await _show(
        ctx, shop, tg, shop["admin_tg_id"], edit, L(shop, "decide_order", id=order["id"]),
        reply_markup=kb(
            [[{"text": L(shop, "wd_approve_btn"),
               "callback_data": f"wdapprove:{order['id']}"},
              {"text": L(shop, "wd_deny_btn"),
               "callback_data": f"wddeny:{order['id']}"}]]))


async def _approve_withdraw_cb(ctx, shop, tg, order_id: int, edit=None) -> None:
    """Owner approves a cancellation (admin-only callback): show the
    buyer's bank details and wait for the refund receipt. The order only
    settles to withdrawn once that receipt arrives and is forwarded."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    order = await _wd_open_order(ctx, shop, order_id)
    if not order:
        await _show(ctx, shop, tg, shop["admin_tg_id"], edit, L(shop, "wd_stale"))
        return
    await _set_session(ctx, shop["id"], shop["admin_tg_id"], "admin",
                       f"await_refund:{order_id}", {"order_id": order_id, "pm": edit})
    await _show(ctx, shop, tg, shop["admin_tg_id"], edit,
        L(shop, "wd_approve_ask", id=order["id"],
          card=order.get("withdraw_card") or ""),
        reply_markup=kb(_cancel_row(shop)))


async def _maybe_scam_warn(ctx, shop, tg, buyer_id, buyer_un) -> None:
    """Warn this shop's owner when a network-flagged scammer shows purchase
    intent. At most one warning per buyer per shop per 24h (the flag is
    panel-global, the warning is per shop). Never raises."""
    from datetime import datetime, timedelta, timezone

    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import is_scammer

    try:
        if not buyer_id or buyer_id == shop.get("admin_tg_id"):
            return
        flag = await is_scammer(ctx, buyer_id)
        if not flag:
            return
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        try:
            noted = await ctx.db.fetchrow(
                "SELECT scam_noted_at FROM plugin_pershop_buyers WHERE shop_id=$1 AND tg_id=$2",
                shop["id"], buyer_id)
            last = (noted or {}).get("scam_noted_at")
            if last:
                last_dt = last if not isinstance(last, str) else datetime.fromisoformat(last)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                if last_dt >= since:
                    return
        except Exception:
            pass
        osh = _owner_shop(shop)
        reason = (flag.get("reason") or "").strip()
        await tg.send_message(
            shop["admin_tg_id"],
            L(osh, "scam_alert", buyer=buyer_un or buyer_id, buyer_id=buyer_id,
              marked_by=flag.get("marked_by_admin") or "?",
              reason=f" ({reason})" if reason else ""))
        try:
            await ctx.db.execute(
                """INSERT INTO plugin_pershop_buyers(shop_id,tg_id,lang,scam_noted_at)
                   VALUES($1,$2,'en',NOW())
                   ON CONFLICT(shop_id,tg_id) DO UPDATE SET scam_noted_at=NOW()""",
                shop["id"], buyer_id)
        except Exception:
            pass
    except Exception as e:
        ctx.logger.warning("shop.scam_warn_failed", extra={"err": str(e)})


def _fmt_exp(value) -> str:
    """YYYY-MM-DD HH:MM UTC out of a datetime or ISO string; never raises."""
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(str(value)) if isinstance(value, str) else value
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return str(value or "?")


async def _send_link_prompt(ctx, shop, tg, sender, edit=None) -> None:
    """Link step: ask for the sub link + Back to subs list."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    rows = [[{"text": L(shop, "back_btn"), "callback_data": "back:subs"}]]
    mid = await _show(ctx, shop, tg, sender, edit, L(shop, "link_sub_prompt"), kb(rows))
    await _set_session(ctx, shop["id"], sender, "buyer", "await_sub_link", {"pm": mid})


async def _send_renew_picker(ctx, shop, tg, sender, sub, edit=None) -> None:
    """Package-picker step for renewing one sub: flat package list + Back to detail."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    pkgs = _full_only(await _packages(ctx, shop["id"]))
    if not pkgs:
        await _show(ctx, shop, tg, sender, edit, L(shop, "no_packages"))
        return
    btns = [[{"text": f"{p['name']} — {p['price_text']}", "callback_data": f"renewpkg:{sub['id']}:{p['id']}"}]
            for p in pkgs]
    btns.append([{"text": L(shop, "back_btn"), "callback_data": f"sub:{sub['id']}"}])
    await _show(ctx, shop, tg, sender, edit, L(shop, "choose_renew_pkg"), kb(btns))


async def _send_renew_tiers(ctx, shop, tg, sender, sub, edit=None) -> None:
    """Renew entry: tier buttons (tier change allowed) + Back to detail."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    tiers = await ctx.db.fetch(
        "SELECT * FROM plugin_pershop_tiers WHERE shop_id=$1 AND active ORDER BY sort, id", shop["id"])
    if not tiers or shop.get("tiers_internal"):
        await _send_renew_picker(ctx, shop, tg, sender, sub, edit=edit)
        return
    rows = [[{"text": t["name"], "callback_data": f"renewtier:{sub['id']}:{t['id']}"}] for t in tiers]
    rows.append([{"text": L(shop, "back_btn"), "callback_data": f"sub:{sub['id']}"}])
    await _show(ctx, shop, tg, sender, edit, L(shop, "choose_tier"), kb(rows))


async def _send_renew_tier_packages(ctx, shop, tg, sender, sub, tier, edit=None) -> None:
    """Renew packages of one tier + Back to the renew tier list."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    pkgs = await ctx.db.fetch(
        "SELECT * FROM plugin_pershop_packages WHERE tier_id=$1 AND active ORDER BY sort, id", tier["id"])
    rows = [[{"text": _pkg_label(shop, p), "callback_data": f"renewpkg:{sub['id']}:{p['id']}"}] for p in _full_only(pkgs)]
    rows.append([{"text": L(shop, "back_btn"), "callback_data": f"renew:{sub['id']}"}])
    await _show(ctx, shop, tg, sender, edit, L(shop, "tier_pkg_title"), kb(rows))




def _hwid_line(shop, pkg, hwid_value):
    """Summary line for hwid, or None when the package leaves panel default."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import format_price, hwid_cfg, hwid_extra

    cfg = hwid_cfg(pkg)
    if cfg["mode"] == "default":
        return None
    if cfg["mode"] == "unlimited":
        value = L(shop, "unlimited_lbl")
    elif cfg["mode"] == "fixed":
        value = str(cfg["fixed"])
    else:
        value = str(hwid_value if hwid_value is not None else cfg["default"])
    base = float(pkg.get("price_units") or 0)
    return L(shop, "summary_opt", name=L(shop, "step_hwid").rstrip(":："),
             value=value, price=format_price(shop, hwid_extra(base, cfg, hwid_value)))


async def _send_hwid(ctx, shop, tg, sender, pkg, value, edit=None) -> None:
    """Device-count picker. Shown only for input-mode packages; stores await_hwid."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import hwid_cfg
    from rwa_plugin_pershop.telegram import kb

    cfg = hwid_cfg(pkg)
    value = max(cfg["min"], min(cfg["max"], int(value or cfg["default"])))
    back_cb = f"back:tier:{pkg['tier_id']}" if pkg.get("tier_id") else "menu"
    await _show(ctx, shop, tg, sender, edit,
        f"<b>{L(shop, 'step_hwid').rstrip(':：')}</b>\n"
        f"{value}  ({cfg['min']} - {cfg['max']})\n"
        f"{L(shop, 'per_extra', p=cfg['percent'])}",
        kb([
            [{"text": "-", "callback_data": "hminus"},
             {"text": "+", "callback_data": "hplus"}],
            [{"text": L(shop, "opt_next"), "callback_data": "hnext"}],
            [{"text": L(shop, "back_btn"), "callback_data": back_cb}],
        ]))
    await _set_session(ctx, shop["id"], sender, "buyer", f"await_hwid:{pkg['id']}",
                       {"package_id": pkg["id"], "tier_id": pkg.get("tier_id"),
                        "hwid": value})


def _contract_time_line(shop, pv, sub):
    """Buyer-facing time line for the contract screen. Never raises."""
    from datetime import datetime, timezone

    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import format_expire, is_timeless

    try:
        effect = pv.get("effect") or "full"
        new = pv.get("new_expire_at")
        if effect == "traffic_only":
            return L(shop, "contract_time_same", date=format_expire(shop, new))
        if pv.get("unlimited_time"):
            return L(shop, "contract_time_keep")
        date = format_expire(shop, new)
        if sub is not None:
            cur = pv.get("current_expire_at")
            try:
                aware = cur if getattr(cur, "tzinfo", None) else cur.replace(tzinfo=timezone.utc)
                if aware and aware > datetime.now(timezone.utc) and not is_timeless(cur):
                    return L(shop, "contract_time_carry",
                             left=L(shop, "t_left", d=(aware - datetime.now(timezone.utc)).days),
                             date=date)
            except Exception:
                pass
        return L(shop, "contract_time_fresh", date=date)
    except Exception:
        return ""


def _contract_traffic_line(shop, pv, sub):
    """Buyer-facing traffic line for the contract screen. Never raises."""
    from rwa_plugin_pershop.lang import L

    try:
        effect = pv.get("effect") or "full"
        target = pv.get("new_cap_bytes")
        if effect == "time_only":
            return L(shop, "contract_traffic_keep")
        if target is None:
            return L(shop, "contract_traffic_unlimited")
        cap = _fmt_gb(target)
        left = pv.get("leftover_bytes")
        rollover = effect == "traffic_only" or (sub is not None and not pv.get("reset", True))
        if rollover and left is not None:
            return L(shop, "contract_traffic_roll", left=_fmt_gb(max(0, left)), cap=cap)
        if sub is None or left is None:
            return L(shop, "contract_cap_new", cap=cap)
        return L(shop, "contract_traffic_reset", cap=cap)
    except Exception:
        return ""


async def _ping_owner(ctx, shop, text) -> None:
    """Best-effort owner DM from bot flows (match prompts). Never raises."""
    try:
        from rwa_plugin_pershop.crypto import decrypt_token
        from rwa_plugin_pershop.telegram import TgClient

        token = await decrypt_token(ctx, shop["bot_token_enc"])
        tg = TgClient(token)
        await tg.send_message(shop["admin_tg_id"], text)
    except Exception as e:
        ctx.logger.warning("shop.ping_owner_failed", extra={"err": str(e)})


async def _send_contract(ctx, shop, tg, sender, sub, pkg, hwid_value=None, back="menu",
                         credit_applied=False, edit=None) -> dict:
    """Universal contract screen: plan, exact outcome, price — then Pay.

    sub None = brand-new buy. Back is a callback string re-rendering the
    previous screen. Stores the await_summary draft that stconfirm reads.
    """
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import contract_preview, credit_balance, format_price
    from rwa_plugin_pershop.telegram import kb

    pv = await contract_preview(ctx, shop, sub, pkg, hwid_value)
    lines = [L(shop, "contract_title"),
             L(shop, "contract_plan", plan=pkg["name"],
                price=format_price(shop, pv["price_units"]))]
    if pv["effect"] == "time_only" and pkg.get("duration_days"):
        lines.append(L(shop, "contract_days_add", days=pkg["duration_days"]))
    for ln in (_contract_time_line(shop, pv, sub), _contract_traffic_line(shop, pv, sub)):
        if ln:
            lines.append(ln)
    hwid = _hwid_line(shop, pkg, hwid_value)
    if hwid:
        lines.append(hwid)
    bal = await credit_balance(ctx, shop["id"], sender)
    credit_row = []
    use_credit = bool(credit_applied) and bal > 0
    if bal > 0 and not use_credit:
        lines.append(L(shop, "credit_contract_line",
                         value=format_price(shop, bal)))
        credit_row = [[{"text": L(shop, "credit_use_btn"), "callback_data": "credituse"}]]
    elif use_credit:
        used = min(bal, float(pv["price_units"] or 0))
        due = float(pv["price_units"] or 0) - used
        lines.append(L(shop, "credit_due", used=format_price(shop, used),
                         due=format_price(shop, due)) if due > 0
                     else L(shop, "credit_full"))
    lines.append(L(shop, "contract_hint"))
    saved = {"package_id": pkg["id"],
             "renew_username": sub["username"] if sub else None,
             "sub_id": sub["id"] if sub else None,
             "hwid": hwid_value, "total_units": pv["price_units"], "back": back,
             "credit": bool(use_credit)}
    await _set_session(ctx, shop["id"], sender, "buyer", f"await_summary:{pkg['id']}", saved)
    await _show(ctx, shop, tg, sender, edit, "\n\n".join(lines), kb(
        [[{"text": L(shop, "contract_pay_btn"), "callback_data": "stconfirm"}]]
        + credit_row +
        [[{"text": L(shop, "back_btn"), "callback_data": back}]]))
    return saved


async def _drop_user_msg(tg, chat_id: int, message_id) -> None:
    """Delete a consumed buyer message (panel taps, /start, consumed inputs),
    RWA-bot style — the DM keeps only bot screens plus the receipt anchor.
    Receipts are never passed here. Best-effort: never raises."""
    try:
        if message_id:
            await tg.delete_message(chat_id, message_id)
    except Exception:
        pass


async def _on_buyer_message(ctx, shop, tg, msg, text) -> None:
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    chat_id = (msg.get("chat") or {}).get("id")
    from_info = msg.get("from") or {}
    buyer_id = from_info.get("id")
    buyer_un = from_info.get("username") or ""
    user_mid = msg.get("message_id")
    ses = await _get_session(ctx, shop["id"], buyer_id, "buyer")

    if text.startswith("/cancel"):
        await _clear_session(ctx, shop["id"], buyer_id, "buyer")
        await tg.send_message(chat_id, L(shop, "cancel_again"))
        await _drop_user_msg(tg, chat_id, user_mid)
        return

    if text.startswith("/start"):
        parts = text.split()
        if len(parts) > 1 and parts[1].startswith("r_"):
            try:
                from rwa_plugin_pershop.service import record_referral

                await record_referral(ctx, shop["id"], int(parts[1][2:]), buyer_id)
                await tg.send_message(chat_id, L(shop, "ref_welcome"))
            except (TypeError, ValueError):
                pass
        await _send_buyer_menu(ctx, shop, tg, chat_id)
        await _clear_session(ctx, shop["id"], buyer_id, "buyer")
        await _drop_user_msg(tg, chat_id, user_mid)
        return

    # Persistent-panel tap (plain-text message): same destinations as the menu.
    # Runs before session input branches, so the panel escapes any screen.
    from rwa_plugin_pershop.lang import menu_action_for_text

    panel_action = menu_action_for_text(text)
    if panel_action:
        await _clear_session(ctx, shop["id"], buyer_id, "buyer")
        await _on_menu_action(ctx, shop, tg, chat_id, buyer_id, buyer_un, panel_action)
        await _drop_user_msg(tg, chat_id, user_mid)
        return

    # Withdraw flow: reason first, then refund card details (two steps).
    if ses and (ses["state"] or "").startswith("await_wd_reason:"):
        import json as _json

        try:
            saved = _json.loads(ses.get("data") or "{}")
            wd_order = int(ses["state"].split(":", 1)[1])
        except (TypeError, ValueError, KeyError, IndexError):
            wd_order, saved = None, {}
        reason = (text or "").strip()[:1000]
        if not wd_order or not reason:
            await tg.send_message(chat_id, L(shop, "withdraw_reason_prompt", id=wd_order or "?"))
            await _drop_user_msg(tg, chat_id, user_mid)
            return
        await _set_session(ctx, shop["id"], buyer_id, "buyer",
                           f"await_wd_card:{wd_order}",
                           {"back": saved.get("back", "my_subs"), "reason": reason})
        await _drop_user_msg(tg, chat_id, user_mid)
        await tg.send_message(
            chat_id, L(shop, "withdraw_card_prompt"),
            reply_markup=kb([[{"text": L(shop, "back_btn"),
                               "callback_data": saved.get("back", "my_subs")}]]))
        return

    if ses and (ses["state"] or "").startswith("await_wd_card:"):
        import json as _json

        try:
            saved = _json.loads(ses.get("data") or "{}")
            wd_order = int(ses["state"].split(":", 1)[1])
        except (TypeError, ValueError, KeyError, IndexError):
            wd_order, saved = None, {}
        card = (text or "").strip()[:1000]
        if not wd_order or not card:
            await tg.send_message(chat_id, L(shop, "withdraw_card_prompt"))
            await _drop_user_msg(tg, chat_id, user_mid)
            return
        await _drop_user_msg(tg, chat_id, user_mid)
        await _finalize_withdraw(ctx, shop, tg, buyer_id, chat_id, wd_order,
                                 saved.get("reason", ""), card)
        return

    # Sub link while awaiting one?
    if ses and (ses["state"] or "") == "await_sub_link":
        from rwa_plugin_pershop.service import _extract_token, link_subscription

        back = kb([[{"text": L(shop, "back_btn"), "callback_data": "back:subs"}]])
        token = _extract_token(text or "")
        await _drop_user_msg(tg, chat_id, user_mid)
        if not token:
            await tg.send_message(chat_id, L(shop, "link_sub_not_found"), reply_markup=back)
            return
        status, info = await link_subscription(ctx, shop, token, buyer_id)
        if status in ("linked", "already_yours"):
            await _clear_session(ctx, shop["id"], buyer_id, "buyer")
            if status == "linked":
                await tg.send_message(chat_id, L(shop, "link_sub_success", username=info["username"],
                                                 expire=_fmt_exp(info.get("expire_at"))))
                # Linked as-is: the sub keeps whatever the system holds
                # (status, expiry, traffic). No plan is needed — renewing
                # later just picks a package from this shop.
            else:
                await tg.send_message(chat_id, L(shop, "link_sub_already_yours"))
            await _send_subs_list(ctx, shop, tg, buyer_id)
            return
        key = {"not_found": "link_sub_not_found", "taken": "link_sub_taken",
               "foreign": "link_sub_not_yours"}.get(status, "link_sub_failed")
        await tg.send_message(chat_id, L(shop, key), reply_markup=back)
        return

    # Service note while awaiting one? (step 1 of an Other-Service order)
    if ses and (ses["state"] or "").startswith("await_svc_note:"):
        from rwa_plugin_pershop.service import service_by_id

        try:
            note_svc_id = int(ses["state"].split(":", 1)[1])
        except (TypeError, ValueError, IndexError):
            note_svc_id = None
        svc = await service_by_id(ctx, note_svc_id) if note_svc_id else None
        if not svc:
            await _clear_session(ctx, shop["id"], buyer_id, "buyer")
            await tg.send_message(chat_id, L(shop, "svc_unavailable"))
            return
        note = (text or "").strip()
        if len(note) > 500:
            await tg.send_message(chat_id, L(shop, "note_toolong"))
            return
        import json as _njson

        try:
            back = _njson.loads(ses.get("data") or "{}").get("back") or f"svc:{svc['id']}"
        except Exception:
            back = f"svc:{svc['id']}"
        await _drop_user_msg(tg, chat_id, user_mid)
        await _send_svc_contract(ctx, shop, tg, buyer_id, svc, note, back)
        return

    # Receipt while awaiting one?
    if ses and (ses["state"] or "").startswith("await_receipt:"):
        import json

        try:
            data = json.loads(ses["data"] or "{}")
        except Exception:
            data = {}
        if data.get("service_id"):
            await _submit_svc_receipt(ctx, shop, tg, msg, text, buyer_id,
                                      buyer_un, chat_id, data)
            return
        if "package_id" not in data:
            await _clear_session(ctx, shop["id"], buyer_id, "buyer")
            await tg.send_message(chat_id, L(shop, "session_expired"))
            return
        receipt_ref, kind, message_id = _extract_receipt(msg, text)
        if not receipt_ref:
            await tg.send_message(chat_id, L(shop, "need_receipt"))
            return
        pkg = await ctx.db.fetchrow("SELECT * FROM plugin_pershop_packages WHERE id=$1", data["package_id"])
        if not pkg:
            await _clear_session(ctx, shop["id"], buyer_id, "buyer")
            await tg.send_message(chat_id, L(shop, "pkg_gone"))
            return
        import json as _json

        from rwa_plugin_pershop.service import (
            contract_preview,
            format_price,
            order_total,
            pending_for_sub,
            pkg_effect,
            resolve_applied,
        )

        if data.get("renew_username") and await pending_for_sub(
                ctx, shop["id"], data["renew_username"]):
            # A second order for the same sub slipped in while paying:
            # keep the first, explain, drop this one.
            await _clear_session(ctx, shop["id"], buyer_id, "buyer")
            await tg.send_message(chat_id, L(shop, "pending_note"))
            return

        hwid_value = data.get("hwid")
        base = float(pkg.get("price_units") or 0)
        total = data.get("total_units")
        total = float(total) if total is not None else order_total(base, pkg, hwid_value)
        credit_used = 0.0
        if data.get("credit"):
            # Re-check at submit: balance may have moved since Pay.
            from rwa_plugin_pershop.service import add_credit as _ac
            from rwa_plugin_pershop.service import credit_balance as _cb2

            _bal = await _cb2(ctx, shop["id"], buyer_id)
            credit_used = min(_bal, total)
            if credit_used > 0:
                await _ac(ctx, shop["id"], buyer_id, -credit_used)
        applied = await resolve_applied(ctx, pkg, hwid_value)
        snapshot = _json.dumps({"hwid": hwid_value, "total_units": total,
                                "applied": applied, "credit_used": credit_used})
        row = await ctx.db.fetchrow(
            """INSERT INTO plugin_pershop_orders(shop_id,package_id,buyer_tg_id,buyer_username,
               receipt_file_id,receipt_kind,renew_username,total_units,options_snapshot)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id""",
            shop["id"], data["package_id"], buyer_id, buyer_un, receipt_ref, kind,
            data.get("renew_username"), total, snapshot,
        )
        await _clear_session(ctx, shop["id"], buyer_id, "buyer")
        await _maybe_scam_warn(ctx, shop, tg, buyer_id, buyer_un)
        osh = _owner_shop(shop)
        action = L(osh, "action_renew") if data.get("renew_username") else L(osh, "action_new")
        header = L(osh, "order_header", id=row["id"], action=action,
                   buyer=buyer_un, buyer_id=buyer_id, pkg=pkg["name"],
                   price=format_price(shop, total))
        if credit_used > 0:
            header += "\n" + L(osh, "credit_owner_line",
                              used=format_price(shop, credit_used),
                              due=format_price(shop, total - credit_used))
        if data.get("renew_username"):
            header += L(osh, "order_renew_line", renew=data["renew_username"])
        hwid_line = _hwid_line(osh, pkg, hwid_value)
        if hwid_line:
            header += "\n" + hwid_line
        if pkg_effect(pkg) != "full" and data.get("renew_username"):
            # Top-up: show the owner the exact math before they decide.
            sub_row = await ctx.db.fetchrow(
                "SELECT * FROM plugin_pershop_subs WHERE shop_id=$1 AND username=$2",
                shop["id"], data["renew_username"])
            pv = await contract_preview(ctx, shop, sub_row, pkg, hwid_value)
            for ln in (_contract_time_line(osh, pv, sub_row),
                       _contract_traffic_line(osh, pv, sub_row)):
                if ln:
                    header += "\n" + ln
        # Forward original message when possible (best fidelity for photos),
        # always follow with a text header + decision buttons. Owner-directed
        # text stays in the shop language; the decide message id is stored so
        # a later withdraw request can strip its Approve/Deny buttons.
        try:
            if kind in ("photo", "doc") and message_id:
                try:
                    await tg.forward_message(shop["admin_tg_id"], chat_id, message_id)
                except Exception:
                    pass
            if kind == "text":
                header += L(osh, "order_receipt_line", ref=receipt_ref)
            await tg.send_message(shop["admin_tg_id"], header)
            decide_id = await tg.send_message(
                shop["admin_tg_id"], L(osh, "decide_order", id=row["id"]),
                reply_markup=kb([[{"text": L(osh, "approve_btn"), "callback_data": f"approve:{row['id']}"},
                                  {"text": L(osh, "deny_btn"), "callback_data": f"deny:{row['id']}"}]]))
            try:
                await ctx.db.execute(
                    "UPDATE plugin_pershop_orders SET owner_chat_id=$1, owner_msg_id=$2 WHERE id=$3",
                    shop["admin_tg_id"], decide_id, row["id"])
            except Exception:
                pass
        except Exception as e:
            ctx.logger.warning("shop.forward_failed", extra={"err": str(e)})
        from rwa_plugin_pershop.service import message_text

        # Receipt exception: drop the prompt, send one fresh waiting message,
        # and remember its id so the later decision can edit it in place.
        try:
            if data.get("pm"):
                await tg.delete_message(chat_id, data["pm"])
        except Exception:
            pass
        thank_id = await tg.send_message(chat_id, message_text(shop, "thank_you", order=row["id"]))
        try:
            await ctx.db.execute(
                "UPDATE plugin_pershop_orders SET buyer_msg_id=$1 WHERE id=$2",
                thank_id, row["id"])
        except Exception:
            pass
        return

    # No command, no pending step: re-show the button menu (no commands to learn).
    await _send_buyer_menu(ctx, shop, tg, chat_id)


async def _on_admin_message(ctx, shop, tg, msg, text) -> None:
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    chat_id = (msg.get("chat") or {}).get("id")
    ses = await _get_session(ctx, shop["id"], shop["admin_tg_id"], "admin")

    if text.startswith("/start"):
        try:
            cnt = await ctx.db.fetchval(
                "SELECT COUNT(*) FROM plugin_pershop_orders WHERE shop_id=$1"
                " AND status IN ('pending','withdraw_requested')",
                shop["id"])
        except Exception:
            cnt = "?"
        await tg.send_message(chat_id, L(shop, "admin_panel", cnt=cnt))
        return

    if text.startswith("/cancel"):
        await _clear_session(ctx, shop["id"], shop["admin_tg_id"], "admin")
        await tg.send_message(chat_id, L(shop, "admin_cancelled"))
        return

    # Owner preview: the persistent buyer panel stays in the owner's client
    # (e.g. from before admin_tg_id was set), so its label taps would land
    # here and die silently. When no admin input session is consuming free
    # text (deny reason / refund receipt), answer them as buyer actions keyed
    # by the owner's own id — the owner can walk the whole buyer flow, and
    # any test order simply arrives back in this same DM for approval.
    _admin_busy = bool(ses and (ses["state"] or "").startswith(("await_refund:", "await_reason:")))
    if not _admin_busy:
        from rwa_plugin_pershop.lang import menu_action_for_text as _maft

        _preview = _maft(text)
        if _preview:
            await _on_menu_action(ctx, shop, tg, chat_id, shop["admin_tg_id"],
                                  f"owner{shop['admin_tg_id']}", _preview)
            return
        # Preview purchase in progress: the receipt the owner sends for their
        # own test order belongs to the buyer flow (role "buyer" session).
        _bses = await _get_session(ctx, shop["id"], shop["admin_tg_id"], "buyer")
        if _bses and (_bses["state"] or "").startswith("await_receipt:"):
            await _on_buyer_message(ctx, shop, tg, msg, text)
            return

    if ses and (ses["state"] or "").startswith("await_refund:"):
        import json

        try:
            _wd = json.loads(ses["data"] or "{}")
            order_id = _wd["order_id"]
        except Exception:
            await _clear_session(ctx, shop["id"], shop["admin_tg_id"], "admin")
            return
        from rwa_plugin_pershop.lang import L as _L2
        from rwa_plugin_pershop.service import resolve_withdraw as _resolve_wd
        from rwa_plugin_pershop.service import shop_for_tg as _sft5

        order = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_orders WHERE id=$1 AND shop_id=$2",
            order_id, shop["id"])
        if not order or order.get("status") != "withdraw_requested":
            await tg.send_message(chat_id, _L2(shop, "wd_stale"))
            await _clear_session(ctx, shop["id"], shop["admin_tg_id"], "admin")
            return
        try:
            _bshop = await _sft5(ctx, shop, order["buyer_tg_id"])
            await tg.send_message(order["buyer_tg_id"],
                _L2(_bshop, "refund_receipt_caption", id=order["id"]))
            await tg.forward_message(order["buyer_tg_id"], chat_id, msg.get("message_id"))
        except Exception as e:
            ctx.logger.warning("shop.refund_forward_failed", extra={"err": str(e)})
        try:
            settled = await _resolve_wd(ctx, order_id, shop.get("owner_admin") or "", True)
        except ValueError as e:
            await tg.send_message(chat_id, _L2(shop, "order_error", id=order_id, err=e))
            await _clear_session(ctx, shop["id"], shop["admin_tg_id"], "admin")
            return
        await _clear_session(ctx, shop["id"], shop["admin_tg_id"], "admin")
        try:
            await _show(ctx, shop, tg, order["buyer_tg_id"], order.get("buyer_msg_id"),
                        _L2(_bshop, "withdraw_done", id=order["id"]))
            if float(settled.get("credit_refunded") or 0) > 0:
                from rwa_plugin_pershop.service import format_price as _fp12

                await tg.send_message(order["buyer_tg_id"],
                    _L2(_bshop, "credit_refunded",
                        value=_fp12(_bshop, float(settled["credit_refunded"]))))
            await _show(ctx, shop, tg, chat_id, _wd.get("pm"),
                        _L2(shop, "owner_wd_done", id=order["id"]))
        except Exception as e:
            ctx.logger.warning("shop.notify_failed", extra={"err": str(e)})
        return
    if ses and (ses["state"] or "").startswith("await_reason:"):
        import json

        try:
            order_id = json.loads(ses["data"] or "{}")["order_id"]
        except Exception:
            await _clear_session(ctx, shop["id"], shop["admin_tg_id"], "admin")
            return
        from rwa_plugin_pershop.service import deny_order

        reason = text or L(shop, "no_reason")
        try:
            refunded = await deny_order(ctx, order_id, "admin-tg", reason)
        except ValueError as e:
            await tg.send_message(chat_id, L(shop, "order_error", id=order_id, err=e))
            await _clear_session(ctx, shop["id"], shop["admin_tg_id"], "admin")
            return
        order = await ctx.db.fetchrow("SELECT * FROM plugin_pershop_orders WHERE id=$1", order_id)
        await _clear_session(ctx, shop["id"], shop["admin_tg_id"], "admin")
        from rwa_plugin_pershop.service import message_text as _mt
        _dplan = ""
        try:
            if order.get("package_id"):
                _dpkg = await ctx.db.fetchrow(
                    "SELECT name FROM plugin_pershop_packages WHERE id=$1", order["package_id"])
                _dplan = (_dpkg or {}).get("name") or ""
            elif order.get("service_id"):
                _dsvc = await ctx.db.fetchrow(
                    "SELECT name FROM plugin_pershop_services WHERE id=$1", order["service_id"])
                _dplan = (_dsvc or {}).get("name") or ""
        except Exception:
            pass

        try:
            await _show(ctx, shop, tg, order["buyer_tg_id"], order.get("buyer_msg_id"),
                        _mt(shop, "denied", reason=reason,
                            username=order.get("buyer_username") or "", plan=_dplan))
            if (refunded or 0) > 0:
                from rwa_plugin_pershop.service import format_price as _fp10
                from rwa_plugin_pershop.service import shop_for_tg as _sft4

                _bshop = await _sft4(ctx, shop, order["buyer_tg_id"])
                await tg.send_message(order["buyer_tg_id"],
                    L(_bshop, "credit_refunded", value=_fp10(_bshop, float(refunded))))
        except Exception as e:
            ctx.logger.warning("shop.notify_buyer_failed", extra={"err": str(e)})
        who = order.get("buyer_username") or str(order.get("buyer_tg_id"))
        await _show(ctx, shop, tg, chat_id, order.get("owner_msg_id"),
            L(_owner_shop(shop), "owner_final_denied",
              id=order_id, username=who, reason=reason))
        try:
            from rwa_plugin_pershop.telegram import kb as _kb3

            await tg.send_message(chat_id,
                L(shop, "scam_offer", id=order_id, reason=reason),
                reply_markup=_kb3(
                    [[{"text": L(shop, "scam_btn"),
                       "callback_data": f"scam:{order_id}"}]]))
        except Exception as e:
            ctx.logger.warning("shop.scam_offer_failed", extra={"err": str(e)})
        return
    await tg.send_message(chat_id, L(shop, "admin_use_buttons"))


async def _load_draft(ctx, shop, sender):
    """Buyer's in-progress draft: (pkg, hwid_value, state, data) or None."""
    import json

    ses = await _get_session(ctx, shop["id"], sender, "buyer")
    if not ses:
        return None
    try:
        data = json.loads(ses["data"] or "{}")
    except Exception:
        data = {}
    pkg = await ctx.db.fetchrow(
        "SELECT * FROM plugin_pershop_packages WHERE id=$1", data.get("package_id"))
    if not pkg:
        return None
    return pkg, data.get("hwid"), ses["state"] or "", data


async def _send_topup_picker(ctx, shop, tg, sender, sub, effect, edit=None) -> None:
    """Offer list for one top-up kind (+traffic / +days) + Back to detail."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import format_price
    from rwa_plugin_pershop.telegram import kb

    offers = await ctx.db.fetch(
        "SELECT * FROM plugin_pershop_packages WHERE shop_id=$1 AND active AND effect=$2"
        " ORDER BY price_units, id", shop["id"], effect)
    tier = None
    if sub.get("package_id"):
        try:
            cur = await ctx.db.fetchrow(
                "SELECT tier_id FROM plugin_pershop_packages WHERE id=$1", sub["package_id"])
        except Exception:
            cur = None
        tier = (cur or {}).get("tier_id")
    offers = [dict(o) for o in offers or []
              if o.get("scope_tier_id") is None
              or (tier is not None and int(o["scope_tier_id"]) == int(tier))]
    if not offers:
        await _send_sub_detail(ctx, shop, tg, sender, sub, edit=edit)
        return
    rows = [[{"text": f"{o['name']} — {format_price(shop, o.get('price_units') or 0)}",
              "callback_data": f"topick:{sub['id']}:{o['id']}"}] for o in offers]
    rows.append([{"text": L(shop, "back_btn"), "callback_data": f"sub:{sub['id']}"}])
    await _show(ctx, shop, tg, sender, edit, L(shop, "topup_pick", username=sub["username"]),
                kb(rows))


async def _topup_sub(ctx, shop, sender, sub_id: int):
    """Owned sub row or None (shared by the top-up callbacks)."""
    try:
        sub_id = int(sub_id)
    except (TypeError, ValueError):
        return None
    sub = await ctx.db.fetchrow(
        "SELECT * FROM plugin_pershop_subs WHERE id=$1 AND shop_id=$2", sub_id, shop["id"])
    if not sub or sub.get("buyer_tg_id") != sender:
        return None
    return sub


async def _draft_sub(ctx, shop, saved):
    """Sub row behind a summary/receipt draft (by id, else renew username)."""
    sub = None
    if saved.get("sub_id"):
        try:
            sub_id = int(saved["sub_id"])
        except (TypeError, ValueError):
            sub_id = None
        if sub_id is not None:
            sub = await ctx.db.fetchrow(
                "SELECT * FROM plugin_pershop_subs WHERE id=$1 AND shop_id=$2",
                sub_id, shop["id"])
    if sub is None and saved.get("renew_username"):
        sub = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_subs WHERE shop_id=$1 AND username=$2",
            shop["id"], saved["renew_username"])
    return sub


async def _to_summary(ctx, shop, tg, sender, pkg, hwid_value=None, sub=None, back=None, edit=None) -> None:
    """Show the contract screen (was: plain summary)."""
    if back is None:
        back = f"back:tier:{pkg['tier_id']}" if pkg.get("tier_id") else "menu"
    await _send_contract(ctx, shop, tg, sender, sub, pkg, hwid_value, back, edit=edit)


async def _auto_credit_order(ctx, shop, tg, cb, kind, item, saved) -> bool:
    """Full-credit auto-approval: when the buyer's credit balance covers the
    whole total, the order is created already paid (receipt_kind 'credit')
    and approved immediately — no receipt, no owner decision. Earned referral
    credit still works because rewards are minted on the cash portion only
    (zero here). Returns True when auto-approved. Never raises."""
    import json as _json3

    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import add_credit as _ac4
    from rwa_plugin_pershop.service import approve_order as _ao2
    from rwa_plugin_pershop.service import credit_balance as _cb5
    from rwa_plugin_pershop.service import format_price as _fp7
    from rwa_plugin_pershop.service import resolve_applied as _ra2

    sender = (cb.get("from") or {}).get("id")
    buyer_un = (cb.get("from") or {}).get("username") or str(sender)
    total = float(saved.get("total_units") or 0)
    if total <= 0:
        return False
    if (await _cb5(ctx, shop["id"], sender)) < total:
        return False
    if kind == "svc":
        snapshot = _json3.dumps({"service": item["id"],
                                 "note": (saved.get("note") or "")[:500],
                                 "credit_used": total})
        row = await ctx.db.fetchrow(
            """INSERT INTO plugin_pershop_orders(shop_id,service_id,buyer_tg_id,buyer_username,
               receipt_file_id,receipt_kind,total_units,options_snapshot)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id""",
            shop["id"], item["id"], sender, buyer_un, None, "credit", total, snapshot)
    else:
        applied = await _ra2(ctx, item, saved.get("hwid"))
        snapshot = _json3.dumps({"hwid": saved.get("hwid"), "total_units": total,
                                 "applied": applied, "credit_used": total})
        row = await ctx.db.fetchrow(
            """INSERT INTO plugin_pershop_orders(shop_id,package_id,buyer_tg_id,buyer_username,
               receipt_file_id,receipt_kind,renew_username,total_units,options_snapshot)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id""",
            shop["id"], item["id"], sender, buyer_un, None, "credit",
            saved.get("renew_username"), total, snapshot)
    await _ac4(ctx, shop["id"], sender, -total)
    order = await ctx.db.fetchrow(
        "SELECT * FROM plugin_pershop_orders WHERE id=$1", row["id"])
    sub_pre = None
    if saved.get("renew_username"):
        sub_pre = await ctx.db.fetchrow(
            "SELECT expire_at FROM plugin_pershop_subs WHERE shop_id=$1 AND username=$2",
            shop["id"], saved["renew_username"])
    try:
        res = await _ao2(ctx, row["id"], "auto-credit")
    except Exception as e:
        # Panel call failed: give the balance back, drop the order row, and
        # let the buyer fall through to the normal receipt flow.
        ctx.logger.warning("shop.auto_approve_failed", extra={"err": str(e)})
        try:
            await _ac4(ctx, shop["id"], sender, total)
            await ctx.db.execute(
                "DELETE FROM plugin_pershop_orders WHERE id=$1", row["id"])
        except Exception:
            pass
        return False
    osh = _owner_shop(shop)
    label = item.get("name") or "?"
    if kind == "pkg" and not saved.get("renew_username") and res.get("username"):
        # Link a new-buy auto order to the sub it created, so a later
        # credit-cancel can find its originating order by username.
        try:
            await ctx.db.execute(
                "UPDATE plugin_pershop_orders SET renew_username=$1 WHERE id=$2",
                res["username"], row["id"])
        except Exception:
            pass
    _cb_mid = (cb.get("message") or {}).get("message_id")
    await _show(ctx, shop, tg, sender, _cb_mid,
        L(shop, "auto_approved_note", id=row["id"], item=label,
          amount=_fp7(shop, total)))
    await _notify_approval(ctx, shop, tg, order, res, sub_pre)
    try:
        await tg.send_message(shop["admin_tg_id"],
            L(osh, "auto_approved_note", id=row["id"], item=label,
              amount=_fp7(osh, total)))
    except Exception as e:
        ctx.logger.warning("shop.owner_note_failed", extra={"err": str(e)})
    await _maybe_scam_warn(ctx, shop, tg, sender, buyer_un)
    return True


async def _notify_approval(ctx, shop, tg, order, res, sub_pre=None, edit=None) -> None:
    """Buyer-side DMs for an approved order: the service done-text or the
    effect-aware VPN texts, plus the inviter earn notice. Shared by the
    owner Approve button and full-credit auto-approval. Never raises."""
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.service import message_text as _mt

    bmid = order.get("buyer_msg_id") or edit
    if res.get("service"):
        try:
            from rwa_plugin_pershop.service import shop_for_tg as _sft
            from rwa_plugin_pershop.service import fmt_safe as _fs

            bshop = await _sft(ctx, shop, order["buyer_tg_id"])
            await _show(ctx, shop, tg, order["buyer_tg_id"], bmid,
                _fs(res.get("done_text") or L(bshop, "service_done_default"),
                    username=order.get("buyer_username") or ""))
        except Exception as e:
            ctx.logger.warning("shop.notify_buyer_failed", extra={"err": str(e)})
        return
    try:
        from rwa_plugin_pershop.service import approval_vars as _av

        _card = await _av(ctx, shop, order, res)
        eff = res.get("effect") or "full"
        pkg = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_packages WHERE id=$1", order["package_id"])
        target = (pkg or {}).get("traffic_limit_bytes")
        if res.get("renewed") and eff == "traffic_only":
            added = max(0, (res.get("traffic_limit_bytes") or 0) - (target or 0))
            await _show(ctx, shop, tg, order["buyer_tg_id"], bmid,
                L(shop, "contract_traffic_roll", left=_fmt_gb(added),
                  cap=_fmt_gb(res.get("traffic_limit_bytes"))))
        elif res.get("renewed") and eff == "time_only":
            days = (pkg or {}).get("duration_days") or 0
            date_ln = _contract_time_line(shop, {
                "effect": "full", "new_expire_at": res.get("expire_at"),
                "current_expire_at": (sub_pre or {}).get("expire_at"),
            }, {"username": res["username"]})
            await _show(ctx, shop, tg, order["buyer_tg_id"], bmid,
                L(shop, "contract_days_add", days=days) + ("\n" + date_ln if date_ln else ""))
        elif res.get("renewed"):
            await _show(ctx, shop, tg, order["buyer_tg_id"], bmid,
                _mt(shop, "renewed", username=res["username"], **_card))
            if target is not None and res.get("traffic_limit_bytes"):
                left = (res["traffic_limit_bytes"] or 0) - target
                if left > 0:
                    await tg.send_message(order["buyer_tg_id"],
                        L(shop, "contract_traffic_roll", left=_fmt_gb(left),
                          cap=_fmt_gb(res["traffic_limit_bytes"])))
        else:
            url = res.get("subscription_url") or L(shop, "url_fallback")
            await _show(ctx, shop, tg, order["buyer_tg_id"], bmid,
                _mt(shop, "approved", username=res["username"], url=url, **_card))
    except Exception as e:
        ctx.logger.warning("shop.notify_buyer_failed", extra={"err": str(e)})
    try:
        _earned = float(res.get("referrer_earned") or 0)
    except (TypeError, ValueError):
        _earned = 0
    if _earned > 0:
        # The buyer came through a referral link: pay the inviter.
        try:
            from rwa_plugin_pershop.service import format_price as _fp6
            from rwa_plugin_pershop.service import shop_for_tg as _sft5

            _ref = await ctx.db.fetchrow(
                "SELECT referrer_tg_id FROM plugin_pershop_referrals"
                " WHERE shop_id=$1 AND referred_tg_id=$2",
                shop["id"], order["buyer_tg_id"])
            if _ref:
                _rshop = await _sft5(ctx, shop, int(_ref["referrer_tg_id"]))
                await tg.send_message(int(_ref["referrer_tg_id"]),
                    L(_rshop, "earned_credit",
                      buyer=order.get("buyer_username") or str(order.get("buyer_tg_id")),
                      plan=res.get("service_name") or res.get("username"),
                      value=_fp6(_rshop, _earned)))
        except Exception as e:
            ctx.logger.warning("shop.referral_dm_failed", extra={"err": str(e)})


async def _on_callback(ctx, shop, tg, cb) -> None:
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.telegram import kb

    data = cb.get("data") or ""
    cb_id = cb.get("id")
    sender = (cb.get("from") or {}).get("id")
    mid = (cb.get("message") or {}).get("message_id")
    try:
        await tg.answer_callback(cb_id)
    except Exception:
        pass

    if data == "my_subs":
        await _send_subs_list(ctx, shop, tg, sender, edit=mid)
        return

    if data == "account":
        await _send_account(ctx, shop, tg, sender, edit=mid)
        return

    if data == "acc:balance":
        await _send_balance(ctx, shop, tg, sender, edit=mid)
        return

    if data == "acc:history":
        await _send_history(ctx, shop, tg, sender, edit=mid)
        return

    if data == "langmenu":
        await _send_lang_menu(ctx, shop, tg, sender, edit=mid)
        return

    if data.startswith("langpick:"):
        from rwa_plugin_pershop.lang import normalize_lang

        code = normalize_lang(data.split(":", 1)[1])
        try:
            await ctx.db.execute(
                """INSERT INTO plugin_pershop_buyers(shop_id,tg_id,lang)
                   VALUES($1,$2,$3)
                   ON CONFLICT(shop_id,tg_id) DO UPDATE SET lang=$3""",
                shop["id"], sender, code)
        except Exception as e:
            ctx.logger.warning("shop.lang_save_failed", extra={"err": str(e)})
        shop = dict(shop)
        shop["_buyer_lang"] = code
        await _send_account(ctx, shop, tg, sender, edit=mid)
        return

    if data.startswith("cancelorder:"):
        await _send_withdraw_reason(ctx, shop, tg, sender, data.split(":", 1)[1], edit=mid)
        return

    if data == "linksub":
        await _send_link_prompt(ctx, shop, tg, sender, edit=mid)
        return

    if data == "pershop:services":
        await _send_svc_list(ctx, shop, tg, sender, edit=mid)
        return

    if data.startswith("svccat:"):
        from rwa_plugin_pershop.service import cat_services

        try:
            cat = await ctx.db.fetchrow(
                "SELECT * FROM plugin_pershop_service_cats WHERE id=$1 AND shop_id=$2",
                int(data.split(":")[1]), shop["id"])
        except (TypeError, ValueError):
            cat = None
        if not cat:
            await _send_svc_list(ctx, shop, tg, sender, edit=mid)
            return
        await _send_svc_items(ctx, shop, tg, sender,
                              await cat_services(ctx, shop["id"], cat["id"]),
                              cat["name"], edit=mid)
        return

    if data == "svcother:":
        from rwa_plugin_pershop.service import cat_services

        await _send_svc_items(ctx, shop, tg, sender,
                              await cat_services(ctx, shop["id"], None),
                              L(shop, "scat_other"), edit=mid)
        return

    if data.startswith("svcbuy:"):
        from rwa_plugin_pershop.service import service_by_id

        try:
            svc = await service_by_id(ctx, int(data.split(":")[1]))
        except (TypeError, ValueError):
            svc = None
        if not svc or svc.get("shop_id") != shop["id"]:
            await _show(ctx, shop, tg, sender, mid, L(shop, "svc_unavailable"))
            return
        await _maybe_scam_warn(ctx, shop, tg, sender,
                               ((cb.get("from") or {}).get("username")) or "")
        await _send_svc_note_prompt(ctx, shop, tg, sender, svc, f"svc:{svc['id']}", edit=mid)
        return

    if data.startswith("svcskip:"):
        from rwa_plugin_pershop.service import service_by_id

        try:
            svc = await service_by_id(ctx, int(data.split(":")[1]))
        except (TypeError, ValueError):
            svc = None
        if not svc or svc.get("shop_id") != shop["id"]:
            await _show(ctx, shop, tg, sender, mid, L(shop, "svc_unavailable"))
            return
        await _send_svc_contract(ctx, shop, tg, sender, svc, "", f"svc:{svc['id']}", edit=mid)
        return

    if data.startswith("svc:"):
        from rwa_plugin_pershop.service import service_by_id

        try:
            svc = await service_by_id(ctx, int(data.split(":")[1]))
        except (TypeError, ValueError):
            svc = None
        if not svc or svc.get("shop_id") != shop["id"]:
            await _show(ctx, shop, tg, sender, mid, L(shop, "svc_unavailable"))
            return
        await _send_svc_detail(ctx, shop, tg, sender, svc, edit=mid)
        return

    if data == "pershop:buy":
        await _send_buy_list(ctx, shop, tg, sender, edit=mid)
        return

    if data == "pershop:test":
        await _send_test_offer(ctx, shop, tg, sender, edit=mid)
        return

    if data == "testconfirm":
        from rwa_plugin_pershop.service import claim_test_sub

        try:
            res = await claim_test_sub(ctx, shop, sender,
                                       ((cb.get("from") or {}).get("username")) or "")
        except ValueError:
            await _show(ctx, shop, tg, sender, mid, L(shop, "test_limit_one"))
            return
        url = res.get("subscription_url") or L(shop, "url_fallback")
        await _show(
            ctx, shop, tg, sender, mid, L(shop, "test_success", username=res["username"],
                      expire=res["expire_at"].strftime("%Y-%m-%d %H:%M UTC"), url=url))
        return

    if data == "pershop:ref":
        await _send_referral(ctx, shop, tg, sender, sender, edit=mid)
        return

    if data == "pershop:how":
        tutorial = (await ctx.settings.get("tutorial_text", default="") or "").strip()
        if tutorial:
            await _show(ctx, shop, tg, sender, mid, tutorial)
        return

    if data == "pershop:help":
        support = (shop.get("support_text") or "").strip()
        if support:
            await _show(ctx, shop, tg, sender, mid, support)
        return

    if data.startswith("tiers:"):
        try:
            tier_id = int(data.split(":")[1])
        except ValueError:
            return
        tier = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_tiers WHERE id=$1 AND shop_id=$2 AND active",
            tier_id, shop["id"])
        if not tier:
            return
        await _send_tier_packages(ctx, shop, tg, sender, tier, edit=mid)
        return

    if data.startswith("back:tier:"):
        try:
            tier_id = int(data.split(":")[2])
        except ValueError:
            return
        tier = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_tiers WHERE id=$1 AND shop_id=$2 AND active",
            tier_id, shop["id"])
        if not tier:
            return
        await _send_tier_packages(ctx, shop, tg, sender, tier, edit=mid)
        return

    if data.startswith("buy:"):
        try:
            pkg_id = int(data.split(":")[1])
        except ValueError:
            return
        pkg = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_packages WHERE id=$1 AND shop_id=$2 AND active",
            pkg_id, shop["id"])
        from rwa_plugin_pershop.service import hwid_cfg, pkg_effect

        if not pkg or pkg_effect(pkg) != "full":
            await _show(ctx, shop, tg, sender, mid, L(shop, "pkg_unavailable"))
            return
        await _maybe_scam_warn(ctx, shop, tg, sender,
                               ((cb.get("from") or {}).get("username")) or "")
        if hwid_cfg(pkg)["mode"] == "input":
            await _send_hwid(ctx, shop, tg, sender, pkg, hwid_cfg(pkg)["default"], edit=mid)
            return
        back_cb = f"back:tier:{pkg['tier_id']}" if pkg.get("tier_id") else "menu"
        await _send_contract(ctx, shop, tg, sender, None, pkg, None, back_cb, edit=mid)
        return

    if data.startswith("cfcancel:"):
        from rwa_plugin_pershop.service import format_price as _fp8

        try:
            _csid = int(data.split(":")[1])
        except ValueError:
            return
        _csub = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_subs WHERE id=$1 AND shop_id=$2", _csid, shop["id"])
        if not _csub or _csub["buyer_tg_id"] != sender:
            return
        _offer = None
        try:
            _offer = await ctx.db.fetchrow(
                """SELECT id, total_units FROM plugin_pershop_orders WHERE shop_id=$1
                   AND buyer_tg_id=$2 AND receipt_kind='credit' AND status='approved'
                   AND renew_username=$3 ORDER BY id DESC LIMIT 1""",
                shop["id"], sender, _csub["username"])
        except Exception:
            _offer = None
        if not _offer or _csub.get("cancelled"):
            await _send_sub_detail(ctx, shop, tg, sender, _csub, edit=mid)
            return
        await _show(ctx, shop, tg, sender, mid,
            L(shop, "credit_cancel_confirm", item=_csub["username"],
              amount=_fp8(shop, float(_offer["total_units"] or 0))),
            reply_markup=kb([[{"text": L(shop, "credit_cancel_btn"),
                               "callback_data": f"cfyes:{_csub['id']}"}],
                             [{"text": L(shop, "back_btn"),
                               "callback_data": f"sub:{_csub['id']}"}]]))
        return

    if data.startswith("cfyes:"):
        from rwa_plugin_pershop.service import cancel_credit_sub
        from rwa_plugin_pershop.service import format_price as _fp9

        try:
            _csid = int(data.split(":")[1])
        except ValueError:
            return
        _csub = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_subs WHERE id=$1 AND shop_id=$2", _csid, shop["id"])
        if not _csub or _csub["buyer_tg_id"] != sender:
            return
        try:
            _out = await cancel_credit_sub(ctx, _csid, sender)
        except ValueError:
            _fresh = await ctx.db.fetchrow(
                "SELECT * FROM plugin_pershop_subs WHERE id=$1 AND shop_id=$2", _csid, shop["id"])
            await _send_sub_detail(ctx, shop, tg, sender, _fresh or _csub, edit=mid)
            return
        except Exception as e:
            ctx.logger.warning("shop.credit_cancel_failed", extra={"err": str(e)})
            await _show(ctx, shop, tg, sender, mid, L(shop, "order_error", id=_csid, err=e))
            return
        await _show(ctx, shop, tg, sender, mid,
            L(shop, "credit_cancel_done", amount=_fp9(shop, _out["amount"])))
        try:
            _osh = _owner_shop(shop)
            _bun = (cb.get("from") or {}).get("username") or str(sender)
            await tg.send_message(shop["admin_tg_id"],
                L(_osh, "owner_credit_cancel", buyer=_bun, sub=_out["username"],
                  amount=_fp9(_osh, _out["amount"])))
        except Exception as e:
            ctx.logger.warning("shop.owner_note_failed", extra={"err": str(e)})
        _fresh = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_subs WHERE id=$1 AND shop_id=$2", _csid, shop["id"])
        await _send_sub_detail(ctx, shop, tg, sender, _fresh or _csub, edit=None)
        return

    if data.startswith("sub:"):
        try:
            sub_id = int(data.split(":")[1])
        except ValueError:
            return
        sub = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_subs WHERE id=$1 AND shop_id=$2", sub_id, shop["id"])
        if not sub or sub["buyer_tg_id"] != sender:
            return
        await _send_sub_detail(ctx, shop, tg, sender, sub, edit=mid)
        return

    if data.startswith("qrenew:"):
        try:
            sub_id = int(data.split(":")[1])
        except ValueError:
            return
        sub = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_subs WHERE id=$1 AND shop_id=$2", sub_id, shop["id"])
        if not sub or sub.get("buyer_tg_id") != sender:
            return
        from rwa_plugin_pershop.service import pending_for_sub, pkg_effect

        if await pending_for_sub(ctx, shop["id"], sub["username"]):
            await _show(ctx, shop, tg, sender, mid, L(shop, "pending_note"),
                reply_markup=kb([[{"text": L(shop, "back_btn"),
                                   "callback_data": f"sub:{sub['id']}"}]]))
            return
        pkg = None
        if sub.get("package_id"):
            pkg = await ctx.db.fetchrow(
                "SELECT * FROM plugin_pershop_packages WHERE id=$1 AND shop_id=$2 AND active",
                sub["package_id"], shop["id"])
        if not pkg or pkg_effect(pkg) != "full":
            # Safety net (the button is hidden for package-less subs):
            # straight into the change-plan chain, no explainer, no ping.
            await _send_renew_tiers(ctx, shop, tg, sender, sub, edit=mid)
            return
        await _send_contract(ctx, shop, tg, sender, sub, pkg, None, back=f"sub:{sub['id']}", edit=mid)
        return

    if data.startswith("renew:"):
        try:
            sub_id = int(data.split(":")[1])
        except ValueError:
            return
        sub = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_subs WHERE id=$1 AND shop_id=$2", sub_id, shop["id"])
        if not sub or sub["buyer_tg_id"] != sender:
            return
        await _send_renew_tiers(ctx, shop, tg, sender, sub, edit=mid)
        return

    if data.startswith("renewtier:"):
        try:
            _, sub_id, tier_id = data.split(":")
            sub_id, tier_id = int(sub_id), int(tier_id)
        except ValueError:
            return
        sub = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_subs WHERE id=$1 AND shop_id=$2", sub_id, shop["id"])
        tier = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_tiers WHERE id=$1 AND shop_id=$2", tier_id, shop["id"])
        if not sub or sub["buyer_tg_id"] != sender or not tier:
            return
        await _send_renew_tier_packages(ctx, shop, tg, sender, sub, tier, edit=mid)
        return

    if data.startswith("back:renewpkgs:"):
        try:
            _, _, sub_id, tier_id = data.split(":")
            sub_id, tier_id = int(sub_id), int(tier_id)
        except ValueError:
            return
        sub = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_subs WHERE id=$1 AND shop_id=$2", sub_id, shop["id"])
        tier = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_tiers WHERE id=$1 AND shop_id=$2", tier_id, shop["id"])
        if not sub or sub["buyer_tg_id"] != sender or not tier:
            return
        await _send_renew_tier_packages(ctx, shop, tg, sender, sub, tier, edit=mid)
        return

    if data.startswith("renewpkg:"):
        try:
            _, sub_id, pkg_id = data.split(":")
            sub_id, pkg_id = int(sub_id), int(pkg_id)
        except ValueError:
            return
        sub = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_subs WHERE id=$1 AND shop_id=$2", sub_id, shop["id"])
        pkg = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_packages WHERE id=$1 AND shop_id=$2 AND active",
            pkg_id, shop["id"])
        from rwa_plugin_pershop.service import pending_for_sub, pkg_effect

        if not sub or sub["buyer_tg_id"] != sender or not pkg or pkg_effect(pkg) != "full":
            return
        if await pending_for_sub(ctx, shop["id"], sub["username"]):
            await _show(ctx, shop, tg, sender, mid, L(shop, "pending_note"),
                reply_markup=kb([[{"text": L(shop, "back_btn"),
                                   "callback_data": f"sub:{sub['id']}"}]]))
            return
        tier_id = pkg.get("tier_id")
        back_cb = (f"back:renewpkgs:{sub['id']}:{tier_id}" if tier_id
                   else f"back:picker:{sub['id']}")
        await _send_contract(ctx, shop, tg, sender, sub, pkg, None, back_cb, edit=mid)
        return

    if data.startswith("addtraffic:") or data.startswith("adddays:"):
        from rwa_plugin_pershop.service import pending_for_sub

        effect = "traffic_only" if data.startswith("addtraffic:") else "time_only"
        sub = await _topup_sub(ctx, shop, sender, data.split(":")[1])
        if not sub:
            return
        if await pending_for_sub(ctx, shop["id"], sub["username"]):
            await _show(ctx, shop, tg, sender, mid, L(shop, "pending_note"),
                reply_markup=kb([[{"text": L(shop, "back_btn"),
                                   "callback_data": f"sub:{sub['id']}"}]]))
            return
        await _send_topup_picker(ctx, shop, tg, sender, sub, effect, edit=mid)
        return

    if data.startswith("topick:"):
        from rwa_plugin_pershop.service import pending_for_sub, pkg_effect

        try:
            _, sub_id, pkg_id = data.split(":")
            sub_id, pkg_id = int(sub_id), int(pkg_id)
        except ValueError:
            return
        sub = await _topup_sub(ctx, shop, sender, sub_id)
        pkg = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_packages WHERE id=$1 AND shop_id=$2 AND active",
            pkg_id, shop["id"])
        if not sub or not pkg or pkg_effect(pkg) == "full":
            return
        if await pending_for_sub(ctx, shop["id"], sub["username"]):
            await _show(ctx, shop, tg, sender, mid, L(shop, "pending_note"),
                reply_markup=kb([[{"text": L(shop, "back_btn"),
                                   "callback_data": f"sub:{sub['id']}"}]]))
            return
        back_cb = f"back:topups:{sub['id']}:{pkg_effect(pkg)}"
        await _send_contract(ctx, shop, tg, sender, sub, pkg, None, back_cb, edit=mid)
        return

    if data.startswith("back:topups:"):
        try:
            _, _, sub_id, effect = data.split(":")
        except ValueError:
            return
        sub = await _topup_sub(ctx, shop, sender, sub_id)
        if not sub or effect not in ("traffic_only", "time_only"):
            return
        await _send_topup_picker(ctx, shop, tg, sender, sub, effect, edit=mid)
        return

    if data == "menu":
        await _clear_session(ctx, shop["id"], sender, "buyer")
        await _send_buyer_menu(ctx, shop, tg, sender, edit=mid)
        return

    if data == "back:subs":
        await _send_subs_list(ctx, shop, tg, sender, edit=mid)
        return

    if data.startswith("back:picker:"):
        try:
            sub_id = int(data.split(":")[2])
        except ValueError:
            return
        sub = await ctx.db.fetchrow(
            "SELECT * FROM plugin_pershop_subs WHERE id=$1 AND shop_id=$2", sub_id, shop["id"])
        if not sub or sub["buyer_tg_id"] != sender:
            return
        await _send_renew_picker(ctx, shop, tg, sender, sub, edit=mid)
        return

    if data in ("hminus", "hplus"):
        from rwa_plugin_pershop.service import hwid_cfg

        draft = await _load_draft(ctx, shop, sender)
        if not draft:
            return
        pkg, hwid_value, state, _d = draft
        if not state.startswith("await_hwid:") or hwid_cfg(pkg)["mode"] != "input":
            return
        cfg = hwid_cfg(pkg)
        try:
            cur = int(hwid_value if hwid_value is not None else cfg["default"])
        except (TypeError, ValueError):
            cur = cfg["default"]
        cur = max(cfg["min"], min(cfg["max"], cur + (1 if data == "hplus" else -1)))
        await _send_hwid(ctx, shop, tg, sender, pkg, cur, edit=mid)
        return

    if data == "hnext":
        from rwa_plugin_pershop.service import hwid_cfg

        draft = await _load_draft(ctx, shop, sender)
        if not draft:
            return
        pkg, hwid_value, state, _d = draft
        if not state.startswith("await_hwid:") or hwid_cfg(pkg)["mode"] != "input":
            return
        await _to_summary(ctx, shop, tg, sender, pkg, hwid_value, edit=mid)
        return

    if data == "back:hwid":
        from rwa_plugin_pershop.service import hwid_cfg

        draft = await _load_draft(ctx, shop, sender)
        if not draft:
            return
        pkg, hwid_value, _state, _d = draft
        if hwid_cfg(pkg)["mode"] != "input":
            return
        await _send_hwid(ctx, shop, tg, sender, pkg, hwid_value, edit=mid)
        return

    if data == "stconfirm":
        from rwa_plugin_pershop.service import credit_balance, format_price, pkg_effect

        sdraft = await _load_svc_draft(ctx, shop, sender)
        if sdraft:
            svc, _note, state, saved = sdraft
            if state.startswith("await_summary:") and saved.get("total_units") is not None:
                saved = dict(saved)
                credit_note = ""
                if saved.get("credit"):
                    _bal = await credit_balance(ctx, shop["id"], sender)
                    _used = min(_bal, float(saved.get("total_units") or 0))
                    saved["credit_used"] = _used
                    if _used > 0:
                        _due = float(saved["total_units"]) - _used
                        credit_note = ("\n" + L(shop, "credit_due", used=format_price(shop, _used),
                                                 due=format_price(shop, _due)) if _due > 0
                                       else "\n" + L(shop, "credit_full"))
                if saved.get("credit") and await _auto_credit_order(
                        ctx, shop, tg, cb, "svc", svc, saved):
                    await _clear_session(ctx, shop["id"], sender, "buyer")
                    return
                await _set_session(ctx, shop["id"], sender, "buyer",
                                   f"await_receipt:svc:{svc['id']}", dict(saved, pm=mid))
                pm = await _show(ctx, shop, tg, sender, mid,
                    L(shop, "send_receipt_new", pkg=svc["name"],
                      price=format_price(shop, saved["total_units"]), card=_card_block(shop))
                    + credit_note,
                    reply_markup=kb([[{"text": L(shop, "back_btn"), "callback_data": "back:contract"}]]))
                if pm:
                    await _set_session(ctx, shop["id"], sender, "buyer",
                                       f"await_receipt:svc:{svc['id']}", dict(saved, pm=pm))
            return
        draft = await _load_draft(ctx, shop, sender)
        if not draft:
            return
        pkg, _hwid, state, saved = draft
        if not state.startswith("await_summary:") or saved.get("total_units") is None:
            return
        saved = dict(saved)
        credit_note = ""
        if saved.get("credit"):
            _bal = await credit_balance(ctx, shop["id"], sender)
            _used = min(_bal, float(saved.get("total_units") or 0))
            saved["credit_used"] = _used
            if _used > 0:
                _due = float(saved["total_units"]) - _used
                credit_note = ("\n" + L(shop, "credit_due", used=format_price(shop, _used),
                                         due=format_price(shop, _due)) if _due > 0
                               else "\n" + L(shop, "credit_full"))
        if saved.get("credit") and await _auto_credit_order(
                ctx, shop, tg, cb, "pkg", pkg, saved):
            await _clear_session(ctx, shop["id"], sender, "buyer")
            return
        await _set_session(ctx, shop["id"], sender, "buyer", f"await_receipt:{pkg['id']}",
                             dict(saved, pm=mid))
        if pkg_effect(pkg) != "full":
            sub = await _draft_sub(ctx, shop, saved)
            pm = await _show(ctx, shop, tg, sender, mid,
                L(shop, "send_receipt_topup", label=pkg["name"],
                  username=(sub["username"] if sub else saved.get("renew_username") or "?"),
                  card=_card_block(shop)) + credit_note,
                reply_markup=kb([[{"text": L(shop, "back_btn"), "callback_data": "back:contract"}]]))
            if pm:
                await _set_session(ctx, shop["id"], sender, "buyer", f"await_receipt:{pkg['id']}",
                                   dict(saved, pm=pm))
            return
        pm = await _show(ctx, shop, tg, sender, mid,
            L(shop, "send_receipt_new", pkg=pkg["name"],
              price=format_price(shop, saved["total_units"]), card=_card_block(shop))
            + credit_note,
            reply_markup=kb([[{"text": L(shop, "back_btn"), "callback_data": "back:contract"}]]))
        if pm:
            await _set_session(ctx, shop["id"], sender, "buyer", f"await_receipt:{pkg['id']}",
                               dict(saved, pm=pm))
        return

    if data == "credituse":
        draft = await _load_draft(ctx, shop, sender)
        if draft:
            pkg, hwid_value, _state, saved = draft
            if saved.get("total_units") is not None:
                sub = await _draft_sub(ctx, shop, saved)
                await _send_contract(ctx, shop, tg, sender, sub, pkg, hwid_value,
                                     saved.get("back") or "menu", credit_applied=True, edit=mid)
                return
        sdraft = await _load_svc_draft(ctx, shop, sender)
        if sdraft:
            svc, note, _st, saved = sdraft
            if saved.get("total_units") is not None:
                await _send_svc_contract(ctx, shop, tg, sender, svc, note,
                                         saved.get("back") or f"svc:{svc['id']}",
                                         credit_applied=True, edit=mid)
        return
    if data == "back:contract":
        sdraft = await _load_svc_draft(ctx, shop, sender)
        if sdraft:
            svc, note, _st, saved = sdraft
            if saved.get("total_units") is not None:
                await _send_svc_contract(ctx, shop, tg, sender, svc, note,
                                         saved.get("back") or f"svc:{svc['id']}", edit=mid)
            return
        draft = await _load_draft(ctx, shop, sender)
        if not draft:
            return
        pkg, hwid_value, _state, saved = draft
        if saved.get("total_units") is None:
            return
        sub = await _draft_sub(ctx, shop, saved)
        await _send_contract(ctx, shop, tg, sender, sub, pkg, hwid_value,
                             saved.get("back") or "menu", edit=mid)
        return

    if data == "cancel":
        role = "admin" if sender == shop["admin_tg_id"] else "buyer"
        await _clear_session(ctx, shop["id"], sender, role)
        await _show(ctx, shop, tg, sender, mid, L(shop, "admin_cancelled" if role == "admin" else "cancel_again"))
        return

    # Admin-only below.
    if sender != shop["admin_tg_id"]:
        return
    if data.startswith("wdprocess:") or data.startswith("wdapprove:"):
        try:
            wd_id = int(data.split(":")[1])
        except ValueError:
            return
        if data.startswith("wdprocess:"):
            await _process_withdraw_cb(ctx, shop, tg, wd_id, edit=mid)
        else:
            await _approve_withdraw_cb(ctx, shop, tg, wd_id, edit=mid)
        return
    if data.startswith("wddeny:"):
        try:
            order_id = int(data.split(":")[1])
        except ValueError:
            return
        if not await _wd_open_order(ctx, shop, order_id):
            from rwa_plugin_pershop.lang import L as _L

            await _show(ctx, shop, tg, shop["admin_tg_id"], mid, _L(shop, "wd_stale"))
            return
        await _set_session(ctx, shop["id"], shop["admin_tg_id"], "admin",
                           f"await_reason:{order_id}", {"order_id": order_id})
        from rwa_plugin_pershop.lang import L
        from rwa_plugin_pershop.telegram import kb

        await _show(ctx, shop, tg, shop["admin_tg_id"], mid, L(shop, "ask_reason", id=order_id),
            reply_markup=kb(_cancel_row(shop)))
        return
    if data.startswith("approve:"):
        try:
            order_id = int(data.split(":")[1])
        except ValueError:
            return
        from rwa_plugin_pershop.service import approve_order

        order = await ctx.db.fetchrow("SELECT * FROM plugin_pershop_orders WHERE id=$1", order_id)
        if not order:
            await tg.send_message(shop["admin_tg_id"], L(shop, "order_not_found", id=order_id))
            return
        sub_pre = None
        if order.get("renew_username"):
            sub_pre = await ctx.db.fetchrow(
                "SELECT expire_at FROM plugin_pershop_subs WHERE shop_id=$1 AND username=$2",
                shop["id"], order["renew_username"])
        try:
            res = await approve_order(ctx, order_id, "admin-tg")
        except ValueError as e:
            await tg.send_message(shop["admin_tg_id"], L(shop, "order_error", id=order_id, err=e))
            return
        except Exception as e:
            ctx.logger.warning("shop.approve_failed", extra={"err": str(e)})
            await tg.send_message(shop["admin_tg_id"], L(shop, "order_error", id=order_id, err=e))
            return
        await _notify_approval(ctx, shop, tg, order, res, sub_pre)
        who = res.get("service_name") or res.get("username") or "?"
        await _show(ctx, shop, tg, shop["admin_tg_id"], order.get("owner_msg_id") or mid,
            L(_owner_shop(shop), "owner_final_approved", id=order_id, username=who))
        return
    if data.startswith("scam:"):
        try:
            order_id = int(data.split(":")[1])
        except ValueError:
            return
        from rwa_plugin_pershop.service import mark_scammer, shop_for_tg

        try:
            res = await mark_scammer(ctx, order_id, shop.get("owner_admin"))
        except ValueError as e:
            await tg.send_message(shop["admin_tg_id"], L(shop, "order_error", id=order_id, err=e))
            return
        except Exception as e:
            ctx.logger.warning("shop.scam_failed", extra={"err": str(e)})
            await tg.send_message(shop["admin_tg_id"], L(shop, "order_error", id=order_id, err=e))
            return
        order = res["order"]
        buyer_disp = order.get("buyer_username") or str(order["buyer_tg_id"])
        try:
            bshop = await shop_for_tg(ctx, res["shop"], order["buyer_tg_id"])
            await _show(ctx, shop, tg, order["buyer_tg_id"], order.get("buyer_msg_id"),
                        L(bshop, "scammed_note"))
        except Exception as e:
            ctx.logger.warning("shop.notify_buyer_failed", extra={"err": str(e)})
        # The scam button lives on the post-deny offer message: replace it with
        # the done note, and append the flag line to the final Denied card.
        await _show(ctx, shop, tg, shop["admin_tg_id"], mid,
            L(_owner_shop(shop), "scam_done", id=order_id, buyer_id=order["buyer_tg_id"]))
        who = order.get("buyer_username") or str(order.get("buyer_tg_id"))
        await _show(ctx, shop, tg, shop["admin_tg_id"], order.get("owner_msg_id"),
            L(_owner_shop(shop), "owner_final_denied", id=order_id, username=who,
              reason=order.get("deny_reason") or "")
            + "\n\n" + L(_owner_shop(shop), "owner_final_scam", buyer=buyer_disp))
        return
    if data.startswith("deny:"):
        try:
            order_id = int(data.split(":")[1])
        except ValueError:
            return
        await _set_session(ctx, shop["id"], shop["admin_tg_id"], "admin",
                           f"await_reason:{order_id}", {"order_id": order_id})
        await _show(ctx, shop, tg, shop["admin_tg_id"], mid, L(shop, "ask_reason", id=order_id),
            reply_markup=kb(_cancel_row(shop)))
        return


def build_telegram_router(ctx):
    router = APIRouter()

    @router.post("/tg/{shop_id}")
    async def tg_webhook(shop_id: int, request: Request,
                         secret: str | None = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token")):
        from rwa_plugin_pershop.service import shop_by_id

        shop = await shop_by_id(ctx, shop_id)
        if not shop:
            return {"ok": False}
        if shop.get("webhook_secret") and secret != shop["webhook_secret"]:
            return {"ok": False}
        try:
            update = await request.json()
        except Exception:
            return {"ok": True}
        try:
            await handle_update(ctx, dict(shop), update)
        except Exception as e:
            ctx.logger.warning("shop.update_failed", extra={"err": str(e)})
        return {"ok": True}

    return router
