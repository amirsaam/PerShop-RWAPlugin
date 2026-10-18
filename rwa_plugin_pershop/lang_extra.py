"""Strings for features shipped AFTER lang.py was frozen as a hand-edited
file. lang.py itself is never touched: this dict is merged in its place with
setdefault, so any wording the user later hand-writes into lang.py keeps
winning, and every other bot language falls back to these English literals.

Merge runs at module load from service.py (routes all import it transitively).
"""
from __future__ import annotations

NEW_LOCALES: dict[str, dict[str, str]] = {
    "en": {
        # promo codes at checkout
        "promo_btn": "🎟️ Promo code",
        "promo_title": "🎟️ Promo code",
        "promo_prompt": "Send your promo code now.\n/cancel to skip.",
        "promo_applied": "🎟️ Promo code {code} registered for this order.",
        "promo_invalid": "That code isn't valid here.",
        "promo_line": "🎟️ Promo {code}: −{value}",
        "campaign_line": "📣 First-order discount {pct}% (invite): −{value}",
        "contract_due": "💳 Payable now: {due}",
        "promo_owner_line": "🎟️ Promo {code}: −{value} → payable {due}",
        "campaign_owner_line": "📣 First-order {pct}%: −{value} → payable {due}",
        "promo_remove_btn": "↩️ Remove promo",
        # sub-card device actions
        "devreset_btn": "📱 Reset devices",
        "devreset_warn": "⚠️ Reset devices for {username}?\n\nEvery device bound to this "
                         "subscription will be kicked free; the next login on any device binds "
                         "it again. The subscription link does NOT change.",
        "devreset_go": "📱 Yes, reset devices",
        "devreset_done": "✅ Devices reset for {username}.\n\nRe-adding the subscription on any "
                         "device binds it again.",
        "devreset_fail": "⚠️ Couldn't reset devices right now. Please try again later.",
        "reissue_btn": "♻️ Full reissue",
        "reissue_warn": "⚠️ Full reissue for {username}?\n\nThis regenerates the subscription "
                        "credentials:\n• the old link stops working immediately,\n• every "
                        "connected device loses the subscription,\n• the NEW link at the end "
                        "replaces the old one everywhere this sub is shared.",
        "reissue_go": "♻️ Confirm reissue",
        "reissue_done": "✅ Subscription re-issued for {username}.\n\nYour new subscription "
                        "link:\n{url}",
        "reissue_fail": "⚠️ Reissue didn't go through. Please try again later.",
        "reissue_sub_btn": "📦 Subscription card",
    },
}


def apply() -> None:
    """Merge NEW_LOCALES over lang.py's LOCALES without ever overwriting an
    entry the user hand-edited."""
    try:
        from rwa_plugin_pershop.lang import LOCALES

        for lang, entries in NEW_LOCALES.items():
            dst = LOCALES.setdefault(lang, {})
            for key, text in entries.items():
                dst.setdefault(key, text)
    except Exception:
        pass


apply()
