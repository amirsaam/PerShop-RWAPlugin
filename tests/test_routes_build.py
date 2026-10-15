"""Route-build smoke test: mirrors the panel loader.

Builds both FastAPI routers with stubbed panel imports. Catches
forward-ref / missing-import errors (e.g. models or names only visible
inside builder functions) that the handler-level fakes cannot see.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _stub_panel():
    web = types.ModuleType("web")
    backend = types.ModuleType("web.backend")
    core = types.ModuleType("web.backend.core")
    plugin_api = types.ModuleType("web.backend.core.plugin_api")

    def auth_deps():
        class AdminUser:
            username = "stub"

        def require_permission(resource, action):
            async def dep():
                return AdminUser()

            return dep

        return AdminUser, require_permission

    plugin_api.auth_deps = auth_deps
    sys.modules["web"] = web
    sys.modules["web.backend"] = backend
    sys.modules["web.backend.core"] = core
    sys.modules["web.backend.core.plugin_api"] = plugin_api


def test_route_build():
    _stub_panel()
    from rwa_plugin_pershop.routes_admin import build_admin_router
    from rwa_plugin_pershop.routes_telegram import build_telegram_router

    admin = build_admin_router(ctx=None)
    tg = build_telegram_router(ctx=None)
    assert len(admin.routes) >= 40, f"admin routes: {len(admin.routes)}"
    assert len(tg.routes) == 1, f"tg routes: {len(tg.routes)}"
    print(f"ROUTE BUILD PASSED (admin={len(admin.routes)}, tg={len(tg.routes)})")


if __name__ == "__main__":
    test_route_build()
