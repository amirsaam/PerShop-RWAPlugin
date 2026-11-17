"""Focused safety regressions for the financial and recovery hardening."""
import asyncio
import copy
import json
import re
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from test_shop import FakeCtx, FakeDB, FakePanel, FakeTg, make_shop

from rwa_plugin_pershop.naming import allocate_username


class RegressionDB(FakeDB):
    """Add the new read shapes without changing the shared fake suite."""

    async def fetch(self, q, *a):
        if q.startswith("SELECT username FROM plugin_pershop_subs WHERE shop_id"):
            self._chk(q, a)
            return [{"username": row["username"]} for row in self.subs.values()
                    if row["shop_id"] == a[0] and row.get("username")]
        if "SELECT provision_username AS username FROM plugin_pershop_orders" in q:
            self._chk(q, a)
            return [{"username": row["provision_username"]} for row in self.orders.values()
                    if row.get("status") == "approving" and row.get("provision_username")]
        if "COALESCE(p.name, v.name) AS package_name" in q and "LEFT JOIN plugin_pershop_scammers" in q:
            self._chk(q, a)
            if "o.status IN ('pending', 'withdraw_requested', 'approving')" in q:
                statuses = {"pending", "withdraw_requested", "approving"}
            elif "o.status=$2" in q:
                statuses = {a[1]}
            else:
                statuses = None
            rows = []
            for order in self.orders.values():
                if order["shop_id"] != a[0] or (statuses is not None and order["status"] not in statuses):
                    continue
                pkg = self.pkgs.get(order.get("package_id")) or {}
                svc = self.svcs.get(order.get("service_id")) or {}
                rows.append(dict(order,
                                 package_name=pkg.get("name") or svc.get("name"),
                                 package_units=pkg.get("price_units") if pkg else svc.get("price_units"),
                                 buyer_flagged=order.get("buyer_tg_id") in self.scammers))
            return sorted(rows, key=lambda row: row["id"], reverse=True)[:200]
        if ("SELECT o.* FROM plugin_pershop_orders o WHERE o.shop_id=$1" in q
                and "o.status IN ('pending','approving','withdraw_requested')" in q):
            self._chk(q, a)
            return sorted((dict(row) for row in self.orders.values()
                           if row["shop_id"] == a[0]
                           and row["status"] in {"pending", "approving", "withdraw_requested"}),
                          key=lambda row: row["id"])
        return await super().fetch(q, *a)


def test_html_escaping_preserves_only_trusted_markup():
    from rwa_plugin_pershop.lang import H, L
    from rwa_plugin_pershop.service import fmt_safe, message_text

    hostile = '<img src=x onerror="boom"> & Co'
    escaped = "&lt;img src=x onerror=&quot;boom&quot;&gt; &amp; Co"

    localized = L("en", "test_success", username=hostile, expire="tomorrow",
                  url=H('<a href="https://safe.example/?a=1&amp;b=2">link</a>'))
    assert hostile not in localized
    assert escaped in localized
    assert "<code>" in localized
    assert '<a href="https://safe.example/?a=1&amp;b=2">link</a>' in localized

    formatted = fmt_safe("<b>Total {amount:.2f}</b>: {buyer} {card}",
                         amount=1.2, buyer=hostile, card=H("<code>pay &amp; go</code>"))
    assert formatted == f"<b>Total 1.20</b>: {escaped} <code>pay &amp; go</code>"

    rendered = message_text(
        {"denied_text": "Owner <b>decision</b> for {username}: {reason}"},
        "denied", username=hostile, reason=hostile)
    assert rendered == f"Owner <b>decision</b> for {escaped}: {escaped}"


def test_casefolded_usernames_include_approval_reservations():
    from rwa_plugin_pershop import service as svc

    async def run():
        db = RegressionDB()
        ctx = FakeCtx(db)
        sid, _pid = make_shop(db)
        for oid, status, username in ((1, "approving", "VIP-Alice"),
                                      (2, "pending", "not-reserved"),
                                      (3, "denied", "also-not-reserved")):
            db.orders[oid] = {"id": oid, "shop_id": sid, "status": status,
                              "provision_username": username}

        names = await svc.existing_usernames(ctx, sid)
        assert "VIP-Alice" in names
        assert "not-reserved" not in names and "also-not-reserved" not in names
        assert allocate_username("vip", "Alice", 111, names) == "vip-Alice2"
        assert allocate_username("vip", "Alice", 111, {"vIp-AlIcE", "VIP-ALICE2"}) == "vip-Alice3"

    asyncio.run(run())


