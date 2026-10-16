# PerShop — Telegram Shop Plugin for remnawave-admin

PerShop (`rwa-plugin-pershop`) gives each panel admin an independent Telegram
storefront. Each admin connects a personal Telegram bot, builds a catalog of
VPN plans and non-VPN services, and sells through a receipt-based approve/deny
flow. Every account sold is created as a standard panel user, stamped with
the selling admin's creator id.

One admin operates one shop through one bot. The superadmin installs and
configures the plugin; each admin manages only their own shop.

---

## 1. Architecture and constraints

- **In-process panel plugin (Plugin API v1).** The manifest declares
  `id="pershop"`, `name="PerShop"`, `api_version=1`, `billing="free"`, with
  RBAC resources `pershop` (`view` / `edit` / `moderate`) and
  `pershop_system` (`manage`, superadmin only). The management console is a
  single frontend page at `/plugins/pershop`. Panel 5.x or newer is required
  (panel 5.x mounts wheel UI on the single route `/plugins/:pluginId`).
- **Access model.** Any admin role granted `pershop:view` / `pershop:edit` /
  `pershop:moderate` can create and run exactly one shop: the first save under
  Shops creates the caller's shop, later saves edit it. Every shop is
  owner-scoped — tiers, packages, services, orders, subs, and buyers are
  invisible to other shops. `pershop_system:manage` (superadmin only)
  additionally unlocks the Plugin Settings tab and the all-shops overview; it
  does not grant anyone else's shop data beyond that overview.
- **Panel-facing only.** All panel reads go through `ctx.db`; all account
  writes go through the in-process `panel_api()` facade (`create_user` /
  `update_user` / `extend_user_expiration` / `delete_user` /
  `get_internal_squads`). The plugin never communicates with Remnawave core
  directly; no core URL or token exists in the shop code.
- **Bot transport.** Each shop bot delivers updates to
  `POST /api/v2/plugins/pershop/tg/{shop_id}`. Telegram signs each call with
  `X-Telegram-Bot-Api-Secret-Token`, verified against the shop's stored
  `webhook_secret`; mismatches are rejected. Where the panel has no public
  HTTPS endpoint, a 15-second long-poll fallback serves all shops while
  `polling_enabled` is on.
- **Background tasks.** `pershop-notifier` every 900 s (expiry/traffic
  reminders and pending-receipt nudges), `pershop-broadcast` every 30 s
  (message fan-out), `pershop-poller` every 15 s (active only in polling
  mode).
- **Database.** A single squashed migration, `pershop_001`, on Alembic branch
  `plugin_pershop` (see Data model). Post-release schema changes must ship as
  new revisions (`pershop_002`, …); `pershop_001` must not be edited after
  installation.

---

## 2. Installation

1. Build the wheel (the panel ships every dependency — install with
   `--no-deps`): `pip wheel . --no-deps -w dist`
2. In the panel: Plugins → Upload wheel → restart the panel.
3. Continue with Superadmin settings (once) and Shop setup (each shop owner).

The distribution name must stay `rwa-plugin-pershop` (the panel derives the
plugin id from it).

---

## 3. Superadmin settings (Plugin Settings tab)

`GET / PUT /api/v2/plugins/pershop/plugin-settings`
(`pershop_system:manage` — superadmin only).

| Field | Type / limits | Meaning |
|---|---|---|
| `polling_enabled` | bool, default `false` | Webhook mode is the default. Enable only when the panel has no public HTTPS endpoint reachable by Telegram; all shop bots then switch to long-polling. |
| `public_base_url` | string, e.g. `https://panel.example.com` | Public origin of the panel. Required for webhook mode: the plugin registers `{base}/api/v2/plugins/pershop/tg/{shop_id}` on each bot via `setWebhook` (must be a real `https://` URL). |
| `tutorial_text` | string | Global "How to Use" body shown in every shop bot. |
| `test_duration_days` | int 1–365, default `1` | Lifetime of every free test subscription, in days. |
| `test_traffic_gb` | int 0–100000, default `1` | Traffic cap of test subscriptions, in GB. `0` = no cap. |
| `test_squad_uuid` | string, default empty | Squad that test accounts join (`active_internal_squads`). Required on panels that only serve in-squad users; otherwise trials are created without squad membership. |
| `test_hwid` | int or null | Device limit stamped on test accounts. Blank = panel default. |

