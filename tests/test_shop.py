"""Integration tests with fakes — no panel, no network."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone

from rwa_plugin_pershop.naming import allocate_username, traffic_ratio


class FakeSettings:
    def __init__(self):
        self.d = {}

    async def get(self, key, default=None):
        return self.d.get(key, default)

    async def set(self, key, value):
        self.d[key] = value

    async def delete(self, key):
        self.d.pop(key, None)


class FakeLogger:
    def warning(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass


class FakeDB:
    def __init__(self):
        self.shops = {}
        self.pkgs = {}
        self.orders = {}
        self.subs = {}
        self.sessions = {}
        self.users = {}  # uuid -> dict
        self.short_users = {}  # short_uuid -> panel-style row dict
        self.tiers = {}
        self.referrals = set()
        self.buyers = {}  # (shop_id, tg_id) -> lang
        self.last_mids = {}  # (shop_id, tg_id) -> tracked last bot message id
        self.credits = {}  # (shop_id, tg_id) -> credit units
        self.scammers = {}  # tg_id -> {marked_by_admin, reason}
        self.test_claims = set()  # tg_ids with a panel-global test claim
        self.scam_noted = set()  # (shop_id, tg_id) warned in last 24h
        self.svc_cats = {}
        self.svcs = {}
        self.bcs = {}
        self.promos = {}  # id -> promo row
        self.promo_uses = set()  # (promo_id, buyer_tg_id)
        self.events = []  # activity-feed rows
        self.versions = {"pershop_001"}  # fake alembic_version rows
        self.dropped = False
        self.seq = {"shop": 0, "pkg": 0, "order": 0, "sub": 0, "tier": 0,
                    "scat": 0, "svc": 0, "bc": 0, "promo": 0, "event": 0}

    def _next(self, k):
        self.seq[k] += 1
        return self.seq[k]

    # -- reads --
    async def fetchrow(self, q, *a):
        rows = await self.fetch(q, *a)
        return rows[0] if rows else None

    async def fetch(self, q, *a):
        if "FROM plugin_pershop_shops WHERE owner_admin" in q:
            return [s for s in self.shops.values() if s["owner_admin"] == a[0]]
        if "FROM plugin_pershop_shops WHERE public_key_hash" in q:
            return [s for s in self.shops.values() if s.get("public_key_hash") == a[0]]
        if "FROM plugin_pershop_shops WHERE id" in q:
            s = self.shops.get(a[0])
            return [s] if s else []
        if "FROM plugin_pershop_shops" in q:
            return list(self.shops.values())
        if "FROM plugin_pershop_subs WHERE shop_id" in q and "expire_at IS NOT NULL" in q:
            return [dict(s) for s in self.subs.values()
                    if s["shop_id"] == a[0] and s.get("expire_at")]
        if "DISTINCT buyer_tg_id FROM plugin_pershop_subs WHERE shop_id" in q:
            return [{"buyer_tg_id": s["buyer_tg_id"]} for s in self.subs.values()
                    if s["shop_id"] == a[0] and s.get("buyer_tg_id")]
        if "FROM plugin_pershop_subs WHERE shop_id" in q and "package_id IS NULL" in q:
            return [s for s in self.subs.values()
                    if s["shop_id"] == a[0] and not s.get("package_id") and not s.get("is_test")]
        if "FROM plugin_pershop_subs WHERE shop_id" in q and "user_uuid" in q:
            return [s for s in self.subs.values() if s["shop_id"] == a[0] and s["user_uuid"] == a[1]]
        if "FROM plugin_pershop_subs WHERE shop_id" in q and "is_test=TRUE" in q:
            return [{"id": sid} for sid, s in self.subs.items()
                    if s["shop_id"] == a[0] and s["buyer_tg_id"] == a[1] and s.get("is_test")]
        if "FROM plugin_pershop_subs WHERE shop_id" in q and "buyer_tg_id" in q:
            return [s for s in self.subs.values() if s["shop_id"] == a[0] and s["buyer_tg_id"] == a[1]]
        if "FROM plugin_pershop_subs WHERE shop_id" in q and "username" in q:
            return [s for s in self.subs.values() if s["shop_id"] == a[0] and s["username"] == a[1]]
        if "FROM plugin_pershop_subs WHERE id" in q:
            if "shop_id" in q:
                s = self.subs.get(a[0])
                return [s] if s and s["shop_id"] == a[1] else []
            s = self.subs.get(a[0])
            return [s] if s else []
        if "FROM plugin_pershop_subs WHERE shop_id" in q:
            out = [s for s in self.subs.values() if s["shop_id"] == a[0]]
            if "NOT cancelled" in q:
                out = [s for s in out if not s.get("cancelled")]
            return out
        if "SELECT username FROM plugin_pershop_subs" in q:
            return [{"username": s["username"]} for s in self.subs.values() if s["shop_id"] == a[0]]
        if "SELECT username FROM users" in q:
            return [{"username": u["username"]} for u in self.users.values()]
        if "FROM users WHERE uuid" in q:
            u = self.users.get(a[0])
            return [u] if u else []
        if "FROM plugin_pershop_orders WHERE id" in q and "shop_id" in q:
            o = self.orders.get(a[0])
            return [o] if o and o["shop_id"] == a[1] else []
        if "FROM plugin_pershop_orders WHERE id" in q:
            o = self.orders.get(a[0])
            return [o] if o else []
        if "FROM plugin_pershop_packages WHERE id" in q and "shop_id" in q:
            p = self.pkgs.get(a[0])
            return [p] if p and p["shop_id"] == a[1] and p.get("active", True) else []
        if "FROM plugin_pershop_packages WHERE id" in q:
            p = self.pkgs.get(a[0])
            return [p] if p else []
        if "FROM plugin_pershop_scammers ORDER BY" in q:
            return sorted(self.scammers.values(), key=lambda r: r["tg_id"])
        if "FROM plugin_pershop_scammers WHERE tg_id" in q:
            r = self.scammers.get(a[0])
            return [r] if r else []
        if "scam_noted_at" in q and "FROM plugin_pershop_buyers" in q:
            ts = datetime.now(timezone.utc) if (a[0], a[1]) in self.scam_noted else None
            return [{"scam_noted_at": ts}]
        if "COALESCE(SUM(total_units)" in q:
            rows = [o for o in self.orders.values()
                    if o["shop_id"] == a[0] and o["status"] == "approved"]
            return [{"n": len(rows), "u": sum(o.get("total_units") or 0 for o in rows)}]
        if "receipt_kind='credit'" in q and "FROM plugin_pershop_orders" in q:
            rows = [o for o in self.orders.values()
                    if o["shop_id"] == a[0] and o.get("buyer_tg_id") == a[1]
                    and o.get("receipt_kind") == "credit" and o["status"] == "approved"
                    and o.get("renew_username") == a[2]]
            rows.sort(key=lambda o: o["id"], reverse=True)
            return rows[:1]
        if "FROM plugin_pershop_orders WHERE shop_id" in q and "buyer_tg_id" in q \
                and "ORDER BY id DESC" in q:
            rows = [dict(o) for o in self.orders.values()
                    if o["shop_id"] == a[0] and o["buyer_tg_id"] == a[1]]
            rows.sort(key=lambda o: -o["id"])
            return rows[:50] if "LIMIT 50" in q else rows
        if "FROM plugin_pershop_orders WHERE shop_id" in q and "renew_username" in q:
            return [o for o in self.orders.values()
                    if o["shop_id"] == a[0] and o.get("renew_username") == a[1]
                    and o["status"] in ("pending", "withdraw_requested")]
        if "FROM plugin_pershop_packages WHERE shop_id" in q and "effect=$2" in q:
            return [p for p in self.pkgs.values()
                    if p["shop_id"] == a[0] and p.get("active", True)
                    and (p.get("effect") or "full") == a[1]]
        if "effect='traffic_only'" in q:
            return [{"id": p["id"]} for p in self.pkgs.values()
                    if p["shop_id"] == a[0] and p.get("active", True)
                    and (p.get("effect") or "full") == "traffic_only"]
        if "FROM plugin_pershop_packages WHERE shop_id" in q:
            return sorted([p for p in self.pkgs.values() if p["shop_id"] == a[0] and p.get("active", True)],
                          key=lambda p: (p.get("sort") or 0, p["id"]))
        if "FROM plugin_pershop_packages WHERE tier_id" in q:
            return sorted([p for p in self.pkgs.values()
                    if p.get("tier_id") == a[0] and ("AND active" not in q or p.get("active", True))],
                    key=lambda p: (p.get("sort") or 0, p["id"]))
        if "FROM plugin_pershop_tiers WHERE shop_id" in q:
            return [t for t in self.tiers.values()
                    if t["shop_id"] == a[0] and ("AND active" not in q or t.get("active", True))]
        if "FROM plugin_pershop_tiers WHERE id" in q:
            t = self.tiers.get(a[0])
            if not t:
                return []
            if "shop_id" in q and t["shop_id"] != a[1]:
                return []
            if "AND active" in q and not t.get("active", True):
                return []
            return [t]
        if "FROM plugin_pershop_service_cats WHERE shop_id" in q:
            return [c for c in self.svc_cats.values()
                    if c["shop_id"] == a[0] and ("AND active" not in q or c.get("active", True))]
        if "FROM plugin_pershop_service_cats WHERE id" in q:
            c = self.svc_cats.get(a[0])
            if not c:
                return []
            if "shop_id" in q and c["shop_id"] != a[1]:
                return []
            if "AND active" in q and not c.get("active", True):
                return []
            return [c]
        if "FROM plugin_pershop_services WHERE id" in q:
            s = self.svcs.get(a[0])
            if not s:
                return []
            if "shop_id" in q and s["shop_id"] != a[1]:
                return []
            if "AND active" in q and not s.get("active", True):
                return []
            return [s]
        if "FROM plugin_pershop_services WHERE shop_id" in q:
            rows = [s for s in self.svcs.values()
                    if s["shop_id"] == a[0] and s.get("active", True)]
            if "cat_id IS NULL" in q:
                return [s for s in rows if not s.get("cat_id")]
            if "cat_id=$2" in q:
                return [s for s in rows if s.get("cat_id") == a[1]]
            return rows
        if "FROM plugin_pershop_sessions" in q:
            s = self.sessions.get((a[0], a[1], a[2]))
            return [s] if s else []
        if "FROM users" in q and "LIMIT" in q:  # external admin API user list
            rows = [u for u in self.users.values() if u.get("created_by_admin_id") == a[0]]
            needle = (a[1] or "").strip("%").lower()
            if needle:
                rows = [u for u in rows if needle in (u.get("username") or "").lower()]
            rows.sort(key=lambda u: u.get("username") or "")
            return rows[a[3]:a[3] + a[2]]
        if "created_by_admin_id IS NOT DISTINCT FROM" in q:
            sub_uuids = {s["user_uuid"] for s in self.subs.values() if s["shop_id"] == a[1]}
            return [{"uuid": u["uuid"], "username": u["username"], "expire_at": u.get("expire_at"),
                     "traffic_limit_bytes": u.get("traffic_limit_bytes")}
                    for u in self.users.values()
                    if u.get("created_by_admin_id") == a[0] and u["uuid"] not in sub_uuids]
        if "INSERT INTO plugin_pershop_subs" in q and "RETURNING id" in q:  # assign-plan (buyer NULL literal)
            nid = self._next("sub")
            if ",NULL,$2,$3,$4,$5,$6" in q:
                self.subs[nid] = {"id": nid, "shop_id": a[0], "buyer_tg_id": None, "user_uuid": a[1],
                                  "username": a[2], "expire_at": a[3], "traffic_limit_bytes": a[4],
                                  "package_id": a[5],
                                  "is_test": False, "is_linked": False, "cancelled": False,
                                  "notified_80": False, "notified_72h": False, "notified_expired": False}
            else:  # external admin api-create: ($1..$7, buyer at $2)
                self.subs[nid] = {"id": nid, "shop_id": a[0], "buyer_tg_id": a[1], "user_uuid": a[2],
                                  "username": a[3], "expire_at": a[4], "traffic_limit_bytes": a[5],
                                  "package_id": a[6],
                                  "is_test": False, "is_linked": False, "cancelled": False,
                                  "notified_80": False, "notified_72h": False, "notified_expired": False}
            return [{"id": nid}]
        if "FROM plugin_pershop_orders o" in q and "LIMIT 10" in q:
            out = []
            for o in sorted(self.orders.values(), key=lambda x: x["id"], reverse=True)[:10]:
                if o["shop_id"] == a[0] and o.get("buyer_tg_id") == a[1]:
                    p = self.pkgs.get(o.get("package_id") or -1) or {}
                    v = self.svcs.get(o.get("service_id") or -1) or {}
                    row = dict(o)
                    row["package_name"] = p.get("name") or v.get("name")
                    row["package_units"] = (p.get("price_units") if p
                                            else v.get("price_units"))
                    out.append(row)
            return out
        if "FROM plugin_pershop_orders o WHERE o.shop_id" in q:
            return [o for o in self.orders.values()
                    if o["shop_id"] == a[0] and o["status"] in ("pending", "withdraw_requested")]
        if "options_snapshot, receipt_kind FROM plugin_pershop_orders" in q:
            rows = [o for o in self.orders.values()
                    if o["shop_id"] == a[0] and o.get("buyer_tg_id") == a[1]
                    and o.get("renew_username") == a[2] and o["status"] == "approved"]
            rows.sort(key=lambda o: o["id"], reverse=True)
            return [{"options_snapshot": o.get("options_snapshot"),
                     "receipt_kind": o.get("receipt_kind")} for o in rows]
        if "SELECT buyer_tg_id FROM plugin_pershop_orders WHERE shop_id" in q:
            return [{"buyer_tg_id": o["buyer_tg_id"]} for o in self.orders.values()
                    if o["shop_id"] == a[0] and o.get("buyer_tg_id")]
        if "SELECT tg_id, lang, credit_units FROM plugin_pershop_buyers" in q and "AND tg_id=$2" in q:
            return [{"tg_id": a[1], "lang": self.buyers.get((a[0], a[1])),
                     "credit_units": self.credits.get((a[0], a[1]), 0.0)}]
        if "SELECT tg_id, lang, credit_units FROM plugin_pershop_buyers WHERE shop_id" in q:
            tgs = {tg for (s, tg) in list(self.buyers) + list(self.credits) if s == a[0]}
            return [{"tg_id": tg, "lang": self.buyers.get((a[0], tg)),
                     "credit_units": self.credits.get((a[0], tg), 0.0)}
                    for tg in sorted(tgs)]
        if "FROM plugin_pershop_orders WHERE shop_id" in q and "status IN" in q:
            return [dict(id=o["id"], buyer_tg_id=o.get("buyer_tg_id"),
                         buyer_username=o.get("buyer_username"),
                         created_at=o.get("created_at"))
                    for o in self.orders.values()
                    if o["shop_id"] == a[0] and o["status"] in ("pending", "withdraw_requested")]
        if "FROM plugin_pershop_events WHERE shop_id" in q:
            rows = [e for e in self.events if e["shop_id"] == a[0]]
            rows.sort(key=lambda e: e["id"], reverse=True)
            return rows[: a[1]]
        if "DISTINCT buyer_tg_id FROM plugin_pershop_orders WHERE shop_id" in q and "status='approved'" in q:
            return [{"buyer_tg_id": o["buyer_tg_id"]} for o in self.orders.values()
                    if o["shop_id"] == a[0] and o["status"] == "approved"]
        if "DISTINCT buyer_tg_id FROM plugin_pershop_orders WHERE shop_id" in q:
            return [{"buyer_tg_id": o["buyer_tg_id"]} for o in self.orders.values()
                    if o["shop_id"] == a[0] and o.get("buyer_tg_id")]
        if "FROM plugin_pershop_orders WHERE shop_id" in q and "buyer_tg_id" in q:
            st = q.split("status='")[1].split("'")[0] if "status='" in q else None
            return [o for o in self.orders.values()
                    if o["shop_id"] == a[0] and o.get("buyer_tg_id") == a[1]
                    and (st is None or o["status"] == st)]
        if "DISTINCT tg_id FROM plugin_pershop_buyers WHERE shop_id" in q:
            return [{"tg_id": tg} for (s, tg) in self.buyers if s == a[0]]
        if "FROM plugin_pershop_buyers WHERE shop_id" in q:
            lang = self.buyers.get((a[0], a[1]))
            return [{"lang": lang}] if lang else []
        if "GROUP BY r.referred_tg_id" in q:
            out = []
            for (s, referrer, referred) in self.referrals:
                if s != a[0] or referrer != a[1]:
                    continue
                earned = sum(float(o.get("credit_units") or 0) for o in self.orders.values()
                             if o["shop_id"] == s and o.get("buyer_tg_id") == referred
                             and o["status"] == "approved")
                uname = next((o.get("buyer_username") for o in self.orders.values()
                              if o["shop_id"] == s and o.get("buyer_tg_id") == referred
                              and o.get("buyer_username")), None)
                out.append({"referred_tg_id": referred, "earned": earned, "uname": uname})
            out.sort(key=lambda r: float(r["earned"]), reverse=True)
            return out
        if "FROM plugin_pershop_referrals r LEFT JOIN" in q:
            pairs = []
            for r in self.referrals:
                if r[0] != a[0]:
                    continue
                olist = [o for o in self.orders.values()
                         if o["shop_id"] == a[0] and o["buyer_tg_id"] == r[2]
                         and o["status"] == "approved"]
                pairs.append({"referrer": r[1], "invited": r[2],
                              "earned": round(sum(float(o.get("credit_units") or 0)
                                                  for o in olist), 4),
                              "buys": len(olist)})
            pairs.sort(key=lambda p: (-p["earned"], p["invited"]))
            return pairs
        if "DISTINCT referred_tg_id FROM plugin_pershop_referrals WHERE shop_id" in q:
            return [{"referred_tg_id": r[2]} for r in self.referrals if r[0] == a[0]]
        if "FROM plugin_pershop_referrals WHERE shop_id" in q:
            return [{"referrer_tg_id": r[1]} for r in self.referrals
                    if r[0] == a[0] and r[2] == a[1]]
        if "FROM plugin_pershop_promos p" in q and "LEFT JOIN" in q:
            return [dict(p, uses=sum(1 for pid, _ in self.promo_uses if pid == pid_))
                    for pid_, p in self.promos.items() if p["shop_id"] == a[0]]
        if "FROM plugin_pershop_promos WHERE id" in q:
            p = self.promos.get(a[0])
            return [p] if p and p.get("shop_id") == a[1] else []
        if "FROM plugin_pershop_promos WHERE shop_id" in q:
            return [p for p in self.promos.values()
                    if p["shop_id"] == a[0] and p["code"] == a[1]]
        if "SELECT COUNT(*) FROM plugin_pershop_promo_uses WHERE promo_id" in q:
            return [{"n": sum(1 for pid, _ in self.promo_uses if pid == a[0])}]
        if "FROM plugin_pershop_promo_uses WHERE promo_id" in q:
            return [{"n": 1}] if (a[0], a[1]) in self.promo_uses else []
        if "FROM plugin_pershop_broadcasts WHERE status IN" in q:
            return [b for b in self.bcs.values() if b["status"] in ("queued", "sending")]
        if "INSERT INTO plugin_pershop_buyers" in q and "RETURNING credit_units" in q:
            key = (a[0], a[1])
            new = max(0.0, self.credits.get(key, 0.0) + float(a[2]))
            self.credits[key] = new
            return [{"credit_units": new}]
        if "INSERT INTO plugin_pershop_orders(shop_id,service_id" in q:
            nid = self._next("order")
            self.orders[nid] = {"id": nid, "shop_id": a[0], "service_id": a[1], "package_id": None,
                                "buyer_tg_id": a[2], "buyer_username": a[3],
                                "receipt_file_id": a[4], "receipt_kind": a[5],
                                "renew_username": None, "status": "pending", "deny_reason": None,
                                "withdraw_reason": None, "withdraw_card": None,
                                "owner_chat_id": None, "owner_msg_id": None,
                                "total_units": a[6], "options_snapshot": a[7],
                                "credit_units": 0,
                                "created_at": datetime.now(timezone.utc), "decided_at": None, "decided_by": None,
                                "owner_nudged_at": None}
            return [{"id": nid}]
        if "INSERT INTO plugin_pershop_orders" in q:
            nid = self._next("order")
            self.orders[nid] = {"id": nid, "shop_id": a[0], "package_id": a[1], "buyer_tg_id": a[2],
                                "buyer_username": a[3], "receipt_file_id": a[4], "receipt_kind": a[5],
                                "renew_username": a[6], "status": "pending", "deny_reason": None,
                                "withdraw_reason": None, "withdraw_card": None,
                                "owner_chat_id": None, "owner_msg_id": None,
                                "total_units": a[7] if len(a) > 7 else None,
                                "options_snapshot": a[8] if len(a) > 8 else None,
                                "credit_units": 0,
                                "created_at": datetime.now(timezone.utc), "decided_at": None, "decided_by": None,
                                "owner_nudged_at": None}
            return [{"id": nid}]
        if "INSERT INTO plugin_pershop_test_claims" in q:
            if a[0] in self.test_claims:
                return []
            self.test_claims.add(a[0])
            return [{"tg_id": a[0]}]
        if "INSERT INTO plugin_pershop_broadcasts" in q:
            nid = self._next("bc")
            self.bcs[nid] = {"id": nid, "shop_id": a[0], "html_text": a[1], "photo": a[2],
                             "recipients": a[3], "total": a[4], "pos": 0,
                             "done": 0, "failed": 0, "status": "queued"}
            return [{"id": nid}]
        if "INSERT INTO plugin_pershop_promos" in q:
            if any(p["shop_id"] == a[0] and p["code"] == a[1] for p in self.promos.values()):
                raise Exception(
                    "duplicate key value violates unique constraint"
                    " \"uq_pershop_promos_shop_code\"")
            pid = self._next("promo")
            row = {"id": pid, "shop_id": a[0], "code": a[1], "kind": a[2],
                   "value": float(a[3]), "max_uses": int(a[4] or 0),
                   "valid_until": a[5], "active": True,
                   "created_by_owner": a[6], "uses": 0}
            self.promos[pid] = row
            return [row]
        raise AssertionError(f"unhandled fetch: {q}")

    # -- panel-table helpers (mirror db_service surface used by the plugin) --
    async def get_user_by_short_uuid(self, short_uuid):
        return self.short_users.get(short_uuid)

    async def get_user_by_uuid(self, user_uuid):
        return self.users.get(user_uuid)

    async def upsert_user(self, user):
        uuid = user.get("uuid") or f"uuid-panel-{user.get('id')}"
        self.users[uuid] = {"uuid": uuid, "username": user.get("username"),
                            "subscription_url": user.get("subscriptionUrl"),
                            "expire_at": user.get("expireAt", user.get("expire_at")),
                            "traffic_limit_bytes": user.get("trafficLimitBytes",
                                                            user.get("traffic_limit_bytes")),
                            "used_traffic_bytes": 0, "created_by_admin_id": None,
                            "_panel_id": user.get("id")}
        return uuid

    async def get_user_uuid_by_panel_id(self, panel_id):
        for u in self.users.values():
            if u.get("_panel_id") == panel_id:
                return u["uuid"]
        return None

    async def get_user_by_uuid(self, uuid):
        return self.users.get(uuid)

    async def fetchval(self, q, *a):
        if "SELECT last_mid FROM plugin_pershop_buyers" in q:
            return self.last_mids.get((a[0], a[1]))
        if "FROM plugin_pershop_buyers" in q and "credit_units" in q:
            return self.credits.get((a[0], a[1]), 0.0)
        if "COUNT(*)" in q and "plugin_pershop_subs" in q and "expire_at" in q:
            return sum(1 for s in self.subs.values()
                       if s["shop_id"] == a[0] and s.get("expire_at") and s["expire_at"] > a[1])
        if "COUNT(*)" in q and "plugin_pershop_subs" in q and "is_test=TRUE" in q:
            return sum(1 for s in self.subs.values() if s["shop_id"] == a[0] and s.get("is_test"))
        if "COUNT(*)" in q and "plugin_pershop_subs" in q and "is_linked=TRUE" in q:
            return sum(1 for s in self.subs.values() if s["shop_id"] == a[0] and s.get("is_linked"))
        if "COUNT(*)" in q and "plugin_pershop_services" in q:
            return sum(1 for s in self.svcs.values()
                       if s["shop_id"] == a[0] and s.get("active", True))
        if "COALESCE(SUM(credit_units)" in q:
            return sum(float(o.get("credit_units") or 0) for o in self.orders.values()
                       if o["shop_id"] == a[0] and o["status"] == "approved")
        if "COUNT(*)" in q and "plugin_pershop_orders" in q and "buyer_tg_id" in q:
            return sum(1 for o in self.orders.values()
                       if o["shop_id"] == a[0] and o.get("buyer_tg_id") == a[1])
        if "COUNT(*)" in q and "plugin_pershop_promo_uses" in q:
            return sum(1 for pid, _ in self.promo_uses if pid == a[0])
        if "status IN (" in q and "plugin_pershop_orders" in q:
            return sum(1 for o in self.orders.values()
                       if o["shop_id"] == a[0] and o["status"] in ("pending", "withdraw_requested"))
        if "COUNT(*)" in q and "plugin_pershop_orders" in q and "created_at <" in q:
            thr = datetime.now(timezone.utc) - timedelta(hours=24)
            return sum(1 for o in self.orders.values()
                       if o["shop_id"] == a[0] and o["status"] == "pending"
                       and isinstance(o.get("created_at"), datetime)
                       and o["created_at"] < thr)
        if "status='" in q and "plugin_pershop_orders" in q:
            st = q.split("status='")[1].split("'")[0]
            return sum(1 for o in self.orders.values() if o["shop_id"] == a[0] and o["status"] == st)
        if "COUNT(*)" in q and "plugin_pershop_orders" in q:
            if "shop_id" in q:
                return sum(1 for o in self.orders.values() if o["shop_id"] == a[0] and o["status"] == a[1])
            return sum(1 for o in self.orders.values()
                       if o["status"] in ("pending", "withdraw_requested"))
        if "COUNT(*)" in q and "plugin_pershop_shops" in q:
            return len(self.shops)
        if "COUNT(*)" in q and "plugin_pershop_subs" in q and "package_id IS NULL" in q:
            return sum(1 for s in self.subs.values()
                       if s["shop_id"] == a[0] and not s.get("package_id")
                       and not s.get("is_test") and not s.get("is_linked"))
        if "COUNT(*)" in q and "plugin_pershop_subs" in q:
            return len(self.subs)
        raise AssertionError(f"unhandled fetchval: {q}")

    async def execute(self, q, *a):
        if q.strip().startswith("DROP TABLE IF EXISTS"):
            for _t, _s in (("plugin_pershop_broadcasts", self.bcs),
                            ("plugin_pershop_referrals", self.referrals),
                            ("plugin_pershop_test_claims", self.test_claims),
                            ("plugin_pershop_scammers", self.scammers),
                            ("plugin_pershop_buyers", self.buyers),
                            ("plugin_pershop_sessions", self.sessions),
                            ("plugin_pershop_orders", self.orders),
                            ("plugin_pershop_subs", self.subs),
                            ("plugin_pershop_packages", self.pkgs),
                            ("plugin_pershop_services", self.svcs),
                            ("plugin_pershop_tiers", self.tiers),
                            ("plugin_pershop_service_cats", self.svc_cats),
                            ("plugin_pershop_shops", self.shops)):
                _s.clear()
            self.dropped = True
            return
        if "DELETE FROM alembic_version" in q:
            self.versions = set()
            return
        if "SET bot_username=$1" in q:
            self.shops[a[1]]["bot_username"] = a[0]
            return
        if "INSERT INTO plugin_pershop_sessions" in q:
            self.sessions[(a[0], a[1], a[2])] = {"shop_id": a[0], "tg_id": a[1], "role": a[2],
                                                "state": a[3], "data": a[4]}
            return
        if "DELETE FROM plugin_pershop_sessions" in q:
            if not a:
                self.sessions.clear()
            else:
                self.sessions.pop((a[0], a[1], a[2]), None)
            return
        if "INSERT INTO plugin_pershop_subs" in q:
            nid = self._next("sub")
            self.subs[nid] = {"id": nid, "shop_id": a[0], "buyer_tg_id": a[1], "user_uuid": a[2],
                              "username": a[3], "expire_at": a[4], "traffic_limit_bytes": a[5],
                              "package_id": a[6] if len(a) > 6 else None,
                              "is_test": "is_test" in q and "TRUE" in q,
                              "is_linked": "is_linked" in q, "cancelled": False,
                              "notified_80": False, "notified_72h": False, "notified_expired": False}
            return
        if "UPDATE plugin_pershop_subs SET package_id" in q:
            self.subs[a[1]]["package_id"] = a[0]
            return
        if "UPDATE plugin_pershop_subs SET buyer_tg_id" in q:
            self.subs[a[1]]["buyer_tg_id"] = a[0]
            self.subs[a[1]]["is_linked"] = True
            return
        if "DELETE FROM plugin_pershop_referrals" in q and not a:
            self.referrals.clear()
            return
        if "plugin_pershop_referrals" in q:
            self.referrals.add((a[0], a[1], a[2]))
            return
        if "INSERT INTO plugin_pershop_scammers" in q:
            self.scammers[a[0]] = {"tg_id": a[0], "marked_by_admin": a[1], "reason": a[2]}
            return
        if "DELETE FROM plugin_pershop_scammers WHERE tg_id" in q:
            self.scammers.pop(a[0], None)
            return
        if "SET status='denied', decided_at=NOW()" in q:
            o = self.orders[a[0]]
            o["status"] = "denied"
            o["decided_at"] = datetime.now(timezone.utc)
            return
        if "SET public_key_hash=$1, public_key_enc=$2 WHERE id=$3" in q:
            self.shops[a[2]]["public_key_hash"] = a[0]
            self.shops[a[2]]["public_key_enc"] = a[1]
            return
        if "SET public_key_hash=NULL, public_key_enc=NULL WHERE id=$1" in q:
            self.shops[a[0]]["public_key_hash"] = None
            self.shops[a[0]]["public_key_enc"] = None
            return
        if "UPDATE plugin_pershop_shops SET test_counter" in q:
            self.shops[a[1]]["test_counter"] = a[0]
            return
        if "plugin_pershop_orders SET status='cancelled'" in q:
            o = self.orders[a[0]]
            o["status"] = "cancelled"
            o["decided_at"] = datetime.now(timezone.utc)
            return
        if "SET options_snapshot=$1 WHERE id=$2" in q:
            self.orders[a[1]]["options_snapshot"] = a[0]
            return
        if "SET status='withdraw_requested'" in q:
            o = self.orders[a[2]]
            o["status"] = "withdraw_requested"
            o["withdraw_reason"] = a[0]
            o["withdraw_card"] = a[1]
            return
        if "plugin_pershop_orders SET status=$1, decided_at" in q:
            o = self.orders[a[2]]
            o["status"] = a[0]
            o["decided_by"] = a[1]
            o["decided_at"] = datetime.now(timezone.utc)
            return
        if "plugin_pershop_orders SET owner_chat_id=$1" in q:
            o = self.orders[a[2]]
            o["owner_chat_id"] = a[0]
            o["owner_msg_id"] = a[1]
            return
        if "plugin_pershop_orders SET buyer_msg_id=$1" in q:
            self.orders[a[1]]["buyer_msg_id"] = a[0]
            return
        if "last_mid" in q and "plugin_pershop_buyers" in q:
            self.last_mids[(a[0], a[1])] = a[2]
            return
        if "scam_noted_at" in q and "plugin_pershop_buyers" in q:
            self.scam_noted.add((a[0], a[1]))
            return
        if "INSERT INTO plugin_pershop_buyers" in q:
            self.buyers[(a[0], a[1])] = a[2]
            return
        if "UPDATE plugin_pershop_broadcasts SET pos=$1" in q:
            b = self.bcs[a[4]]
            b["pos"], b["done"], b["failed"] = a[0], a[1], a[2]
            b["status"] = "done" if a[0] >= a[3] else "sending"
            return
        if "UPDATE plugin_pershop_broadcasts SET status=" in q:
            self.bcs[a[0]]["status"] = "sending" if "'sending'" in q else "done"
            return
        if "plugin_pershop_orders SET owner_nudged_at" in q:
            self.orders[a[0]]["owner_nudged_at"] = datetime.now(timezone.utc)
            return
        if "UPDATE plugin_pershop_orders SET renew_username" in q:
            self.orders[a[1]]["renew_username"] = a[0]
            return
        if "UPDATE plugin_pershop_shops SET bot_token_enc=NULL WHERE id=$1" in q:
            self.shops[a[0]]["bot_token_enc"] = None
            return
        if "UPDATE plugin_pershop_shops SET bot_username=$1 WHERE id=$2" in q:
            self.shops[a[1]]["bot_username"] = a[0]
            return
        if q.strip().startswith("DELETE FROM plugin_pershop_"):
            table = q.strip().split()[2]
            store = {"plugin_pershop_broadcasts": self.bcs,
                     "plugin_pershop_referrals": self.referrals,
                     "plugin_pershop_test_claims": self.test_claims,
                     "plugin_pershop_scammers": self.scammers,
                     "plugin_pershop_buyers": self.buyers,
                     "plugin_pershop_sessions": self.sessions,
                     "plugin_pershop_orders": self.orders,
                     "plugin_pershop_subs": self.subs,
                     "plugin_pershop_packages": self.pkgs,
                     "plugin_pershop_services": self.svcs,
                     "plugin_pershop_tiers": self.tiers,
                     "plugin_pershop_service_cats": self.svc_cats,
                     "plugin_pershop_shops": self.shops}.get(table)
            if store is None:
                raise AssertionError(f"unhandled wipe: {q!r}")
            store.clear()
            return
        if "UPDATE plugin_pershop_subs SET cancelled=TRUE" in q:
            self.subs[a[0]]["cancelled"] = True
            return
        if "plugin_pershop_orders SET status='approved'" in q:
            o = self.orders[a[1]]
            o["status"] = "approved"
            o["decided_at"] = datetime.now(timezone.utc)
            o["decided_by"] = a[0]
            return
        if "SET credit_units=" in q and "plugin_pershop_orders" in q:
            self.orders[a[1]]["credit_units"] = a[0]
            return
        if "plugin_pershop_orders SET status='denied'" in q:
            o = self.orders[a[2]]
            if o["status"] not in ("pending", "withdraw_requested"):
                return
            o["status"] = "denied"
            o["deny_reason"] = a[0]
            o["decided_by"] = a[1]
            o["decided_at"] = datetime.now(timezone.utc)
            return
        if "plugin_pershop_subs SET expire_at=$1" in q:
            s = self.subs[a[2]]
            s["expire_at"] = a[0]
            if "traffic_limit_bytes" in q and a[1] is not None:
                s["traffic_limit_bytes"] = a[1]
            if "notified_80=FALSE" in q:
                s["notified_80"] = False
            if "notified_72h=FALSE" in q:
                s["notified_72h"] = False
            if "notified_expired=FALSE" in q:
                s["notified_expired"] = False
            if "notified_out=FALSE" in q:
                s["notified_out"] = False
            return
        if "plugin_pershop_subs SET notified_out=TRUE" in q:
            self.subs[a[0]]["notified_out"] = True
            return
        if "plugin_pershop_subs SET notified_out=FALSE" in q:
            self.subs[a[0]]["notified_out"] = False
            return
        if "plugin_pershop_subs SET notified_expired=TRUE" in q:
            self.subs[a[0]]["notified_expired"] = True
            return
        if "plugin_pershop_subs SET notified_72h=TRUE" in q:
            self.subs[a[0]]["notified_72h"] = True
            return
        if "plugin_pershop_subs SET notified_72h=FALSE" in q:
            self.subs[a[0]]["notified_72h"] = False
            return
        if "plugin_pershop_subs SET notified_80=TRUE" in q:
            self.subs[a[0]]["notified_80"] = True
            return
        if "plugin_pershop_subs SET notified_80=FALSE" in q:
            self.subs[a[0]]["notified_80"] = False
            return
        if "plugin_pershop_subs SET expire_at=$1, notified_72h=FALSE" in q:
            s = self.subs[a[1]]
            s["expire_at"] = a[0]
            s["notified_72h"] = False
            s["notified_expired"] = False
            return
        if "UPDATE users SET expire_at" in q:
            if a[1] in self.users:
                self.users[a[1]]["expire_at"] = a[0]
            return
        if "UPDATE users SET created_by_admin_id" in q:
            u = self.users.get(a[1])
            if u:
                u["created_by_admin_id"] = a[0]
            return
        if "UPDATE plugin_pershop_subs SET expire_at=$1, traffic_limit_bytes=$2 WHERE shop_id=$3" in q:
            for s in self.subs.values():
                if s["shop_id"] == a[2] and s.get("user_uuid") == a[3]:
                    s["expire_at"] = a[0]
                    s["traffic_limit_bytes"] = a[1]
            return
        if "INSERT INTO plugin_pershop_promos" in q:
            pid = self._next("promo")
            self.promos[pid] = {
                "id": pid, "shop_id": a[0], "code": a[1], "kind": a[2],
                "value": float(a[3]), "max_uses": int(a[4] or 0),
                "valid_until": a[5], "active": True,
                "created_by_owner": a[6], "uses": 0}
            return {"id": pid}
        if "INSERT INTO plugin_pershop_promo_uses" in q:
            self.promo_uses.add((a[0], a[3]))
            return
        if "UPDATE plugin_pershop_promos SET code" in q:
            p = self.promos.get(a[6])
            if p:
                p.update(code=a[0], kind=a[1], value=float(a[2]), max_uses=int(a[3]),
                         valid_until=a[4], active=a[5])
            return
        if "DELETE FROM plugin_pershop_promos WHERE id=$1" in q:
            self.promos.pop(a[0], None)
            return
        if "UPDATE plugin_pershop_shops SET campaign_percent" in q:
            self.shops[a[1]]["campaign_percent"] = float(a[0])
            return
        if "INSERT INTO plugin_pershop_events" in q:
            self.events.append({"id": self._next("event"), "shop_id": a[0],
                                "kind": a[1], "buyer_tg_id": a[2], "detail": a[3],
                                "created_at": datetime.now(timezone.utc)})
            return []
        raise AssertionError(f"unhandled execute: {q}")


class FakeCtx:
    def __init__(self, db):
        self.db = db
        self.settings = FakeSettings()
        self.logger = FakeLogger()


class FakePanel:
    """Mirrors the real api_client: exact kwargs, {"response": ...} envelope."""

    def __init__(self, v3_numeric=False):
        self.created = []
        self.updated = []
        self.extended = []
        self.deleted = []
        self.v3_numeric = v3_numeric

    async def create_user(self, **kwargs):
        self.created.append(kwargs)
        n = len(self.created)
        user = {"id": 1000 + n, "username": kwargs["username"],
                "subscriptionUrl": "https://sub/x",
                "trafficLimitBytes": kwargs.get("traffic_limit_bytes"),
                "expireAt": kwargs.get("expire_at")}
        if not self.v3_numeric:
            user["uuid"] = f"uuid-{n}"
        return {"response": user}

    async def update_user(self, user_id, **fields):
        self.updated.append((user_id, fields))
        if "telegramId" in fields:  # mirror core persistence for link tests
            for r in getattr(self, "short_db", {}).values():
                if r.get("uuid") == user_id:
                    r["telegramId"] = fields["telegramId"]
        return {"response": {"ok": True}}

    async def extend_user_expiration(self, user_id, days):
        self.extended.append((user_id, days))
        return {"response": {"ok": True}}

    async def delete_user(self, user_id):
        self.deleted.append(user_id)
        return {"response": True}

    async def get_internal_squads(self):
        return {"response": {"internalSquads": [{"uuid": "sqT", "name": "Tier A"},
                                               {"uuid": "sq2", "name": "Other"}]}}


class FakeTg:
    instances = []
    _mid = 0

    def __init__(self, token, timeout=25.0):
        self.sent = []
        FakeTg.instances.append(self)

    @classmethod
    def _next_mid(cls):
        cls._mid += 1
        return 1000 + cls._mid

    async def call(self, method, payload):
        self.sent.append((method, payload))
        if method == "getMe":
            return {"ok": True, "result": {"username": "vipshopbot"}}
        if method == "getWebhookInfo":
            return {"ok": True, "result": {"url": "https://panel.example.com/api/v2/plugins/pershop/tg/1",
                                           "pending_update_count": 0}}
        if method == "editMessageReplyMarkup":
            return {"ok": True, "result": True}
        if method == "sendMessage":
            return {"ok": True, "result": {"message_id": self._next_mid()}}
        return {"ok": True}

    async def get_updates(self, offset, timeout_s=20):
        return []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append(("sendMessage", {"chat_id": chat_id, "text": text, "rm": reply_markup}))
        return self._next_mid()

    async def send_photo(self, chat_id, data, filename="photo.jpg", caption=None, reply_markup=None):
        self.sent.append(("sendPhoto", {"chat_id": chat_id, "bytes": len(data or b""),
                                        "caption": caption, "rm": reply_markup}))
        return self._next_mid()

    async def edit_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.sent.append(("editReplyMarkup", {"chat_id": chat_id, "message_id": message_id,
                                              "rm": reply_markup}))
        return True

    async def edit_text(self, chat_id, message_id, text, reply_markup=None):
        self.sent.append(("editMessageText", {"chat_id": chat_id, "message_id": message_id,
                                              "text": text, "rm": reply_markup}))
        return True

    async def delete_message(self, chat_id, message_id):
        self.sent.append(("deleteMessage", {"chat_id": chat_id, "message_id": message_id}))
        return True

    async def forward_message(self, to_chat, from_chat, message_id):
        self.sent.append(("forward", {"to": to_chat}))

    async def answer_callback(self, callback_id, text=""):
        pass


def make_shop(db, prefix="vip", admin_tg=999):
    sid = db._next("shop")
    db.shops[sid] = {"id": sid, "owner_admin": "admin1", "bot_token_enc": "DUMMY", "bot_username": None,
                     "admin_tg_id": admin_tg, "prefix": prefix, "card_text": "pay here",
                     "welcome_text": "", "webhook_secret": None, "lang": "en",
                     "currency_label": "تومان", "unit_rate": 250000,
                     "test_enabled": False, "test_counter": 0, "tiers_internal": False,
                     "referral_text": "", "referral_percent": 10, "support_text": ""}
    pid = db._next("pkg")
    db.pkgs[pid] = {"id": pid, "shop_id": sid, "tier_id": None, "name": "Monthly",
                    "traffic_limit_bytes": 1000, "duration_days": 30,
                    "price_text": "$5", "price_units": None, "squad_uuid": None, "active": True}
    return sid, pid


def make_stepped(db, sid):
    """Predefined package: tier (with squad) + fixed 30d/64GB + input-mode hwid."""
    tid = db._next("tier")
    db.tiers[tid] = {"id": tid, "shop_id": sid, "name": "Tier A", "squad_uuid": "sqT",
                     "hwid_default": 1, "sort": 1, "active": True}
    pid = db._next("pkg")
    db.pkgs[pid] = {"id": pid, "shop_id": sid, "tier_id": tid, "name": "Pro",
                    "traffic_limit_bytes": 64 * 1073741824, "duration_days": 30,
                    "price_text": "", "price_units": 2.5, "squad_uuid": None, "active": True,
                    "hwid_mode": "input", "hwid_min": 1, "hwid_default": 1,
                    "hwid_max": 10, "hwid_free": 1, "hwid_percent": 50,
                    "hwid_fixed": None, "sort": 0, "effect": "full", "traffic_reset": True}
    return tid, pid


def test_naming():
    assert allocate_username("vip", "alice", 1, {"vip-alice", "vip-alice2"}) == "vip-alice3"
    assert allocate_username("vip", "bob", 2, set()) == "vip-bob"
    # Owners type prefixes in any case — no lowercase forcing anymore.
    assert allocate_username("VIP", "alice", 3, {"VIP-alice"}) == "VIP-alice2"
    from rwa_plugin_pershop.naming import sanitize_fragment
    assert sanitize_fragment("My Shop") == "MyShop"
    assert traffic_ratio(80, 100) == 0.8
    assert traffic_ratio(5, 0) is None


def test_templates():
    from rwa_plugin_pershop.service import message_text

    shop = {"thank_you_text": "Thanks {missing}!", "approved_text": "", "denied_text": "No. {reason}"}
    assert message_text(shop, "thank_you") == "Receipt received. Waiting for admin review."
    assert message_text(shop, "denied", reason="late") == "No. late"
    out = message_text({"approved_text": "Hi {username} {url}"}, "approved",
                       username="vip-a", expire="tomorrow", url="https://s")
    assert out == "Hi vip-a https://s"


def test_lang():
    from rwa_plugin_pershop.lang import L, normalize_lang
    from rwa_plugin_pershop.service import message_text

    assert normalize_lang("FA") == "fa"
    assert normalize_lang("de") == "en"
    assert normalize_lang(None) == "en"
    # every locale covers every English key
    from rwa_plugin_pershop.lang import LOCALES

    for lang, table in LOCALES.items():
        for key in LOCALES["en"]:
            assert key in table, f"{lang} missing {key}"
    # spot checks: no English leaks in chrome strings
    assert "اشتراک" in L({"lang": "fa"}, "my_subs_btn")
    assert "подписки" in L({"lang": "ru"}, "my_subs_btn")
    assert "订阅" in L({"lang": "zh"}, "my_subs_btn")
    assert L({"lang": "xx"}, "cancel_again").startswith("Cancelled")
    # template defaults follow shop language; custom text still wins
    assert "رسید" in message_text({"lang": "fa"}, "thank_you")
    assert "Чек" in message_text({"lang": "ru"}, "thank_you")
    assert "凭证" in message_text({"lang": "zh"}, "thank_you")
    assert message_text({"lang": "fa", "thank_you_text": "custom!"}, "thank_you") == "custom!"


async def _approve_new():
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod

    async def fake_dec(ctx, enc):
        return "DUMMY"

    crypto_mod.decrypt_token = fake_dec
    tgmod.TgClient = FakeTg
    db, ctx = FakeDB(), None
    ctx = FakeCtx(db)
    sid, pid = make_shop(db)
    panel = FakePanel()
    svc._panel_client = lambda: panel
    db.orders[1] = {"id": 1, "shop_id": sid, "package_id": pid, "buyer_tg_id": 111,
                    "buyer_username": "alice", "receipt_file_id": "r", "receipt_kind": "text",
                    "renew_username": None, "status": "pending"}
    db.seq["order"] = 1
    res = await svc.approve_order(ctx, 1, "admin1")
    assert res["username"] == "vip-alice", res
    assert db.orders[1]["status"] == "approved"
    assert len(db.subs) == 1
    return db, ctx, svc


async def _full():
    db, ctx, svc = await _approve_new()
    # deny-after-approve must fail
    try:
        await svc.deny_order(ctx, 1, "admin1", "late")
        raise SystemExit("deny should have raised")
    except ValueError:
        pass
    # renew flow
    sub = list(db.subs.values())[0]
    db.users[sub["user_uuid"]] = {"uuid": sub["user_uuid"], "username": sub["username"],
                                  "expire_at": sub["expire_at"], "used_traffic_bytes": 850,
                                  "traffic_limit_bytes": 1000}
    db.orders[2] = {"id": 2, "shop_id": sub["shop_id"], "package_id": 1, "buyer_tg_id": 111,
                    "buyer_username": "alice", "receipt_file_id": "r2", "receipt_kind": "text",
                    "renew_username": sub["username"], "status": "pending"}
    db.seq["order"] = 2
    res = await svc.approve_order(ctx, 2, "admin1")
    assert res["renewed"] is True
    # notifier: 80% traffic flag
    from rwa_plugin_pershop.tasks import _notify_shop
    import rwa_plugin_pershop.telegram as tgmod

    tgmod.TgClient = FakeTg
    FakeTg.instances.clear()
    shop = dict(db.shops[sub["shop_id"]])
    await _notify_shop(ctx, shop)
    assert db.subs[sub["id"]]["notified_80"] is True
    assert any("80%" in str(m) or "used" in str(m) for _, m in FakeTg.instances[0].sent), FakeTg.instances[0].sent
    # out-of-traffic: the traffic twin of the expired notice (once, re-arms)
    db.users[sub["user_uuid"]]["used_traffic_bytes"] = 1000
    FakeTg.instances.clear()
    await _notify_shop(ctx, shop)
    assert db.subs[sub["id"]].get("notified_out") is True, db.subs[sub["id"]]

    def _texts():
        return [p["text"] for inst in FakeTg.instances for m, p in inst.sent if m in ("sendMessage", "editMessageText")]

    assert any("ran out of traffic" in t for t in _texts()), _texts()
    n = len(_texts())
    await _notify_shop(ctx, shop)
    assert len(_texts()) == n, "out-of-traffic notice duplicated"
    db.users[sub["user_uuid"]]["used_traffic_bytes"] = 500
    await _notify_shop(ctx, shop)
    assert db.subs[sub["id"]].get("notified_out") is False, db.subs[sub["id"]]
    # webhook buy flow
    from rwa_plugin_pershop.routes_telegram import handle_update

    FakeTg.instances.clear()
    await handle_update(ctx, shop, {"update_id": 1, "message": {"message_id": 1, "from": {"id": 111, "username": "alice"},
                                                               "chat": {"id": 111}, "text": "/start"}})
    assert FakeTg.instances, "no reply to /start"
    await handle_update(ctx, shop, {"update_id": 2, "callback_query": {"id": "c1", "from": {"id": 111},
                                                                      "data": "buy:1"}})
    await handle_update(ctx, shop, {"update_id": 20, "callback_query": {"id": "c1b", "from": {"id": 111},
                                                                      "data": "stconfirm"}})
    _prompt = [p["text"] for inst in FakeTg.instances for _, p in inst.sent
               if isinstance(p, dict) and "text" in p]
    assert any("<code>pay here</code>" in t for t in _prompt), "card not auto-wrapped as code block"
    await handle_update(ctx, shop, {"update_id": 3, "message": {"message_id": 5, "from": {"id": 111, "username": "alice"},
                                                               "chat": {"id": 111}, "text": "receipt-xyz"}})
    new_orders = [o for o in db.orders.values() if o["status"] == "pending"]
    assert new_orders, "receipt did not create order"
    assert not any(m == "deleteMessage" and p.get("message_id") == 5
                   for inst in FakeTg.instances for m, p in inst.sent), "receipt anchor deleted"
    # breadcrumb Back: new-buy receipt -> Back -> main menu, session cleared
    FakeTg.instances.clear()
    await handle_update(ctx, shop, {"update_id": 4, "callback_query": {"id": "c2", "from": {"id": 111},
                                                                       "data": "buy:1"}})
    n_before = len(db.orders)
    await handle_update(ctx, shop, {"update_id": 5, "callback_query": {"id": "c3", "from": {"id": 111},
                                                                       "data": "menu"}})
    await handle_update(ctx, shop, {"update_id": 6, "message": {"message_id": 7, "from": {"id": 111, "username": "alice"},
                                                               "chat": {"id": 111}, "text": "late-receipt"}})
    assert len(db.orders) == n_before, "text after Back created an order"
    # renew breadcrumb: subs -> detail -> picker -> receipt -> Back -> picker -> Back -> subs -> Back -> menu
    FakeTg.instances.clear()
    flow = [("my_subs", "c4"), ("sub:1", "c4b"), ("renew:1", "c5"), ("renewpkg:1:1", "c6")]
    for i, (data, cid) in enumerate(flow):
        await handle_update(ctx, shop, {"update_id": 10 + i, "callback_query": {"id": cid, "from": {"id": 111},
                                                                                "data": data}})
    await handle_update(ctx, shop, {"update_id": 13, "callback_query": {"id": "c7", "from": {"id": 111},
                                                                        "data": "back:picker:1"}})
    await handle_update(ctx, shop, {"update_id": 14, "callback_query": {"id": "c8", "from": {"id": 111},
                                                                        "data": "back:subs"}})
    await handle_update(ctx, shop, {"update_id": 15, "callback_query": {"id": "c9", "from": {"id": 111},
                                                                        "data": "menu"}})
    assert len(db.orders) == n_before, "Back navigation created an order"
    sent = [p for inst in FakeTg.instances for m, p in inst.sent if m in ("sendMessage", "editMessageText")]
    texts = [p.get("text", "") for p in sent]
    rms = [p["rm"] for p in sent if p["rm"]]
    assert any("renewpkg:" in str(rm) for rm in rms), sent
    assert any("renew:1" in str(rm) for rm in rms), sent
    # subs list shows plain username buttons now, detail shows info + renew/back/menu
    assert any("sub:1" in str(rm) for rm in rms), sent
    detail_rms = [str(rm) for rm in rms
                  if "renew:1" in str(rm) and "back:subs" in str(rm) and "'menu'" in str(rm)]
    assert detail_rms, sent
    assert any("Traffic Limit" in t and "Expire Date" in t for t in texts), texts
    assert "Buy Subscription" in str(rms[-1]), sent
    print("ALL INTEGRATION CHECKS PASSED")


def test_hwid_pricing():
    from rwa_plugin_pershop.service import format_price, hwid_cfg, hwid_extra, order_total

    shop = {"currency_label": "تومان", "unit_rate": 250000}
    assert format_price(shop, 2.5) == "625,000 تومان", format_price(shop, 2.5)
    assert format_price({"currency_label": "", "unit_rate": None}, 2.5) == "2.5 units"
    pkg = {"price_units": 2.5, "hwid_mode": "input", "hwid_min": 1, "hwid_default": 1,
           "hwid_max": 10, "hwid_free": 1, "hwid_percent": 50, "hwid_fixed": None}
    cfg = hwid_cfg(pkg)
    assert hwid_extra(2.5, cfg, 1) == 0
    assert hwid_extra(2.5, cfg, 2) == 1.25  # 1 extra x 50% x 2.5
    assert order_total(2.5, pkg, 2) == 3.75
    assert format_price(shop, 3.75) == "937,500 تومان"
    assert order_total(2.5, dict(pkg, hwid_mode="default"), 9) == 2.5  # ignored
    assert order_total(2.5, dict(pkg, hwid_mode="fixed", hwid_fixed=7), 9) == 2.5
    assert hwid_cfg({})["mode"] == "default"
    assert hwid_cfg({"hwid_mode": "bogus"})["mode"] == "default"


async def _step_flow():
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import handle_update

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    tid, tpid = make_stepped(db, sid)
    panel = FakePanel()
    svc._panel_client = lambda: panel
    shop = dict(db.shops[sid])
    FakeTg.instances.clear()

    async def cb(data, cid):
        await handle_update(ctx, shop, {"update_id": 100, "callback_query": {
            "id": cid, "from": {"id": 111}, "data": data}})

    async def txt(text):
        await handle_update(ctx, shop, {"update_id": 101, "message": {
            "message_id": 9, "from": {"id": 111, "username": "alice"},
            "chat": {"id": 111}, "text": text}})

    def last_text():
        return FakeTg.instances[-1].sent[-1][1]["text"]

    def last_rm():
        return str(FakeTg.instances[-1].sent[-1][1]["rm"])

    await txt("/start")
    await cb("pershop:buy", "t0")
    assert f"tiers:{tid}" in last_rm(), last_rm()
    await cb(f"tiers:{tid}", "t1")
    assert f"buy:{tpid}" in last_rm(), last_rm()
    assert "625,000" in last_rm(), last_rm()  # 2.5u x 250000 base price
    # Back from the tier's package list returns to the tier list, not menu
    assert "pershop:buy" in last_rm(), last_rm()
    await cb(f"buy:{tpid}", "t2")
    assert "devices" in last_text().lower(), last_text()  # hwid picker only
    # Back from the device picker returns to this tier's package list
    assert f"back:tier:{tid}" in last_rm(), last_rm()
    await cb("hplus", "t3")
    assert "2 " in last_text() or "2  " in last_text(), last_text()
    await cb("hnext", "t4")
    assert "937,500" in last_text(), last_text()  # contract with converted total
    # Back from contract returns to the tier package list
    await cb(f"back:tier:{tid}", "t5")
    assert f"buy:{tpid}" in last_rm(), last_rm()
    await cb(f"buy:{tpid}", "t6")
    assert "devices" in last_text().lower(), last_text()  # hwid picker again
    await cb("hplus", "t7")
    await cb("hnext", "t8")
    await cb("stconfirm", "t10")
    # Back from receipt returns to the contract
    await cb("back:contract", "t11")
    assert "937,500" in last_text(), last_text()
    await cb("stconfirm", "t12")
    n_before = len(db.orders)
    await txt("receipt-abc")
    assert len(db.orders) == n_before + 1
    order = list(db.orders.values())[-1]
    assert abs(float(order["total_units"]) - 3.75) < 1e-9, order
    import json as _json

    snap = _json.loads(order["options_snapshot"])
    assert snap["hwid"] == 2, snap
    assert snap["applied"]["squad_uuid"] == "sqT", snap
    assert snap["applied"]["traffic_limit_bytes"] == 64 * 1073741824, snap
    assert snap["applied"]["expire_days"] == 30, snap
    assert snap["applied"]["hwid"] == 2, snap
    res = await svc.approve_order(ctx, order["id"], "admin1")
    assert res["username"] == "vip-alice", res
    from datetime import datetime as _dt, timezone as _tz

    payload = panel.created[0]
    assert payload["hwid_device_limit"] == 2, payload
    assert payload["traffic_limit_bytes"] == 64 * 1073741824, payload
    assert payload["active_internal_squads"] == ["sqT"], payload
    assert "squad_uuid" not in payload and "created_by" not in payload, payload
    assert payload["status"] == "ACTIVE", payload
    assert payload["traffic_limit_strategy"] == "MONTH", payload
    assert payload["description"] == f"pershop:vip order #{order['id']}", payload
    delta = _dt.fromisoformat(payload["expire_at"]) - _dt.now(_tz.utc)
    assert 29 <= delta.days <= 30, payload
    print("HWID FLOW PASSED")


async def _renew_tier_flow():
    """Renew with tier change: tiers -> Tier B pkgs -> receipt -> approve moves squad sqT -> sqB."""
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import handle_update
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    tidA, pidA = make_stepped(db, sid)  # Tier A / sqT
    tidB = db._next("tier")
    db.tiers[tidB] = {"id": tidB, "shop_id": sid, "name": "Tier B", "squad_uuid": "sqB",
                      "hwid_default": 1, "sort": 2, "active": True}
    pidB = db._next("pkg")
    db.pkgs[pidB] = {"id": pidB, "shop_id": sid, "tier_id": tidB, "name": "Basic",
                     "traffic_limit_bytes": 32 * 1073741824, "duration_days": 30,
                     "price_text": "", "price_units": 1.0, "squad_uuid": None, "active": True,
                     "hwid_mode": "default", "hwid_min": 1, "hwid_default": 1,
                     "hwid_max": 1, "hwid_free": 1, "hwid_percent": 0, "hwid_fixed": None}
    exp = _dt.now(_tz.utc) + _td(days=10)
    subid = db._next("sub")
    db.subs[subid] = {"id": subid, "shop_id": sid, "buyer_tg_id": 111, "user_uuid": "uu-r",
                      "username": "vip-alice", "expire_at": exp,
                      "traffic_limit_bytes": 64 * 1073741824,
                      "is_test": False, "is_linked": False, "cancelled": False,
                      "notified_80": False, "notified_72h": False, "notified_expired": False}
    db.users["uu-r"] = {"uuid": "uu-r", "username": "vip-alice", "status": "ACTIVE",
                        "expire_at": exp, "used_traffic_bytes": 100,
                        "traffic_limit_bytes": 64 * 1073741824,
                        "activeInternalSquads": [{"uuid": "sqT", "name": "Tier A"}]}
    panel = FakePanel()
    svc._panel_client = lambda: panel
    shop = dict(db.shops[sid])
    FakeTg.instances.clear()

    async def cb(data, cid):
        await handle_update(ctx, shop, {"update_id": 200, "callback_query": {
            "id": cid, "from": {"id": 111}, "data": data}})

    async def txt(text):
        await handle_update(ctx, shop, {"update_id": 201, "message": {
            "message_id": 9, "from": {"id": 111, "username": "alice"},
            "chat": {"id": 111}, "text": text}})

    def last_text():
        return FakeTg.instances[-1].sent[-1][1]["text"]

    def last_rm():
        return str(FakeTg.instances[-1].sent[-1][1]["rm"])

    await cb("my_subs", "r0")
    assert f"sub:{subid}" in last_rm(), last_rm()
    await cb(f"sub:{subid}", "r1")
    assert "Traffic Limit" in last_text(), last_text()
    await cb(f"renew:{subid}", "r2")  # renew entry is the tier list now
    assert f"renewtier:{subid}:{tidA}" in last_rm(), last_rm()
    assert f"renewtier:{subid}:{tidB}" in last_rm(), last_rm()
    await cb(f"renewtier:{subid}:{tidB}", "r3")
    assert f"renewpkg:{subid}:{pidB}" in last_rm(), last_rm()
    assert f"renewpkg:{subid}:{pidA}" not in last_rm(), last_rm()
    await cb(f"back:renewpkgs:{subid}:{tidB}", "r3b")  # Back -> tier packages again
    assert f"renewpkg:{subid}:{pidB}" in last_rm(), last_rm()
    await cb(f"renewtier:{subid}:{tidB}", "r3c")
    await cb(f"renewpkg:{subid}:{pidB}", "r4")  # contract first: plan + new date + cap
    assert "Basic" in last_text(), last_text()
    await cb("stconfirm", "r4b")
    assert "back:contract" in last_rm(), last_rm()  # receipt Back -> contract
    n_before = len(db.orders)
    await txt("receipt-tierB")
    renew_orders = [o for o in db.orders.values()
                    if o["status"] == "pending" and o.get("renew_username") == "vip-alice"]
    assert len(db.orders) == n_before + 1 and renew_orders, db.orders
    res = await svc.approve_order(ctx, renew_orders[-1]["id"], "admin1")
    assert res["renewed"] is True and res["username"] == "vip-alice", res
    assert res["squad_changed"] is True, res
    assert ("uu-r", {"activeInternalSquads": ["sqB"]}) in panel.updated, panel.updated
    print("RENEW TIER FLOW PASSED")


def test_renew_tier_flow():
    asyncio.run(_renew_tier_flow())


async def _match_topup_flow():
    """Owner matching + reset toggle: unmapped pool -> assign -> link keeps plan ->
    qrenew receipt -> approve tops up leftover (32 left + 64 = 96GB)."""
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import handle_update
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg

    async def _fake_owner(_c, _n):
        return 42

    async def _fake_not_super(_c, _s):
        return False

    real_owner, real_super = svc._owner_account_id, svc._owner_is_superadmin
    svc._owner_account_id = _fake_owner
    svc._owner_is_superadmin = _fake_not_super
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    tid, pid = make_stepped(db, sid)
    db.pkgs[pid]["traffic_reset"] = False  # this package tops up on renew
    exp = _dt.now(_tz.utc) + _td(days=100)
    GB = 1073741824
    db.users["uu-1"] = {"uuid": "uu-1", "username": "own-one", "status": "ACTIVE",
                        "expire_at": exp, "used_traffic_bytes": 32 * GB,
                        "traffic_limit_bytes": 64 * GB, "created_by_admin_id": 42}
    db.users["uu-2"] = {"uuid": "uu-2", "username": "own-two", "status": "ACTIVE",
                        "expire_at": exp, "used_traffic_bytes": 0,
                        "traffic_limit_bytes": 10 * GB, "created_by_admin_id": 42}
    db.users["uu-9"] = {"uuid": "uu-9", "username": "other-guy", "status": "ACTIVE",
                        "expire_at": exp, "used_traffic_bytes": 0,
                        "traffic_limit_bytes": 10 * GB, "created_by_admin_id": 99}
    panel = FakePanel()
    svc._panel_client = lambda: panel
    shop = dict(db.shops[sid])

    pool = await svc.unmapped_users(ctx, shop)
    assert {o["username"] for o in pool["orphans"]} == {"own-one", "own-two"}, pool
    assert pool["unplanned"] == [], pool
    try:
        await svc.assign_plan(ctx, shop, "uu-9", pid)
        raise SystemExit("foreign assign should fail")
    except ValueError:
        pass
    try:
        await svc.assign_plan(ctx, shop, "uu-1", 9999)
        raise SystemExit("bad package should fail")
    except ValueError:
        pass
    res = await svc.assign_plan(ctx, shop, "uu-1", pid)
    assert res["created"] is True and res["username"] == "own-one", res
    res2 = await svc.assign_plan(ctx, shop, "uu-1", pid)
    assert res2["created"] is False, res2
    pool = await svc.unmapped_users(ctx, shop)
    assert [o["username"] for o in pool["orphans"]] == ["own-two"], pool
    assert pool["unplanned"] == [], pool  # own-one now has a plan

    # link attaches the buyer to the owner-premapped row, keeping the plan
    db.short_users["tok1"] = {"uuid": "uu-1", "username": "own-one", "shortUuid": "tok1",
                              "telegramId": None, "createdByAdminId": 42,
                              "expireAt": exp.isoformat(), "trafficLimitBytes": 64 * GB,
                              "activeInternalSquads": [{"uuid": "sqT"}]}
    status, _info = await svc.link_subscription(ctx, shop, "tok1", 111)
    # stricter rule: numeric owner-match no longer passes; only superadmin/empty link
    assert status == "foreign", (status, _info)
    # use a superadmin empty-creator token instead for the link test
    db.short_users["tokS"] = {"uuid": "uu-s", "username": "super-bob", "shortUuid": "tokS",
                              "telegramId": None, "createdByAdminId": "superadmin",
                              "expireAt": exp.isoformat(), "trafficLimitBytes": 64 * GB,
                              "activeInternalSquads": [{"uuid": "sqT"}]}
    svc._owner_account_row = lambda _c, _o: (True, None)
    status2, info2 = await svc.link_subscription(ctx, shop, "tokS", 222)
    assert status2 == "linked" and info2["username"] == "super-bob", (status2, info2)
    # superadmin-created token: the buyer himself can claim his premapped user
    # (numerics stay foreign, but "superadmin" creator links regardless of owner)
    db.short_users["tok1S"] = {"uuid": "uu-1", "username": "own-one", "shortUuid": "tok1S",
                               "telegramId": None, "createdByAdminId": "superadmin",
                               "expireAt": exp.isoformat(), "trafficLimitBytes": 64 * GB,
                               "activeInternalSquads": [{"uuid": "sqT"}]}
    status3, info3 = await svc.link_subscription(ctx, shop, "tok1S", 111)
    assert status3 == "linked" and info3["username"] == "own-one", (status3, info3)
    res3 = await svc.assign_plan(ctx, shop, "uu-1", pid)
    assert res3["created"] is False and res3["buyer_tg_id"] == 111, res3
    sub = [s for s in db.subs.values() if s["username"] == "own-one"][0]
    assert sub["buyer_tg_id"] == 111 and sub["package_id"] == pid, sub
    # plan the superadmin-linked user too, so only own-two stays unplanned below
    db.users["uu-s"] = {"uuid": "uu-s", "username": "super-bob", "status": "ACTIVE",
                        "expire_at": exp, "used_traffic_bytes": 0,
                        "traffic_limit_bytes": 64 * GB, "created_by_admin_id": 42}
    res4 = await svc.assign_plan(ctx, shop, "uu-s", pid)
    assert res4["created"] is False and res4["buyer_tg_id"] == 222, res4

    # overuse clamps to the fresh cap (no negative rollover)
    db.users["uu-1"]["used_traffic_bytes"] = 200 * GB
    lim, _u = await svc._renew_traffic_target(
        ctx, {"user_uuid": "uu-1"}, {"traffic_reset": False, "traffic_limit_bytes": 64 * GB},
        {"traffic_limit_bytes": 64 * GB})
    assert lim == 64 * GB, lim
    db.users["uu-1"]["used_traffic_bytes"] = 32 * GB

    FakeTg.instances.clear()

    async def cb(data, cid):
        await handle_update(ctx, shop, {"update_id": 300, "callback_query": {
            "id": cid, "from": {"id": 111}, "data": data}})

    async def txt(text):
        await handle_update(ctx, shop, {"update_id": 301, "message": {
            "message_id": 9, "from": {"id": 111, "username": "alice"},
            "chat": {"id": 111}, "text": text}})

    def last_text():
        return FakeTg.instances[-1].sent[-1][1]["text"]

    def last_rm():
        return str(FakeTg.instances[-1].sent[-1][1]["rm"])

    await cb("my_subs", "m0")
    assert f"sub:{sub['id']}" in last_rm(), last_rm()
    await cb(f"sub:{sub['id']}", "m1")
    assert "Tier A · Pro" in last_text(), last_text()
    await cb(f"qrenew:{sub['id']}", "m2")  # mapped -> contract screen first
    assert "Pro" in last_text() and "rolls over" in last_text(), last_text()
    await cb("stconfirm", "m2b")  # confirm terms -> receipt prompt
    await txt("receipt-topup")
    renew_orders = [o for o in db.orders.values()
                    if o["status"] == "pending" and o.get("renew_username") == "own-one"]
    assert len(renew_orders) == 1, db.orders
    res = await svc.approve_order(ctx, renew_orders[0]["id"], "admin1")
    assert res["renewed"] is True, res
    assert res["traffic_limit_bytes"] == 96 * GB, res  # 32 left + 64 new
    assert ("uu-1", {"trafficLimitBytes": 96 * GB}) in [
        (u, {k: v for k, v in f.items() if k == "trafficLimitBytes"}) for u, f in panel.updated
        if "trafficLimitBytes" in f], panel.updated
    assert db.subs[sub["id"]]["traffic_limit_bytes"] == 96 * GB, db.subs[sub["id"]]

    # unmapped sub: quick renew explains instead of pricing
    sub2id = db._next("sub")
    db.subs[sub2id] = {"id": sub2id, "shop_id": sid, "buyer_tg_id": 111, "user_uuid": "uu-2",
                       "username": "own-two", "expire_at": exp, "traffic_limit_bytes": 10 * GB,
                       "package_id": None, "is_test": False, "is_linked": True,
                       "notified_80": False, "notified_72h": False, "notified_expired": False}
    # trial claim subs are package-less too — they must not pollute the pool
    trialid = db._next("sub")
    db.subs[trialid] = {"id": trialid, "shop_id": sid, "buyer_tg_id": 555, "user_uuid": "uu-trial",
                        "username": "trial-one", "expire_at": exp, "traffic_limit_bytes": 1 * GB,
                        "package_id": None, "is_test": True, "is_linked": True,
                        "notified_80": False, "notified_72h": False, "notified_expired": False}
    pool = await svc.unmapped_users(ctx, shop)
    assert [s["username"] for s in pool["unplanned"]] == ["own-two"], pool
    assert all(s["username"] != "trial-one" for s in pool["unplanned"]), pool
    # package-less sub: quick renew goes straight to the change-plan chain —
    # no explainer, no owner ping.
    await cb(f"qrenew:{sub2id}", "m3")
    chain_rms = [p[1].get("rm") for inst in FakeTg.instances for p in inst.sent
                 if p[0] in ("sendMessage", "editMessageText") and p[1].get("rm")]
    assert any("renewtier:" in str(r) for r in chain_rms), chain_rms
    assert not any("exact terms" in p[1]["text"] for inst in FakeTg.instances for p in inst.sent
                   if p[0] in ("sendMessage", "editMessageText")), "explainer should be gone"
    svc._owner_account_id, svc._owner_is_superadmin = real_owner, real_super
    print("MATCH TOPUP FLOW PASSED")


async def _effects_flow():
    """Top-up effects + timeless + pending guard, end to end through the bot."""
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import _set_session, handle_update
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg

    async def _fake_owner(_c, _n):
        return 42

    async def _fake_not_super(_c, _s):
        return False

    real_owner, real_super = svc._owner_account_id, svc._owner_is_superadmin
    svc._owner_account_id = _fake_owner
    svc._owner_is_superadmin = _fake_not_super
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    tid, pid = make_stepped(db, sid)
    GB = 1073741824
    HW = {"hwid_mode": "default", "hwid_min": 1, "hwid_default": 1, "hwid_max": 10,
          "hwid_free": 1, "hwid_percent": 0, "hwid_fixed": None}
    tup = db._next("pkg")
    db.pkgs[tup] = {"id": tup, "shop_id": sid, "tier_id": tid, "name": "+50GB",
                    "traffic_limit_bytes": 50 * GB, "duration_days": 0,
                    "price_text": "", "price_units": 1.0, "squad_uuid": None,
                    "active": True, "effect": "traffic_only", "traffic_reset": True, **HW}
    day = db._next("pkg")
    db.pkgs[day] = {"id": day, "shop_id": sid, "tier_id": tid, "name": "+15 days",
                    "traffic_limit_bytes": None, "duration_days": 15,
                    "price_text": "", "price_units": 0.5, "squad_uuid": None,
                    "active": True, "effect": "time_only", "traffic_reset": True, **HW}
    life = db._next("pkg")
    db.pkgs[life] = {"id": life, "shop_id": sid, "tier_id": tid, "name": "Lifetime",
                     "traffic_limit_bytes": None, "duration_days": 0,
                     "price_text": "", "price_units": 10.0, "squad_uuid": None,
                     "active": True, "effect": "full", "traffic_reset": True, **HW}
    exp = _dt.now(_tz.utc) + _td(days=100)
    db.users["uu-1"] = {"uuid": "uu-1", "username": "own-one", "status": "ACTIVE",
                        "expire_at": exp, "used_traffic_bytes": 32 * GB,
                        "traffic_limit_bytes": 64 * GB, "created_by_admin_id": 42}
    panel = FakePanel()
    svc._panel_client = lambda: panel
    shop = dict(db.shops[sid])
    db.short_users["tok1"] = {"uuid": "uu-1", "username": "own-one", "shortUuid": "tok1",
                              "telegramId": None, "createdByAdminId": "superadmin",
                              "expireAt": exp.isoformat(), "trafficLimitBytes": 64 * GB,
                              "activeInternalSquads": [{"uuid": "sqT"}]}
    assert await svc.assign_plan(ctx, shop, "uu-1", pid)
    status, _info = await svc.link_subscription(ctx, shop, "tok1", 111)
    assert status == "linked", (status, _info)  # superadmin-created token links
    sub = [s for s in db.subs.values() if s["username"] == "own-one"][0]

    assert svc.is_timeless(_dt(2999, 6, 1, tzinfo=_tz.utc))
    assert not svc.is_timeless(exp) and not svc.is_timeless(None)
    ap = await svc.resolve_applied(ctx, db.pkgs[life], None)
    assert ap["unlimited"] is True, ap
    pv = await svc.contract_preview(ctx, shop, sub, db.pkgs[tup], None)
    assert pv["leftover_bytes"] == 32 * GB and pv["new_cap_bytes"] == 82 * GB, pv
    assert pv["new_expire_at"] == exp, pv  # top-up never moves the date

    FakeTg.instances.clear()

    async def cb(data, cid):
        await handle_update(ctx, shop, {"update_id": 400, "callback_query": {
            "id": cid, "from": {"id": 111}, "data": data}})

    async def txt(text):
        await handle_update(ctx, shop, {"update_id": 401, "message": {
            "message_id": 9, "from": {"id": 111, "username": "alice"},
            "chat": {"id": 111}, "text": text}})

    def texts():
        return [p[1]["text"] for inst in FakeTg.instances for p in inst.sent if p[0] in ("sendMessage", "editMessageText")]

    def last_text():
        return FakeTg.instances[-1].sent[-1][1]["text"]

    def last_rm():
        return str(FakeTg.instances[-1].sent[-1][1]["rm"])

    # traffic top-up: picker -> contract (date untouched, cap math) -> receipt
    await cb(f"addtraffic:{sub['id']}", "e1")
    assert "+50GB" in last_rm(), last_rm()
    await cb(f"topick:{sub['id']}:{tup}", "e2")
    assert "untouched" in last_text() and "rolls over" in last_text(), last_text()
    await cb("stconfirm", "e3")
    await txt("receipt-t1")
    pend = [o for o in db.orders.values() if o["status"] == "pending"]
    assert len(pend) == 1 and pend[0]["package_id"] == tup, db.orders

    # double receipt while pending: dropped, buyer reassured, still 1 order
    await _set_session(ctx, sid, 111, "buyer", f"await_receipt:{tup}",
                       {"package_id": tup, "renew_username": "own-one",
                        "sub_id": sub["id"], "total_units": 1.0})
    await txt("receipt-t2")
    assert len([o for o in db.orders.values() if o["status"] == "pending"]) == 1, db.orders
    assert any("usually confirms within a few hours" in t for t in texts()), texts()

    db.subs[sub["id"]]["notified_72h"] = True  # must survive a traffic-only approval
    res = await svc.approve_order(ctx, pend[0]["id"], "admin1")
    assert res["effect"] == "traffic_only" and res["renewed"] is True, res
    assert res["traffic_limit_bytes"] == 82 * GB, res
    assert res["expire_at"] == exp, res
    assert db.subs[sub["id"]]["traffic_limit_bytes"] == 82 * GB
    assert db.subs[sub["id"]]["notified_72h"] is True, db.subs[sub["id"]]
    assert ("uu-1", {"trafficLimitBytes": 82 * GB}) in [
        (u, {k: v for k, v in f.items() if k == "trafficLimitBytes"}) for u, f in panel.updated
        if "trafficLimitBytes" in f], panel.updated

    # time top-up: date moves, traffic kept
    await cb(f"adddays:{sub['id']}", "e4")
    assert "+15 days" in last_rm(), last_rm()
    await cb(f"topick:{sub['id']}:{day}", "e5")
    assert "15" in last_text() and "untouched" in last_text(), last_text()
    await cb("stconfirm", "e6")
    await txt("receipt-d1")
    pend = [o for o in db.orders.values() if o["status"] == "pending"]
    assert len(pend) == 1 and pend[0]["package_id"] == day, db.orders
    res = await svc.approve_order(ctx, pend[0]["id"], "admin1")
    assert res["effect"] == "time_only" and res["renewed"] is True, res
    assert res["traffic_limit_bytes"] == 82 * GB, res  # untouched
    assert ("uu-1", 15) in panel.extended, panel.extended

    # timeless buy: 2999 expiry, infinity row in My subs
    await cb("pershop:buy", "e7")
    await cb(f"tiers:{tid}", "e8")
    await cb(f"buy:{life}", "e9")
    assert "No expiry" in last_text(), last_text()
    await cb("stconfirm", "e10")
    await txt("receipt-life")
    pend = [o for o in db.orders.values() if o["status"] == "pending"]
    assert len(pend) == 1, db.orders
    res = await svc.approve_order(ctx, pend[0]["id"], "admin1")
    assert res["renewed"] is False and res["expire_at"].year == 2999, res
    await cb("my_subs", "e11")
    assert "♾️" in "\n".join(texts()), texts()
    svc._owner_account_id, svc._owner_is_superadmin = real_owner, real_super
    print("EFFECTS FLOW PASSED")


async def _webhook_flow():
    """Auto-webhook: plugin points the bot at itself, no manual setWebhook."""
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    db.shops[sid]["webhook_secret"] = "s3cr3t"
    shop = dict(db.shops[sid])
    await ctx.settings.set("public_base_url", "https://panel.example.com/")
    FakeTg.instances.clear()
    res = await svc.register_webhook(ctx, shop)
    assert res["ok"] is True, res
    assert res["url"] == f"https://panel.example.com/api/v2/plugins/pershop/tg/{sid}", res
    calls = [p for inst in FakeTg.instances for m, p in inst.sent if m == "setWebhook"]
    assert len(calls) == 1, calls
    assert calls[0]["url"] == res["url"] and calls[0]["secret_token"] == "s3cr3t", calls
    st = await svc.webhook_status(ctx, shop)
    assert st["ok"] is True and st["url"].endswith(f"/tg/1") and st["pending"] == 0, st
    assert db.shops[sid]["bot_username"] == "vipshopbot", db.shops[sid]
    await ctx.settings.set("public_base_url", "")
    res = await svc.register_webhook(ctx, shop)
    assert res["ok"] is False and "public URL" in res["error"], res
    await ctx.settings.set("public_base_url", "http://plain.example.com")
    res = await svc.register_webhook(ctx, shop)
    assert res["ok"] is False and "https" in res["error"], res
    print("WEBHOOK FLOW PASSED")


def test_webhook_flow():
    asyncio.run(_webhook_flow())


async def _internal_tiers_flow():
    """Internal tiers + sort order: tier buttons by default; flat sorted package
    list when the owner marks tiers internal; tracked bot-message cleanup."""
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import handle_update

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    tid, tied = make_stepped(db, sid)
    second = db._next("pkg")
    db.pkgs[second] = {"id": second, "shop_id": sid, "tier_id": tid, "name": "Second",
                       "traffic_limit_bytes": None, "duration_days": 30,
                       "price_text": "", "price_units": 1.0, "squad_uuid": None, "active": True,
                       "effect": "full", "hwid_mode": "default", "sort": -1}
    shop = db.shops[sid]
    shop.update({"referral_text": "Invite!", "bot_username": "vipshopbot"})
    FakeTg.instances.clear()

    async def cb(data, cid, uid=111, un="alice"):
        await handle_update(ctx, shop, {"update_id": 300, "callback_query": {
            "id": cid, "from": {"id": uid, "username": un}, "data": data}})

    async def txt(text, uid=111, un="alice"):
        await handle_update(ctx, shop, {"update_id": 301, "message": {
            "message_id": 9, "from": {"id": uid, "username": un},
            "chat": {"id": uid}, "text": text}})

    def rms():
        return [p["rm"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p.get("rm")]

    def texts():
        return [p["text"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText")]

    def edits():
        return [(m, p) for inst in FakeTg.instances for m, p in inst.sent if m == "editMessageText"]

    def deletes():
        return [(m, p) for inst in FakeTg.instances for m, p in inst.sent if m == "deleteMessage"]

    # default: tier buttons only, no direct package buttons
    await txt("🛒 Buy Subscription")
    flat = str(rms()[-1])
    assert f"tiers:{tid}" in flat and "buy:" not in flat, flat
    # second tap reuses the tracked message: edit, no fresh send
    def sends():
        return [p for inst in FakeTg.instances for m, p in inst.sent if m == "sendMessage"]
    sends_before = len(sends())
    await txt("🛒 Buy Subscription")
    assert len(sends()) == sends_before, "menu tap should edit, not send"
    assert edits(), "expected an editMessageText"
    assert db.last_mids.get((sid, 111)), db.last_mids
    assert any(p["message_id"] == 9 for _, p in deletes()), "panel tap not cleaned up"
    # internal tiers: flat package list sorted by sort (Second first), no tier buttons
    shop["tiers_internal"] = True
    await cb("menu", "m1")
    await txt("🛒 Buy Subscription")
    flat = str(rms()[-1])
    assert "tiers:" not in flat, flat
    assert f"buy:{second}" in flat and f"buy:{tied}" in flat, flat
    assert flat.index(f"buy:{second}") < flat.index(f"buy:{tied}"), "sort order not respected"
    # renew with internal tiers goes straight to the flat picker
    db.subs[1] = {"id": 1, "shop_id": sid, "buyer_tg_id": 111, "user_uuid": "uu-9",
                  "username": "vip-alice", "package_id": None, "expire_at": None,
                  "traffic_limit_bytes": None, "is_test": False, "is_linked": True,
                  "cancelled": False}
    await cb("renew:1", "l2")
    flat = str(rms()[-1])
    assert f"renewpkg:1:{second}" in flat and f"renewpkg:1:{tied}" in flat, flat
    assert "renewtier:" not in flat, flat
    # referral: usable t.me link, not a bare code
    await txt("👥 Referral Program")
    assert "t.me/vipshopbot?start=r_111" in texts()[-1], texts()[-1]
    assert not deletes() or all(p["message_id"] == 9 for _, p in deletes()), \
        "only the panel-tap message may be deleted here"
    print("INTERNAL TIERS FLOW PASSED")


def test_internal_tiers_flow():
    asyncio.run(_internal_tiers_flow())


def test_effects_flow():
    asyncio.run(_effects_flow())


def test_match_topup_flow():
    asyncio.run(_match_topup_flow())


def test_squad_listing():
    import rwa_plugin_pershop.service as svc

    real = svc._panel_client

    class _Bare:
        pass

    async def go():
        svc._panel_client = lambda: FakePanel()
        rows = await svc.list_squads()
        assert rows == [{"uuid": "sqT", "name": "Tier A"},
                        {"uuid": "sq2", "name": "Other"}], rows
        svc._panel_client = lambda: _Bare()
        assert await svc.list_squads() == []
        def _boom():
            raise RuntimeError("no facade")
        svc._panel_client = _boom
        assert await svc.list_squads() == []

    try:
        asyncio.run(go())
    finally:
        svc._panel_client = real
    print("SQUAD LISTING PASSED")


async def _create_v3():
    import rwa_plugin_pershop.service as svc

    real = svc._panel_client
    db = FakeDB()
    ctx = FakeCtx(db)
    panel = FakePanel(v3_numeric=True)
    svc._panel_client = lambda: panel
    try:
        res = await svc.panel_create_user(
            ctx, username="vip-bob", expire_at=datetime.now(timezone.utc) + timedelta(days=30),
            traffic_limit_bytes=10, telegram_id=222, squad_uuid="sqT",
            hwid_device_limit=1, owner_admin="ghost-admin",
            description="pershop:vip order #9")
    finally:
        svc._panel_client = real
    assert res["username"] == "vip-bob", res
    assert res["subscription_url"] == "https://sub/x", res
    mapped = await db.get_user_uuid_by_panel_id(1001)
    assert res["uuid"] == mapped, (res, mapped)
    payload = panel.created[0]
    assert payload["active_internal_squads"] == ["sqT"], payload
    assert payload["status"] == "ACTIVE" and payload["traffic_limit_strategy"] == "MONTH", payload
    print("V3 NUMERIC CREATE PASSED")


def test_create_shapes():
    asyncio.run(_create_v3())


def test_integration():
    asyncio.run(_full())


def test_step_flow():
    asyncio.run(_step_flow())


async def _menu_flow():
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import handle_update

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    await ctx.settings.set("tutorial_text", "How to use: buy, pay, enjoy.")
    await ctx.settings.set("test_duration_days", 2)
    await ctx.settings.set("test_traffic_gb", 5)
    sid, _legacy = make_shop(db)
    shop = db.shops[sid]
    shop.update({"welcome_text": "", "test_enabled": True, "bot_username": "vipshopbot",
                 "referral_text": "Invite friends, get bonus!", "support_text": "Contact @support"})
    panel = FakePanel()
    svc._panel_client = lambda: panel
    FakeTg.instances.clear()

    async def txt(text, uid=111, un="alice"):
        await handle_update(ctx, shop, {"update_id": 200, "message": {
            "message_id": 9, "from": {"id": uid, "username": un},
            "chat": {"id": uid}, "text": text}})

    async def cb(data, cid, uid=111, un="alice"):
        await handle_update(ctx, shop, {"update_id": 201, "callback_query": {
            "id": cid, "from": {"id": uid, "username": un}, "data": data}})

    def rms():
        return [p["rm"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p.get("rm")]

    def texts():
        return [p["text"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText")]

    # full seven-entry bottom panel (labels, not callbacks)
    await txt("/start")
    panel_txt = str(rms()[-1])
    assert '"keyboard"' in panel_txt or "'keyboard'" in panel_txt, panel_txt
    for label in ("Buy Subscription", "Test Subscription", "My subs / renew",
                  "Referral Program", "How to Use", "Request Support", "My account"):
        assert label in panel_txt, panel_txt
    # panel taps route like the old inline menu (incl. other languages)
    await txt("🛒 Buy Subscription")
    assert any("buy:" in str(rm) for rm in rms()), rms()
    await txt("🔁 اشتراک‌های من / تمدید")
    assert any("linksub" in str(rm) for rm in rms()), rms()
    # empty-subs keyboard is well-formed: 3 flat rows (buy / link / back)
    empty_rm = [rm for rm in rms() if rm.get("inline_keyboard")
                and any(any(b.get("callback_data") == "pershop:buy"
                            for b in row) for row in rm["inline_keyboard"])]
    assert empty_rm, rms()
    kb_rows = empty_rm[-1]["inline_keyboard"]
    assert len(kb_rows) == 3, kb_rows
    assert all(isinstance(b, dict) and b.get("text") for row in kb_rows for b in row), kb_rows
    # referral deep-link recorded
    await txt("/start r_777", uid=222, un="bob")
    assert (sid, 777, 222) in db.referrals, db.referrals
    assert any("referral" in t.lower() or "Welcome" in t for t in texts())
    # test claim via panel: terms -> creates vip-Test-1
    await txt("🎁 Test Subscription")
    assert "2" in texts()[-1] and "5" in texts()[-1], texts()[-1]
    await cb("testconfirm", "m2")
    subs = [s for s in db.subs.values() if s.get("is_test")]
    assert len(subs) == 1 and subs[0]["username"] == "vip-Test-1", db.subs
    assert panel.created[0]["username"] == "vip-Test-1", panel.created
    assert "vip-Test-1" in texts()[-1], texts()[-1]
    # second claim blocked, no new sub
    n_subs = len(db.subs)
    await cb("testconfirm", "m3")
    assert len(db.subs) == n_subs
    assert "already" in texts()[-1].lower(), texts()[-1]
    # referral / tutorial / support bodies via panel taps
    await txt("👥 Referral Program")
    assert "t.me/vipshopbot?start=r_111" in texts()[-1], texts()[-1]
    await txt("📖 How to Use")
    assert "How to use" in texts()[-1], texts()[-1]
    # tutorial URL buttons from the plugin setting ride above the Back row
    await ctx.settings.set("tutorial_buttons",
                           "Video guide | https://tutorials.example.com/x\nbad line\nDocs|javascript:alert(1)\n")
    await txt("📖 How to Use")
    rm = str(rms()[-1])
    assert '"url": "https://tutorials.example.com/x"' in rm.replace("'", '"'), rm
    assert "Video guide" in rm, rm
    assert "javascript" not in rm, rm  # non-http(s) schemes dropped
    assert "bad line" not in rm, rm
    from rwa_plugin_pershop.service import tutorial_link_rows as _tlr
    assert _tlr("A | https://a.io\nhttp ok | http://b.io\nB|not-a-url\n") == [
        [{"text": "A", "url": "https://a.io"}],
        [{"text": "http ok", "url": "http://b.io"}]]
    # two buttons side-by-side on one row via ||
    assert _tlr("Video | https://v.io || Docs | https://d.io\n") == [
        [{"text": "Video", "url": "https://v.io"},
         {"text": "Docs", "url": "https://d.io"}]]
    # a bad chunk inside a line doesn't poison the good ones
    assert _tlr("Good | https://g.io || junk\n") == [[{"text": "Good", "url": "https://g.io"}]]
    await txt("☎️ Request Support")
    assert "@support" in texts()[-1], texts()[-1]
    # disabled test + unconfigured texts hide their panel buttons
    shop.update({"test_enabled": False, "referral_text": "", "referral_percent": 0,
                 "support_text": ""})
    await ctx.settings.set("tutorial_text", "")
    FakeTg.instances.clear()
    await txt("/start")
    panel_txt = str(rms()[-1])
    assert "Buy Subscription" in panel_txt and "My subs / renew" in panel_txt, panel_txt
    assert "Test Subscription" not in panel_txt and "Referral Program" not in panel_txt, panel_txt
    assert "How to Use" not in panel_txt and "Request Support" not in panel_txt, panel_txt
    print("MENU FLOW PASSED")


def test_menu_flow():
    asyncio.run(_menu_flow())


async def _referral_cards_flow():
    """Referral self-heal (getMe when no stored username, {percent}/{link}
    filled in), welcome {shop}, and the RWA-style approved/renewed cards
    carrying plan/traffic/price/when."""
    from datetime import datetime, timedelta, timezone
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, pid = make_shop(db)
    shop = db.shops[sid]
    assert not shop.get("bot_username")
    shop["referral_text"] = "Reward {percent}%! Share: {link}"
    shop["welcome_text"] = "Welcome to {shop}!"
    from rwa_plugin_pershop.routes_telegram import handle_update

    async def txt(text, uid=111, un="alice"):
        await handle_update(ctx, shop, {"update_id": 210, "message": {
            "message_id": 19, "from": {"id": uid, "username": un},
            "chat": {"id": uid}, "text": text}})

    def texts():
        return [p["text"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText")]

    def rms():
        return [p["rm"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p.get("rm")]

    await txt("👥 Referral Program")
    assert "t.me/vipshopbot?start=r_111" in texts()[-1], texts()[-1]
    assert "10" in texts()[-1], texts()[-1]
    # the referral screen always carries a Back button to the main menu
    assert rms()[-1]["inline_keyboard"][-1][0]["callback_data"] == "menu", rms()[-1]
    assert db.shops[sid]["bot_username"] == "vipshopbot", db.shops[sid]
    # second tap uses the cached username, no new getMe needed for the link
    await txt("👥 Referral Program")
    assert "t.me/vipshopbot?start=r_111" in texts()[-1], texts()[-1]
    # welcome text fills in the shop prefix
    FakeTg.instances.clear()
    await txt("/start")
    assert "Welcome to vip!" in texts()[-1], texts()[-1]
    # approved/renewed cards carry plan, traffic, price, relative date
    exp = datetime.now(timezone.utc) + timedelta(days=30)
    order = {"package_id": pid, "total_units": 2.5,
             "buyer_tg_id": 111, "buyer_username": "alice"}
    result = {"username": "vip-alice", "expire_at": exp,
              "subscription_url": "https://sub/abc"}
    card = await svc.approval_vars(ctx, shop, order, result)
    assert card["plan"] == "Monthly", card
    assert "625,000" in card["price"], card
    bought = svc.message_text(shop, "approved", username="vip-alice",
                              **card)  # card["url"] comes from approval_vars (23cca14)
    for needle in ("vip-alice", "Monthly", "625,000", "Traffic Limit", "Subscription Link",
                   "HWID Limit", "Expire Date"):
        assert needle in bought, bought
    renewed = svc.message_text(shop, "renewed", username="vip-alice", **card)
    assert "Expire Date" in renewed and "Monthly" in renewed, renewed
    # empty owner text + reward % set: builtin default pitch, button still shows
    shop["referral_text"] = ""
    FakeTg.instances.clear()
    await txt("/start")
    assert "Referral Program" in str(rms()[-1]), str(rms()[-1])
    await txt("👥 Referral Program")
    assert "earn 10%" in texts()[-1], texts()[-1]
    assert "t.me/vipshopbot?start=r_111" in texts()[-1], texts()[-1]
    assert svc.fmt_safe("Thanks {username}!", username="alice") == "Thanks alice!"
    assert "{unknown}" in svc.fmt_safe("Hi {unknown}!", username="alice")
    print("REFERRAL CARDS FLOW PASSED")


def test_referral_cards_flow():
    asyncio.run(_referral_cards_flow())


async def _tier_squad_flow():
    import rwa_plugin_pershop.service as svc

    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    tid, tpid = make_stepped(db, sid)
    pkg = db.pkgs[tpid]

    # tier squad wins over the legacy package squad_uuid
    pkg["squad_uuid"] = "sqPKG"
    applied = await svc.resolve_applied(ctx, pkg, 3)
    assert applied["squad_uuid"] == "sqT", applied
    assert applied["hwid"] == 3, applied
    assert applied["traffic_limit_bytes"] == 64 * 1073741824, applied
    assert applied["expire_days"] == 30, applied

    # hwid mode=default: panel default (None), even though tier sets hwid_default=1
    pkg["hwid_mode"] = "default"
    applied = await svc.resolve_applied(ctx, pkg, None)
    assert applied["hwid"] == 1, applied  # tier fallback for panel-default mode
    assert applied["squad_uuid"] == "sqT", applied

    # explicit fixed mode always wins over the tier fallback
    pkg["hwid_mode"] = "fixed"
    pkg["hwid_fixed"] = 7
    applied = await svc.resolve_applied(ctx, pkg, None)
    assert applied["hwid"] == 7, applied
    print("TIER SQUAD FLOW PASSED")


def test_tier_squad_flow():
    asyncio.run(_tier_squad_flow())


async def _link_flow():
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import handle_update

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    make_stepped(db, sid)  # tier with squad_uuid=sqT
    shop = db.shops[sid]
    panel = FakePanel()
    panel.short_db = db.short_users
    real_client, real_owner = svc._panel_client, svc._owner_account_id
    real_row = svc._owner_account_row
    svc._panel_client = lambda: panel

    async def fake_owner(_c, _o):
        return 42

    svc._owner_account_id = fake_owner
    try:
        db.short_users["tokA"] = {"uuid": "uu-a", "username": "old-bob", "shortUuid": "tokA",
                                  "telegramId": None, "createdByAdminId": 42,
                                  "expireAt": "2026-12-01T00:00:00+00:00",
                                  "trafficLimitBytes": None,
                                  "activeInternalSquads": [{"uuid": "sqT", "name": "Tier A"}]}
        db.short_users["tokB"] = {"uuid": "uu-b", "username": "taken-joe", "shortUuid": "tokB",
                                  "telegramId": 555, "createdByAdminId": 42,
                                  "expireAt": "2026-12-01T00:00:00+00:00",
                                  "trafficLimitBytes": None, "activeInternalSquads": []}
        # taken is now a bot-level state: buyer 555 already owns uu-b's sub row
        taken_id = db._next("sub")
        db.subs[taken_id] = {"id": taken_id, "shop_id": sid, "buyer_tg_id": 555,
                             "user_uuid": "uu-b", "username": "taken-joe",
                             "expire_at": None, "traffic_limit_bytes": None,
                             "package_id": None, "is_test": False, "is_linked": True,
                             "notified_80": False, "notified_72h": False,
                             "notified_expired": False}
        db.short_users["tokC"] = {"uuid": "uu-c", "username": "foreign-ann", "shortUuid": "tokC",
                                  "telegramId": None, "createdByAdminId": 777,
                                  "expireAt": "2026-12-01T00:00:00+00:00",
                                  "trafficLimitBytes": None, "activeInternalSquads": []}
        db.short_users["tokD"] = {"uuid": "uu-d", "username": "squad-kim", "shortUuid": "tokD",
                                  "telegramId": None, "createdByAdminId": 777,
                                  "expireAt": "2026-12-01T00:00:00+00:00",
                                  "trafficLimitBytes": None,
                                  "activeInternalSquads": [{"uuid": "sqt", "name": "Tier A"}]}

        # service-level: only superadmin ("superadmin") or empty creator link;
        # matching numeric owner no longer passes (stricter rule).
        st, info = await svc.link_subscription(ctx, shop, "tokA", 111)
        assert st == "foreign", (st, info)  # numeric creator (42) != superadmin/empty
        st, _ = await svc.link_subscription(ctx, shop, "tokD", 112)
        assert st == "foreign", st  # different creator: shop squad no longer helps
        st, _ = await svc.link_subscription(ctx, shop, "tokB", 111)
        assert st == "taken", st
        st, _ = await svc.link_subscription(ctx, shop, "tokC", 111)
        assert st == "foreign", st
        st, _ = await svc.link_subscription(ctx, shop, "nope", 111)
        assert st == "not_found", st
        # unattributed user (superadmin-created, no squads): sub-admin owner -> foreign
        db.short_users["tokF"] = {"uuid": "uu-f", "username": "legacy-max", "shortUuid": "tokF",
                                  "telegramId": None,
                                  "expireAt": "2026-12-01T00:00:00+00:00",
                                  "trafficLimitBytes": None, "activeInternalSquads": []}

        async def fake_row_sub(_c, _o):
            return True, {"id": 42, "role_name": "admin"}

        svc._owner_account_row = fake_row_sub
        st, _ = await svc.link_subscription(ctx, shop, "tokF", 113)
        assert st == "foreign", st
        # same user, row-less superadmin owner -> allowed (both sides unattributed)
        async def fake_row_god(_c, _o):
            return True, None

        svc._owner_account_row = fake_row_god
        st, info = await svc.link_subscription(ctx, shop, "tokF", 113)
        assert st == "linked" and info["username"] == "legacy-max", (st, info)
        assert panel.updated[-1] == ("uu-f", {"telegramId": 113}), panel.updated
        linked = [s for s in db.subs.values() if s.get("is_linked")]
        assert len(linked) == 2, db.subs

        # bot-level: my_subs shows link button, prompt, paste link, success
        FakeTg.instances.clear()

        async def txt(text, uid=111, un="alice"):
            await handle_update(ctx, shop, {"update_id": 300, "message": {
                "message_id": 9, "from": {"id": uid, "username": un},
                "chat": {"id": uid}, "text": text}})

        async def cb(data, cid, uid=111, un="alice"):
            await handle_update(ctx, shop, {"update_id": 301, "callback_query": {
                "id": cid, "from": {"id": uid, "username": un}, "data": data}})

        def texts(uid=333):
            return [p["text"] for inst in FakeTg.instances for m, p in inst.sent
                    if m in ("sendMessage", "editMessageText") and p.get("chat_id") == uid]

        await cb("my_subs", "l0", uid=333, un="cara")
        assert "linksub" in str(FakeTg.instances[-1].sent), FakeTg.instances[-1].sent
        await cb("linksub", "l1", uid=333, un="cara")
        assert "subscription link" in texts()[-1], texts()[-1]
        db.short_users["tokE"] = {"uuid": "uu-e", "username": "cara-sub", "shortUuid": "tokE",
                                  "telegramId": None, "createdByAdminId": None,
                                  "expireAt": "2026-12-02T00:00:00+00:00",
                                  "trafficLimitBytes": None, "activeInternalSquads": []}
        # tokE is an unattributed (empty-creator) token: links as-is, no owner ping;
        # "foreign" display is covered by tokC below.
        await txt("https://sub.example.com/s/tokE", uid=333, un="cara")
        assert "linked" in texts()[-2].lower(), texts()[-2:]
        # linked as-is: NO owner ping about plan matching anymore
        assert not any(p.get("chat_id") != 333
                       for inst in FakeTg.instances for m, p in inst.sent
                       if m == "sendMessage"), "owner ping should be gone"
        assert panel.updated[-1] == ("uu-e", {"telegramId": 333}), panel.updated
        # same link again -> already yours
        await cb("linksub", "l2", uid=333, un="cara")
        await txt("tokE", uid=333, un="cara")
        assert "already" in texts()[-2].lower(), texts()[-2:]
        # taken + foreign + unknown surface their errors, session stays for retry
        await cb("linksub", "l3", uid=333, un="cara")
        await txt("tokB", uid=333, un="cara")
        assert "another Telegram account" in texts()[-1], texts()[-1]
        await txt("tokC", uid=333, un="cara")
        assert "belong" in texts()[-1], texts()[-1]
        await txt("https://sub.example.com/s/zzz", uid=333, un="cara")
        assert "No subscription found" in texts()[-1], texts()[-1]
    finally:
        svc._panel_client = real_client
        svc._owner_account_id = real_owner
        svc._owner_account_row = real_row
    print("LINK FLOW PASSED")


def test_link_flow():
    asyncio.run(_link_flow())


def test_relatives():
    from datetime import timedelta

    from rwa_plugin_pershop.service import format_when, rel_words

    now = datetime(2027, 1, 1, tzinfo=timezone.utc)
    en, fa = {"lang": "en"}, {"lang": "fa"}
    assert rel_words(en, now + timedelta(days=5), now) == "in 5 days"
    assert rel_words(en, now + timedelta(days=1), now) == "tomorrow"
    assert rel_words(en, now + timedelta(hours=7), now) == "in 7 hours"
    assert rel_words(en, now - timedelta(days=3), now) == "3 days ago"
    assert rel_words(en, now - timedelta(days=1), now) == "yesterday"
    assert rel_words(fa, now + timedelta(days=5), now) == "5 روز دیگر"
    assert rel_words(fa, now - timedelta(hours=2), now) == "2 ساعت پیش"
    assert format_when(en, now + timedelta(days=5), now) == "in 5 days (2027-01-06)"
    assert format_when(en, datetime(2999, 12, 31)) == "No expiry"
    assert format_when(en, None) != ""
    print("RELATIVES PASSED")


async def _account_cancel_flow():
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import handle_update

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    shop = db.shops[sid]
    pid = db._next("pkg")
    db.pkgs[pid] = {"id": pid, "shop_id": sid, "tier_id": None, "name": "Pro",
                     "traffic_limit_bytes": 64 * 1073741824, "duration_days": 30,
                     "price_units": 2.5, "price_text": "", "effect": "full", "active": True,
                     "hwid_mode": "default"}
    future = datetime.now(timezone.utc) + timedelta(days=30)
    db.subs[1] = {"id": 1, "shop_id": sid, "buyer_tg_id": 111, "user_uuid": "uu-1",
                  "username": "vip-alice", "expire_at": future,
                  "traffic_limit_bytes": 64 * 1073741824, "package_id": pid,
                  "is_test": False, "is_linked": False, "cancelled": False,
                  "notified_80": False, "notified_72h": False,
                  "notified_expired": False, "notified_out": False}
    db.users["uu-1"] = {"uuid": "uu-1", "username": "vip-alice", "status": "ACTIVE",
                        "expire_at": future, "traffic_limit_bytes": 64 * 1073741824,
                        "used_traffic_bytes": 0}
    ins = ("INSERT INTO plugin_pershop_orders(shop_id,package_id,buyer_tg_id,buyer_username,"
           "receipt_file_id,receipt_kind,renew_username,total_units,options_snapshot)"
           " VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id")
    r1 = await db.fetchrow(ins, sid, pid, 111, "alice", "f1", "photo", "vip-alice", 2.5, "{}")
    r2 = await db.fetchrow(ins, sid, pid, 111, "alice", "f2", "photo", "vip-alice", 2.5, "{}")
    db.orders[r1["id"]]["status"] = "approved"
    pend = r2["id"]
    db.orders[pend]["owner_chat_id"] = 999
    db.orders[pend]["owner_msg_id"] = 555
    FakeTg.instances.clear()

    async def cb(data, cid, uid=111, un="alice"):
        await handle_update(ctx, shop, {"update_id": 300, "callback_query": {
            "id": cid, "from": {"id": uid, "username": un}, "data": data}})

    async def msg(text, uid=111, un="alice"):
        await handle_update(ctx, shop, {"update_id": 301, "message": {
            "message_id": 7, "from": {"id": uid, "username": un},
            "chat": {"id": uid}, "text": text}})

    def mine(uid=111):
        return [p["text"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p["chat_id"] == uid]

    def owner_msgs():
        return [(p["text"], p.get("rm")) for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p["chat_id"] == 999]

    def flat_rm(rm):
        return str(rm or "")

    # purchase history: both orders, both statuses
    await cb("account", "a1")
    await cb("acc:history", "a1b")
    hist = mine()[-1]
    assert "Pro" in hist and "waiting for admin" in hist and "approved" in hist, hist
    # step 1: reason prompt carries the no-pullback caveat
    await cb(f"cancelorder:{pend}", "a2")
    assert "can't be pulled back" in mine()[-1], mine()[-1]
    # step 2: reason -> card prompt
    await msg("changed my mind, found cheaper")
    assert "refund" in mine()[-1].lower() or "card" in mine()[-1].lower(), mine()[-1]
    # submit card -> order marked, old buttons stripped, owner gets confirm/keep
    await msg("IR-1234-5678")
    o = db.orders[pend]
    assert o["status"] == "withdraw_requested", o
    assert o["withdraw_reason"] == "changed my mind, found cheaper", o
    assert o["withdraw_card"] == "IR-1234-5678", o
    assert any("withdraw request sent" in t.lower() for t in mine()[-2:]), mine()[-2:]
    card_edits = [p for inst in FakeTg.instances for m, p in inst.sent
                  if m == "editMessageText" and p.get("message_id") == 555]
    assert card_edits and "changed my mind" in card_edits[-1]["text"], card_edits
    wd = [t for t, rm in owner_msgs() if f"wdprocess:{pend}" in flat_rm(rm)]
    assert wd and "changed my mind" in wd[-1] and "IR-1234-5678" in wd[-1], owner_msgs()
    assert not any("wdconfirm:" in flat_rm(rm) or "wdreject:" in flat_rm(rm)
                   for _, rm in owner_msgs()), "no keep option may exist"
    # approve while withdraw-requested is impossible: order untouched
    n_before = len(mine())
    await cb(f"approve:{pend}", "a3", uid=999)
    assert db.orders[pend]["status"] == "withdraw_requested"
    assert len(mine()) == n_before, "no delivery must go out for a withdrawn order"
    # owner processes: exactly two buttons, approve or deny-as-fake
    await cb(f"wdprocess:{pend}", "a4", uid=999)
    proc = [rm for t, rm in owner_msgs() if f"wdapprove:{pend}" in flat_rm(rm)]
    assert proc and f"wddeny:{pend}" in flat_rm(proc[-1]), owner_msgs()
    # approve path: bank info shown, nothing settles before the receipt
    await cb(f"wdapprove:{pend}", "a5", uid=999)
    assert "IR-1234-5678" in owner_msgs()[-1][0], owner_msgs()
    assert db.orders[pend]["status"] == "withdraw_requested"
    # owner sends the refund receipt -> forwarded to buyer, order withdrawn
    await msg("REFUND-768-ABC", uid=999, un="owner")
    assert db.orders[pend]["status"] == "withdrawn"
    fwds = [p for inst in FakeTg.instances for m, p in inst.sent
            if m == "forward" and p.get("to") == 111]
    assert fwds, "refund receipt must reach the buyer"
    assert "refund receipt" in mine()[-2].lower(), mine()[-2:]
    assert "refund" in mine()[-1].lower(), mine()[-1]
    assert any("withdraw #2 confirmed" in t.lower() for t, _ in owner_msgs()), owner_msgs()
    # repeat tap after decision: stale notice, status stands
    await cb(f"wdprocess:{pend}", "a6", uid=999)
    assert db.orders[pend]["status"] == "withdrawn"
    assert "already handled" in owner_msgs()[-1][0], owner_msgs()
    # second order: deny as fake -> reason -> denied + scam offer (never pre-deny)
    r3 = await db.fetchrow(ins, sid, pid, 111, "alice", "f3", "photo", "vip-alice", 2.5, "{}")
    p3 = r3["id"]
    await cb(f"cancelorder:{p3}", "a7")
    await msg("oops wrong plan")
    await msg("IR-0000")
    assert db.orders[p3]["status"] == "withdraw_requested"
    await cb(f"wdprocess:{p3}", "a8", uid=999)
    await cb(f"wddeny:{p3}", "a9", uid=999)
    assert "reason" in owner_msgs()[-1][0].lower(), owner_msgs()
    await msg("fake receipt, amount does not match", uid=999, un="owner")
    assert db.orders[p3]["status"] == "denied", db.orders[p3]
    offer = [rm for t, rm in owner_msgs() if f"scam:{p3}" in flat_rm(rm)]
    assert offer, "scam report must only appear after the deny"
    await cb(f"scam:{p3}", "a10", uid=999)
    assert 111 in db.scammers, db.scammers
    assert any("marked as a scammer" in t for t in mine()), mine()
    print("ACCOUNT WITHDRAW FLOW PASSED")


def test_account_cancel_flow():
    asyncio.run(_account_cancel_flow())


async def _mixed_withdraw_flow():
    import json as _json
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.telegram as tgmod
    import rwa_plugin_pershop.service as svc
    from rwa_plugin_pershop.routes_telegram import handle_update

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    shop = db.shops[sid]
    pid = db._next("pkg")
    db.pkgs[pid] = {"id": pid, "shop_id": sid, "tier_id": None, "name": "Pro",
                     "traffic_limit_bytes": 64 * 1073741824, "duration_days": 30,
                     "price_units": 3.0, "price_text": "", "effect": "full", "active": True,
                     "hwid_mode": "default"}
    future = datetime.now(timezone.utc) + timedelta(days=30)
    db.subs[1] = {"id": 1, "shop_id": sid, "buyer_tg_id": 111, "user_uuid": "uu-1",
                  "username": "vip-alice", "expire_at": future,
                  "traffic_limit_bytes": 64 * 1073741824, "package_id": pid,
                  "is_test": False, "is_linked": False, "cancelled": False,
                  "notified_80": False, "notified_72h": False,
                  "notified_expired": False, "notified_out": False}
    db.users["uu-1"] = {"uuid": "uu-1", "username": "vip-alice", "status": "ACTIVE",
                        "expire_at": future, "traffic_limit_bytes": 64 * 1073741824,
                        "used_traffic_bytes": 0}
    db.credits[(sid, 111)] = 0.0
    ins = ("INSERT INTO plugin_pershop_orders(shop_id,package_id,buyer_tg_id,buyer_username,"
           "receipt_file_id,receipt_kind,renew_username,total_units,options_snapshot)"
           " VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id")
    FakeTg.instances.clear()

    async def cb(data, cid, uid=111, un="alice"):
        await handle_update(ctx, shop, {"update_id": 400, "callback_query": {
            "id": cid, "from": {"id": uid, "username": un}, "data": data}})

    async def msg(text, uid=111, un="alice"):
        await handle_update(ctx, shop, {"update_id": 401, "message": {
            "message_id": 7, "from": {"id": uid, "username": un},
            "chat": {"id": uid}, "text": text}})

    def mine(uid=111):
        return [p["text"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p["chat_id"] == uid]

    def owner_msgs():
        return [(p["text"], p.get("rm")) for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p["chat_id"] == 999]

    def flat_rm(rm):
        return str(rm or "")

    async def withdraw(oid, tag, reason="wrong amount sent", card="IR-9999"):
        await cb(f"cancelorder:{oid}", f"{tag}1")
        assert "Step 1 of 2" in mine()[-1], mine()[-1]
        await msg(reason)
        await msg(card)

    # 3.0u order, 1.0u paid with credit: submit refunds the credit instantly
    r1 = await db.fetchrow(ins, sid, pid, 111, "alice", "f1", "photo",
                           "vip-alice", 3.0, _json.dumps({"credit_used": 1.0}))
    m1 = r1["id"]
    db.orders[m1]["owner_chat_id"] = 999
    db.orders[m1]["owner_msg_id"] = 555
    await withdraw(m1, "m1")
    o = db.orders[m1]
    assert o["status"] == "withdraw_requested", o
    snap = _json.loads(o["options_snapshot"])
    assert snap.get("credit_prefunded") is True and snap.get("credit_used") == 1.0, snap
    assert await svc.credit_balance(ctx, sid, 111) == 1.0
    assert any("250,000" in t and "no need to contact" in t for t in mine()[-2:]), mine()[-2:]
    wd = [t for t, rm in owner_msgs() if f"wdprocess:{m1}" in flat_rm(rm)]
    assert wd and "already returned" in wd[-1] and "250,000" in wd[-1], owner_msgs()
    # process -> approve: bank info shown, then the refund receipt settles it
    await cb(f"wdprocess:{m1}", "m1p", uid=999)
    await cb(f"wdapprove:{m1}", "m1a", uid=999)
    assert "IR-9999" in owner_msgs()[-1][0], owner_msgs()
    assert db.orders[m1]["status"] == "withdraw_requested"
    assert await svc.credit_balance(ctx, sid, 111) == 1.0
    await msg("BANK-REF-001", uid=999, un="owner")
    assert db.orders[m1]["status"] == "withdrawn"
    assert await svc.credit_balance(ctx, sid, 111) == 1.0, "settle must not refund credit twice"
    assert sum("250,000" in t for t in mine()) == 1, mine()
    # no keep path anymore: deny-as-fake settles the second order as denied
    r2 = await db.fetchrow(ins, sid, pid, 111, "alice", "f2", "photo",
                           "vip-alice", 3.0, _json.dumps({"credit_used": 0.5}))
    m2 = r2["id"]
    await withdraw(m2, "m2")
    assert await svc.credit_balance(ctx, sid, 111) == 1.5
    await cb(f"wdprocess:{m2}", "m2p", uid=999)
    await cb(f"wddeny:{m2}", "m2d", uid=999)
    await msg("amount does not match the receipt", uid=999, un="owner")
    assert db.orders[m2]["status"] == "denied", db.orders[m2]
    assert await svc.credit_balance(ctx, sid, 111) == 1.5, "deny must not refund prefunded credit"
    assert any(f"scam:{m2}" in flat_rm(rm) for _, rm in owner_msgs()), "scam offer follows the deny"
    # approved orders cannot be cancelled at all
    r3 = await db.fetchrow(ins, sid, pid, 111, "alice", "f3", "photo",
                           "vip-alice", 3.0, _json.dumps({"credit_used": 1.0}))
    m3 = r3["id"]
    db.orders[m3]["status"] = "approved"
    await cb(f"cancelorder:{m3}", "m4")
    assert any("no longer possible" in t for t in mine()[-2:]), mine()[-2:]
    print("MIXED WITHDRAW FLOW PASSED")


def test_mixed_withdraw_flow():
    asyncio.run(_mixed_withdraw_flow())


async def _buyer_lang_flow():
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import handle_update

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    shop = db.shops[sid]
    FakeTg.instances.clear()

    async def cb(data, cid, uid=111, un="alice"):
        await handle_update(ctx, shop, {"update_id": 310, "callback_query": {
            "id": cid, "from": {"id": uid, "username": un}, "data": data}})

    def rms(uid=111):
        return [p.get("rm") for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p["chat_id"] == uid]

    def flat(rm):
        rows = (rm or {}).get("inline_keyboard", [])
        return [b.get("callback_data") for r in rows for b in (r or [])]

    await cb("pershop:buy", "l0")
    # account is the last main-menu panel button
    await cb("menu", "l0b")
    menu_en = rms()[-1]
    keys = [b.get("text") for r in (menu_en or {}).get("keyboard", []) for b in (r or [])]
    assert keys and keys[-1] == "👤 My account", menu_en
    await cb("account", "l1")
    assert "🇬🇧 en" in str(rms()[-1]), rms()[-1]
    await cb("langmenu", "l2")
    assert "🇬🇧 English ✅" in str(rms()[-1]), rms()[-1]
    await cb("langpick:fa", "l3")
    assert db.buyers.get((sid, 111)) == "fa", db.buyers
    assert "🇮🇷 fa" in str(rms()[-1]), rms()[-1]
    await cb("menu", "l4")
    menu_fa = rms()[-1]
    assert menu_fa != menu_en, "buyer language must change the menu"
    print("BUYER LANG FLOW PASSED")


async def _account_hub_flow():
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import handle_update

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    _tid, pid = make_stepped(db, sid)
    shop = db.shops[sid]
    db.credits[(sid, 111)] = 5.0
    now = datetime.now(timezone.utc)

    def _order(buyer, uname, units, earned, status="approved"):
        oid = db._next("order")
        db.orders[oid] = {"id": oid, "shop_id": sid, "package_id": pid, "service_id": None,
                          "buyer_tg_id": buyer, "buyer_username": uname,
                          "receipt_file_id": "f1", "receipt_kind": "photo",
                          "renew_username": None, "status": status, "deny_reason": None,
                          "withdraw_reason": None, "withdraw_card": None,
                          "owner_chat_id": None, "owner_msg_id": None,
                          "total_units": units, "options_snapshot": None,
                          "credit_units": earned, "created_at": now,
                          "decided_at": None, "decided_by": None,
                          "owner_nudged_at": None}
        return oid

    _order(111, "alice", 2.5, 0)
    _order(222, "carol", 2.5, 0.25)
    db.referrals.add((sid, 111, 222))
    db.referrals.add((sid, 111, 333))
    FakeTg.instances.clear()

    async def cb(data, cid, uid=111, un="alice"):
        await handle_update(ctx, shop, {"update_id": 320, "callback_query": {
            "id": cid, "from": {"id": uid, "username": un}, "data": data}})

    def rms(uid=111):
        return [p.get("rm") for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p["chat_id"] == uid]

    def texts(uid=111):
        return [p.get("text", "") for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p["chat_id"] == uid]

    def btns(rm):
        return [b.get("text") for r in (rm or {}).get("inline_keyboard", []) for b in (r or [])]

    await cb("account", "h1")
    hub = btns(rms()[-1])
    assert hub[0] == "💰 Credit Balance (1,250,000 تومان)", hub
    assert hub[1] == "🧾 Purchase History [1 item(s)]", hub
    assert hub[2] == "🌐 Language (🇬🇧 en)", hub

    await cb("acc:history", "h2")
    assert "Pro" in texts()[-1] and "approved" in texts()[-1], texts()[-1]

    await cb("acc:balance", "h3")
    assert "@carol" in texts()[-1], texts()[-1]
    assert "62,500" in texts()[-1], texts()[-1]
    assert "333" not in texts()[-1], texts()[-1]

    await cb("acc:balance", "h4", uid=333, un="dave")
    assert "No invitee" in texts(333)[-1], texts(333)
    print("ACCOUNT HUB FLOW PASSED")


def test_account_hub_flow():
    asyncio.run(_account_hub_flow())


def test_buyer_lang_flow():
    asyncio.run(_buyer_lang_flow())


async def _broadcast_flow():
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.service import queue_broadcast
    from rwa_plugin_pershop.tasks import broadcast_tick

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    future = datetime.now(timezone.utc) + timedelta(days=10)
    db.subs[1] = {"id": 1, "shop_id": sid, "buyer_tg_id": 111, "user_uuid": "uu-1",
                  "username": "vip-alice", "expire_at": future,
                  "traffic_limit_bytes": None, "package_id": None,
                  "is_test": False, "is_linked": False, "cancelled": False,
                  "notified_80": False, "notified_72h": False,
                  "notified_expired": False, "notified_out": False}
    ins = ("INSERT INTO plugin_pershop_orders(shop_id,package_id,buyer_tg_id,buyer_username,"
           "receipt_file_id,receipt_kind,renew_username,total_units,options_snapshot)"
           " VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id")
    await db.fetchrow(ins, sid, 1, 222, "bob", "f", "photo", None, 1.0, "{}")
    db.referrals.add((sid, 111, 333))
    db.buyers[(sid, 111)] = "fa"
    res = await queue_broadcast(ctx, sid, "<b>Hi all</b>", b"PHOTO")
    assert res["recipients"] == 3, res
    FakeTg.instances.clear()
    await broadcast_tick(ctx)
    got = {}
    for inst in FakeTg.instances:
        for m, p in inst.sent:
            if m == "sendPhoto":
                got.setdefault(p["chat_id"], []).append(("photo", p["caption"]))
    assert set(got) == {111, 222, 333}, got
    assert all(c == "<b>Hi all</b>" for v in got.values() for _, c in v), got
    bc = db.bcs[res["id"]]
    assert bc["status"] == "done" and bc["done"] == 3 and bc["failed"] == 0, bc

    # --- broadcast segments narrow the audience (Bedolaga-style) ---
    r_sub = await queue_broadcast(ctx, sid, "s", None, "subs")
    assert r_sub["recipients"] == 1, r_sub  # buyer 111 has the sub row
    r_inv = await queue_broadcast(ctx, sid, "i", None, "invited")
    assert r_inv["recipients"] == 1, r_inv  # buyer 333 was invited
    r_buy = await queue_broadcast(ctx, sid, "b", None, "buyers")
    assert r_buy["recipients"] == 1, r_buy  # only 111 chose a language
    r_no = await queue_broadcast(ctx, sid, "n", None, "noorders")
    assert r_no["recipients"] == 1, r_no  # nobody has an approved order yet
    import rwa_plugin_pershop.service as svc_mod
    svc_mod._panel_client = lambda: FakePanel()
    db.subs[1]["expire_at"] = datetime.now(timezone.utc) - timedelta(days=3)
    r_exp = await queue_broadcast(ctx, sid, "e", None, "expired")
    assert r_exp["recipients"] == 1, r_exp
    db.subs[1]["expire_at"] = future

    # --- health probes ---
    from rwa_plugin_pershop.service import shop_health, list_events
    h = await shop_health(ctx, {"id": sid, "bot_token_enc": "DUMMY"})
    assert h["bot_ok"] is True, h
    assert h["pending"] == 1, h  # the one pending receipt order
    assert h["pending_old"] == 0, h

    # --- activity feed rows (events) ---
    evs = await list_events(ctx, sid)
    kinds = [e["kind"] for e in evs]
    assert kinds.count("broadcast") >= 6, kinds
    # newest first
    assert evs == sorted(evs, key=lambda e: e["id"], reverse=True), evs
    print("BROADCAST FLOW PASSED")


def test_broadcast_flow():
    asyncio.run(_broadcast_flow())


async def _nag_flow():
    from datetime import timedelta

    from rwa_plugin_pershop.tasks import _nag_pending

    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    shop = db.shops[sid]
    shop.update({"pending_nag_hours": 1, "admin_tg_id": 999})
    ins = ("INSERT INTO plugin_pershop_orders(shop_id,package_id,buyer_tg_id,buyer_username,"
           "receipt_file_id,receipt_kind,renew_username,total_units,options_snapshot)"
           " VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id")
    r = await db.fetchrow(ins, sid, 7, 111, "alice", "f9", "photo", "vip-alice", 1.0, "{}")
    oid = r["id"]
    db.orders[oid]["created_at"] = datetime.now(timezone.utc) - timedelta(hours=2)
    tg = FakeTg("DUMMY")
    await _nag_pending(ctx, tg, shop, datetime.now(timezone.utc))
    assert len(tg.sent) == 1, tg.sent
    assert tg.sent[0][1]["chat_id"] == 999 and "waiting" in tg.sent[0][1]["text"], tg.sent
    # nudged: same window stays silent
    await _nag_pending(ctx, tg, shop, datetime.now(timezone.utc))
    assert len(tg.sent) == 1, tg.sent
    # off switch: new stale order, nag disabled
    shop["pending_nag_hours"] = 0
    r2 = await db.fetchrow(ins, sid, 7, 111, "alice", "f10", "photo", "vip-alice", 1.0, "{}")
    db.orders[r2["id"]]["created_at"] = datetime.now(timezone.utc) - timedelta(hours=9)
    await _nag_pending(ctx, tg, shop, datetime.now(timezone.utc))
    assert len(tg.sent) == 1, tg.sent
    print("NAG FLOW PASSED")


def test_nag_flow():
    asyncio.run(_nag_flow())


async def _followup6_flow():
    """Tasks 1-6: cancelled subs silent + summary nag; superadmin test config;
    test subs never renew; buyer lookup rows + unmark; mixed-payment note."""
    import json
    from datetime import timedelta

    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import handle_update
    from rwa_plugin_pershop.tasks import _nag_pending, _notify_sub

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    tid, pid = make_stepped(db, sid)
    shop = db.shops[sid]
    panel = FakePanel()
    svc._panel_client = lambda: panel
    FakeTg.instances.clear()

    async def cb(data, cid, uid=111, un="alice"):
        await handle_update(ctx, shop, {"update_id": 320, "callback_query": {
            "id": cid, "from": {"id": uid, "username": un}, "data": data}})

    def texts(uid=111):
        return [p["text"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p.get("chat_id") == uid]

    def rms(uid=111):
        return [p.get("rm") for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p["chat_id"] == uid]

    def flat(rm):
        rows = (rm or {}).get("inline_keyboard", [])
        return [b.get("callback_data") for r in rows for b in (r or [])]

    # --- task 2: superadmin test config (squad + hwid) reaches the panel ---
    shop["test_enabled"] = True
    await ctx.settings.set("test_squad_uuid", "sqT")
    await ctx.settings.set("test_hwid", 3)
    out = await svc.claim_test_sub(ctx, shop, 111, "alice")
    assert out["username"] == "vip-Test-1", out
    payload = panel.created[-1]
    assert payload.get("active_internal_squads") == ["sqT"], payload
    assert payload.get("hwid_device_limit") == 3, payload

    # --- task 3: test subs show info, never renew buttons; buy-new instead ---
    await cb("my_subs", "f6a")
    assert "sub:1" in flat(rms()[-1]), rms()[-1]
    await cb("sub:1", "f6b")
    assert "can't be renewed" in texts()[-1], texts()[-1]
    assert not any((d or "").startswith(("renew:", "qrenew:"))
                   for d in flat(rms()[-1])), rms()[-1]
    assert "pershop:buy" in flat(rms()[-1]), rms()[-1]

    # test sub notifications: no 72h warning at all; expired + traffic
    # notices point at the Buy flow, never at renew/top-up.
    from datetime import timedelta as _td
    now = datetime.now(timezone.utc)
    sub1 = db.subs[1]
    uuid1 = sub1["user_uuid"]
    db.users[uuid1]["expire_at"] = now + _td(hours=24)
    sub1["expire_at"] = now + _td(hours=24)
    sub1["notified_72h"] = False
    sub1["notified_expired"] = False
    tg3b = FakeTg("DUMMY")
    await _notify_sub(ctx, tg3b, shop, sub1, now)
    assert tg3b.sent == [], tg3b.sent

    uuid1_exp = now - _td(hours=1)
    db.users[uuid1]["expire_at"] = uuid1_exp
    sub1["expire_at"] = uuid1_exp
    tg3c = FakeTg("DUMMY")
    await _notify_sub(ctx, tg3c, shop, sub1, now)
    exp_rm = [p.get("rm") for m, p in tg3c.sent
              if m == "sendMessage" and p.get("chat_id") == 111]
    assert exp_rm, tg3c.sent
    assert any(d == "pershop:buy" for d in flat(exp_rm[-1])), exp_rm[-1]
    assert not any((d or "").startswith(("qrenew:", "addtraffic:"))
                   for d in flat(exp_rm[-1])), exp_rm[-1]

    # out-of-traffic on a test sub: buy-new button, no traffic top-up.
    future = now + _td(days=30)
    db.users[uuid1]["expire_at"] = future
    sub1["expire_at"] = future
    db.users[uuid1]["traffic_limit_bytes"] = 1073741824
    db.users[uuid1]["used_traffic_bytes"] = 1073741824
    sub1["notified_out"] = False
    tg3d = FakeTg("DUMMY")
    await _notify_sub(ctx, tg3d, shop, sub1, now)
    out_rm = [p.get("rm") for m, p in tg3d.sent
              if m == "sendMessage" and p.get("chat_id") == 111]
    assert out_rm, tg3d.sent
    assert any(d == "pershop:buy" for d in flat(out_rm[-1])), out_rm[-1]
    assert not any((d or "").startswith(("qrenew:", "addtraffic:"))
                   for d in flat(out_rm[-1])), out_rm[-1]
    db.users[uuid1]["traffic_limit_bytes"] = 0
    db.users[uuid1]["used_traffic_bytes"] = 0

    # --- task 1: cancelled subs stay silent; nag is one friendly summary ---
    sub = db.subs[1]
    sub["cancelled"] = True
    tg = FakeTg("DUMMY")
    await _notify_sub(ctx, tg, shop, sub, datetime.now(timezone.utc))
    assert [p for m, p in tg.sent
            if m == "sendMessage" and p.get("chat_id") == 111] == [], tg.sent
    shop.update({"pending_nag_hours": 1, "admin_tg_id": 999})
    ins = ("INSERT INTO plugin_pershop_orders(shop_id,package_id,buyer_tg_id,buyer_username,"
           "receipt_file_id,receipt_kind,renew_username,total_units,options_snapshot)"
           " VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id")
    now = datetime.now(timezone.utc)
    for buyer, name, f in ((111, "alice", "f1"), (222, "bob", "f2")):
        r = await db.fetchrow(ins, sid, pid, buyer, name, f, "photo",
                              f"vip-{name}", 1.0, "{}")
        db.orders[r["id"]]["created_at"] = now - timedelta(hours=2)
    tg2 = FakeTg("DUMMY")
    await _nag_pending(ctx, tg2, shop, now)
    assert len(tg2.sent) == 1, tg2.sent
    assert tg2.sent[0][1]["chat_id"] == 999, tg2.sent
    assert "2 order(s)" in tg2.sent[0][1]["text"], tg2.sent

    # --- tasks 4+5: buyer lookup rows compose; scammer can be unmarked ---
    db.buyers[(sid, 111)] = "en"
    db.credits[(sid, 111)] = 1.5
    prefs = await db.fetch(
        "SELECT tg_id, lang, credit_units FROM plugin_pershop_buyers WHERE shop_id=$1", sid)
    assert prefs == [{"tg_id": 111, "lang": "en", "credit_units": 1.5}], prefs
    oids = [oid for oid, o in db.orders.items() if o["buyer_tg_id"] == 111]
    await svc.mark_scammer(ctx, oids[0], "admin1")
    assert 111 in db.scammers, db.scammers
    flags = await svc.list_scammers(ctx)
    assert [f["tg_id"] for f in flags] == [111], flags
    assert flags[0]["marked_by_admin"] == "admin1", flags
    assert await svc.unmark_scammer(ctx, 111) is True
    assert await svc.unmark_scammer(ctx, 111) is False
    assert 111 not in db.scammers, db.scammers

    # --- task 6: mixed-payment sub detail carries the support note ---
    GB = 1073741824
    future = now + timedelta(days=20)
    csid = db._next("sub")
    db.subs[csid] = {"id": csid, "shop_id": sid, "buyer_tg_id": 333,
                     "user_uuid": "uu-carol", "username": "vip-carol",
                     "expire_at": future, "traffic_limit_bytes": 64 * GB,
                     "package_id": pid, "is_test": False, "is_linked": False,
                     "cancelled": False, "notified_80": False,
                     "notified_72h": False, "notified_expired": False,
                     "notified_out": False}
    cnid = db._next("order")
    db.orders[cnid] = {"id": cnid, "shop_id": sid, "package_id": pid,
                       "service_id": None, "buyer_tg_id": 333,
                       "buyer_username": "carol", "receipt_file_id": "f9",
                       "receipt_kind": "photo", "renew_username": "vip-carol",
                       "status": "approved",
                       "options_snapshot": json.dumps({"credit_used": 1.0}),
                       "total_units": 2.5, "credit_units": 0,
                       "created_at": now, "decided_at": now, "decided_by": "admin-tg",
                       "owner_nudged_at": None, "owner_chat_id": None,
                       "owner_msg_id": None, "deny_reason": None,
                       "withdraw_reason": None, "withdraw_card": None}
    await cb("my_subs", "f6c", uid=333, un="carol")
    sub_cbs = [d for d in flat(rms(333)[-1]) if (d or "").startswith("sub:")]
    assert len(sub_cbs) == 1, rms(333)[-1]
    await cb(sub_cbs[0], "f6d", uid=333, un="carol")
    assert "already approved and activated" in texts(333)[-1], texts(333)[-1]
    print("FOLLOWUP6 FLOW PASSED")


def test_followup6_flow():
    asyncio.run(_followup6_flow())


async def _scope_flow():
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import handle_update

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    shop = db.shops[sid]
    ta, tb = db._next("tier"), db._next("tier")
    db.tiers[ta] = {"id": ta, "shop_id": sid, "name": "A", "squad_uuid": "sqA", "active": True}
    db.tiers[tb] = {"id": tb, "shop_id": sid, "name": "B", "squad_uuid": "sqB", "active": True}
    pid = db._next("pkg")
    db.pkgs[pid] = {"id": pid, "shop_id": sid, "tier_id": ta, "name": "Pro",
                     "traffic_limit_bytes": 64 * 1073741824, "duration_days": 30,
                     "price_units": 2.5, "price_text": "", "effect": "full", "active": True,
                     "hwid_mode": "default"}
    off = db._next("pkg")
    db.pkgs[off] = {"id": off, "shop_id": sid, "tier_id": None, "name": "+50GB",
                     "traffic_limit_bytes": 50 * 1073741824, "duration_days": 30,
                     "price_units": 1.0, "price_text": "", "effect": "traffic_only",
                     "scope_tier_id": tb, "active": True, "hwid_mode": "default"}
    future = datetime.now(timezone.utc) + timedelta(days=30)
    db.subs[1] = {"id": 1, "shop_id": sid, "buyer_tg_id": 111, "user_uuid": "uu-9",
                  "username": "vip-alice", "expire_at": future,
                  "traffic_limit_bytes": 64 * 1073741824, "package_id": pid,
                  "is_test": False, "is_linked": False, "cancelled": False,
                  "notified_80": False, "notified_72h": False,
                  "notified_expired": False, "notified_out": False}
    db.users["uu-9"] = {"uuid": "uu-9", "username": "vip-alice", "status": "ACTIVE",
                        "expire_at": future, "traffic_limit_bytes": 64 * 1073741824,
                        "used_traffic_bytes": 0}
    FakeTg.instances.clear()

    async def cb(data, cid):
        await handle_update(ctx, shop, {"update_id": 400, "callback_query": {
            "id": cid, "from": {"id": 111, "username": "alice"}, "data": data}})

    def last_rm():
        return str(FakeTg.instances[-1].sent[-1][1]["rm"])

    # offer scoped to tier B: tier-A sub never sees it
    await cb("addtraffic:1", "s1")
    assert "topick:" not in last_rm(), last_rm()
    # re-scope to tier A: offer appears
    db.pkgs[off]["scope_tier_id"] = ta
    await cb("addtraffic:1", "s2")
    assert f"topick:1:{off}" in last_rm(), last_rm()
    # unscoped: visible again
    db.pkgs[off]["scope_tier_id"] = None
    await cb("addtraffic:1", "s3")
    assert f"topick:1:{off}" in last_rm(), last_rm()
    print("SCOPE FLOW PASSED")


def test_scope_flow():
    asyncio.run(_scope_flow())


async def _scam_flow():
    """Mark-as-scammer: third decide button denies the order + flags the TG
    id network-wide; a later purchase attempt in another shop warns that
    owner once per 24h; approve-after-scam is a no-op."""
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import handle_update
    from datetime import datetime as _dt, timezone as _tz

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sidA, pidA = make_shop(db)  # owner admin1, admin tg 999
    sidB = db._next("shop")
    db.shops[sidB] = dict(db.shops[sidA], id=sidB, owner_admin="admin2",
                           admin_tg_id=998, prefix="other")
    _tB, pidB = make_stepped(db, sidB)
    oid = db._next("order")
    db.orders[oid] = {"id": oid, "shop_id": sidA, "package_id": pidA, "buyer_tg_id": 111,
                      "buyer_username": "alice", "receipt_file_id": "f1", "receipt_kind": "photo",
                      "renew_username": None, "status": "pending", "deny_reason": None,
                      "withdraw_reason": None, "withdraw_card": None, "owner_chat_id": None,
                      "owner_msg_id": None, "total_units": 1.0, "options_snapshot": None,
                      "created_at": _dt.now(_tz.utc), "decided_at": None, "decided_by": None,
                      "owner_nudged_at": None}
    shopA = dict(db.shops[sidA])
    shopB = dict(db.shops[sidB])
    FakeTg.instances.clear()

    async def acbA(data, cid):
        await handle_update(ctx, shopA, {"update_id": 300, "callback_query": {
            "id": cid, "from": {"id": 999, "username": "owner"}, "data": data}})

    async def cbB(data, cid, uid=111, un="alice"):
        await handle_update(ctx, shopB, {"update_id": 301, "callback_query": {
            "id": cid, "from": {"id": uid, "username": un}, "data": data}})

    def texts_to(cid):
        return [p["text"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p.get("chat_id") == cid]

    # deny first (reason flow), the report button only appears after that
    await acbA(f"deny:{oid}", "s0")
    await handle_update(ctx, shopA, {"update_id": 300, "message": {
        "message_id": 9, "from": {"id": 999, "username": "owner"},
        "chat": {"id": 999}, "text": "fake receipt, wrong amount"}})
    assert db.orders[oid]["status"] == "denied", db.orders[oid]
    offered = [p for inst in FakeTg.instances for m, p in inst.sent
               if m == "sendMessage" and f"scam:{oid}" in str(p.get("rm"))]
    assert offered, "scam report must follow the deny, never precede it"
    await acbA(f"scam:{oid}", "s1")
    assert db.orders[oid]["status"] == "denied", db.orders[oid]
    assert db.scammers[111]["marked_by_admin"] == "admin1", db.scammers
    assert any("marked as a scammer" in t for t in texts_to(111)), texts_to(111)
    assert any("network-wide" in t for t in texts_to(999)), texts_to(999)
    # decided order: approve afterwards changes nothing, buyer hears nothing new
    await acbA(f"approve:{oid}", "s2")
    assert db.orders[oid]["status"] == "denied", db.orders[oid]
    assert len(texts_to(111)) == 2, texts_to(111)
    # same TG tries the other shop: that owner is warned once per 24h
    await cbB(f"buy:{pidB}", "s3")
    warns = [t for t in texts_to(998) if "scammer" in t.lower()]
    assert len(warns) == 1 and "admin1" in warns[0], texts_to(998)
    await cbB(f"buy:{pidB}", "s4")
    assert len([t for t in texts_to(998) if "scammer" in t.lower()]) == 1, texts_to(998)
    print("SCAM FLOW PASSED")


def test_scam_flow():
    asyncio.run(_scam_flow())


async def _global_test_flow():
    """One test account per Telegram id across the whole panel: claim in
    shop A blocks the same TG in shop B, but not a different TG."""
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import handle_update

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sidA, _p = make_shop(db)
    db.shops[sidA]["test_enabled"] = True
    sidB = db._next("shop")
    db.shops[sidB] = dict(db.shops[sidA], id=sidB, owner_admin="admin2",
                           admin_tg_id=998, prefix="other", test_enabled=True)
    panel = FakePanel()
    svc._panel_client = lambda: panel
    shopA = dict(db.shops[sidA])
    shopB = dict(db.shops[sidB])
    FakeTg.instances.clear()

    async def confirm(shop, cid, uid):
        await handle_update(ctx, shop, {"update_id": 302, "callback_query": {
            "id": cid, "from": {"id": uid, "username": "u"}, "data": "testconfirm"}})

    def texts():
        return [p["text"] for inst in FakeTg.instances for m, p in inst.sent if m in ("sendMessage", "editMessageText")]

    await confirm(shopA, "t1", 111)
    assert sum(1 for s in db.subs.values() if s.get("is_test")) == 1, db.subs
    await confirm(shopB, "t2", 111)
    assert sum(1 for s in db.subs.values() if s.get("is_test")) == 1, db.subs
    assert "already received" in texts()[-1], texts()[-1]
    await confirm(shopB, "t3", 222)
    assert sum(1 for s in db.subs.values() if s.get("is_test")) == 2, db.subs
    print("GLOBAL TEST FLOW PASSED")


def test_global_test_flow():
    asyncio.run(_global_test_flow())


async def _stats_flow():
    """Owner overview numbers: active/pending/approved+units/denied/
    withdrawn/trials/linked."""
    import rwa_plugin_pershop.service as svc
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _p = make_shop(db)
    now = _dt.now(_tz.utc)

    def _sub(buyer, exp, test=False, linked=False):
        i = db._next("sub")
        db.subs[i] = {"id": i, "shop_id": sid, "buyer_tg_id": buyer, "user_uuid": f"u{i}",
                      "username": f"vip-x{i}", "expire_at": exp, "traffic_limit_bytes": None,
                      "package_id": None, "is_test": test, "is_linked": linked}

    _sub(111, now + _td(days=5))
    _sub(112, now - _td(days=1))
    _sub(113, now + _td(days=1), test=True)
    _sub(114, now - _td(days=2), linked=True)

    def _ord(st, units=None):
        i = db._next("order")
        db.orders[i] = {"id": i, "shop_id": sid, "status": st, "total_units": units}

    _ord("pending")
    _ord("withdraw_requested")
    _ord("approved", 2.5)
    _ord("approved", 1.5)
    _ord("denied")
    _ord("withdrawn")
    st = await svc.shop_stats(ctx, dict(db.shops[sid]))
    assert st["subs_active"] == 2, st
    assert st["orders_pending"] == 2, st
    assert st["orders_approved"] == 2 and st["units_sold"] == 4.0, st
    assert st["orders_denied"] == 1 and st["orders_withdrawn"] == 1, st
    assert st["tests_claimed"] == 1 and st["subs_linked"] == 1, st
    print("STATS FLOW PASSED")


def test_stats_flow():
    asyncio.run(_stats_flow())


async def _service_flow(ctx, shop, db):
    from rwa_plugin_pershop.routes_telegram import handle_update

    sid = shop["id"]
    catid = db._next("scat")
    db.svc_cats[catid] = {"id": catid, "shop_id": sid, "name": "Extras",
                          "sort": 0, "active": True}
    svcid = db._next("svc")
    db.svcs[svcid] = {"id": svcid, "shop_id": sid, "cat_id": catid, "name": "Setup help",
                      "price_units": 1.5, "description": "We configure everything.",
                      "done_text": "Done — check your inbox.", "sort": 0, "active": True}
    loose = db._next("svc")
    db.svcs[loose] = {"id": loose, "shop_id": sid, "cat_id": None, "name": "Top-up call",
                      "price_units": 0.5, "description": "", "done_text": "",
                      "sort": 1, "active": True}
    FakeTg.instances.clear()

    async def cb(data, cid, uid=555, un="bob"):
        await handle_update(ctx, shop, {"update_id": 500, "callback_query": {
            "id": cid, "from": {"id": uid, "username": un}, "data": data}})

    async def msg(text, uid=555, un="bob"):
        await handle_update(ctx, shop, {"update_id": 501, "message": {
            "message_id": 9, "from": {"id": uid, "username": un},
            "chat": {"id": uid}, "text": text}})

    def mine(uid=555):
        return [p["text"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p["chat_id"] == uid]

    def rms():
        return [p["rm"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p.get("rm")]

    await handle_update(ctx, shop, {"update_id": 502, "message": {"message_id": 1,
        "from": {"id": 555, "username": "bob"}, "chat": {"id": 555}, "text": "/start"}})
    assert any("Other Services" in str(rm) for rm in rms()), rms()
    await msg("🛎 Other Services")
    assert any("svccat:" in str(rm) for rm in rms()), rms()
    assert any("svcother:" in str(rm) for rm in rms()), rms()
    await cb(f"svccat:{catid}", "s2")
    assert any(f"svc:{svcid}" in str(rm) for rm in rms()), rms()
    await cb(f"svc:{svcid}", "s2b")
    assert "375,000" in mine()[-1], mine()[-1]
    assert any(f"svcbuy:{svcid}" in str(rm) for rm in rms()), rms()
    await cb(f"svcbuy:{svcid}", "s3")
    assert "note" in mine()[-1].lower(), mine()[-1]
    assert any("svcskip:" in str(rm) for rm in rms()), rms()
    await msg("please hurry, need it tonight")
    contract = mine()[-1]
    assert "Setup help" in contract and "375,000" in contract, contract
    assert "please hurry" in contract, contract
    await cb("stconfirm", "s4")
    await msg("svc-receipt-1")
    orders = [o for o in db.orders.values() if o.get("service_id") == svcid]
    assert len(orders) == 1 and orders[0]["status"] == "pending", db.orders
    owner = [p["text"] for inst in FakeTg.instances for m, p in inst.sent
             if m in ("sendMessage", "editMessageText") and p["chat_id"] == 999]
    assert any("[service]" in t and "please hurry" in t for t in owner), owner
    assert any("approve:" in str(rm) for rm in rms()), rms()
    assert not any("scam:" in str(rm) for rm in rms()), "scam report must not show pre-deny"
    assert not db.subs, db.subs
    oid = orders[0]["id"]
    await cb(f"approve:{oid}", "s5", uid=999)
    assert db.orders[oid]["status"] == "approved", db.orders[oid]
    done_edits = [p for inst in FakeTg.instances for m, p in inst.sent
                  if m == "editMessageText" and "check your inbox" in p.get("text", "")]
    assert done_edits, "approval must edit the buyer waiting message with the done text"
    await cb("account", "s6")
    await cb("acc:history", "s6b")
    assert "Setup help" in mine()[-1], mine()[-1]
    print("SERVICE FLOW PASSED")


def test_service_flow():
    import asyncio

    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _pid = make_shop(db)
    awaitable = _service_flow(ctx, db.shops[sid], db)
    asyncio.run(awaitable)


async def _public_api_flow(ctx, shop, db):
    import hashlib
    import json

    from rwa_plugin_pershop import service as svc

    sid = shop["id"]
    _tid, _pid = make_stepped(db, sid)  # Tier A + 2.5u Pro package
    catid = db._next("scat")
    db.svc_cats[catid] = {"id": catid, "shop_id": sid, "name": "Extras",
                          "sort": 0, "active": True}
    svcid = db._next("svc")
    db.svcs[svcid] = {"id": svcid, "shop_id": sid, "cat_id": catid, "name": "Setup help",
                      "price_units": 1.5, "description": "We configure everything.",
                      "done_text": "Done.", "sort": 0, "active": True}

    fresh = await svc.new_public_key(ctx)
    await ctx.db.execute(
        "UPDATE plugin_pershop_shops SET public_key_hash=$1, public_key_enc=$2 WHERE id=$3",
        fresh["key_hash"], fresh["key_enc"], sid)
    assert db.shops[sid]["public_key_hash"] == hashlib.sha256(fresh["key"].encode()).hexdigest()

    assert await svc.shop_by_public_key(ctx, "0" * 64) is None
    found = await svc.shop_by_public_key(ctx, fresh["key_hash"])
    assert found and found["id"] == sid
    assert await svc.verify_public_key(ctx, found, fresh["key"]) is True
    assert await svc.verify_public_key(ctx, found, "wrong-key") is False
    assert await svc.verify_public_key(ctx, found, "") is False

    cat = await svc.shop_catalog(ctx, dict(found))
    dumped = json.dumps(cat, default=str)
    for banned in ("bot_token_enc", "admin_tg_id", "webhook_secret", "owner_admin",
                   "public_key", "squad_uuid", "card_text", "buyer_tg", "referrer"):
        assert banned not in dumped, banned
    assert cat["shop"]["prefix"] == "vip"
    pkgs = {p["name"]: p for p in cat["packages"]}
    assert "625,000" in pkgs["Pro"]["price_display"], pkgs["Pro"]
    assert pkgs["Pro"]["tier_name"] == "Tier A"
    assert pkgs["Pro"]["traffic_limit_gb"] == round(64 * 1073741824 / 1e9, 2)
    assert pkgs["Pro"]["price_units"] == 2.5
    assert pkgs["Pro"]["currency_label"] == "تومان"
    assert "Monthly" in pkgs
    svcs = {s["name"]: s for s in cat["services"]}
    assert "375,000" in svcs["Setup help"]["price_display"], svcs["Setup help"]
    assert svcs["Setup help"]["cat_name"] == "Extras"
    assert cat["trial"]["enabled"] is False
    assert cat["tiers"] and cat["service_categories"]

    await ctx.db.execute(
        "UPDATE plugin_pershop_shops SET public_key_hash=NULL, public_key_enc=NULL WHERE id=$1", sid)
    assert await svc.shop_by_public_key(ctx, fresh["key_hash"]) is None
    print("PUBLIC API FLOW PASSED")


def test_public_api_flow():
    import asyncio
    import importlib

    import rwa_plugin_pershop.crypto as crypto_mod

    importlib.reload(crypto_mod)  # earlier suites leave a fake decrypt_token patched in
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _pid = make_shop(db)
    asyncio.run(_public_api_flow(ctx, db.shops[sid], db))


async def _pub_users_flow(ctx, shop, db):
    import sys
    import types
    from datetime import datetime, timedelta, timezone

    from rwa_plugin_pershop import service as svc

    GB = 1073741824

    async def _fake_owner(_c, _n):
        return 7

    async def _fake_not_super(_c, _s):
        return False

    real_owner, real_super = svc._owner_account_id, svc._owner_is_superadmin
    real_client = svc._panel_client
    real_acct_row = svc._owner_account_row
    svc._owner_account_id = _fake_owner
    svc._owner_is_superadmin = _fake_not_super
    panel = FakePanel()
    svc._panel_client = lambda: panel

    async def _fake_acct_row(_c, _n):
        return True, {"id": 7, "role_id": 1, "role_name": "admin"}

    svc._owner_account_row = _fake_acct_row
    saved_mods = {}
    try:
        sid = shop["id"]
        exp = datetime.now(timezone.utc) + timedelta(days=100)
        db.users["uu-1"] = {"uuid": "uu-1", "username": "vip-alice", "status": "ACTIVE",
                            "expire_at": exp, "used_traffic_bytes": GB,
                            "traffic_limit_bytes": 10 * GB, "created_by_admin_id": 7}
        db.users["uu-9"] = {"uuid": "uu-9", "username": "vip-stranger", "status": "ACTIVE",
                            "expire_at": exp, "used_traffic_bytes": 0,
                            "traffic_limit_bytes": 10 * GB, "created_by_admin_id": 99}
        # create: prefix-enforced, squad passed, attribution + plan-less subs row
        new_exp = datetime.now(timezone.utc) + timedelta(days=30)
        res = await svc.api_create_user(ctx, dict(shop), username="vip-bob",
                                        expire_at=new_exp, traffic_limit_bytes=20 * GB,
                                        telegram_id=555, squad_uuid="sqT",
                                        hwid_device_limit=2)
        assert res["uuid"] == "uuid-1", res
        assert res["sub_id"] is not None
        kw = panel.created[0]
        assert kw["username"] == "vip-bob" and kw["active_internal_squads"] == ["sqT"], kw
        assert kw["description"] == "pershop:vip api", kw
        assert kw["hwid_device_limit"] == 2 and kw["telegram_id"] == 555, kw
        assert db.users["uuid-1"]["created_by_admin_id"] == 7
        sub = db.subs[res["sub_id"]]
        assert sub["package_id"] is None and sub["buyer_tg_id"] == 555, sub
        stats = await svc.shop_stats(ctx, dict(shop))
        assert stats["subs_manual"] == 1, stats
        # prefix-free username refused before touching the panel
        n_before = len(panel.created)
        try:
            await svc.api_create_user(ctx, dict(shop), username="bob", expire_at=new_exp)
            assert False, "prefix-free username accepted"
        except ValueError:
            pass
        assert len(panel.created) == n_before
        # squad outside the owner scope refused (fake RBAC layer denies all)
        saved_mods = {}
        for name in ("web", "web.backend", "web.backend.core", "web.backend.core.rbac"):
            saved_mods[name] = sys.modules.get(name)
            stub = types.ModuleType(name)
            stub.__path__ = []
            sys.modules[name] = stub

        async def _deny(_admin, _rt, _uuid, _action="view"):
            return False

        async def _visible(_admin):
            return {"uu-1"}

        sys.modules["web.backend.core.rbac"].check_access = _deny
        sys.modules["web.backend.core.rbac"].get_visible_user_uuids = _visible
        try:
            await svc.api_create_user(ctx, dict(shop), username="vip-denied",
                                      expire_at=new_exp, squad_uuid="sqX")
            assert False, "out-of-scope squad accepted"
        except ValueError as e:
            assert "squad" in str(e), e
        assert len(panel.created) == n_before
        for _name in ("web", "web.backend", "web.backend.core", "web.backend.core.rbac"):
            sys.modules.pop(_name, None)
        saved_mods.clear()
        sys.modules["web"] = types.ModuleType("web")
        sys.modules["web.backend"] = types.ModuleType("web.backend")
        sys.modules["web.backend.core"] = types.ModuleType("web.backend.core")
        sys.modules["web.backend.core.rbac"] = types.ModuleType("web.backend.core.rbac")
        sys.modules["web.backend.core.rbac"].get_visible_user_uuids = _visible
        # list: owner-created only, RBAC visibility applied, search works
        users = await svc.api_list_users(ctx, dict(shop))
        uuids = {u["uuid"] for u in users}
        assert uuids == {"uu-1"}, uuids
        users = await svc.api_list_users(ctx, dict(shop), search="ALI")
        assert [u["username"] for u in users] == ["vip-alice"], users
        users = await svc.api_list_users(ctx, dict(shop), limit=1)
        assert len(users) == 1
        # get: allowlisted shape, foreign + missing read as missing
        one = await svc.api_get_user(ctx, dict(shop), "uu-1")
        assert set(one) == set(svc._API_USER_FIELDS), set(one)
        assert one["username"] == "vip-alice" and one["created_by_admin_id"] == 7
        for bad in ("uu-9", "nope"):
            try:
                await svc.api_get_user(ctx, dict(shop), bad)
                assert False, bad
            except ValueError:
                pass
        # update: absolute expiry + traffic up + disable in one call
        upd = await svc.api_update_user(ctx, dict(shop), "uu-1", expire_at=new_exp,
                                        traffic_limit_bytes=15 * GB, status="disabled")
        assert sorted(upd["updated"]) == ["expireAt", "status", "trafficLimitBytes"], upd
        _uid, fields = panel.updated[-1]
        assert _uid == "uu-1" and fields["status"] == "DISABLED", fields
        assert fields["trafficLimitBytes"] == 15 * GB and fields["expireAt"], fields
        # hwid + telegram + squad move on the api-created user (naive expiry ok)
        upd2 = await svc.api_update_user(
            ctx, dict(shop), res["uuid"], expire_at=new_exp.replace(tzinfo=None),
            hwid_device_limit=3, telegram_id=556, squad_uuid="sq2")
        _u2, f2 = panel.updated[-1]
        assert f2["hwidDeviceLimit"] == 3 and f2["telegramId"] == 556, f2
        assert f2["activeInternalSquads"] == ["sq2"], f2
        assert db.subs[res["sub_id"]]["traffic_limit_bytes"] == 20 * GB
        # empty update, bad status, foreign update all refused; foreign never sent
        for kwargs in ({}, {"status": "weird"}):
            try:
                await svc.api_update_user(ctx, dict(shop), "uu-1", **kwargs)
                assert False, kwargs
            except ValueError:
                pass
        try:
            await svc.api_update_user(ctx, dict(shop), "uu-9", status="disabled")
            assert False, "foreign update accepted"
        except ValueError:
            pass
        assert all(u != "uu-9" for u, _f in panel.updated), panel.updated
        print("PUB USERS FLOW PASSED")
    finally:
        svc._owner_account_id, svc._owner_is_superadmin = real_owner, real_super
        svc._panel_client = real_client
        svc._owner_account_row = real_acct_row
        for name, prev in saved_mods.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev


def test_pub_users_flow():
    import asyncio

    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _pid = make_shop(db)
    asyncio.run(_pub_users_flow(ctx, db.shops[sid], db))


async def _credit_flow():
    import json as _json

    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import handle_update

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    tid, pid = make_stepped(db, sid)
    shop = db.shops[sid]
    shop["referral_percent"] = 10
    panel = FakePanel()
    svc._panel_client = lambda: panel
    FakeTg.instances.clear()

    async def txt(text, uid=111, un="alice"):
        await handle_update(ctx, shop, {"update_id": 300, "message": {
            "message_id": 9, "from": {"id": uid, "username": un},
            "chat": {"id": uid}, "text": text}})

    async def cb(data, cid, uid=111, un="alice"):
        await handle_update(ctx, shop, {"update_id": 301, "callback_query": {
            "id": cid, "from": {"id": uid, "username": un}, "data": data}})

    def rms():
        return [p["rm"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p["rm"]]

    def texts(uid=None):
        return [p["text"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and (uid is None or p.get("chat_id") == uid)]

    async def buy_contract(uid, un, cid):
        await cb(f"tiers:{tid}", f"{cid}a", uid=uid, un=un)
        await cb(f"buy:{pid}", f"{cid}b", uid=uid, un=un)
        await cb("hnext", f"{cid}c", uid=uid, un=un)

    # unit-level: balance starts 0, overspend floors at 0
    assert await svc.credit_balance(ctx, sid, 555) == 0
    assert await svc.add_credit(ctx, sid, 555, 3.0) == 3.0
    assert await svc.add_credit(ctx, sid, 555, -10.0) == 0.0
    assert await svc.credit_balance(ctx, sid, 555) == 0
    # 777 invites 111; 111 buys Pro (2.5u) with no credit -> 777 earns 10%
    await svc.record_referral(ctx, sid, 777, 111)
    await buy_contract(111, "alice", "k1")
    assert not any("credituse" in str(rm) for rm in rms()), rms()
    await cb("stconfirm", "k1d", uid=111, un="alice")
    await txt("receipt-ref-1", uid=111, un="alice")
    o1 = max(db.orders.values(), key=lambda o: o["id"])
    assert o1["buyer_tg_id"] == 111 and o1["status"] == "pending", o1
    assert _json.loads(o1["options_snapshot"]).get("credit_used", 0) == 0
    # owner approves from the DM: inviter gets the earn notice
    await cb(f"approve:{o1['id']}", "k1e", uid=999, un="owner")
    assert db.orders[o1["id"]]["status"] == "approved", db.orders[o1["id"]]
    assert db.orders[o1["id"]]["credit_units"] == 0.25, db.orders[o1["id"]]
    assert await svc.credit_balance(ctx, sid, 777) == 0.25
    assert any("62,500" in t for t in texts(777)), texts(777)
    # 777 tops up to 1.25u and spends it on Pro: covers 1.25 of 2.5
    await svc.add_credit(ctx, sid, 777, 1.0)
    assert await svc.credit_balance(ctx, sid, 777) == 1.25
    await txt("👤 My account", uid=777, un="bob")
    await cb("acc:balance", "k1f", uid=777, un="bob")
    assert any("312,500" in t for t in texts(777)), texts(777)
    await buy_contract(777, "bob", "k2")
    assert any("credituse" in str(rm) for rm in rms()), rms()
    await cb("credituse", "k2d", uid=777, un="bob")
    # 1.25u x 250000 = 312,500 covered, same due
    assert any("312,500" in t for t in texts(777)), texts(777)
    await cb("stconfirm", "k2e", uid=777, un="bob")
    assert any("312,500" in t for t in texts(777)), texts(777)
    await txt("receipt-ref-2", uid=777, un="bob")
    o2 = max(db.orders.values(), key=lambda o: o["id"])
    snap2 = _json.loads(o2["options_snapshot"])
    assert snap2["credit_used"] == 1.25, snap2
    assert await svc.credit_balance(ctx, sid, 777) == 0
    assert any("Credit used" in t for t in texts(999)), texts(999)
    # owner denies from the DM: spent credit returns, buyer is told
    await cb(f"deny:{o2['id']}", "k2f", uid=999, un="owner")
    await txt("changed my mind", uid=999, un="owner")
    assert db.orders[o2["id"]]["status"] == "denied", db.orders[o2["id"]]
    assert await svc.credit_balance(ctx, sid, 777) == 1.25
    assert any("returned" in t for t in texts(777)), texts(777)
    # withdraw-confirm refunds too: 111 spends 0.5u, then withdraws
    await svc.add_credit(ctx, sid, 111, 0.5)
    await buy_contract(111, "alice", "k3")
    await cb("credituse", "k3d", uid=111, un="alice")
    await cb("stconfirm", "k3e", uid=111, un="alice")
    await txt("receipt-ref-3", uid=111, un="alice")
    o3 = max(db.orders.values(), key=lambda o: o["id"])
    assert _json.loads(o3["options_snapshot"])["credit_used"] == 0.5
    assert await svc.credit_balance(ctx, sid, 111) == 0
    w = await svc.request_withdraw(ctx, o3["id"], 111, "oops", "card 123")
    assert w["status"] == "withdraw_requested", w
    out = await svc.resolve_withdraw(ctx, o3["id"], "admin1", True)
    assert out["credit_refunded"] == 0.5, out
    assert await svc.credit_balance(ctx, sid, 111) == 0.5
    # stats count what the program minted
    st = await svc.shop_stats(ctx, shop)
    assert st["referral_earned"] == 0.25, st
    print("CREDIT FLOW PASSED")


async def _credit_auto_flow():
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import handle_update

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    tid, pid = make_stepped(db, sid)
    shop = db.shops[sid]
    shop["referral_percent"] = 10
    panel = FakePanel()
    svc._panel_client = lambda: panel
    FakeTg.instances.clear()

    async def cb(data, cid, uid=777, un="bob"):
        await handle_update(ctx, shop, {"update_id": 302, "callback_query": {
            "id": cid, "from": {"id": uid, "username": un}, "data": data}})

    def rms():
        return [p["rm"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p["rm"]]

    def texts(uid=None):
        return [p["text"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and (uid is None or p.get("chat_id") == uid)]

    # 111 invited 777; 777 holds 5.0u and buys Pro (2.5u) fully on credit.
    await svc.record_referral(ctx, sid, 111, 777)
    await svc.add_credit(ctx, sid, 777, 5.0)
    await cb(f"tiers:{tid}", "a1a")
    await cb(f"buy:{pid}", "a1b")
    await cb("hnext", "a1c")
    await cb("credituse", "a1d")
    await cb("stconfirm", "a1e")
    o = max(db.orders.values(), key=lambda x: x["id"])
    assert o["status"] == "approved" and o["receipt_kind"] == "credit", o
    assert o["renew_username"] == "vip-bob", o
    assert await svc.credit_balance(ctx, sid, 777) == 2.5
    # full-credit sale mints no referral reward: the cash portion is zero.
    assert o["credit_units"] == 0, o
    assert await svc.credit_balance(ctx, sid, 111) == 0
    # buyer got the auto note + credentials; owner got a note, no decide buttons.
    assert any("auto-approved" in t for t in texts(777)), texts(777)
    assert any("No action needed" in t for t in texts(999)), texts(999)
    assert not any("approve:" in str(rm) for rm in rms()), rms()
    # sub detail offers the automatic cancel button.
    sub = next(s for s in db.subs.values() if s["username"] == "vip-bob")
    await cb(f"sub:{sub['id']}", "a1f")
    assert any("cfcancel" in str(rm) for rm in rms()), rms()
    await cb(f"cfcancel:{sub['id']}", "a1g")
    assert any("625,000" in t for t in texts(777)), texts(777)
    await cb(f"cfyes:{sub['id']}", "a1h")
    assert any("back on your balance" in t for t in texts(777)), texts(777)
    assert db.subs[sub["id"]]["cancelled"] is True
    assert await svc.credit_balance(ctx, sid, 777) == 5.0
    assert any(f.get("status") == "DISABLED" for _u, f in panel.updated), panel.updated
    assert any("cancelled" in t for t in texts(999)), texts(999)
    # repeat tap is safe: the cancelled detail re-renders.
    await cb(f"cfyes:{sub['id']}", "a1i")
    assert "Cancelled" in texts(777)[-1], texts(777)[-1]
    print("CREDIT AUTO FLOW PASSED")


async def _edit_place_flow():
    """Edit-in-place: button taps rewrite the tapped message; the receipt
    step is the only exception (prompt deleted, fresh waiting message).
    Owner approve/deny rewrite both the buyer waiting message and the
    owner decide card, stripping the buttons."""
    db, ctx, svc = await _approve_new()
    from rwa_plugin_pershop.routes_telegram import handle_update

    shop = dict(db.shops[1])
    FakeTg.instances.clear()

    def _all():
        return [(m, p) for inst in FakeTg.instances for m, p in inst.sent]

    def _sends():
        return [p for m, p in _all() if m == "sendMessage"]

    def _edits():
        return [p for m, p in _all() if m == "editMessageText"]

    async def _tap(data, who=111, mid=5000, cid="e"):
        await handle_update(ctx, shop, {"update_id": 100, "callback_query":
            {"id": cid, "from": {"id": who}, "data": data, "message": {"message_id": mid}}})

    # buyer taps rewrite the same message, no new sends. The menu tap is
    # the exception: the reply-keyboard panel only rides on a FRESH
    # message, so going back to the menu sends it new (re-asserting the
    # persistent panel) and deletes the screen it came from.
    await _tap("my_subs", mid=5001, cid="e1")
    await _tap("back:subs", mid=5001, cid="e2")
    await _tap("account", mid=5001, cid="e3")
    assert not _sends(), _sends()
    assert len(_edits()) == 3, _edits()
    await _tap("menu", mid=5001, cid="e4")
    assert len(_sends()) == 1, _sends()
    assert _sends()[0]["rm"].get("is_persistent"), _sends()
    assert any(m == "deleteMessage" and p.get("message_id") == 5001
               for m, p in _all()), _all()

    # full buy with taps: contract edits, receipt prompt edits, submit
    # deletes the prompt and sends ONE fresh waiting message
    await _tap("pershop:buy", mid=5001, cid="e5")
    await _tap("buy:1", mid=5001, cid="e6")
    await _tap("stconfirm", mid=5001, cid="e7")
    n_sends = len(_sends())
    await handle_update(ctx, shop, {"update_id": 101, "message": {"message_id": 60,
        "from": {"id": 111, "username": "alice"}, "chat": {"id": 111}, "text": "rcpt-1"}})
    pend = [o for o in db.orders.values() if o["status"] == "pending"]
    assert pend, "receipt did not create order"
    order = pend[-1]
    assert any(m == "deleteMessage" for m, _ in _all()), _all()
    fresh = [p for p in _sends()[n_sends:] if p["chat_id"] == 111]
    assert len(fresh) == 1, _sends()
    assert order.get("buyer_msg_id"), order
    assert order.get("owner_msg_id"), order

    # owner approves from the decide card: buyer waiting message becomes
    # the credentials, decide card becomes the final Approved stub
    FakeTg.instances.clear()
    await _tap(f"approve:{order['id']}", who=999, mid=order["owner_msg_id"], cid="e8")
    assert db.orders[order["id"]]["status"] == "approved"
    edits = _edits()
    buyer_edits = [p for p in edits if p["chat_id"] == 111]
    owner_edits = [p for p in edits if p["chat_id"] == 999]
    assert any(p["message_id"] == order["buyer_msg_id"] and "vip-alice" in p["text"]
               for p in buyer_edits), edits
    assert any(p["message_id"] == order["owner_msg_id"] and "Approved" in p["text"]
               and not p["rm"] for p in owner_edits), edits
    assert not [p for p in _sends() if p["chat_id"] == 111], _sends()

    # deny path on a second order, then post-deny scammer report
    db.orders[10] = {"id": 10, "shop_id": 1, "package_id": 1, "buyer_tg_id": 112,
                     "buyer_username": "bob", "receipt_file_id": "r", "receipt_kind": "text",
                     "renew_username": None, "status": "pending",
                     "buyer_msg_id": 7001, "owner_msg_id": 7002}
    db.seq["order"] = 10
    FakeTg.instances.clear()
    await _tap("deny:10", who=999, mid=7002, cid="e9")
    await handle_update(ctx, shop, {"update_id": 102, "message": {"message_id": 61,
        "from": {"id": 999}, "chat": {"id": 999}, "text": "fake receipt"}})
    assert db.orders[10]["status"] == "denied"
    edits = _edits()
    assert any(p["chat_id"] == 112 and p["message_id"] == 7001
               and "fake receipt" in p["text"] for p in edits), edits
    assert any(p["chat_id"] == 999 and p["message_id"] == 7002
               and "Denied" in p["text"] and "fake receipt" in p["text"]
               and not p["rm"] for p in edits), edits
    FakeTg.instances.clear()
    await _tap("scam:10", who=999, mid=7003, cid="e10")
    edits = _edits()
    assert any("scam" in p["text"].lower() and p["message_id"] == 7003
               for p in edits if p["chat_id"] == 999), edits
    assert any(p["message_id"] == 7002 and "marked as scammer" in p["text"]
               for p in edits if p["chat_id"] == 999), edits
    print("EDIT IN PLACE FLOW PASSED")


def test_edit_place_flow():
    import asyncio

    asyncio.run(_edit_place_flow())


def test_credit_auto_flow():
    asyncio.run(_credit_auto_flow())


def test_credit_flow():
    asyncio.run(_credit_flow())


async def _shop_admin_flow():
    """Disconnect keeps settings, reset wipes everything, linked subs show
    as-is with a single Renew button into the package chain."""
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.routes_telegram import handle_update
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _pid = make_shop(db)
    shop = dict(db.shops[sid])

    # 1. disconnect: Telegram deleteWebhook fires, token cleared, rest stays
    FakeTg.instances.clear()
    res = await svc.disconnect_shop(ctx, "admin1")
    assert res == {"ok": True, "disconnected": True}, res
    assert any(m == "deleteWebhook" for inst in FakeTg.instances for m, _p in inst.sent), "no deleteWebhook"
    assert db.shops[sid]["bot_token_enc"] is None, db.shops[sid]
    assert db.shops[sid]["prefix"] == "vip", "settings must survive disconnect"
    res = await svc.disconnect_shop(ctx, "admin1")
    assert res == {"ok": True, "disconnected": False}, res
    res = await svc.disconnect_shop(ctx, "nobody")
    assert res["ok"] is False, res

    # 2. reset: every table wiped, every plugin setting deleted
    await ctx.settings.set("polling_enabled", True)
    await ctx.settings.set("public_base_url", "https://x.example")
    await ctx.settings.set("tg_offset:1", 42)
    assert len(db.shops) == 1
    res = await svc.reset_all(ctx)
    assert res["ok"] is True and len(res["dropped"]) == 16, res
    assert db.dropped is True and db.versions == set(), res
    assert db.shops == {} and db.pkgs == {} and db.subs == {}, db
    assert ctx.settings.d == {}, ctx.settings.d

    # 3. linked package-less sub: detail shows system values, plan "—",
    #    single Renew button (no quick renew), renew opens the tier chain
    sid, _pid = make_shop(db)
    tid, _ppid = make_stepped(db, sid)
    shop = dict(db.shops[sid])
    exp = _dt.now(_tz.utc) + _td(days=100)
    GB = 1073741824
    db.users["uu-l"] = {"uuid": "uu-l", "username": "legacy-amy", "status": "ACTIVE",
                        "expire_at": exp, "used_traffic_bytes": 5 * GB,
                        "traffic_limit_bytes": 50 * GB, "created_by_admin_id": 42}
    subid = db._next("sub")
    db.subs[subid] = {"id": subid, "shop_id": sid, "buyer_tg_id": 555, "user_uuid": "uu-l",
                      "username": "legacy-amy", "expire_at": exp,
                      "traffic_limit_bytes": 50 * GB, "package_id": None,
                      "is_test": False, "is_linked": True, "cancelled": False,
                      "notified_80": False, "notified_72h": False, "notified_expired": False}
    FakeTg.instances.clear()

    async def cb(data, cid, uid=555, un="amy"):
        await handle_update(ctx, shop, {"update_id": 900, "callback_query": {
            "id": cid, "from": {"id": uid, "username": un}, "data": data}})

    await cb(f"sub:{subid}", "s1")
    last = FakeTg.instances[-1].sent[-1][1]
    assert "legacy-amy" in last["text"] and "—" in last["text"], last["text"]
    assert "Quick renew" not in str(last["rm"]), last["rm"]
    assert f"renew:{subid}" in str(last["rm"]), last["rm"]
    await cb(f"renew:{subid}", "s2")
    rms = str(FakeTg.instances[-1].sent[-1][1].get("rm"))
    assert "Tier A" in rms and f"renewtier:{subid}:{tid}" in rms, rms
    print("SHOP ADMIN FLOW PASSED")


def test_shop_admin_flow():
    asyncio.run(_shop_admin_flow())


async def _admin_preview_flow():
    """Owner testing from their own Telegram account: panel-label taps must
    answer (buyer preview, keyed by the owner's id), and a full self-buy
    approves end to end. Guards the Persian-silence report."""
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.lang import LOCALES
    from rwa_plugin_pershop.routes_telegram import _clear_session, _set_session, handle_update

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)  # admin_tg=999
    tid, pid = make_stepped(db, sid)
    shop = db.shops[sid]
    shop["lang"] = "fa"
    panel = FakePanel()
    svc._panel_client = lambda: panel
    FakeTg.instances.clear()

    async def txt(text, uid=999, un="owner"):
        await handle_update(ctx, shop, {"update_id": 400, "message": {
            "message_id": 9, "from": {"id": uid, "username": un},
            "chat": {"id": uid}, "text": text}})

    async def cb(data, cid, uid=999, un="owner"):
        await handle_update(ctx, shop, {"update_id": 401, "callback_query": {
            "id": cid, "from": {"id": uid, "username": un}, "data": data}})

    def texts():
        return [p["text"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText")]

    def rms():
        return [p["rm"] for inst in FakeTg.instances for m, p in inst.sent
                if m in ("sendMessage", "editMessageText") and p.get("rm")]

    fa = LOCALES["fa"]
    # admin /start still shows the owner panel, not the buyer menu
    await txt("/start")
    assert texts(), "admin /start answered"
    # panel-label taps answer as buyer preview instead of silence
    n0 = len(texts())
    await txt(fa["buy_subs_btn"])
    assert len(texts()) > n0, "admin buy tap answered"
    assert any(f"tiers:{tid}" in str(rm) for rm in rms()), rms()
    await txt(fa["my_subs_btn"])
    assert len(texts()) > n0, "admin my-subs tap answered"
    # guard: while an admin input session is open, free text is NOT hijacked
    # into the buyer flow (deny path reports the missing order instead).
    await _set_session(ctx, sid, 999, "admin", "await_reason:4242", {"order_id": 4242})
    n1 = len(rms())
    await txt(fa["buy_subs_btn"])
    assert not any(f"tiers:{tid}" in str(rm) for rm in rms()[n1:]), rms()[n1:]
    await _clear_session(ctx, sid, 999, "admin")
    # full self-buy: tier -> package -> contract -> receipt -> self-approve
    await cb(f"tiers:{tid}", "p1")
    await cb(f"buy:{pid}", "p2")
    await cb("hnext", "p3")
    await cb("stconfirm", "p4")
    await txt("owner-test-receipt")
    o = max(db.orders.values(), key=lambda x: x["id"])
    assert o["buyer_tg_id"] == 999 and o["status"] == "pending", o
    await cb(f"approve:{o['id']}", "p5")
    assert db.orders[o["id"]]["status"] == "approved", db.orders[o["id"]]
    assert any("vip-owner" in t for t in texts()), texts()
    print("ADMIN PREVIEW FLOW PASSED")


def test_admin_preview_flow():
    asyncio.run(_admin_preview_flow())


async def _empty_texts_flow():
    """A shop saved with every text blank must still answer every screen:
    builtin defaults fill in, and no empty message text ever goes out
    (Telegram rejects those, which surfaces as total bot silence)."""
    import rwa_plugin_pershop.crypto as crypto_mod
    import rwa_plugin_pershop.service as svc
    import rwa_plugin_pershop.telegram as tgmod
    from rwa_plugin_pershop.lang import LOCALES
    from rwa_plugin_pershop.routes_telegram import handle_update

    async def _fake_dec(_c, _e):
        return "DUMMY"

    crypto_mod.decrypt_token = _fake_dec
    tgmod.TgClient = FakeTg
    db = FakeDB()
    ctx = FakeCtx(db)
    sid, _legacy = make_shop(db)
    tid, pid = make_stepped(db, sid)
    shop = db.shops[sid]
    shop["lang"] = "fa"
    for k in ("card_text", "welcome_text", "thank_you_text", "approved_text",
              "denied_text", "referral_text", "support_text"):
        shop[k] = ""
    shop["referral_percent"] = 0
    panel = FakePanel()
    svc._panel_client = lambda: panel
    FakeTg.instances.clear()

    async def txt(text, uid=111, un="alice"):
        await handle_update(ctx, shop, {"update_id": 500, "message": {
            "message_id": 9, "from": {"id": uid, "username": un},
            "chat": {"id": uid}, "text": text}})

    async def cb(data, cid, uid=111, un="alice"):
        await handle_update(ctx, shop, {"update_id": 501, "callback_query": {
            "id": cid, "from": {"id": uid, "username": un}, "data": data}})

    fa = LOCALES["fa"]
    await txt("/start")
    menu_mid = 1000 + FakeTg._mid
    assert db.last_mids.get((sid, 111)) == menu_mid, db.last_mids
    await txt(fa["buy_subs_btn"])
    await cb(f"tiers:{tid}", "e1")
    await cb(f"buy:{pid}", "e2")
    await cb("hnext", "e3")
    await cb("stconfirm", "e4")
    await txt("receipt-empty-test")
    o = max(db.orders.values(), key=lambda x: x["id"])
    assert o["buyer_tg_id"] == 111 and o["status"] == "pending", o
    await cb(f"approve:{o['id']}", "e5", uid=999, un="owner")
    assert db.orders[o["id"]]["status"] == "approved", db.orders[o["id"]]
    bad = [(m, (p.get("text") or "")[:40]) for inst in FakeTg.instances
           for m, p in inst.sent
           if m in ("sendMessage", "editMessageText") and not (p.get("text") or "").strip()]
    assert not bad, bad
    print("EMPTY TEXTS FLOW PASSED")


def test_empty_texts_flow():
    asyncio.run(_empty_texts_flow())


async def _promo_campaign_flow():
    """Promos + the referral first-order campaign: create/validate/burn.

    Service-level — the repo's FakeDB models exactly the SQL the plugin
    runs, so this pins the promo engine end to end without Telegram.
    """
    import json as pj

    from rwa_plugin_pershop import service as svc

    db = FakeDB()
    ctx = FakeCtx(db)
    sid, pid = make_shop(db)
    db.shops[sid]["campaign_percent"] = 15  # owner wrote the campaign percent
    shop = db.shops[sid]
    buyer = 4242

    # referral row exists (buyer came through an invite link), no prior order
    db.referrals.add((sid, 777, buyer))

    # -- campaign: invitee's first order gets 15% off
    cut = await svc.campaign_units_for(ctx, shop, buyer, 200.0)
    assert cut == 30.0, cut
    # a buyer with a completed order gets nothing
    db.orders[1] = {"id": 1, "shop_id": sid, "buyer_tg_id": 4242, "status": "approved"}
    assert await svc.campaign_units_for(ctx, shop, buyer, 200.0) == 0.0
    db.orders[1]["buyer_tg_id"] = 9999  # other buyer again → eligible
    # campaign off (owner never wrote a percent) → nothing
    off = dict(shop, campaign_percent=0)
    assert await svc.campaign_units_for(ctx, off, buyer, 200.0) == 0.0

    # -- promo create: validation errors and success
    try:
        await svc.create_promo(ctx, sid, code="", kind="percent", value=10)
        raise SystemExit("empty code accepted")
    except ValueError:
        pass
    try:
        await svc.create_promo(ctx, sid, code="X", kind="percent", value=150)
        raise SystemExit("percent > 100 accepted")
    except ValueError:
        pass
    promo = await svc.create_promo(ctx, sid, code="save10", kind="percent",
                                   value=10, max_uses=2, created_by_owner="admin1")
    assert promo["code"] == "SAVE10" and promo["active"] is True
    try:
        await svc.create_promo(ctx, sid, code="SAVE10", kind="units", value=5)
        raise SystemExit("duplicate accepted")
    except ValueError as e:
        assert "already" in str(e)

    # -- validate: active + unused for this buyer
    got = await svc.promo_validate(ctx, sid, " save10 ", buyer)
    assert got and got["id"] == promo["id"]
    assert await svc.promo_validate(ctx, sid, "NOPE", buyer) is None

    # -- checkout: promo 10% + campaign 15% stack off a 200-order
    cd = await svc.checkout_discount(ctx, shop, buyer, 200.0, promo)
    assert cd["promo_units"] == 20.0 and cd["campaign_units"] == 30.0, cd
    assert cd["discount_units"] == 50.0
    # per-buyer single use: burned promo stops applying for that buyer
    order = {"id": 2, "shop_id": sid, "buyer_tg_id": buyer,
             "options_snapshot": pj.dumps({"promo_code": "SAVE10"})}
    await svc.count_promo_use(ctx, order)
    assert (promo["id"], buyer) in db.promo_uses
    db.orders[2] = dict(order, status="approved")
    await svc.count_promo_use(ctx, dict(order))  # idempotent burn (no dup)
    assert len([u for u in db.promo_uses if u[0] == promo["id"]]) == 1
    assert await svc.promo_validate(ctx, sid, "SAVE10", buyer) is None
    # per-buyer burn doesn't stop other buyers (2-use cap)
    got2 = await svc.promo_validate(ctx, sid, "SAVE10", 5151)
    assert got2

    # -- max uses: burn two buyers, third is refused
    await svc.count_promo_use(ctx, dict(order, buyer_tg_id=5151,
                                       options_snapshot=pj.dumps({"promo_code": "SAVE10"})))
    assert await svc.promo_validate(ctx, sid, "SAVE10", 5555) is None

    # -- admin listing shape
    rows = await svc.list_promos_with_uses(ctx, sid)
    assert len(rows) == 1 and rows[0]["uses"] == 2, rows

    # -- expired promo never validates
    from datetime import datetime, timezone

    old = dict(promo, id=db._next("promo"), valid_until=datetime(2020, 1, 1, tzinfo=timezone.utc))
    db.promos[old["id"]] = old
    assert await svc.promo_validate(ctx, sid, old["code"], buyer) is None

    print("PROMO CAMPAIGN FLOW PASSED")


def test_promo_campaign_flow():
    asyncio.run(_promo_campaign_flow())


async def _buyer_referral_flow():
    import rwa_plugin_pershop.service as svc
    from rwa_plugin_pershop.service import (add_credit, buyer_detail,
                                            credit_balance, referral_pairs)

    db = FakeDB()
    ctx = FakeCtx(db)
    sid, pid = make_shop(db)
    shop = {"id": sid, "lang": "en", "referral_percent": 10, "campaign_percent": 5}
    db.buyers[(sid, 222)] = "fa"
    db.referrals.add((sid, 111, 222))
    ins = ("INSERT INTO plugin_pershop_orders(shop_id,package_id,buyer_tg_id,"
           "buyer_username,receipt_file_id,receipt_kind,renew_username,"
           "total_units,options_snapshot) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)"
           " RETURNING id")
    await db.fetchrow(ins, sid, pid, 222, "invited-one", "f", "photo", None,
                      5.0, "{}")
    db.orders[1]["status"] = "approved"
    db.orders[1]["credit_units"] = 2.5

    res = await referral_pairs(ctx, shop)
    assert res["invited"] == 1 and len(res["pairs"]) == 1, res
    p = res["pairs"][0]
    assert p["referrer"] == 111 and p["invited"] == 222, p
    assert p["buys"] == 1 and float(p["earned"]) == 2.5, p
    assert float(res["earned_units"]) == 2.5 and res["referral_percent"] == 10, res

    det = await buyer_detail(ctx, shop, 222)
    assert det["tg_id"] == 222 and det["lang"] == "fa", det
    assert len(det["orders"]) == 1 and det["orders"][0]["status"] == "approved", det
    assert det["orders"][0]["credit_used"] == 0 and det["subs"] == [], det
    assert det["flag"] in (None, {}), det

    await add_credit(ctx, sid, 222, 3.0)
    assert await credit_balance(ctx, sid, 222) == 3.0, db.credits
    det = await buyer_detail(ctx, shop, 222)
    assert det["credit_units"] == 3.0, det

    det2 = await buyer_detail(ctx, shop, 999999)
    assert det2["orders"] == [] and det2["subs"] == [] and det2["credit_units"] == 0.0, det2

    print("BUYER REFERRAL FLOW PASSED")


def test_buyer_referral_flow():
    asyncio.run(_buyer_referral_flow())


if __name__ == "__main__":
    test_naming()
    test_templates()
    test_lang()
    test_relatives()
    test_hwid_pricing()
    test_squad_listing()
    test_create_shapes()
    test_integration()
    test_step_flow()
    test_renew_tier_flow()
    test_match_topup_flow()
    test_webhook_flow()
    test_internal_tiers_flow()
    test_effects_flow()
    test_menu_flow()
    test_referral_cards_flow()
    test_tier_squad_flow()
    test_link_flow()
    test_account_cancel_flow()
    test_mixed_withdraw_flow()
    test_buyer_lang_flow()
    test_account_hub_flow()
    test_broadcast_flow()
    test_nag_flow()
    test_followup6_flow()
    test_scope_flow()
    test_scam_flow()
    test_service_flow()
    test_public_api_flow()
    test_pub_users_flow()
    test_global_test_flow()
    test_stats_flow()
    test_credit_flow()
    test_credit_auto_flow()
    test_edit_place_flow()
    test_shop_admin_flow()
    test_admin_preview_flow()
    test_empty_texts_flow()
    test_promo_campaign_flow()
    test_buyer_referral_flow()
