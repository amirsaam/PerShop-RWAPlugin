"""Explicit RWA dependency fixtures used by PerShop's tests.

The production policy is fail closed. These helpers model the one intentional
compatibility case: a successfully resolved, row-less legacy/env superadmin.
They are test scaffolding, not production bypasses.
"""
from __future__ import annotations

from contextlib import contextmanager
import sys
import types


@contextmanager
def recognized_legacy_owner():
    names = ("web", "web.backend", "web.backend.core",
             "web.backend.core.admin_accounts")
    saved = {name: sys.modules.get(name) for name in names}
    for name in names:
        module = types.ModuleType(name)
        module.__path__ = []
        sys.modules[name] = module

    async def no_account_row(_username):
        return None

    async def accept_counter(*_args):
        return True

    accounts = sys.modules["web.backend.core.admin_accounts"]
    accounts.get_admin_account_by_username = no_account_row
    accounts.increment_usage_counter = accept_counter
    try:
        yield
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