def _check_auto_credit_replay(monkeypatch, status):
    from rwa_plugin_pershop import routes_telegram as routes
    from rwa_plugin_pershop import service as svc

    async def run():
        db = RegressionDB()
        ctx = FakeCtx(db)
        sid, pid = make_shop(db)
        shop = db.shops[sid]
        callback_id = "replayed-callback"
        key = f"checkout:{sid}:111:callback:{callback_id}"
        snapshot = json.dumps({"credit_used": 2.0, "total_units": 2.0})
        db.orders[1] = {
            "id": 1, "shop_id": sid, "package_id": pid, "service_id": None,
            "buyer_tg_id": 111, "buyer_username": "alice", "receipt_file_id": None,
            "receipt_kind": "credit", "renew_username": None, "total_units": 2.0,
            "options_snapshot": snapshot, "idempotency_key": key, "status": status,
            "approval_stage": '{"state":"preparing"}' if status == "approving" else None,
        }
        db.seq["order"] = 1
        db.credits[(sid, 111)] = 3.0
        db.ledger[(sid, key)] = {
            "shop_id": sid, "tg_id": 111, "order_id": 1, "kind": "debit",
            "units": -2.0, "idempotency_key": key,
        }
        before = copy.deepcopy((db.orders, db.ledger, db.credits, db.events))
        approvals = []

        async def resume_approval(*args, **kwargs):
            approvals.append((args, kwargs))
            raise RuntimeError("stop after checking the recovery boundary")

        shown = []

        async def capture_show(*args, **kwargs):
            shown.append((args, kwargs))

        monkeypatch.setattr(svc, "approve_order", resume_approval)
        monkeypatch.setattr(routes, "_show", capture_show)
        cb = {"id": callback_id, "from": {"id": 111, "username": "alice"},
              "message": {"message_id": 77}}
        result = await routes._auto_credit_order(
            ctx, shop, FakeTg("DUMMY"), cb, "pkg", db.pkgs[pid],
            {"total_units": 2.0, "credit": True, "renew_username": None})

        assert result is True
        assert shown, "the replay should still conclude the Telegram handler"
        if status in ("pending", "approving"):
            assert len(approvals) == 1, "the open order did not resume its durable approval"
            assert approvals[0][1]["shop_id"] == sid
        else:
            assert approvals == [], "a finalized checkout replay invoked approval"
        assert db.orders[1]["status"] == status
        assert len(db.orders) == 1
        assert (db.orders, db.ledger, db.credits, db.events) == before

    asyncio.run(run())


def test_auto_credit_replay_of_approved_order_is_inert(monkeypatch):
    _check_auto_credit_replay(monkeypatch, "approved")


@pytest.mark.parametrize("status", ["pending", "approving"])
def test_auto_credit_replay_after_interrupted_handler_resumes_safely(monkeypatch, status):
    """Open committed rows resume approval; checkout and wallet work stay idempotent."""
    _check_auto_credit_replay(monkeypatch, status)


def test_approving_auto_failure_offers_resume_without_deny(monkeypatch):
    from rwa_plugin_pershop import routes_telegram as routes
    from rwa_plugin_pershop import service as svc
    from rwa_plugin_pershop.lang import L

    async def run():
        db = RegressionDB()
        ctx = FakeCtx(db)
        sid, pid = make_shop(db)
        shop = db.shops[sid]

        async def fail_after_claim(_ctx, order_id, _decided_by, **_kwargs):
            db.orders[order_id]["status"] = "approving"
            raise RuntimeError("handler failed after committing its claim")

        async def capture_show(*_args, **_kwargs):
            return None

        monkeypatch.setattr(svc, "approve_order", fail_after_claim)
        monkeypatch.setattr(routes, "_show", capture_show)
        tg = FakeTg("DUMMY")
        cb = {"id": "initial-checkout", "from": {"id": 111, "username": "alice"},
              "message": {"message_id": 88}}
        result = await routes._auto_credit_order(
            ctx, shop, tg, cb, "pkg", db.pkgs[pid], {"total_units": 0.0})

        assert result is True
        order = db.orders[1]
        assert order["status"] == "approving"
        cards = [payload for method, payload in tg.sent
                 if method == "sendMessage" and payload.get("chat_id") == shop["admin_tg_id"]
                 and payload.get("rm")]
        assert len(cards) == 1, tg.sent
        buttons = [button for row in cards[0]["rm"]["inline_keyboard"] for button in row]
        assert [button["callback_data"] for button in buttons] == [f"approve:{order['id']}"]
        assert buttons[0]["text"] == L(shop, "resume_btn")
        assert all("deny:" not in button["callback_data"] for button in buttons)

    asyncio.run(run())


def _admin_router(monkeypatch, ctx):
    class Admin:
        username = "admin1"

    def require_permission(_resource, _action):
        async def dependency():
            return Admin()
        return dependency

    plugin_api = types.ModuleType("web.backend.core.plugin_api")
    plugin_api.auth_deps = lambda: (Admin, require_permission)
    for name in ("web", "web.backend", "web.backend.core"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "web.backend.core.plugin_api", plugin_api)

    from rwa_plugin_pershop.routes_admin import build_admin_router

    return build_admin_router(ctx=ctx), Admin


def _endpoint(router, method, path):
    return next(route.endpoint for route in router.routes
                if route.path == path and method in route.methods)


