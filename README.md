# PerShop — Telegram Shop Plugin for remnawave-admin

PerShop (`rwa-plugin-pershop`) gives each panel admin an independent Telegram storefront. Each admin connects a personal Telegram bot, builds a catalog of VPN plans (`tiers` → `packages`) and non-VPN `services`, and sells through a receipt-based approve/deny flow. Every sold account is a standard panel user stamped with the owner's `created_by_admin_id`.

One admin = one shop = one bot. The superadmin installs and configures the plugin; each admin manages only their own shop.

---

## 1. Architecture and constraints

- **In-process panel plugin (Plugin API v1).** Manifest: `id="pershop"`, `name="PerShop"`, `version` from installed wheel (`rwa-plugin-pershop`), `api_version=1`, `billing="free"`.
- **RBAC.** `pershop` (`view` / `edit` / `moderate`) for shop owners; `pershop_system` (`manage`, superadmin only) unlocks Plugin Settings and all-shops overview.
- **Single-route console.** `/plugins/pershop` (panel 5.x `PluginUI(kind="module", path="/app")`). Sub-paths fall through; no separate URLs.
- **Panel-facing only.** Reads via `ctx.db`; writes via `panel_api()` facade (`create_user`, `update_user`, etc.). No direct Remnawave core calls.
- **Bot transport.** `POST /api/v2/plugins/pershop/tg/{shop_id}`; auth via `X-Telegram-Bot-Api-Secret-Token` vs `shops.webhook_secret`. Polling fallback (`polling_enabled`) every 15 s when no public HTTPS endpoint.
- **Scheduled tasks.** `pershop-notifier` (900 s: expiry/traffic reminders + pending nudge), `pershop-broadcast` (30 s: fan-out ~25 recipients at a time), `pershop-poller` (15 s: polling only).
- **Database.** `pershop_001` (base schema: shops, tiers, packages, orders, subs, buyers, referrals, promotions, service categories/services, sessions, test claims, broadcasts, scammers). `pershop_002` (updates). `pershop_003` (`plugin_pershop_events`: append-only feed of shop moments — orders, approvals, denials, links, promos, device resets/reissues, broadcasts). Never edit frozen revisions; new DDL = `pershop_004`+.

---

## 2. Installation

1. Build locally: `rm -rf dist build *.egg-info && pip wheel . --no-deps -w dist_local/`.
2. Upload `dist_local/*.whl` in panel → Plugins; restart panel.
3. Distribution must stay `rwa-plugin-pershop` (panel derives plugin id from it); install with `--no-deps`.

---

## 3. Superadmin settings (`pershop_system:manage`)

`GET / PUT /api/v2/plugins/pershop/plugin-settings`.

| Field | Meaning |
|---|---|
| `polling_enabled` | Webhook default; enable only when no public HTTPS endpoint reachable by Telegram. |
| `public_base_url` | Origin for webhook registration (`https://...`). |
| `tutorial_text` | Global buyer-facing "How to Use" body. |
| `test_duration_days` (1–365, def 1) | Trial lifetime. |
| `test_traffic_gb` (0–100000, def 1) | Trial cap (`0` = unlimited). |
| `test_squad_uuid` | Squad for trial accounts (`active_internal_squads`). |
| `test_hwid` | Device limit stamped on trials (blank = panel default). |

Returns `version` (wheel version) + `counts: {shops, pending_orders, subs}`.

**Danger zone (superadmin only):** `DELETE /admin/reset` drops every `plugin_pershop_*` table + unstamps migrations; sold panel accounts untouched.

---

## 4. Shop setup (`pershop:view` / `edit`)

`GET / PUT /api/v2/plugins/pershop/shops/mine` (owner-scoped). One admin = one shop.

