"""Bot-token encryption using panel-shipped `cryptography`.

Key lives in plugin settings (plugin_settings table) under `bot_key`.
"""
from __future__ import annotations


async def _get_or_create_key(ctx) -> bytes:
    key = await ctx.settings.get("bot_key", default=None)
    if key:
        return key.encode() if isinstance(key, str) else key
    from cryptography.fernet import Fernet

    raw = Fernet.generate_key()
    await ctx.settings.set("bot_key", raw.decode())
    return raw


async def encrypt_token(ctx, token: str) -> str:
    from cryptography.fernet import Fernet

    key = await _get_or_create_key(ctx)
    return Fernet(key).encrypt(token.encode()).decode()


async def decrypt_token(ctx, token_enc: str) -> str:
    from cryptography.fernet import Fernet

    key = await _get_or_create_key(ctx)
    return Fernet(key).decrypt(token_enc.encode()).decode()
