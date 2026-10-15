"""Per-admin Telegram shop — remnawave-admin Plugin API v1.

Constraint: shop code talks ONLY to remnawave-admin (ctx.db + panel_api()
facade + plugin routes). Never to Remnawave core directly.
"""
from __future__ import annotations


def _own_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("rwa-plugin-pershop")
    except PackageNotFoundError:
        return "0.0.0"


def _build(ctx):
    from web.backend.core.plugins import PluginParts, ScheduledTask

    from rwa_plugin_pershop.routes_admin import build_admin_router
    from rwa_plugin_pershop.routes_telegram import build_telegram_router
    from rwa_plugin_pershop.tasks import broadcast_tick, notifier_tick, poller_tick
    from fastapi import APIRouter

    import functools

    admin_router = build_admin_router(ctx)
    tg_router = build_telegram_router(ctx)

    root = APIRouter()
    root.include_router(admin_router)
    root.include_router(tg_router)

    return PluginParts(
        router=root,
        scheduled_tasks=[
            ScheduledTask(
                name="pershop-notifier",
                interval_seconds=900,
                coro=functools.partial(notifier_tick, ctx),
            ),
            ScheduledTask(
                name="pershop-poller",
                interval_seconds=15,
                coro=functools.partial(poller_tick, ctx),
            ),
            ScheduledTask(
                name="pershop-broadcast",
                interval_seconds=30,
                coro=functools.partial(broadcast_tick, ctx),
            ),
        ],
    )


def manifest():
    from web.backend.core.plugins import NavEntry, PluginManifest, PluginUI

    # Panel 5.x: PluginUI exists and the frontend mounts wheel UI via
    # ExternalPluginPage on the single route /plugins/:pluginId — one nav
    # entry only (sub-paths have no route and fall through to NotFound).
    # (Panel 4.4.x has no PluginUI/loader at all; minimum panel is 5.x.)
    return PluginManifest(
        id="pershop",
        name="PerShop",
        version=_own_version(),
        api_version=1,
        billing="free",
        build=_build,
        rbac_resources={
            "pershop": ["view", "edit", "moderate"],
            # Auto-granted to superadmin only; grant to no regular role.
            "pershop_system": ["manage"],
        },
        navigation=[
            NavEntry(
                path="/plugins/pershop",
                label_i18n="PerShop",
                icon="Bot",
                permission=("pershop", "view"),
                section_i18n="RWA-PerShop",
            ),
        ],
        ui=PluginUI(kind="module", path="/app"),
    )