1. **Bot token** (`bot_token_enc`, Fernet-encrypted); owner Telegram id; **prefix** (globally unique, first half of sold username).
2. **Language** (`en` / `fa` / `ru` / `zh`): owner DM language; buyers choose independently (`plugin_pershop_buyers.lang`).
3. **Pricing display:** `currency_label` (e.g. `تومان`) + `unit_rate` (local per unit). Empty = raw `2.5u`.
4. **Catalog grouping:** `tiers_internal` (`bool`, default `false`). When `true`, buyers skip tier buttons and see one flat package list sorted by `sort`; tiers stay internal.
5. **Texts (buyer-facing):** `welcome_text` (`{shop}`), `thank_you_text` (`{order}`), `approved_text` (`{username} {dot} {status} {plan} {price} {when} {traffic} {hwid} {url}`; legacy `{expire}` accepted), `denied_text` (`{reason} {username} {plan}`), `referral_text` (`{percent} {link}`), `support_text`. Each field has a **↺ Default** button that refetches `GET /shops/default-texts?lang=xx` in the currently selected form language (`lang`); empty fields fall back automatically, except card details and support (button hidden until set).
6. **Promo codes:** `promo_btn`, `promo_applied`, `promo_invalid`, `promo_line`, `promo_owner_line`, `promo_remove_btn`, `promo_title`, `promo_prompt`. Buyers enter codes at checkout; owner manages via panel (`/promos`).
7. **Referral / invitee discount:** `referral_percent` (0–100, default 0); `campaign_percent` (0–100, default 0). `campaign_line`: first-order discount applied automatically when a buyer uses an invite link (`https://t.me/<bot>?start=r_<id>`); no code needed. Balance-funded portions earn no referral reward.
8. **Device actions (sub cards):** `devreset_btn` (`📱`), `devreset_warn`, `devreset_go`, `devreset_done`, `devreset_fail`; `reissue_btn` (`♻️`), `reissue_warn`, `reissue_go`, `reissue_done`, `reissue_fail`.
9. **Pending nudge:** `pending_nag_hours` (0–168, default 6). Once per window the owner gets one summary DM instead of per-order repeats until decided.
10. **Save.** Never requires a token: settings split into three dedicated buttons (`save_conn` connects/updates bot + webhook; `save_price` saves currency/pricing/test options/nudge; `save_text` saves all texts + `lang`). `lang` included in every patch so Persian (`fa`) sticks (`بازنشانی` preserved in `lang.py`, never overwritten).

Related endpoints: `GET /shops/mine/apikey`, `POST /shops/mine/apikey/rotate`, `DELETE /shops/mine/apikey`, `GET /shops/all` (superadmin, tokens stripped).

---

## 5. Catalog: tiers → packages, categories → services

### 5.1 Tiers (`GET / POST /tiers`, `PUT / DELETE /tiers/{id}`)

Internal squad mapping + device default. `name` (1–120, unique per shop), `squad_uuid`, `hwid_default`, `sort`, `active`. `tiers_internal`: buyers see flat list.

### 5.2 Packages (`GET / POST /packages`, `PUT / DELETE /packages/{id}`)

Predefined VPN plans. `name` (1–120), `tier_id` (required), `traffic_limit_bytes` (`null` = unlimited), `duration_days` (`0` = timeless, year 2999), `price_units` (≥ 0), `effect`: `full` (plan) / `time_only` (+days top-up) / `traffic_only` (+GB top-up; requires cap), `traffic_reset` (`true` = discard leftover; `false` = roll over + cap), `hwid_mode`: `default` / `fixed` (`hwid_fixed`) / `input` (`hwid_min`/`max`/`default`/`free`/`percent`) / `unlimited`, `scope_tier_id` (top-up visibility), `squad_uuid` (legacy fallback), `sort`, `active`.

Deleting a package referenced by orders/subs is refused (409).

### 5.3 Other Services (`GET / POST /service-cats`, `PUT / DELETE /service-cats/{id}`; `GET / POST /services`, `PUT / DELETE /services/{svc_id}`)

Non-VPN sellables: `name`, `price_units`, `description`, `done_text`, `cat_id`, `sort`, `active`. No duration/traffic/device/squad/effect. Deleting category moves items to buyer's "Other" bucket; deleting service with orders refused (409).

---

## 6. Buyer experience (Telegram bot)

**Main menu (persistent reply keyboard, 2 buttons/row):** Buy Subscription · Test Subscription (if enabled) · My Subscriptions · Other Services (if any) · Referral Program (if `referral_percent` > 0 or text set) · How to Use (if `tutorial_text`) · Request Support (if `support_text`) · My account (last). Taps leave screen; submitted orders unaffected.

