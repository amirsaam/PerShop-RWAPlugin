"""Authorization, external-auth and financial-input hardening regressions."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import sys
import types

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from test_shop import FakeCtx, FakeDB, FakePanel, make_shop
from test_safety_regressions import _admin_router, _endpoint

from rwa_plugin_pershop import service as svc


GB = 1073741824
EXP = datetime.now(timezone.utc) + timedelta(days=30)


def _modules(monkeypatch, *, account="default", squad="allow",
              visible="all", increment="accept", panel_id=True):
    for name in ("web", "web.backend", "web.backend.core", "web.backend.core.rbac",
                 "web.backend.core.admin_accounts"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))

    async def get_account(_owner):
        if account == "raise":
            raise RuntimeError("sensitive owner lookup failure")
        if account == "none":
            return None
        return {"id": 7, "role_id": 1, "role_name": "admin"}

    async def counter(*_args):
        if increment == "raise":
            raise RuntimeError("sensitive quota failure")
        return increment != "refuse"

    async def check_access(*_args):
        if squad == "raise":
            raise RuntimeError("sensitive RBAC failure")
        return squad == "allow"

    async def get_visible(_ns):
        if visible == "raise":
            raise RuntimeError("sensitive visibility failure")
        if visible == "all":
            return {"uu-a", "uu-b", "uuid-1"}
        return set(visible)

    monkeypatch.setattr(sys.modules["web.backend.core.admin_accounts"],
                        "get_admin_account_by_username", get_account, raising=False)
    monkeypatch.setattr(sys.modules["web.backend.core.admin_accounts"],
                        "increment_usage_counter", counter, raising=False)
    if squad != "missing":
        monkeypatch.setattr(sys.modules["web.backend.core.rbac"],
                            "check_access", check_access, raising=False)
    if visible != "missing":
        monkeypatch.setattr(sys.modules["web.backend.core.rbac"],
                            "get_visible_user_uuids", get_visible, raising=False)
    if panel_id:
        monkeypatch.setitem(sys.modules, "shared", types.ModuleType("shared"))
        monkeypatch.setitem(sys.modules, "shared.data_access",
                            types.ModuleType("shared.data_access"))

        async def resolve(user_uuid):
            return user_uuid

        monkeypatch.setattr(sys.modules["shared.data_access"],
                            "resolve_panel_user_id", resolve, raising=False)


def _ctx_with_shop():
    db = FakeDB()
    ctx = FakeCtx(db)
    shop_id, _ = make_shop(db)
    return ctx, db, dict(db.shops[shop_id])


def _create(ctx, shop, **kwargs):
    args = dict(username="vip-new", expire_at=EXP, traffic_limit_bytes=GB,
                telegram_id=555, squad_uuid="sqT",
                idempotency_key="hardening-key-0001")
    args.update(kwargs)
    return svc.api_create_user(ctx, shop, **args)


@pytest.mark.parametrize("failure", ["deny", "raise", "missing"])
def test_squad_denial_and_rbac_failures_never_create(monkeypatch, failure):
    async def run():
        _modules(monkeypatch, squad=("missing" if failure == "missing"
                                     else "raise" if failure == "raise" else "deny"))
        monkeypatch.setattr(svc, "_owner_account_row", _account_row)
        ctx, db, shop = _ctx_with_shop()
        panel = FakePanel()
        monkeypatch.setattr(svc, "_panel_client", lambda: panel)
        with pytest.raises(ValueError):
            await _create(ctx, shop)
        assert panel.created == []
        db.users["uu-a"] = {"uuid": "uu-a", "username": "vip-local",
                            "created_by_admin_id": 7, "expire_at": EXP}
        with pytest.raises(ValueError):
            await svc.api_update_user(ctx, shop, "uu-a", squad_uuid="sqT")
        assert panel.updated == []

    asyncio.run(run())


async def _account_row(_ctx, owner):
    account_id = 99 if owner == "other-owner" else 7
    return True, {"id": account_id, "role_id": 1, "role_name": "admin"}


def test_owner_and_quota_failures_do_not_return_success(monkeypatch):
    async def run():
        _modules(monkeypatch, account="raise", increment="raise")
        ctx, _db, shop = _ctx_with_shop()
        panel = FakePanel()
        monkeypatch.setattr(svc, "_panel_client", lambda: panel)
        with pytest.raises(ValueError, match="owner scope unavailable"):
            await _create(ctx, shop)
        assert panel.created == []
        with pytest.raises(RuntimeError, match="owner policy unavailable"):
            await svc._owner_policy("admin1")
        monkeypatch.setattr(svc, "_owner_account_row", _account_row)
        _modules(monkeypatch, increment="raise")
        with pytest.raises(RuntimeError, match="quota requires reconciliation"):
            await svc.panel_create_user(
                ctx, username="vip-quota", expire_at=EXP,
                traffic_limit_bytes=GB, telegram_id=555,
                owner_admin="admin1", description="pershop:vip test")
        assert len(panel.created) == 1

    asyncio.run(run())


def test_visibility_failure_and_cross_shop_access_fail_closed(monkeypatch):
    async def run():
        _modules(monkeypatch, visible="raise")
        monkeypatch.setattr(svc, "_owner_account_row", _account_row)
        ctx, db, shop_a = _ctx_with_shop()
        second_id, _ = make_shop(db)
        shop_b = dict(db.shops[second_id], owner_admin="other-owner")
        db.users["uu-b"] = {"uuid": "uu-b", "username": "other-user",
                            "created_by_admin_id": 99,
                            "expire_at": EXP, "traffic_limit_bytes": GB}
        db.users["uu-a"] = {"uuid": "uu-a", "username": "vip-local",
                            "created_by_admin_id": 7,
                            "expire_at": EXP, "traffic_limit_bytes": GB}
        with pytest.raises(ValueError, match="visibility unavailable"):
            await svc.api_list_users(ctx, shop_a)
        with pytest.raises(ValueError, match="visibility unavailable"):
            await svc.api_get_user(ctx, shop_a, "uu-a")

        _modules(monkeypatch, visible={"uu-a", "uu-b"})
        panel = FakePanel()
        monkeypatch.setattr(svc, "_panel_client", lambda: panel)
        for operation in (
                lambda: svc.api_get_user(ctx, shop_a, "uu-b"),
                lambda: svc.api_update_user(ctx, shop_a, "uu-b", status="disabled"),
                lambda: svc.api_update_user(ctx, shop_b, "uu-a", status="disabled")):
            with pytest.raises(ValueError):
                await operation()
        visible_a = await svc.api_list_users(ctx, shop_a)
        assert [row["uuid"] for row in visible_a] == ["uu-a"]
        assert panel.updated == []

    asyncio.run(run())


def test_update_quota_charge_exception_restores_and_never_succeeds(monkeypatch):
    async def run():
        _modules(monkeypatch, increment="raise", visible={"uu-a"})
        monkeypatch.setattr(svc, "_owner_account_row", _account_row)
        ctx, db, shop = _ctx_with_shop()
        db.users["uu-a"] = {"uuid": "uu-a", "username": "vip-local",
                            "created_by_admin_id": 7, "traffic_limit_bytes": GB,
                            "expire_at": EXP}
        panel = FakePanel()
        monkeypatch.setattr(svc, "_panel_client", lambda: panel)
        with pytest.raises(RuntimeError, match="user update requires reconciliation"):
            await svc.api_update_user(ctx, shop, "uu-a", traffic_limit_bytes=3 * GB)
        assert panel.updated[-1] == ("uu-a", {"trafficLimitBytes": GB})

    asyncio.run(run())


def test_credit_rejects_non_finite_and_preserves_signed_flooring():
    async def run():
        ctx, db, shop = _ctx_with_shop()
        sid = shop["id"]
        for value in (float("nan"), float("inf"), float("-inf")):
            with pytest.raises(ValueError, match="finite"):
                await svc.add_credit(ctx, sid, 111, value)
        assert await svc.add_credit(ctx, sid, 111, 5) == 5
        assert await svc.add_credit(ctx, sid, 111, -2) == 3
        assert await svc.add_credit(ctx, sid, 111, -10) == 0
        from rwa_plugin_pershop.routes_admin import CreditIn
        from pydantic import ValidationError
        for value in (float("nan"), float("inf"), float("-inf")):
            with pytest.raises(ValidationError):
                CreditIn(units=value)

    asyncio.run(run())


def _request(path, *, key=None, query=b"", method="GET"):
    headers = [(b"x-shop-key", key.encode())] if key else []
    return Request({"type": "http", "method": method, "path": path,
                    "headers": headers, "query_string": query})


async def _set_public_key(ctx, db, shop_id):
    fresh = await svc.new_public_key(ctx)
    await db.execute(
        "UPDATE plugin_pershop_shops SET public_key_hash=$1, public_key_enc=$2 WHERE id=$3",
        fresh["key_hash"], fresh["key_enc"], shop_id)
    return fresh["key"]


def test_user_endpoints_reject_query_keys_and_catalog_compatibility(monkeypatch):
    async def run():
        ctx, db, shop = _ctx_with_shop()
        key = await _set_public_key(ctx, db, shop["id"])
        router, _admin = _admin_router(monkeypatch, ctx)
        from rwa_plugin_pershop.routes_admin import PubUserCreate, PubUserUpdate
        calls = [
            (_endpoint(router, "GET", "/pub/users"),
             (_request("/pub/users", query=b"key=shop-secret"), 50, 0, "")),
            (_endpoint(router, "POST", "/pub/users"),
             (PubUserCreate(username="vip-new", expire_at=EXP),
              _request("/pub/users", query=b"key=shop-secret", method="POST"),
              "hardening-key-0001")),
            (_endpoint(router, "GET", "/pub/users/{user_uuid}"),
             ("uu-a", _request("/pub/users/uu-a", query=b"key=shop-secret"))),
            (_endpoint(router, "PATCH", "/pub/users/{user_uuid}"),
             ("uu-a", PubUserUpdate(status="disabled"),
              _request("/pub/users/uu-a", query=b"key=shop-secret", method="PATCH"))),
        ]
        for endpoint, args in calls:
            with pytest.raises(HTTPException) as raised:
                await endpoint(*args)
            assert raised.value.status_code == 401
            assert "query-string" in raised.value.detail
            assert key not in str(raised.value.detail)
        catalog = await _endpoint(router, "GET", "/pub/catalog")(
            _request("/pub/catalog", query=b"key=" + key.encode()))
        assert catalog["shop"]["id"] == shop["id"]
        create_endpoint = _endpoint(router, "POST", "/pub/users")
        with pytest.raises(HTTPException) as missing_idem:
            await create_endpoint(
                PubUserCreate(username="vip-new", expire_at=EXP),
                _request("/pub/users", key=key, method="POST"), None)
        assert missing_idem.value.status_code == 400
        assert "Idempotency-Key" in missing_idem.value.detail

    asyncio.run(run())
