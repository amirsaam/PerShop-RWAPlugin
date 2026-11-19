"""Real PostgreSQL hardening for manual credit adjustments."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from postgres_support import Context, seed_shop
from test_postgres_billing import pg_test
from rwa_plugin_pershop.service import add_credit


@pg_test
async def test_concurrent_signed_credit_adjustments_are_exact(pool):
    shop_id, _package = await seed_shop(pool, credit="10")
    await asyncio.gather(*[
        add_credit(Context(pool), shop_id, 111, 1.5 if index % 2 == 0 else -0.25)
        for index in range(40)
    ])
    # Twenty +1.5 adjustments and twenty -0.25 adjustments: 10 + 30 - 5.
    balance = await Context(pool).db.fetchval(
        "SELECT credit_units FROM plugin_pershop_buyers WHERE shop_id=$1 AND tg_id=111",
        shop_id)
    assert balance == Decimal("35.00")
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="finite"):
            await add_credit(Context(pool), shop_id, 111, value)
    after = await Context(pool).db.fetchval(
        "SELECT credit_units FROM plugin_pershop_buyers WHERE shop_id=$1 AND tg_id=111",
        shop_id)
    assert after == balance