- **Buy Subscription** → tiers (or flat list when `tiers_internal`) → package → optional device counter (`hwid_input`) → **contract screen** (plan, exact date/cap, leftover-traffic treatment, converted price, card details wrapped in `<code>` automatically, `promo_btn` available) → optional credit spend (`Use credit` button shown when buyer holds non-withdrawable balance; balances display as converted money only) → Pay. If balance covers full price: auto-approved (`credit_units` recorded in snapshot); buyer gets credentials immediately; owner gets read-only note. Otherwise buyer sends receipt (photo/file/text); forwarded to owner with **Approve / Deny**; denial collects typed reason (cancellable), then offers report-scammer (`scammers`). Approval provisions account (`prefix-tgusername[…n]`); renewals extend existing user. Service purchases: optional buyer note (`Skip`), approval delivers `done_text`.
- **Promo codes (`promo`).** Buyer enters at checkout; owner manages `promo` rows (`code`, `percent`/`units`, `max_uses`, `valid_until`). Lines: `promo_line` (buyer-facing discount), `promo_owner_line` (owner card), `promo_applied`, `promo_invalid`, `promo_remove_btn`.
- **Invitee discount (`campaign_percent`).** Applied automatically on first purchase via invite link (`start=r_…`); line shown: `campaign_line`. No manual code needed.
- **My Subscriptions — status dashboard.** States: 🟢 healthy · 🟡 running out (≤3 days or traffic ≥80%) · 🔴 expired/overdue (traffic ≥100%) · ♾️ lifetime (`duration_days=0`, rendered as "never expires", year 2999) · ⏳ waiting for admin. Tapping username: live status, exact date beside relative word, traffic used/of, valid actions (Quick renew, Change plan, Add traffic, Add days). Fully lifetime = no actions; linked pre-shop subs show system values.
- **Link subscription.** Eligibility: `creator_id` = owner id + short UUID matches; creator-less + matching short UUID links only to superadmin shop; else refused (`404`). Once linked, subscription joins notifier/renewals/My Subs; renewal uses shop packages.
- **Pending state.** After receipt: buttons disappear, ⏳ shown, "receipt received". Second receipt discarded (no duplicate). Reminders silent until decided.
- **Withdraw.** Only undecided orders. Marks `withdraw_requested`; owner's previous buttons replaced with single **Process cancellation** card. Process: **Approve cancellation / Deny (fake receipt)**. Approving shows buyer's bank details; owner sends refund receipt, bot forwards to buyer, settles `withdrawn`. Denying runs standard denial then offers scam report. Mixed payment: credit returns immediately at submit; transfer portion refunded manually; owner's card records only transfer portion outstanding.
- **Test Subscription.** If `test_enabled`: one confirmation creates `prefix-Test-N` account (`trial` package); one per person panel-wide (`test_claims`). Size/squad/device from superadmin settings. Informational in My Subs; no renewals/top-ups.
- **Referral Program.** `referral_percent` (reward %). Deep-link `?start=r_...`; `getMe` re-fetches bot username if missing and caches. Owner text: `{percent}`, `{link}`. Empty text falls back to built-in pitch with invite link auto-appended. Inviter gets DM per reward; `referral_earned` in stats. No reward for denied/withdrawn orders. Credit refund on deny/withdraw. Full-credit purchase can be self-cancelled by buyer: panel account disabled, units returned, owner read-only note.
- **My account (last).** Last 10 orders (human-readable dates, converted prices, localized states), language chooser (`en`/`fa`/`ru`/`zh`), credit balance line (`buyer` table). All bot messages from that point use buyer's language; owner DMs use shop default (`_buyer_lang` stripped via `_owner_shop`).
- **Dates.** Relative words ("in 5 days", "tomorrow", "3 days ago") + exact `YYYY-MM-DD`. Year 2999 = "never expires".
- **Mixed payments.** Pending + credit = withdraw as any other (credit back immediately, transfer refunded manually). Approved + mixed = note, no withdraw path (nothing pending).
- **Service categories.** Plain shelves (`service-cats`) analogous to tiers; delete moves items to "Other".

---

## 7. Renewal, legacy subscriptions, and plan matching

- **Quick renew** extends matched plan; time stacks onto current expiry (expired subs count from today); traffic follows `traffic_reset`; timeless (`duration_days=0`) keeps 2999, renews traffic-only.
- **Change plan** re-selects tier → package through contract screen; squad moves only on tier change; only `full` plans.
- **Top-ups** (`time_only` / `traffic_only`) adjust one resource; reset only that resource's reminder flag (`plugin_pershop_subs`).
- **Linking pre-shop subs.** Buyers attach via My Subs → Link (`plugin_pershop_subs` by `username` or `user_uuid`); linked rows show system values; renewing uses shop packages.
- **Plan matching (Shops tab).** `GET /unmapped`: owner's panel users without subscription row + sub rows with no plan. `POST /subs/assign-plan {user_uuid, package_id}` attaches (`full` only). Buyer self-assignment unavailable; buyer linked = plan assignment announced by DM.

---

## 8. Owner operations (Telegram DM + Orders tab)

