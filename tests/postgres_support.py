"""Local, disposable PostgreSQL support for the integration tests."""
from __future__ import annotations

import atexit
import importlib.util
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import types
import uuid

import asyncpg


class PostgresServer:
    def __init__(self):
        self._temp = None
        self._dsn = os.environ.get("PERSHOP_TEST_POSTGRES_DSN")
        self._owned = self._dsn is None
        self._started = False

    def start(self):
        if self._started:
            return
        if self._owned:
            initdb = shutil.which("initdb")
            pg_ctl = shutil.which("pg_ctl")
            if not initdb or not pg_ctl:
                raise RuntimeError(
                    "install PostgreSQL or set PERSHOP_TEST_POSTGRES_DSN")
            self._temp = Path(tempfile.mkdtemp(prefix="pershop-postgres-"))
            data = self._temp / "data"
            sockets = self._temp / "socket"
            sockets.mkdir()
            subprocess.run(
                [initdb, "-D", str(data), "-A", "trust", "-U", "postgres",
                 "--no-locale", "-E", "UTF8"],
                check=True, capture_output=True, text=True)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            subprocess.run(
                [pg_ctl, "-D", str(data), "-w", "-o",
                 f"-p {port} -k {sockets} -c listen_addresses=127.0.0.1",
                 "start"],
                check=True, capture_output=True, text=True)
            self._dsn = f"postgresql://postgres@127.0.0.1:{port}/postgres"
        self._started = True

    def stop(self):
        if self._temp and (self._temp / "data").exists():
            subprocess.run(
                [shutil.which("pg_ctl") or "pg_ctl", "-D",
                 str(self._temp / "data"), "-m", "immediate", "stop"],
                capture_output=True, text=True)
        if self._temp:
            shutil.rmtree(self._temp, ignore_errors=True)
        self._temp = None
        self._started = False

    async def database(self):
        """Return (pool, database_name); the caller drops the database."""
        self.start()
        name = f"pershop_test_{uuid.uuid4().hex}"
        admin = await asyncpg.connect(self._dsn)
        try:
            await admin.execute(f'CREATE DATABASE "{name}"')
        finally:
            await admin.close()
        pool = await asyncpg.create_pool(self._dsn, database=name,
                                         min_size=1, max_size=32)
        return pool, name

    async def drop(self, pool, name):
        """Close clients, then always attempt to remove the disposable database."""
        try:
            if pool is not None:
                await pool.close()
        finally:
            admin = await asyncpg.connect(self._dsn)
            try:
                await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                await admin.close()


SERVER = PostgresServer()
atexit.register(SERVER.stop)


async def migrated_database():
    """Start a database and execute the real migration functions."""
    pool, name = await SERVER.database()
    try:
        async with pool.acquire() as connection:
            versions = Path(__file__).parents[1] / "rwa_plugin_pershop" / "migrations" / "versions"
            for path in sorted(versions.glob("pershop_*.py")):
                revision = path.stem
                spec = importlib.util.spec_from_file_location(revision, path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                statements = []
                fake_alembic = types.ModuleType("alembic")
                fake_alembic.op = types.SimpleNamespace(
                    execute=statements.append)
                previous = sys.modules.get("alembic")
                sys.modules["alembic"] = fake_alembic
                try:
                    module.upgrade()
                finally:
                    if previous is None:
                        sys.modules.pop("alembic", None)
                    else:
                        sys.modules["alembic"] = previous
                for statement in statements:
                    await connection.execute(statement)
        return pool, name
    except Exception:
        await SERVER.drop(pool, name)
        raise


class DatabaseMirror:
    """Pool facade with the core-user helpers used by the real service."""

    def __init__(self, pool):
        self.pool = pool

    def __getattr__(self, name):
        return getattr(self.pool, name)

    async def upsert_user(self, user):
        """Small core-users mirror used by the real panel creation helper."""
        from datetime import datetime

        user_uuid = str(user.get("uuid") or f"uuid-panel-{user.get('id')}")
        expire = user.get("expireAt", user.get("expire_at"))
        if isinstance(expire, str):
            expire = datetime.fromisoformat(expire.replace("Z", "+00:00"))
        await self.execute(
            """INSERT INTO users(uuid,username,subscription_url,expire_at,
               traffic_limit_bytes,telegram_id,description,panel_id)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8)
               ON CONFLICT(uuid) DO UPDATE SET username=EXCLUDED.username,
                 subscription_url=EXCLUDED.subscription_url,expire_at=EXCLUDED.expire_at,
                 traffic_limit_bytes=EXCLUDED.traffic_limit_bytes,
                 telegram_id=EXCLUDED.telegram_id,description=EXCLUDED.description,
                 panel_id=EXCLUDED.panel_id""",
            user_uuid, user.get("username"),
            user.get("subscriptionUrl", user.get("subscription_url")), expire,
            user.get("trafficLimitBytes", user.get("traffic_limit_bytes")),
            user.get("telegramId", user.get("telegram_id")), user.get("description"),
            user.get("id"))
        return user_uuid

    async def get_user_uuid_by_panel_id(self, panel_id):
        return await self.fetchval(
            "SELECT uuid FROM users WHERE panel_id=$1", int(panel_id))

    async def get_user_by_uuid(self, user_uuid):
        return await self.fetchrow("SELECT * FROM users WHERE uuid=$1", str(user_uuid))


class Context:
    def __init__(self, db):
        self.db = DatabaseMirror(db)
        self.logger = types.SimpleNamespace(
            error=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
            info=lambda *args, **kwargs: None)


async def seed_shop(pool, *, credit="10", referral_percent="0"):
    shop_id = await pool.fetchval(
        "INSERT INTO plugin_pershop_shops(owner_admin,admin_tg_id,prefix,"
        "referral_percent) VALUES($1,999,'vip',$2) RETURNING id",
        f"owner-{uuid.uuid4().hex}", referral_percent)
    package_id = await pool.fetchval(
        "INSERT INTO plugin_pershop_packages(shop_id,name,price_units)"
        " VALUES($1,'Pro',10) RETURNING id", shop_id)
    await pool.execute(
        "INSERT INTO plugin_pershop_buyers(shop_id,tg_id,credit_units)"
        " VALUES($1,111,$2::numeric)", shop_id, credit)
    return shop_id, package_id


async def new_order(pool, shop_id, package_id, key, *, buyer=111,
                    credit="0", total="4", status="pending",
                    options_snapshot=None, renew_username=None):
    import json

    from rwa_plugin_pershop.billing import create_order_with_credit

    return await create_order_with_credit(
        Context(pool), shop_id=shop_id, buyer_tg_id=buyer,
        buyer_username=str(buyer), package_id=package_id,
        receipt_file_id=f"receipt-{key}", receipt_kind="photo",
        renew_username=renew_username, total_units=total,
        options_snapshot=options_snapshot or json.dumps(
            {"credit_used": str(credit)}),
        credit_units=credit, idempotency_key=key)