`GET` additionally returns `version` (wheel version) and
`counts: {shops, pending_orders, subs}`.

**Who can open this tab.** Only roles holding
`pershop_system:manage` — the superadmin role, not per-shop roles. Shop
owners with only `pershop` permissions see an access-denied notice here.
(A panel superadmin acting as a shop owner can additionally open a shop of
their own from the Shops tab.)

**Danger zone (superadmin only).** `DELETE /admin/reset` drops every
`plugin_pershop_*` table (schema and rows) and deletes every
`plugin_settings` key the plugin owns (shops, catalog, orders, subs,
referrals, buyer preferences, credit balances, scammer flags, test claims,
broadcasts). It also unstamps the plugin's migration revisions, so the next
panel restart recreates all tables empty via the normal migration run. Sold
panel accounts are never touched. The button asks for confirmation twice.

Currency is configured per shop (see Shop setup), not globally. Shops using
different currencies coexist on one panel.

---

## 4. Shop setup (Shops tab)

`GET / PUT /api/v2/plugins/pershop/shops/mine`
(`pershop:view` / `pershop:edit`, always scoped to the caller's own shop).

1. **Create a bot with BotFather** and copy its token. One bot serves the
   entire shop; each admin owns exactly one shop.
2. **Enter the token**, the owner's own numeric Telegram id (the destination
   for order DMs, withdraw cards, nudges, and scam warnings), and a
   **prefix** (letters/digits, globally unique — it becomes the first half
   of every sold username).
3. **Select the shop language** (`en` / `fa` / `ru` / `zh`). This sets the
   owner's language for admin DMs; buyers select their own language
   independently (see Buyer experience).
4. **Configure pricing display**: `currency_label` (e.g. `تومان`) and
   `unit_rate` (local currency per unit, e.g. `250000`). A package priced at
   `2.5` units then renders as `625,000 تومان` on buttons, contracts,
   receipt headers, and public API responses. With both fields empty, prices
   render as `2.5u`.
5. **Write the buyer-facing texts**: payment-card details, welcome
   (`{shop}`), thank-you (`{order}`), approved (`{username} {dot}
   {status} {plan} {price} {when} {traffic} {hwid} {url}`, legacy `{expire}`
   still accepted), denied (`{reason} {username} {plan}`), referral
   (`{percent} {link}`), service descriptions (`{price}`), completion
   texts (`{username}`). Every text has a **↺ Default** button restoring
   the built-in template in the shop language
   (`GET /shops/default-texts`). Empty fields fall back to those built-ins
   automatically.
6. **Save.** Saving never requires a bot token: texts, currency, catalog,
   and matching can be configured first and the bot connected later (or after
   a disconnect). When a token is supplied, it is stored Fernet-encrypted; the plugin generates the
   shop's `webhook_secret` and registers the webhook on the bot via
   `setWebhook` automatically — no manual webhook configuration is needed.
   The response reports the registration result. The **Connect bot** button
   (`POST /shops/mine/webhook`) re-registers on demand and reports live
   `getWebhookInfo` state (`url`, queued `pending` count, `last_error`).
   `POST /shops/mine/rotate-secret` rotates the webhook secret
   (re-register afterwards). The **Disconnect bot** button
   (`POST /shops/mine/disconnect`) calls `deleteWebhook` on Telegram's side
   and clears the stored token while keeping every other setting; a new token
   can be connected later. A shop without a token keeps all its data, but the
   bot sends and receives nothing: background tasks skip it and the webhook
   endpoint answers `{"ok": false}`.
7. **Pending-receipt nudge** (`pending_nag_hours`, 0–168, default 6): the
   maximum time a receipt may wait before the owner receives a Telegram
   reminder. `0` disables it. The reminder repeats once per window until the
   order is decided.

Related owner endpoints: `GET /shops/mine/apikey` (key value + endpoint
URL), `POST /shops/mine/apikey/rotate`, `DELETE /shops/mine/apikey` (see
External API), `GET /shops/all` (superadmin: every shop, tokens excluded).

---

## 5. The catalog: tiers → packages, categories → services