def test_approving_orders_are_visible_in_owner_recovery_surfaces(monkeypatch):
    from rwa_plugin_pershop import service as svc
    from rwa_plugin_pershop.lang import L
    from rwa_plugin_pershop.tasks import _nag_pending

    async def run():
        db = RegressionDB()
        ctx = FakeCtx(db)
        sid, _pid = make_shop(db)
        shop = db.shops[sid]
        shop["pending_nag_hours"] = 1
        now = datetime.now(timezone.utc)
        for oid, status, created in (
                (1, "pending", now), (2, "approving", now - timedelta(days=2)),
                (3, "approved", now - timedelta(days=2))):
            db.orders[oid] = {"id": oid, "shop_id": sid, "status": status,
                              "buyer_tg_id": 100 + oid, "buyer_username": f"u{oid}",
                              "total_units": 1.0, "created_at": created,
                              "owner_nudged_at": None}
        db.seq["order"] = 3
        monkeypatch.setattr(svc, "_panel_client", lambda: FakePanel())

        stats = await svc.shop_stats(ctx, shop)
        assert stats["orders_pending"] == 2
        assert stats["orders_approving"] == 1
        health = await svc.shop_health(ctx, shop)
        assert health["pending"] == 2 and health["pending_old"] == 1

        router, admin_type = _admin_router(monkeypatch, ctx)
        pending_endpoint = _endpoint(router, "GET", "/orders")
        pending = await pending_endpoint("pending", admin_type())
        approving = await pending_endpoint("approving", admin_type())
        assert {row["id"] for row in pending["orders"]} == {1, 2}
        assert [row["id"] for row in approving["orders"]] == [2]

        response = await _endpoint(router, "GET", "/app")(admin_type())
        javascript = response.body.decode()
        branch = re.search(
            r"o\.status === 'approving'\s*\?\s*(.*?)\s*"
            r": \(status === 'pending' && o\.status === 'pending'", javascript, re.S)
        assert branch, "approving action branch is missing"
        assert ">Resume</button>" in branch.group(1)
        assert "Deny" not in branch.group(1)

        tg = FakeTg("DUMMY")
        await _nag_pending(ctx, tg, shop, now)
        assert [(method, payload["text"]) for method, payload in tg.sent] == [
            ("sendMessage", L(shop, "nag_reconcile_header", n=1))]
        assert db.orders[2]["owner_nudged_at"] is not None
        assert db.orders[1]["owner_nudged_at"] is None

    asyncio.run(run())


def test_mark_scammer_pending_credit_order_settles_once():
    from rwa_plugin_pershop import service as svc

    async def run():
        db = RegressionDB()
        ctx = FakeCtx(db)
        sid, pid = make_shop(db)
        oid = 1
        checkout_key = f"checkout:{sid}:111:callback:credit-order"
        db.orders[oid] = {
            "id": oid, "shop_id": sid, "package_id": pid, "service_id": None,
            "buyer_tg_id": 111, "buyer_username": "alice", "status": "pending",
            "receipt_kind": "credit", "total_units": 2.0,
            "options_snapshot": json.dumps({"credit_used": 2.0, "promo_code": "SAVE"}),
            "idempotency_key": checkout_key,
        }
        db.seq["order"] = oid
        db.credits[(sid, 111)] = 3.0
        db.ledger[(sid, checkout_key)] = {
            "shop_id": sid, "tg_id": 111, "order_id": oid, "kind": "debit",
            "units": -2.0, "idempotency_key": checkout_key,
        }
        promo_id = db._next("promo")
        db.promos[promo_id] = {"id": promo_id, "shop_id": sid, "code": "SAVE",
                               "uses_reserved": 1, "active": True}
        db.promo_uses.add((promo_id, 111))
        db.promo_use_orders[(promo_id, 111)] = oid
        db.campaign_uses[(sid, 111)] = oid

        result = await svc.mark_scammer(ctx, oid, "admin1")
        assert result["order"]["status"] == "denied"
        assert db.orders[oid]["deny_reason"] == "order #1 (Monthly)"
        assert db.credits[(sid, 111)] == 5.0
        assert len(db.ledger) == 2
        refund = db.ledger[(sid, f"refund:order:{oid}")]
        assert refund["kind"] == "refund" and refund["units"] == 2.0
        assert db.promo_uses == set() and db.promo_use_orders == {}
        assert db.promos[promo_id]["uses_reserved"] == 0
        assert db.campaign_uses == {}
        assert db.scammers[111]["marked_by_admin"] == "admin1"

        settled = copy.deepcopy((db.orders, db.ledger, db.credits, db.promo_uses,
                                 db.promo_use_orders, db.promos, db.campaign_uses, db.events))
        await svc.mark_scammer(ctx, oid, "admin1")
        assert (db.orders, db.ledger, db.credits, db.promo_uses,
                db.promo_use_orders, db.promos, db.campaign_uses, db.events) == settled


    asyncio.run(run())