- **Order DM (`🧾`).** `new`/`renew`/`service` with buyer, item, converted price, traffic/time lines. Receipt forwarded with **Approve / Deny**. Denial collects typed reason (cancellable), then offers scam report (`scammers`). Withdraw: card with reason + refund details + **Process cancellation**. Process: approve (show bank details, wait for refund receipt, settle `withdrawn`) / deny (standard denial + scam offer). Mixed: credit back at submit; only transfer portion recorded as outstanding.
- **Stale receipts.** Once per `pending_nag_hours` window: one summary DM (not per-order repeats) until everything decided.
- **Orders tab (`GET /orders?status=`).** `pending` (includes `withdraw_requested`, decided in DM — no panel buttons), `approved`, `denied`, `withdrawn`, `cancelled`, `all`. Plain pending rows have inline approve/deny; every row resolves item name (packages or services) and flags scammers (🚫). Capped 200, newest first.
- **Stats (`GET /stats`).** `subs_active`, `orders_pending`, `orders_approved`, `units_sold` (`total_units` of approved orders), `orders_denied`, `orders_withdrawn`, `tests_claimed`, `subs_linked`, `subs_manual` (key-created, no plan yet), `referral_earned` (program payouts). Counts only.
- **Broadcast (`POST /broadcast`, `GET /broadcasts`).** Message in buyers' language, Telegram HTML (`fmtBar`: bold/italic/underline/strike/code/links; `varBar`: variables `{username}`, `{price}`, etc.), optional photo ≤ 5 MB, text ≤ 4000 bytes. Returns `{id, recipients}`; 30 s tick fans out ~25 at a time. Last 10 broadcasts: `{id, total, done, failed, status, created_at}` (`queued → sending → done`). Recipients: all seen buyers, linked users, language choosers, referred users.
- **Scammer network.** Report offered only after denial with reason (`scammers` table, panel-wide). Flagged buyer shows owner who flagged + order. New purchase intent in any other shop triggers at most one DM per buyer per shop per 24h. `DELETE /scammers/{tg_id}` lifts flag; `GET /buyers` shows balance, language, sub count, flag.
- **Health + KPIs.** Dashboard: `stats` grid (KPIs), health chip (`🟢` connected / `🔴` stuck / `🟡` stale orders), quick-action buttons (`orders`, `promos`, `packages`, `marketing`, `buyers`), recent activity (`events` feed from `pershop_003`), all-shops card (superadmin).

---

## 9. Automatic reminders (notifier, 15 min / 900 s)

Fires once per sub; re-arms when resource resets on renewal/top-up. Buyer-less matched rows skipped; pending silent; self-cancelled removed entirely; timeless (`duration_days=0`) never warned.

| Event | Condition | Button / action |
|---|---|---|
| 80% traffic | `used / cap ≥ 0.8`, cap exists | Add traffic (if matching offer exists) |
| Out of traffic | `used / cap ≥ 1.0` | Add-traffic offer, else Quick renew |
| 72 h pre-expiry | `0 < time-left ≤ 72 h`, year < 2999 | Quick renew |
| Expired | past expiry, year < 2999 | Reactivate (quick renew) |

Button taps rewrite the current bot message in place (no new message). Consumed messages (`/start`, `/cancel`, links, reasons, card details, service notes) deleted after reading. Receipt message is never edited/deleted — it stays as the order anchor. Approval rewrites both buyer waiting message and owner's decision card.

---

## 10. External API (per-shop key `X-Shop-Key`)

Base: `https://<panel-host>/api/v2/plugins/pershop`. Auth: SHA-256 hash lookup, constant time; missing = `401 invalid key`. No admin session required.

### 10.1 Catalog (`GET /pub/catalog`)

`{"shop": {id, prefix, bot_username, lang, currency_label, unit_rate, welcome_text, referral_text, support_text}, "trial": {enabled, duration_days, traffic_gb}, "tutorial_text", "tiers": […], "packages": […], "service_categories": […], "services": […]}`. Packages: full fields (`price_display` converted). Services: `id, name, cat_id, cat_name, description, price_units, price_display, currency_label`. Active rows only. No tokens, secrets, admin ids, orders, subs exposed.

### 10.2 User management (`GET /pub/users`, `GET /pub/users/{uuid}`, `POST /pub/users`, `PATCH /pub/users/{uuid}`)

Full CRUD on that shop's users, bounded by owner's RBAC. `username` must start with `<prefix>-`. `expire_at` required (naive = UTC). `traffic_limit_bytes` ≥ 0; `hwid_device_limit` int; `telegram_id` int; `squad_uuid` must be in owner's scope. `PATCH`: only non-null fields change; `squad_uuid: ""` clears squad; `status`: `active`/`disabled`. `GET /pub/users` clamped 1–200.