### 5.1 Tiers (VPN only)

`GET / POST /tiers`, `PUT / DELETE /tiers/{id}`.
A tier is an internal squad and the first step of every VPN purchase.

| Field | Meaning |
|---|---|
| `name` (1–120 chars, unique per shop) | Buyer-facing tier label. |
| `squad_uuid` | Internal squad, selected from a dropdown of squads visible to the admin (`GET /squads` is scope-filtered). Every account sold from this tier lands in this squad; the tier squad takes precedence over any package-level value. |
| `hwid_default` (optional int) | Fallback device limit, applied only when a package uses "panel default" mode and the panel assigns no value. |
| `sort`, `active` | Ordering (lower shows first, in the bot and in the panel list) and visibility. |

When the owner enables **Tiers are internal** (My Shop checkbox), buyers
skip the tier buttons and see every package as one flat list sorted by
`sort`; tiers stay in the panel for squads and grouping only.

Deleting a tier that still has packages is refused (409) — move or delete
the packages first.

### 5.2 Packages (VPN only — fully predefined)

`GET / POST /packages`, `PUT / DELETE /packages/{id}`. Buyers never select
traffic or expiry; the device count is the only buyer-configurable value,
and only when the owner enables it.

| Field | Type / rule | Meaning |
|---|---|---|
| `name` | 1–120 chars | Buyer-facing plan name. |
| `tier_id` | tier id, required | The tier (squad) this plan belongs to. Every package belongs to exactly one tier — a squad-less subscription gets no connection. Change it any time to move the package between tiers. |
| `traffic_limit_bytes` | int ≥ 0 or null | Fixed cap. Null/blank = unlimited. |
| `duration_days` | int 0–3650, default 30 | Fixed lifetime. **`0` = timeless**: accounts are written with expiry `2999-12-31`, rendered everywhere as "never expires". |
| `price_units` | number ≥ 0 | Price in shop units (converted for display per Shop setup, step 4). |
| `effect` | `full` / `time_only` / `traffic_only` | `full` = a purchasable plan (buy + renew). `time_only` = a "+days" top-up offer (moves the expiry date, traffic untouched). `traffic_only` = a "+GB" top-up offer (raises the cap, expiry untouched). Top-ups only extend existing subscriptions: they cannot be bought standalone or matched as plans, and they never move squads. `time_only` requires `duration_days > 0`; `traffic_only` requires a cap. |
| `traffic_reset` | bool, default `true` | Renewal math (see Renewal): `true` = leftover traffic is discarded (the cap becomes the package value); `false` = leftover traffic rolls over (package traffic is added on top, floored at 0; overuse clamps to the fresh cap). |
| `hwid_mode` | `default` / `fixed` / `input` / `unlimited` | `default` = the panel-assigned value (the tier `hwid_default` applies only if the owner set one). `fixed` = a silent admin value (`hwid_fixed` required). `input` = buyer-selected in the bot (−/+ counter between `hwid_min`/`hwid_max`, starting at `hwid_default`; the first `hwid_free` devices are free, each additional device costs `hwid_percent` % of the package price). `unlimited` = no device cap. |
| `scope_tier_id` | tier id or null | Top-up visibility: null = offered for every tier's subscriptions; set = offered only for subscriptions matched to that tier's plan. |
| `squad_uuid` | legacy fallback | Used only when the package's tier has no squad. Prefer the tier field. |
| `active` | bool | Inactive packages disappear from the bot but keep their history. |

Deleting a package referenced by orders or subscriptions is refused (409).

### 5.3 Other Services (non-VPN sellables)

`GET / POST /service-cats`, `PUT / DELETE /service-cats/{id}`;
`GET / POST /services`, `PUT / DELETE /services/{svc_id}`.
A service carries no duration, traffic, device, squad, or effect data — only
a name, a unit price, a description (shown before purchase), and a
completion text (sent on approval), filed under plain category shelves
analogous to tiers. Deleting a category moves its items to the buyer's
"Other" bucket; deleting a service with orders is refused (409). Services
follow the same receipt → approve / deny pipeline as packages (the scammer
report is offered only after a denial), except approval delivers the
completion text instead of creating a panel account — renewals, reminders,
and linking do not apply.

---

## 6. Buyer experience (Telegram bot)

**Main menu — a persistent bottom panel** (reply keyboard, two buttons per
row, buyer-language labels; taps are matched in all four locales, so
switching language mid-shop keeps working). Entries appear only when
configured, in this order: Buy Subscription · Test Subscription (if
enabled) · My Subscriptions · Other Services (if any) · Referral Program
(if text set) · How to Use (if tutorial set) · Request Support (if text
set) · **My account (always last)**. Tapping a panel button leaves the
current screen; submitted orders are unaffected. All screens below the menu
use inline buttons on their own message.

- **Buy Subscription** → tiers → packages (converted prices, sorted by
  Sort order; a single flat package list when tiers are internal) → optional
  device counter → **contract screen** (plan, exact resulting date/cap,
  leftover-traffic treatment, price, payment card) → optional **credit
  spend** ("Use credit" button, shown when the buyer holds a referral
  balance; balances display as converted money only) → Pay. When the
  balance covers the full price, the order is created already paid and
  **approved automatically** — no receipt and no owner decision; the buyer
  receives the credentials immediately and the owner receives a read-only
  note. Otherwise the buyer sends the receipt (photo, file, or text). The
  receipt is forwarded to the owner with **Approve / Deny** buttons; the
  report-scammer action is not part of this message. Approval provisions
  the account — new purchases receive `prefix-tgusername[2…n]`, renewals
  extend the existing user — and sends the buyer the login, expiry, and
  subscription link; denial relays the owner's reason, after which the
  owner is offered the report-scammer action for that order. Service
  purchases follow the same flow with an optional buyer note first
  (one-tap Skip); approval delivers the completion text.
- **My Subscriptions — a status dashboard.** Each row carries its state
  (🟢 healthy · 🟡 running out · 🔴 expired/overdue · ♾️ lifetime ·
  ⏳ waiting for admin). Tapping a username opens its detail: live status,
  human-readable expiry with the exact date beside it, traffic used/of,
  and the actions valid for that state — Quick renew (same plan, one tap
  to the contract), Change plan (re-selects tier → package; the squad
  moves only when the tier changes), Add traffic (capped subscriptions
  with a matching offer only), Add days (genuinely expiring
  subscriptions with a matching offer only). Fully lifetime subscriptions
  (no expiry, no cap) present no actions. A **Link my subscription** row
  accepts a pasted subscription link and attaches pre-shop accounts (see
  Renewal).
- **Pending state.** After a receipt is sent, payment buttons disappear and
  the subscription shows ⏳ with a "receipt received" notice. A second
  receipt while an order is pending is discarded with the same notice; no
  duplicate order is created. Reminders stay silent until the order is
  decided.
- **Withdraw** (detail → Withdraw receipt → reason → refund card/bank
  details). The first step discloses that the bot cannot reverse a sent
  transfer. Only undecided orders qualify; decided orders are refused.
  Submitting marks the order `withdraw_requested`: the owner's previous
  buttons are removed and replaced with a withdraw card carrying a single
  **Process cancellation** button. No action returns a cancellation
  request to pending. Process opens **Approve cancellation / Deny (fake
  receipt)**: approving displays the buyer's bank details and waits for
  the owner to send the refund receipt, which the bot forwards to the
  buyer before settling `withdrawn`; denying runs the standard denial
  flow and then offers the scammer report. **Mixed payment:** an order
  paid partly with credit and partly by transfer follows the same flow,
  except the credit portion returns to the buyer's balance immediately at
  submit time (recorded in the order snapshot, so later steps cannot pay
  it twice); the buyer is informed the owner refunds the remainder
  manually without the buyer needing to contact anyone, and the owner's
  card records that only the transfer portion is outstanding. An order
  the owner already approved cannot be withdrawn or cancelled.
- **Test Subscription** (if enabled): one confirmation creates a
  `prefix-Test-N` account immediately, **one per person across the whole
  panel** (enforced by a panel-global claim registry; a second shop
  refuses with an "already received" notice). No approval is involved.
  Size, squad, and device limit come from the superadmin settings. Trial
  subscriptions are informational in My Subs — no renewals or top-ups; a
  full plan is purchased through the Buy menu like any new subscription.