Every created user is stamped `created_by_admin_id` (owner id) + receives a plan-less subscription row (`subs_manual`). Errors: `400` (bad input / nothing to update), `404` (foreign/missing), `422` (quota/panel refusal).

### 10.3 Panel routes (`pershop` permission)

See table in section 5–8; includes health (`hcheck`), KPI (`/stats`), buyer lookup (`/buyers`), broadcast (`/broadcast`, `/broadcasts`), promo (`/promos`), referral (`/buy` with `start=r_`), events (`pershop_003` feed), sub link retrieval (`GET /subs` / `PUT` by username), plan matching (`/unmapped`, `/subs/assign-plan`), external key lifecycle (`/shops/mine/apikey`), disconnect/rotate/re-register, superadmin overview (`/shops/all`, `/plugin-settings`), reset (`DELETE /admin/reset`).

---

## 11. Data model (`pershop_001`, `pershop_002`, `pershop_003`)

`plugin_pershop_shops` (`owner_admin` UNIQUE, encrypted token, `admin_tg_id`, `prefix` UNIQUE, `lang`, texts, `currency_label`, `unit_rate`, `test_enabled`, `pending_nag_hours`, `referral_percent`, `campaign_percent`, `tutorial_text`, `tutorial_buttons`, `tiers_internal`) → `plugin_pershop_tiers` → `plugin_pershop_packages` → `plugin_pershop_service_cats` / `services` → `plugin_pershop_orders` (one of `package_id` / `service_id`; `renew_username`; `total_units`; snapshot; `credit_units`; `status`: `pending` / `approved` / `denied` / `cancelled` / `withdrawn` / `withdraw_requested`) → `plugin_pershop_subs` (`buyer_tg_id`, `user_uuid` `TEXT`, `username` UNIQUE, `package_id`, expiry, cap, `test`/`linked`/`cancelled` flags, 4 reminder flags). Plus `plugin_pershop_promos`, `plugin_pershop_buyers`, `plugin_pershop_referrals`, `plugin_pershop_scammers`, `plugin_pershop_test_claims`, `plugin_pershop_broadcasts`, `plugin_pershop_events` (`pershop_003`: `kind`: `order_new`, `order_credit`, `approved`, `denied`, `linked`, `promo`, `broadcast`, `devreset`, `reissue`, `credit`). Plugin tables reference panel `users` by id only; accounts live in `users`, stamped with owner's `created_by_admin_id`.

---

## 12. Non-goals

- Refunds not executed through bot; bank transfers reversed manually.
- No buyer self-service plan mapping.
- Orders do not expire automatically.
- No per-buyer bans; fraud handled via panel-wide `scammers` flag.
- Broadcasts single-language, composed by owner.
- Statistics limited to overview strip.
- Receipt verification manual; each order DM shows buyer + plan + price + receipt together.

---

## 13. Development

- `rwa_plugin_pershop/` structure: `routes_telegram.py` (bot/webhook/update handler; keyboard `_back_home_row` unpacked with `*`; emoji picker with embedded `FULL_EMOJIS` array + `emoji-picker-element` module import; sub card URL retrieval via `plugin_pershop_subs.user_uuid` with `::text` cast; trial package `"trial"`), `routes_admin.py` (admin CRUD; `ShopIn` includes `campaign_percent`, `tiers_internal`, `pending_nag_hours`, `referral_percent`; `PromoIn`, `SvcIn`; `PubUserCreate`), `service.py`, `telegram.py`, `crypto.py`, `naming.py`, `tasks.py` (`broadcast_tick`, `notifier_tick`, `poller_tick`), `lang.py` (`fa`: `بازنشانی`; never overwritten, keys added only), `migrations/versions/pershop_001/002/003.py`, `tests/test_shop.py` (36 flows).
- Console: `node --check rwa_plugin_pershop/static/app.js`; `static/app.js` contains `FULL_EMOJIS`, `emojiPicker()` (body-appended `fixed` dropdown, `zIndex: 99999`, `overflow-x-visible`), `fmtBar()` (formatting toolbar: `b`/`i`/`u`/`s`/`code`/`link`), `varBar()` (variables: `{username}`, `{price}`, `{plan}`, etc.), split saves (`save_conn`/`save_price`/`save_text`), `openModal()` (glass card UI).
- Tests: `python3 tests/test_shop.py` — 36 flows (fake panel facade, Telegram, DB — buy/renew/top-up/link/withdraw/broadcast/scammer/services/promo/invitee/referral/public-API).
- Build: `rm -rf dist build *.egg-info && pip wheel . --no-deps -w dist_local/`. No manual uploads.