- **Referral Program** (if text set): shows the buyer's personal copyable
  `https://t.me/<bot>?start=r_<id>` link; deep-link starts are recorded
  per shop. If the stored bot username is missing (e.g. the shop was
  connected while the panel had no Telegram egress), it is re-fetched via
  `getMe` the first time a buyer opens this screen and cached. The owner
  text supports `{percent}` (reward %) and `{link}` (the invite URL). The owner
  sets a **reward %** on the Shops tab (`0` = off): every approved
  purchase by an invited buyer credits that percentage of the order's
  **cash-paid** units to the inviter's **non-withdrawable balance**,
  spendable on any package or service. Balance-funded portions earn no
  reward. Balances display as converted money only. The inviter receives
  a DM per reward; `referral_earned` in the owner stats totals program
  payouts. No reward is minted for denied/withdrawn orders. Credit spent
  by a buyer on an order is refunded automatically when that order is
  denied, or immediately at withdraw-submit time. A subscription bought
  entirely with credit can later be **cancelled by the buyer without
  owner involvement**: the panel account is disabled, the paid units
  return to the balance, and the owner receives a read-only note.
- **My account (last):** the last 10 orders with human-readable dates,
  converted prices, and localized states — plus the buyer's **language
  chooser** and the **credit balance line** (converted money). From that
  point, every bot message, reminder, and verdict uses the buyer's
  language; owner DMs always use the shop language.
- **Dates** render as human-relative words ("in 5 days", "tomorrow",
  "3 days ago") with the exact `YYYY-MM-DD` beside them; year 2999 always
  renders as "never expires", never as a date.
- **Mixed payments, two cases:** a still-pending order paid partly with
  credit withdraws like any other — credit back immediately, transfer
  refunded manually. An approved-and-activated mixed subscription shows
  an explanatory note instead of any cancel path: with nothing pending
  there is nothing to withdraw.

---

## 7. Renewal, legacy subscriptions, and plan matching

- **Quick renew** extends the subscription's matched plan: time stacks onto
  the current expiry (expired subscriptions count from today, per the
  panel's rule); traffic follows the package's `traffic_reset` toggle
  (discard vs. roll over; overuse clamps to the fresh package). Timeless
  subscriptions keep 2999 and renew traffic-only by nature.
- **Change plan** re-selects tier → package through the same contract
  screen; the squad moves only when the tier changes, and only
  for `full` plans.
- **Top-ups** (`time_only` / `traffic_only` offers) adjust one resource and
  reset only that resource's reminder flag.
- **Linking pre-shop subscriptions:** buyers attach older accounts via My
  Subs → Link. Eligibility is determined solely by creator attribution —
  creator id equals the shop owner's account id and the short UUID
  matches, the account links under that shop; a creator-less account with
  a matching short UUID links only into a superadmin-owned shop; anything
  else is refused. Linked rows show their system values as-is and join the
  notifier, renewals, and My Subs immediately; renewing is always done
  through the shop's packages, so no plan matching is needed at link time.
  A subscription without a matched plan shows a single Renew button that
  opens the change-plan flow.
- **Plan matching (Shops tab, owner-side):** `GET /unmapped` lists the owner's panel users with no subscription row plus
  subscription rows with no plan; `POST /subs/assign-plan
  {user_uuid, package_id}` (full plans only) attaches one. Buyer
  self-assignment is not available. When the buyer is linked, plan
  assignment is announced by DM.

---

## 8. Owner operations (Telegram DM + Orders tab)

- **Order DM:** `🧾 Order #N [new|renew|service]` with buyer, plan or item,
  converted price, and traffic/time calculation lines, followed by the
  forwarded receipt with **Approve / Deny**. Denial collects a typed
  reason (cancellable) and is followed by an optional report-scammer
  action for that order. Withdraw requests arrive as a card with the
  buyer's reason and refund details plus a single **Process
  cancellation** button: processing opens **Approve cancellation / Deny
  (fake receipt)**. Approving displays the buyer's bank details and waits
  for the owner's refund receipt, which the bot forwards to the buyer
  before settling `withdrawn`; denying follows the standard denial flow
  and then offers the scammer report. Scam-flagged buyers show who
  flagged them and why.
- **Stale receipts:** once per `pending_nag_hours` window the owner
  receives one summary DM ("N orders waiting for action") instead of
  per-order repeats — until everything pending is decided.
- **Orders tab** (`GET /orders?status=`): `pending` (includes
  `withdraw_requested`, decided in the DM — no panel buttons),
  `approved`, `denied`, `withdrawn`, `cancelled`, `all`. Plain pending
  rows carry inline approve/deny; every row resolves the item name from
  either packages or services and flags scammer buyers with 🚫. Responses
  are capped at 200, newest first.
- **Stats strip** (`GET /stats`): `subs_active`, `orders_pending`,
  `orders_approved`, `units_sold` (sums `total_units` of approved orders),
  `orders_denied`, `orders_withdrawn`, `tests_claimed`, `subs_linked`,
  `subs_manual` (key-created users not yet plan-matched),
  `referral_earned` (units the referral program has paid out). Counts
  only.
- **Broadcast** (Shops tab): one message in the buyers' language,
  Telegram HTML (bold/italic/underline/strike/code/links via toolbar),
  optional photo ≤ 5 MB. `POST /broadcast` (multipart `text` + `photo`;
  text capped at 4000 bytes) returns `{id, recipients}`; a 30 s tick fans
  out ~25 recipients at a time. `GET /broadcasts` shows the last 10 with
  `{id, total, done, failed, status, created_at}` (`queued → sending →
  done`). Recipients are everyone the shop has seen (buyers, linked
  users, language choosers, referred users).
- **Scammer network:** the report action is offered only after an order
  has been denied with a reason, and flags the Telegram id panel-wide.
  When a flagged id shows purchase intent in any other shop, that owner
  receives at most one DM per buyer per shop per 24 h (identifying who
  flagged it and on which order). The flagged buyer is informed that
  other shops will be warned. Flags can be lifted: **Buyer lookup**
  (`GET /buyers`, on the Shops tab — balance, language, subscription
  count, scammer flag per buyer) and the Orders tab both offer **Unmark**
  (`DELETE /scammers/{tg_id}`).

---

## 9. Automatic reminders (notifier, every 15 min)

Each event fires once per subscription and re-arms when its resource resets
on renewal or top-up. Buyer-less matched rows are skipped, pending
subscriptions stay silent, self-cancelled subscriptions leave the queue
entirely, and timeless subscriptions never receive expiry warnings:

| Event | Condition | Button |
|---|---|---|
| 80% traffic | used ÷ cap ≥ 0.8, cap exists | Add traffic (only if the shop sells a matching offer) |
| Out of traffic | used ÷ cap ≥ 1.0 | Add-traffic offer, else Quick renew |
| 72 h pre-expiry | 0 < time-left ≤ 72 h, year < 2999 | Quick renew |
| Expired | past expiry, year < 2999 | Reactivate (quick renew) |

Approvals flow into the panel's own account stream, not a custom notice card:
new sales and renewals go through the panel's native `fire_event("user.created")`
/ account machinery, so the RWA bot's standard user-created notification fires
exactly as it does for panel-created accounts — no duplicate plugin card.

Button taps rewrite the current bot message in place instead of sending a
new one, so the buyer DM stays to a single screen. Consumed buyer messages
(panel taps, /start and /cancel, entered links, reasons, card details, service
notes) are deleted after reading, RWA-bot style. The buyer's receipt
message is never deleted or edited — it stays as the anchor of the order —
and owner approve/deny rewrites both the buyer's waiting message and the
owner's decide card with the final result.

---

## 10. External API (per-shop key)

Base: `https://<panel-host>/api/v2/plugins/pershop`. Authentication for
every `/pub` route is the shop's own key — `X-Shop-Key` header or `?key=`
— looked up by SHA-256 hash and verified in constant time;
missing/unknown keys return `401 invalid key`. No admin session is
required. Key lifecycle is managed in the Shops tab → External API (view,
rotate — the old key stops working immediately, revoke — the endpoint
returns 401).

### 10.1 `GET /pub/catalog` — the bot offering as JSON

`{"shop": {id, prefix, bot_username, lang, currency_label, unit_rate,
welcome_text, referral_text, support_text}, "trial": {enabled,
duration_days, traffic_gb}, "tutorial_text", "tiers": [{id, name}],
"packages": […], "service_categories": [{id, name}], "services": […]}`.
Each package carries `id, name, tier_id, tier_name, duration_days,
timeless, traffic_limit_bytes, traffic_limit_gb, hwid_mode, hwid_fixed,
effect, traffic_reset, scope_tier_id, scope_tier_name, price_units,
price_display, currency_label`; each service carries `id, name, cat_id,
cat_name, description, price_units, price_display, currency_label`. Only
active rows are included. Tokens, secrets, admin ids, buyers, orders, and
subscriptions are never part of this response.

### 10.2 User management — the key holder's own medium

The same key performs full create/read/update on **that shop's users**,
bounded by the owner's RBAC (squad view scope, traffic policy, quota
counters). Every created user is stamped `created_by_admin_id` with the
owner's id — like bot sales — and receives a plan-less subscription row,
so stats count it as `manual` and plan matching applies identically.
Package reads are deliberately absent: plan semantics belong to the bot.

- `GET /pub/users?search=&limit=50&offset=0` → `{"users": […]}` (limit
  clamped 1–200). Reads cover owner-created users only, with panel
  visibility applied on top.
- `GET /pub/users/{uuid}` → `{"user": …}`; foreign or missing users →
  `404 user not found`.
- `POST /pub/users` → `201 {uuid, username, subscription_url, sub_id}`.
  Body: `username` (required, **must start with `<prefix>-`**),
  `expire_at` (required datetime, naive values assumed UTC), optional
  `traffic_limit_bytes` (≥ 0), `hwid_device_limit`, `telegram_id`,
  `squad_uuid` (must fall inside the owner's scope). Invalid input → `400`
  (the message states the violated rule: prefix, squad scope, traffic
  policy, quota); panel/quota refusal → `422`.
- `PATCH /pub/users/{uuid}` → `{uuid, username, updated: […]}` listing the
  core field names sent (`expireAt`, `trafficLimitBytes`,
  `hwidDeviceLimit`, `telegramId`, `activeInternalSquads`, `status`).
  Only provided non-null fields change (explicit null leaves the field —
  the core drops nulls); `squad_uuid: ""` clears squads; `status` accepts
  `active|disabled`. Traffic increases charge the owner's quota and are
  reverted and refused when the gate closes. The plugin subscription row
  is re-synchronized (expiry, cap) on a best-effort basis. Errors: `400`
  bad input / nothing to update, `404` foreign or missing, `422`
  quota/panel refusal.

User object shape (all four routes): `{uuid, username, status, expire_at,
traffic_limit_bytes, used_traffic_bytes, hwid_device_limit, telegram_id,
created_by_admin_id}`.

### 10.3 Panel routes (admin session + `pershop` permission)

All paths below are relative to `/api/v2/plugins/pershop`; authentication
is the panel admin session with `require_permission(resource, action)`.
Response envelopes: `{"shops": […]}`, `{"orders": […]}`,
`{"stats": {...}}`, `{"squads": [{uuid, name}]}`,
`{"broadcasts": […]}`. Errors are `400` (validation / no shop), `404`
(foreign id), `409` (order already decided, tier/package delete blocked by
references).

| Method & path | Permission | Purpose |
|---|---|---|
| `GET /shops/mine` · `PUT /shops/mine` | view / edit | Own shop (Shop setup). Saving with a fresh `bot_token` auto-registers the webhook and returns the result. |
| `POST /shops/mine/webhook` | edit | (Re)register `setWebhook` + live `getWebhookInfo` state |
| `POST /shops/mine/rotate-secret` | edit | New Telegram secret (re-register after) |
| `POST /shops/mine/disconnect` | edit | `deleteWebhook` + clear stored token; settings kept |
| `GET /shops/mine/apikey` · `POST …/rotate` · `DELETE …` | view / edit / edit | External key lifecycle |
| `GET /tiers` · `POST /tiers` · `PUT /tiers/{id}` · `DELETE /tiers/{id}` | view / edit… | Tiers |
| `GET /packages` · `POST …` · `PUT /packages/{id}` · `DELETE …` | view / edit… | Packages |
| `GET /service-cats` · `POST …` · `PUT /service-cats/{id}` · `DELETE …` | view / edit… | Service shelves |
| `GET /services` · `POST …` · `PUT /services/{id}` · `DELETE …` | view / edit… | Service items |
| `GET /orders?status=` | moderate | Owner operations (default `pending`) |
| `POST /orders/{id}/approve` | moderate | Same code path as the DM button; returns the approval result and notifies the buyer |
| `POST /orders/{id}/deny {reason}` | moderate | Same path as DM deny; `reason` 1–1000 chars, relayed to the buyer |
| `GET /unmapped` · `POST /subs/assign-plan {user_uuid, package_id}` | edit | Plan matching |
| `GET /squads` | view | Owner-visible squads for the tier dropdown |
| `GET /stats` | view | Owner stats strip |
| `POST /broadcast` · `GET /broadcasts` | edit / view | Broadcast |
| `GET /buyers?search=` | view | Buyer lookup: balance, language, subscription count, scammer flag |
| `DELETE /scammers/{tg_id}` | moderate | Lift a network scammer flag |
| `DELETE /admin/reset` | `pershop_system:manage` | Drop all plugin tables + unstamp migrations (never panel accounts) |
| `GET /shops/all` | `pershop_system:manage` | Every shop, tokens stripped |
| `GET /plugin-settings` · `PUT /plugin-settings` | `pershop_system:manage` | Superadmin settings |
| `GET /app` | view | The console bundle itself |

---

## 11. Data model (migration `pershop_001`)

`plugin_pershop_shops` (one row per admin: `owner_admin` UNIQUE, encrypted
bot token, `admin_tg_id`, `prefix` UNIQUE, five buyer texts, `lang`,
`currency_label`, `unit_rate`, test toggle + counter, referral/support
texts, `webhook_secret`, `pending_nag_hours` default 6, `referral_percent`
default 0, external-key hash + encrypted copy) → `plugin_pershop_tiers`
(squad + hwid default) → `plugin_pershop_packages`
(duration/traffic/price-units/effect/reset/hwid toggle/scope tier) →
`plugin_pershop_orders` (exactly one of `package_id` / `service_id` via
check constraint; buyer id + username; receipt file/kind; `renew_username`;
`total_units` + JSON snapshot; `credit_units` — reward minted by this
order; `status` pending/approved/denied/cancelled/withdrawn, plus
transient `withdraw_requested`; deny/withdraw reasons + refund card; owner
decide-message pointers; per-window nudge stamp) →
`plugin_pershop_subs` (buyer id nullable until linked, panel `user_uuid`,
username UNIQUE, matched `package_id`, expiry, cap, test/linked/cancelled
flags, four reminder flags). Plus `plugin_pershop_service_cats` /
`services`, `sessions` (per buyer/role conversational state), `referrals`
(unique per referred user), `buyers` (buyer language + scam-noted stamp +
credit balance), `scammers` (panel-global flag list), `test_claims`
(panel-global one-trial registry), `broadcasts` (queue with photo bytes +
recipient list + progress). Plugin tables reference panel tables by id
only — sold accounts themselves live in the panel's `users`, stamped with
the owner's creator id.

---

## 12. Non-goals

- Refunds are not executed through the bot. Bank transfers cannot be
  reversed programmatically; withdrawal is a request the owner settles
  manually.
- No buyer self-service plan mapping.
- Orders do not expire automatically.
- No per-buyer bans; fraud is handled through the panel-wide scammer flag.
- Broadcasts are single-language, composed by the owner.
- Statistics are limited to the overview strip.
- Receipt verification is manual; each order DM presents buyer, plan,
  converted price, and receipt together.

---

## 13. Development

- Layout: `rwa_plugin_pershop/` (bot, admin routes, service layer,
  scheduler, locales en/fa/ru/zh, `static/app.js` console),
  `migrations/versions/pershop_001.py`, `tests/test_shop.py`.
- `python3 tests/test_shop.py` runs the integration suite (fake panel
  facade, fake Telegram, fake DB — buy/renew/top-up/link/withdraw/
  broadcast/scammer/services/public-API flows, currently 27 suites green).
- `node --check rwa_plugin_pershop/static/app.js` for the console.
