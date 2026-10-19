window.rwaPluginUI = window.rwaPluginUI || {};
window.rwaPluginUI['pershop'] = {
  mount(el) {
    const api = '/api/v2/plugins/pershop';
    const q = (id) => el.querySelector('#' + id);
    const j = async (url, opts) => {
      const o = Object.assign({headers: {'Content-Type': 'application/json'}, credentials: 'include'}, opts || {});
      const method = (o.method || 'GET').toUpperCase();
      if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
        // Panel auth is cookie-based (rw_access); mutating requests need the
        // double-submit CSRF token from the readable rw_csrf cookie.
        const m = document.cookie.match(/(?:^|; )rw_csrf=([^;]*)/);
        if (m) o.headers['X-CSRF-Token'] = decodeURIComponent(m[1]);
      }
      const r = await fetch(url, o);
      if (!r.ok) { const e = new Error(await r.text()); e.status = r.status; throw e; }
      return r.json();
    };
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

    // ---- Panel look, copied from the RWA panel itself (web/frontend):
    // buttons carry the panel glow, inputs/labels/rows use the glass tokens
    // (border/bg --glass-*, text-dark-*) so every theme + light mode applies.
    const BTN_P = 'inline-flex h-9 items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-[0_0_20px_-5px_rgba(var(--glow-rgb),0.35)] hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50';
    const BTN_S = 'inline-flex h-9 items-center justify-center rounded-md bg-[var(--glass-bg)] px-4 py-2 text-sm font-medium text-secondary-foreground border border-[var(--glass-border)] shadow-sm hover:bg-[var(--glass-bg-hover)] disabled:pointer-events-none disabled:opacity-50';
    const BTN_D = 'inline-flex h-9 items-center justify-center rounded-md bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground shadow-[0_0_16px_-5px_rgba(239,68,68,0.3)] hover:bg-destructive/90 disabled:pointer-events-none disabled:opacity-50';
    const BTN_M = 'inline-flex h-8 items-center justify-center rounded-md border border-[var(--glass-border)] bg-[var(--glass-bg)] px-3 text-xs font-medium text-dark-100 shadow-sm hover:bg-[var(--glass-bg-hover)] hover:border-[var(--glass-border-hover)] disabled:pointer-events-none disabled:opacity-50';
    const INP = 'flex h-9 w-full rounded-md border border-[var(--glass-border)] bg-[var(--glass-bg)] px-3 py-1 text-sm text-dark-50 shadow-sm placeholder:text-dark-200 hover:border-[var(--glass-border-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:border-primary/40';
    const TA = 'flex min-h-[80px] w-full rounded-md border border-[var(--glass-border)] bg-[var(--glass-bg)] px-3 py-2 text-sm text-dark-50 shadow-sm placeholder:text-dark-200 hover:border-[var(--glass-border-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:border-primary/40';
    const LBL = 'mb-1.5 block text-sm font-medium text-dark-100';
    const HELP = 'mt-1 text-[11px] text-dark-300';
    const CARD = 'glass-card p-5';
    const CARD_T = 'text-base font-semibold text-white mb-4';
    const SEC_T = 'text-xs font-medium uppercase tracking-wider text-dark-300 mb-2';
    const ROW = 'flex items-center justify-between gap-2 rounded-md border border-[var(--glass-border)] bg-[var(--glass-bg)] px-3 py-2 text-sm text-dark-100';
    const PILL = 'inline-flex items-center rounded-md border px-2.5 py-0.5 text-xs font-medium ';
    const pillFor = (s) => PILL + ({pending: 'border-yellow-500/30 bg-gradient-to-br from-yellow-500/15 to-yellow-500/10 text-yellow-400',
      withdraw_requested: 'border-orange-500/30 bg-gradient-to-br from-orange-500/15 to-orange-500/10 text-orange-400',
      approved: 'border-green-500/30 bg-gradient-to-br from-green-500/15 to-green-500/10 text-green-400',
      denied: 'border-red-500/30 bg-gradient-to-br from-red-500/15 to-red-500/10 text-red-400',
      withdrawn: 'border-gray-500/30 bg-gradient-to-br from-gray-500/15 to-gray-500/10 text-gray-400',
      cancelled: 'border-gray-500/30 bg-gradient-to-br from-gray-500/15 to-gray-500/10 text-gray-400'}[s]
      || 'border-gray-500/30 bg-gradient-to-br from-gray-500/15 to-gray-500/10 text-gray-400');

    // ---- Rich-text helpers: every buyer message sends as Telegram HTML, so all
    // message fields share one formatting toolbar; fields with {variables} also
    // get insert buttons. Variables only work where listed — anywhere else they
    // would be sent literally.
    const FMT_TAGS = {b: ['<b>', '</b>'], i: ['<i>', '</i>'], u: ['<u>', '</u>'],
      s: ['<s>', '</s>'], code: ['<code>', '</code>'], link: ['<a href="URL">', '</a>']};
    const FMT_NAMES = {b: 'Bold', i: 'Italic', u: 'Underline', s: 'Strike', code: 'Code', link: 'Link'};
    const FULL_EMOJIS = ['🛒','🔁','👥','📖','🎁','☎️','👤','💰','📦','🏷️','🏆','🔗','✅','❌','⏳','🟢','🔴','🟡','📊','📈','📝','💬','📢','📣','⚠️','🔔','🔕','💡','🎯','🚀','🔥','💎','⭐','✨','💼','📋','🗂️','📁','📂','☀️','🌙','❤️','💔','💘','💊','💉','🩹','🩺','⚕️','🧬','🩻','🩼','🧫','🧪','🌡️','🚽','🚰','🚿','🛁','🛀','🧴','🧷','🧹','🧺','🧻','🧼','🧽','🎧','🎈','🎉','🎊','🎤','🎸','🎹','🎻','🎮','🎯','🎲','🧸','🪀','🪁','🎪','🎭','🩰','🩱','🩲','🩳','👑','👒','🎩','🎓','🧢','👔','📔','📕','📖','📗','📘','📙','📚','📓','📒','📃','📜','📄','🗂️','🗃️','🗄️','🗑️','🔑','🗝️','🔐','🔒','🔓','💎','💍','💄','💅','🐵','🐒','🦍','🐶','🐕','🐩','🐺','🦊','🐱','🐈','🐈‍⬛','🦁','🐯','🐅','🐆','🐴','🐎','🦄','🦓','🦌','🐮','🐂','🐃','🐄','🐷','🐖','🐗','🐏','🐑','🐐','🐬','🐟','🐠','🐡','🐙','🐚','🐌','🦋','🐛','🐜','🐝','🐞','🕷️','🦂','🐉','🐲','🌵','🎄','🌲','🌳','🍀','🍁','🍂','🍃','🍇','🍈','🍉','🍊','🍋','🍌','🍍','🍎','🍏','🍑','🍒','🍓','🍅','🍆','🥑','🍖','🍗','🍘','🍙','🍚','🍛','🍜','🍝','🍞','🍟','🍕','🫓','🥪','🥨','🥯','🧀','🥚','🍳','🧈','🥘','🍲','🥣','🥗','🍿','🍱','🍫','🍬','🍭','🍮','🍯','🍼','🥛','☕','🍵','🍶','🍾','🍴','🍽️','🍸','🍹','🍺','🍻','🍷','💐','🌸','💮','🏵️','🌹','🌺','🌻','🌼','🌷','☘️','🍀','🍁','🍂','🍃','🍇','🍈','🍉','🍊','🍋','🍌','🍍','🍎','🍏','🍑','🍒','🍓','🍅','🍆','🥑','🍖','🍗','🍘','🍙','🍚','🍛','🍜','🍝','🍞','🍟','🍕','🫓','🥪','🥨','🥯','🧀','🥚','🍳','🧈','🥘','🍲','🥣','🥗','🍿','🍱','🍫','🍬','🍭','🍮','🍯','🍼','🥛','☕','🍵','🍶','🍾','🍴','🍽️','🍸','🍹','🍺','🍻','🍷','🟢','🔴','🟡','⚪','⚫','🟤','🟥','🟧','🟨','🟩','🟦','🟪','🟫','📢','📣','⚠️','🔔','🔕','💡','🎯','🚀','🔥','💎','⭐','✨','💼','📋','🗂️','📁','📂','☀️','🌙','📊','📈','📝','💬'];
    const emojiPicker = (targetId) => '<button type="button" onclick="window.__emojiOpen(\'' + targetId + '\', this)" class="' + BTN_M + '">😀</button>';

    window.__emojiOpen = function(targetId, btn) {
      const rect = btn.getBoundingClientRect();
      const existing = document.getElementById('emoji_' + targetId);
      if (existing) { existing.remove(); return; }
      const drop = document.createElement('div');
      drop.id = 'emoji_' + targetId;
      drop.style.position = 'fixed';
      drop.style.zIndex = '99999';
      drop.style.top = (rect.bottom + window.scrollY + 4) + 'px';
      drop.style.left = Math.max(8, rect.left + window.scrollX) + 'px';
      drop.style.maxHeight = '320px';
      drop.style.overflowY = 'auto';
      drop.style.width = '320px';
      drop.innerHTML = '<emoji-picker></emoji-picker>';
      document.body.appendChild(drop);
      const mod = document.createElement('script');
      mod.type = 'module';
      mod.textContent = "import {Picker} from 'https://unpkg.com/emoji-picker-element@1.21.0/index.js'; const p=document.querySelector('#emoji_"+targetId+" emoji-picker'); if(p){p.addEventListener('emoji-click',(e)=>{const f=document.getElementById('"+targetId+"'); if(f){f.value+=(f.value?' ':'')+e.detail.unicode; f.focus();} document.getElementById('emoji_"+targetId+"').remove();});}";
      document.head.appendChild(mod);
      document.addEventListener('click', function closeEmoji(e) { if (!drop.contains(e.target) && e.target !== btn) { drop.remove(); document.removeEventListener('click', closeEmoji); } }, {once:true});
    };
    const fmtBar = (id) => '<div class="flex flex-wrap gap-1.5 mb-2">' + Object.keys(FMT_TAGS).map((t) =>
      '<button data-fmt="' + t + '" data-for="' + id + '" class="' + BTN_M + '">' + FMT_NAMES[t] + '</button>').join('')
      + '<button data-br data-for="' + id + '" class="' + BTN_M + '" title="Insert a blank line">⏎ Break</button>'
      + emojiPicker(id) + '</div>';
    const varBar = (id, vars) => (vars && vars.length)
      ? '<div class="flex flex-wrap items-center gap-1.5 mb-2"><span class="text-[11px] text-dark-300">Insert:</span>' + vars.map((v) =>
        '<button data-var="' + v + '" data-for="' + id + '" class="' + BTN_M + '">{' + v + '}</button>').join('') + '</div>' : '';
    const wireFmt = (root) => {
      root.querySelectorAll('[data-fmt]').forEach((b) => { b.onclick = () => {
        const t = el.querySelector('#' + b.dataset.for);
        if (!t) return;
        let tag = FMT_TAGS[b.dataset.fmt];
        if (b.dataset.fmt === 'link') { const u = prompt('Link URL:'); if (!u) return; tag = ['<a href="' + u + '">', '</a>']; }
        const s = t.selectionStart || 0, e = t.selectionEnd || 0, v = t.value;
        t.value = v.slice(0, s) + tag[0] + (v.slice(s, e) || 'text') + tag[1] + v.slice(e);
        t.focus();
      }; });
      root.querySelectorAll('[data-br]').forEach((b) => { b.onclick = () => {
        const t = el.querySelector('#' + b.dataset.for);
        if (!t) return;
        const s = t.selectionStart || 0, e = t.selectionEnd || 0, v = t.value;
        t.value = v.slice(0, s) + '\n\n' + v.slice(e);
        t.focus();
        t.selectionStart = t.selectionEnd = s + 2;
      }; });
      root.querySelectorAll('[data-var]').forEach((b) => { b.onclick = () => {
        const t = el.querySelector('#' + b.dataset.for);
        if (!t) return;
        const s = t.selectionStart || 0, e = t.selectionEnd || 0, v = t.value, ins = '{' + b.dataset.var + '}';
        t.value = v.slice(0, s) + ins + v.slice(e);
        t.focus();
        t.selectionStart = t.selectionEnd = s + ins.length;
      }; });
    };
    // ---- Modal, copied from the panel create-user dialog: overlay + glass-heavy
    // content, icon title + description, sectioned body, Cancel/Save footer.
    const openModal = (title, desc, bodyHTML, onSave, saveLabel) => {
      const ov = document.createElement('div');
      ov.className = 'fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-end sm:items-center justify-center';
      ov.innerHTML = '<div class="glass-heavy rounded-t-xl sm:rounded-lg p-4 sm:p-6 w-full sm:max-w-[520px] max-h-[85dvh] sm:max-h-[90vh] overflow-y-auto shadow-deep">'
        + '<div class="flex items-start justify-between gap-3 mb-1"><h2 class="text-lg font-semibold text-white flex items-center gap-2">'
        + '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-5 h-5 text-primary-400"><path d="M5 12h14"/><path d="M12 5v14"/></svg>'
        + esc(title) + '</h2><button data-mclose class="rounded-sm opacity-70 hover:opacity-100 text-dark-200 text-xl leading-none px-1">×</button></div>'
        + '<p class="text-sm text-dark-300 mb-5">' + esc(desc) + '</p>'
        + '<div id="merr"></div><div class="space-y-5">' + bodyHTML + '</div>'
        + '<div class="flex justify-end gap-2 mt-6"><button data-mclose class="' + BTN_S + '">Cancel</button>'
        + '<button data-msave class="' + BTN_P + '">' + esc(saveLabel || 'Save') + '</button></div></div>';
      el.appendChild(ov);
      const close = () => ov.remove();
      ov.addEventListener('mousedown', (e) => { if (e.target === ov) close(); });
      ov.querySelectorAll('[data-mclose]').forEach((b) => { b.onclick = close; });
      const saveBtn = ov.querySelector('[data-msave]');
      saveBtn.onclick = async () => {
        saveBtn.disabled = true;
        try { await onSave(ov); close(); }
        catch (e) {
          ov.querySelector('#merr').innerHTML = '<div class="p-3 mb-4 bg-red-500/10 border border-red-500/20 rounded-lg text-sm text-red-400">' + esc(e.message) + '</div>';
          saveBtn.disabled = false;
        }
      };
      return ov;
    };
    const ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-5 h-5 text-primary-400 shrink-0"><path d="m2 7 4.41-4.41A2 2 0 0 1 7.83 2h8.34a2 2 0 0 1 1.42.59L22 7"/><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><path d="M15 22v-4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4"/><path d="M2 7h20"/></svg>';

    // Panel 5.x mounts this module once on the single route /plugins/:pluginId,
    // so navigation is in-module state, not separate URLs — a sidebar (desktop)
    // that becomes a horizontal strip on narrow screens.
    let shopCache = null;
    let adminMode = false;
    let page = 'dashboard';
    let orderFilter = 'pending';
    let buyerFilter = '';

    const fmtU = (u) => {
      if (u == null) return '';
      if (shopCache && shopCache.unit_rate && shopCache.currency_label)
        return (Number(u) * Number(shopCache.unit_rate)).toLocaleString('en-US') + ' ' + shopCache.currency_label;
      return u + (shopCache && shopCache.currency_label ? ' ' + shopCache.currency_label : ' units');
    };
    const chip = (ok, label) => '<span class="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 '
      + (ok === null ? 'text-dark-300' : ok ? 'text-green-400 border-green-500/30' : 'text-red-400 border-red-500/30')
      + '">' + (ok === null ? '•' : (ok ? '🟢' : '🔴')) + ' ' + label + '</span>';
    // PUT /shops/mine needs the full payload, so every partial save merges the
    // cached shop and overrides only the keys the page owns.
    const saveShop = async (patch) => {
      if (!shopCache) throw new Error('Save your shop first (Shop → Bot connection).');
      const body = Object.assign({
        prefix: shopCache.prefix, admin_tg_id: shopCache.admin_tg_id, lang: shopCache.lang,
        card_text: shopCache.card_text, currency_label: shopCache.currency_label,
        unit_rate: shopCache.unit_rate || null,
        welcome_text: shopCache.welcome_text, thank_you_text: shopCache.thank_you_text,
        approved_text: shopCache.approved_text, denied_text: shopCache.denied_text,
        referral_text: shopCache.referral_text, support_text: shopCache.support_text,
        test_enabled: shopCache.test_enabled, tiers_internal: shopCache.tiers_internal,
        referral_percent: shopCache.referral_percent || 0,
        campaign_percent: shopCache.campaign_percent || 0,
        pending_nag_hours: shopCache.pending_nag_hours == null ? 6 : shopCache.pending_nag_hours}, patch);
      if (patch._token) body.bot_token = patch._token;
      const r = await j(api + '/shops/mine', {method: 'PUT', body: JSON.stringify(body)});
      const w = r.webhook || {};
      if (r.shop) shopCache = Object.assign({}, shopCache, r.shop);
      return {r, w};
    };
    const eventLine = (ev) => '<div class="py-1 border-b border-border/40 last:border-0">'
      + (EVENT_ICON[ev.kind] || '•') + ' ' + esc(String(ev.created_at).slice(0, 16).replace('T', ' '))
      + (ev.detail ? ' · ' + esc(ev.detail) : '') + '</div>';
    const EVENT_ICON = {order_new: '🛒', order_credit: '💰', approved: '✅', denied: '❌',
      linked: '🔗', promo: '🎟️', broadcast: '📣', devreset: '📱', reissue: '♻️', credit: 'wallet'};
    const loadShop = async (silent) => {
      try {
        const {shop} = await j(api + '/shops/mine');
        shopCache = shop || null;
        return shopCache;
      } catch (e) {
        if (!silent) throw e;
        return null;
      }
    };

    // ============================== PAGES ==============================
    const NAV = [
      ['dashboard', '🏠', 'Dashboard'],
      ['orders', '🧾', 'Orders'],
      ['packages', '📦', 'Packages'],
      ['services', '🧰', 'Services'],
      ['promos', '🎟️', 'Promo codes'],
      ['referrals', '🤝', 'Referrals'],
      ['buyers', '👥', 'Buyers'],
      ['marketing', '📣', 'Broadcast'],
      ['settings', '⚙️', 'Settings'],
    ];
    const renderNav = () => {
      const opts = adminMode ? NAV : NAV.filter(([k]) => k !== 'settings' ? true : true);
      q('nav').innerHTML = opts.map(([k, ic, label]) =>
        '<button data-nav="' + k + '" class="flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium whitespace-nowrap transition-all '
        + (k === page ? 'bg-background text-foreground shadow border border-border' : 'text-muted-foreground hover:text-foreground hover:bg-muted/50')
        + '"><span>' + ic + '</span> ' + label + '</button>').join('');
      q('nav').querySelectorAll('[data-nav]').forEach((b) => { b.onclick = () => goto(b.dataset.nav); });
    };
    const go = () => {
      renderNav();
      const body = q('pagebody');
      body.innerHTML = '<div class="' + CARD + '"><span class="text-sm text-dark-300">Loading…</span></div>';
      ({dashboard: renderDashboard, orders: () => renderOrders(body), packages: () => renderPackages(body),
        services: () => renderServices(body), promos: () => renderPromos(body),
        referrals: () => renderReferrals(body), buyers: () => renderBuyers(body),
        buyer: () => renderBuyerDetail(body), marketing: () => renderMarketing(body),
        settings: () => renderSettings(body)}[page] || renderDashboard)(body);
    };
    const goto = (p) => { if (NAV.some(([k]) => k === p) || p === 'buyer') { page = p; go(); } };

    // ---- Dashboard: health + KPIs + live activity + quick jumps (+ admin all-shops).
    const renderDashboard = (body) => {
      body.innerHTML = '<div class="space-y-6">'
        + '<div class="grid gap-4 grid-cols-2 sm:grid-cols-4" id="stats"></div>'
        + '<div class="' + CARD + '"><div class="flex items-center justify-between mb-3"><h2 class="' + CARD_T + '">Shop health</h2>'
        + '<button id="hcheck" class="' + BTN_M + '">Recheck</button></div><div id="health" class="flex flex-wrap gap-2"></div>'
        + (adminMode ? '' : '<p class="' + HELP + '">🟢 Bot connected = buyers can reach the shop. Stuck orders sit unread past 24h — approve or deny them in Orders.</p>')
        + '</div>'
        + '<div class="grid gap-4 sm:grid-cols-2">'
        + '<div class="' + CARD + '"><h2 class="' + CARD_T + '">Quick actions</h2><div class="flex flex-wrap gap-2">'
        + '<button data-go="orders" class="' + BTN_M + '">Review orders</button>'
        + '<button data-go="promos" class="' + BTN_M + '">New promo code</button>'
        + '<button data-go="packages" class="' + BTN_M + '">Manage packages</button>'
        + '<button data-go="marketing" class="' + BTN_M + '">Send broadcast</button>'
        + '<button data-go="buyers" class="' + BTN_M + '">Buyers &amp; credit</button>'
        + '</div></div>'
        + '<div class="' + CARD + '"><h2 class="' + CARD_T + '">Recent activity</h2>'
        + '<p class="text-xs text-dark-300 mb-3">Newest first. 🛒 order · 💰 auto-approve (credit) · ✅ approved · ❌ denied · ✉️ receipt · 🔗 linked · 🎟️ promo · 📣 broadcast · 📱 devices · ♻️ reissue.</p>'
        + '<div id="events" class="text-sm"><i class="text-dark-300">loading…</i></div></div>'
        + '</div>'
        + (adminMode ? '<div class="' + CARD + '"><h2 class="' + CARD_T + '">All shops (superadmin)</h2><div id="all"></div></div>' : '')
        + '</div>';
      body.querySelectorAll('[data-go]').forEach((b) => { b.onclick = () => goto(b.dataset.go); });
      const s = {subs_active: 0, orders_pending: 0, orders_approved: 0, units_sold: 0,
        orders_denied: 0, orders_withdrawn: 0, tests_claimed: 0, subs_linked: 0, referral_earned: 0};
      const renderKPIs = () => {
        // Bedolaga-style MiniStat: label top-left, big value, tinted icon bubble top-right.
        const kpis = [
          [s.subs_active || 0, 'Active subs', '🟢', 'text-emerald-400'],
          [s.orders_pending || 0, 'Pending orders', '⏳', 'text-amber-400'],
          [(s.orders_approved || 0) + ' · ' + (s.units_sold || 0) + 'u', 'Approved · units sold', '✅', 'text-teal-400'],
          [s.orders_denied || 0, 'Denied', '❌', 'text-rose-400'],
          [s.orders_withdrawn || 0, 'Withdrawn', '↩️', 'text-dark-300'],
          [s.tests_claimed || 0, 'Trials', '🧪', 'text-cyan-400'],
          [s.subs_linked || 0, 'Linked', '🔗', 'text-violet-400'],
          [fmtU(s.referral_earned || 0), 'Referral paid', '💰', 'text-blue-400'],
        ];
        const cell = (v, l, icon, color) => '<div class="glass-card p-4 flex items-start justify-between">'
          + '<div class="min-w-0"><div class="text-[10px] text-dark-300 uppercase tracking-wider truncate">' + l + '</div>'
          + '<div class="text-xl font-bold text-white mt-0.5 truncate">' + v + '</div></div>'
          + '<div class="p-2 rounded-lg bg-[var(--glass-bg)] flex-shrink-0 ' + color + '">' + icon + '</div></div>';
        q('stats').innerHTML = kpis.map((k) => cell(k[0], k[1], k[2], k[3])).join('');
      };
      Promise.all([
        j(api + '/stats'), adminMode ? j(api + '/shops/all') : Promise.resolve({shops: adminMode ? [] : undefined}),
      ]).then(([st, allr]) => {
        Object.assign(s, st.stats || {});
        renderKPIs();
        if (adminMode) {
          const shops = allr.shops || [];
          q('all').innerHTML = shops.length ? '<table class="table"><thead><tr><th>Shop</th><th>Owner</th><th>Prefix</th><th>Bot</th></tr></thead><tbody>'
            + shops.map((x) => '<tr><td>#' + x.id + '</td><td>' + esc(x.owner_admin) + '</td><td><code>' + esc(x.prefix || '—') + '</code></td>'
              + '<td>' + (x.webhook_configured ? '<span class="' + pillFor('approved') + '">connected</span>' : '<span class="' + pillFor('pending') + '">not connected</span>') + '</td></tr>').join('')
            + '</tbody></table>' : '<i>no shops yet</i>';
        }
        const loadEvents = async () => {
          try {
            const {events} = await j(api + '/events');
            q('events').innerHTML = (events || []).slice(0, 20).map(eventLine).join('') || '<i>nothing yet</i>';
          } catch (e) { q('events').textContent = 'unavailable'; }
        };
        loadEvents();
      }).catch((e) => {
        q('stats').innerHTML = '<div class="glass-card p-4 text-sm text-dark-300 col-span-4">Stats unavailable: ' + esc(e.message) + '</div>';
      });
      const loadHealth = async () => {
        q('health').innerHTML = '<span class="text-dark-300">checking…</span>';
        try {
          const h = await j(api + '/health');
          q('health').innerHTML = chip(h.bot_ok, 'Bot connected') + chip(h.panel_ok, 'Panel API')
            + '<span class="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-dark-300">Pending orders: ' + h.pending + '</span>'
            + '<span class="inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1 '
            + (h.pending_old ? 'text-amber-400 border-amber-500/30' : 'text-dark-300')
            + '">Stuck over 24h: ' + h.pending_old + '</span>';
        } catch (e) { q('health').innerHTML = '<span class="text-red-400">unavailable</span>'; }
      };
      q('hcheck').onclick = loadHealth;
      loadHealth();
    };

    // ---- Orders: status filter + table; approve/deny inline.
    const renderOrders = (body) => {
      body.innerHTML = '<div class="flex gap-1.5 mb-4">'
        + ['pending', 'approved', 'denied', 'withdrawn'].map((s) =>
          '<button data-s="' + s + '" class="inline-flex items-center justify-center whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium capitalize transition-all border '
          + (s === orderFilter ? 'bg-background text-foreground shadow' : 'text-muted-foreground hover:text-foreground')
          + '">' + s + '</button>').join('')
        + '</div><div class="' + CARD + ' !p-0 overflow-hidden"><table class="table"><thead><tr><th>Order</th><th>Item</th><th>Buyer</th><th>Amount</th><th>Type</th><th>Status</th><th></th></tr></thead>'
        + '<tbody id="ob"></tbody></table></div>';
      const load = async (status) => {
        orderFilter = status;
        try {
          const {orders} = await j(api + '/orders?status=' + status);
          q('ob').innerHTML = (orders || []).map((o) =>
            '<tr data-id="' + o.id + '"><td class="font-medium">#' + o.id + '</td><td>' + esc(o.package_name || '-') + '</td>'
            + '<td>@' + esc(o.buyer_username || o.buyer_tg_id) + ' <button data-a="buyer" data-tg="' + o.buyer_tg_id + '" class="' + BTN_M + ' !h-6 px-2">open</button>'
            + (o.buyer_flagged ? ' 🚫 <button data-a="unscam" data-tg="' + o.buyer_tg_id + '" class="' + BTN_M + ' !h-6 px-2">Unmark</button>' : '') + '</td>'
            + '<td>' + (o.total_units != null ? esc(String(o.total_units)) + 'u' : '—') + '</td>'
            + '<td>' + (o.renew_username ? 'renew ' + esc(o.renew_username) : 'new') + '</td>'
            + '<td>' + (o.status === 'withdraw_requested' ? '<span class="' + pillFor('withdraw_requested') + '">withdraw requested</span><div class="text-xs text-dark-300 mt-1">reason: ' + esc(o.withdraw_reason || '') + ' · refund to: ' + esc(o.withdraw_card || '') + ' (decide in the bot DM)</div>'
              : '<span class="' + pillFor(o.status) + '">' + esc(o.status) + '</span>') + '</td>'
            + '<td class="whitespace-nowrap">' + (status === 'pending' && o.status === 'pending'
              ? '<button data-a="ok" class="' + BTN_M + '">Approve</button> <button data-a="no" class="' + BTN_M + '">Deny</button>' : '') + '</td></tr>').join('')
            || '<tr><td colspan="7"><i class="text-sm text-dark-300">none</i></td></tr>';
          q('ob').querySelectorAll('button').forEach((b) => { b.onclick = async () => {
            if (b.dataset.a === 'buyer') return openBuyer(b.dataset.tg);
            const id = b.closest('[data-id]').dataset.id;
            if (b.dataset.a === 'ok') await j(api + '/orders/' + id + '/approve', {method: 'POST'});
            else if (b.dataset.a === 'unscam') await j(api + '/scammers/' + b.dataset.tg, {method: 'DELETE'});
            else { const r = prompt('Decline reason:'); if (!r) return;
              await j(api + '/orders/' + id + '/deny', {method: 'POST', body: JSON.stringify({reason: r})}); }
            load(status);
          }; });
        } catch (e) {
          q('ob').innerHTML = '<tr><td colspan="7" class="text-sm text-red-400">Load failed: ' + esc(e.message) + '</td></tr>';
        }
      };
      body.querySelectorAll('[data-s]').forEach((b) => { b.onclick = () => {
        body.querySelectorAll('[data-s]').forEach((x) => {
          x.className = x.className.replace(' bg-background text-foreground shadow', ' text-muted-foreground');
        });
        b.className = b.className.replace(' text-muted-foreground', ' text-foreground') + ' bg-background shadow';
        load(b.dataset.s); }; });
      load(orderFilter);
    };

    const openBuyer = (tg) => {
      page = 'buyer';
      buyerFilter = tg;
      go();
    };

    // ---- Packages: tiers above packages above plan matching.
    const renderPackages = (body) => {
      body.innerHTML = '<div class="space-y-6">'
        + '<div class="' + CARD + '"><div class="flex items-center justify-between mb-4"><h2 class="' + CARD_T + '">Tiers</h2>'
        + '<button id="addtier" class="' + BTN_S + '">+ Add tier</button></div>'
        + '<p class="' + HELP + ' mb-3">Buyer groups. Each tier puts sold accounts in one internal squad; each package belongs to exactly one tier.</p><div id="tiers" class="space-y-1.5"><i class="text-sm text-dark-300">loading…</i></div></div>'
        + '<div class="' + CARD + '"><div class="flex items-center justify-between mb-4"><h2 class="' + CARD_T + '">Packages</h2>'
        + '<button id="addpkg" class="' + BTN_S + '">+ Add package</button></div><div id="pkgs" class="space-y-1.5"><i class="text-sm text-dark-300">loading…</i></div></div>'
        + '<div class="' + CARD + '"><div class="flex items-center justify-between mb-4"><h2 class="' + CARD_T + '">Plan matching</h2>'
        + '<button id="uload" class="' + BTN_M + '">Reload</button></div>'
        + '<p class="' + HELP + ' mb-2">Pre-existing panel users / linked subscriptions without a shop plan. "Match" links them to a package (they must pay their first order like everyone else).</p>'
        + '<div id="unmapped"><i class="text-sm text-dark-300">press Reload</i></div></div>'
        + '</div>';
      const squadNames = async () => {
        let names = {};
        try { const r = await j(api + '/squads'); (r.squads || []).forEach((s) => { names[s.uuid] = s.name; }); } catch (e) {}
        return names;
      };
      const squadOpts = async (cur) => {
        let sq = [];
        try { sq = (await j(api + '/squads')).squads || []; } catch (e) {}
        if (!sq.length) return '<input id="m_squad" class="' + INP + '" placeholder="squad uuid" value="' + esc(cur || '') + '">';
        return '<select id="m_squad" class="' + INP + '">'
          + (cur && !sq.some((s) => s.uuid === cur) ? '<option value="' + esc(cur) + '">current: ' + esc(cur.slice(0, 8)) + '…</option>' : '')
          + '<option value="">— pick a squad —</option>'
          + sq.map((s) => '<option value="' + esc(s.uuid) + '"' + (s.uuid === cur ? ' selected' : '') + '>' + esc(s.name) + '</option>').join('')
          + '</select>';
      };
      const tierModal = (t, names) => {
        const isNew = !t;
        t = t || {name: '', squad_uuid: '', hwid_default: null, sort: 0, active: true};
        openModal(isNew ? 'Add tier' : 'Edit tier', 'Buyer groups. Each tier puts sold accounts in one squad.',
          '<div><label class="' + LBL + '">Tier name</label><div class="flex items-center gap-2"><input id="m_name" class="' + INP + '" value="' + esc(t.name) + '">' + emojiPicker('m_name') + '</div><p class="' + HELP + '">Shown to buyers as the first step of the buy flow.</p></div>'
          + '<div><label class="' + LBL + '">Internal squad</label><span id="m_squadwrap">loading…</span><p class="' + HELP + '">Sold accounts join this squad. Lists the squads you can see.</p></div>'
          + '<div class="grid grid-cols-2 gap-3"><div><label class="' + LBL + '">HWID default</label><input id="m_hwid" class="' + INP + '" inputmode="numeric" placeholder="blank = panel default" value="' + (t.hwid_default == null ? '' : t.hwid_default) + '"></div>'
          + '<div><label class="' + LBL + '">Sort order</label><input id="m_sort" class="' + INP + '" inputmode="numeric" value="' + (t.sort || 0) + '"></div></div>'
          + '<div><label class="text-sm text-dark-300"><input type="checkbox" id="m_active" class="h-4 w-4 accent-primary"' + (t.active === false ? '' : ' checked') + '> active</label></div>',
          async (ov) => {
            const body = {name: ov.querySelector('#m_name').value.trim(),
              squad_uuid: ov.querySelector('#m_squad').value.trim() || null,
              hwid_default: ov.querySelector('#m_hwid').value.trim() === '' ? null : Number(ov.querySelector('#m_hwid').value),
              sort: Number(ov.querySelector('#m_sort').value) || 0,
              active: ov.querySelector('#m_active').checked};
            if (!body.name) throw new Error('Tier name is required.');
            if (isNew) await j(api + '/tiers', {method: 'POST', body: JSON.stringify(body)});
            else await j(api + '/tiers/' + t.id, {method: 'PUT', body: JSON.stringify(body)});
            loadCatalog();
          }, isNew ? 'Add tier' : 'Save tier');
        squadOpts(t.squad_uuid).then((h) => { const w = el.querySelector('#m_squadwrap'); if (w) w.innerHTML = h; });
      };
      const EFFLBL = {full: 'full plan', time_only: 'time top-up', traffic_only: 'traffic top-up'};
      const pkgBadges = (p, tiers) => {
        const tn = (tiers.find((x) => x.id === p.tier_id) || {}).name || ('tier ' + p.tier_id);
        return '<span class="' + pillFor('pending') + '">' + esc(tn) + '</span> '
          + '<span>' + esc(p.name) + ' · ' + (Number(p.duration_days) === 0 ? 'never expires' : p.duration_days + 'd') + ' · '
          + (p.traffic_limit_bytes ? (p.traffic_limit_bytes / 1073741824) + 'GB' : 'unlimited') + ' · ' + fmtU(p.price_units)
          + ' · ' + esc(EFFLBL[p.effect] || 'full plan') + (p.active ? '' : ' · off') + '</span>';
      };
      const hwidFields = (p) => {
        p = p || {};
        const m = p.hwid_mode || 'default';
        return '<div><label class="' + LBL + '">Tier</label><select id="m_tier" class="' + INP + '"></select>'
          + '<p class="' + HELP + '">Required — the tier decides the internal squad. Change any time to move the package.</p></div>'
          + '<div><label class="' + LBL + '">Device limit (HWID)</label>'
          + '<select id="m_hmode" class="' + INP + '"><option value="default"' + (m === 'default' ? ' selected' : '') + '>hwid: panel default</option><option value="input"' + (m === 'input' ? ' selected' : '') + '>hwid: buyer input</option><option value="fixed"' + (m === 'fixed' ? ' selected' : '') + '>hwid: fixed by admin</option><option value="unlimited"' + (m === 'unlimited' ? ' selected' : '') + '>hwid: unlimited</option></select></div>'
          + '<div id="m_hinput" class="grid grid-cols-2 sm:grid-cols-5 gap-2"' + (m === 'input' ? '' : ' style="display:none"') + '>'
          + '<div><label class="' + LBL + '">Min</label><input id="m_hmin" class="' + INP + '" inputmode="numeric" value="' + (p.hwid_min == null ? 1 : p.hwid_min) + '"></div>'
          + '<div><label class="' + LBL + '">Start value</label><input id="m_hstart" class="' + INP + '" inputmode="numeric" value="' + (p.hwid_default == null ? 1 : p.hwid_default) + '"></div>'
          + '<div><label class="' + LBL + '">Max</label><input id="m_hmax" class="' + INP + '" inputmode="numeric" value="' + (p.hwid_max == null ? 10 : p.hwid_max) + '"></div>'
          + '<div><label class="' + LBL + '">Free incl.</label><input id="m_hfree" class="' + INP + '" inputmode="numeric" value="' + (p.hwid_free == null ? 1 : p.hwid_free) + '"></div>'
          + '<div><label class="' + LBL + '">Extra %</label><input id="m_hpct" class="' + INP + '" inputmode="numeric" value="' + (p.hwid_percent == null ? 50 : p.hwid_percent) + '"></div></div>'
          + '<div id="m_hfixed"' + (m === 'fixed' ? '' : ' style="display:none"') + '><label class="' + LBL + '">Fixed device count</label><input id="m_hfixedn" class="' + INP + '" style="width:8rem" inputmode="numeric" value="' + (p.hwid_fixed || 1) + '"></div>';
      };
      const pkgModal = (p, tiers) => {
        const isNew = !p;
        p = p || {name: '', duration_days: 30, traffic_limit_bytes: null, price_units: 1, effect: 'full', scope_tier_id: null, tier_id: null, active: true, traffic_reset: true};
        const GB = 1073741824;
        const ov = openModal(isNew ? 'Add package' : 'Edit package', 'Standalone sellable. Pick a tier to decide where buyers find it.',
          '<div class="grid gap-3 sm:grid-cols-2">'
          + '<div><label class="' + LBL + '">Package name</label><div class="flex items-center gap-2"><input id="m_pname" class="' + INP + '" value="' + esc(p.name) + '">' + emojiPicker('m_pname') + '</div></div>'
          + '<div><label class="' + LBL + '">Duration (days)</label><input id="m_pdays" class="' + INP + '" inputmode="numeric" value="' + (p.duration_days == null ? 30 : p.duration_days) + '"><p class="' + HELP + '">0 = never expires (∞).</p></div>'
          + '<div><label class="' + LBL + '">Traffic cap (GB)</label><input id="m_pgb" class="' + INP + '" inputmode="decimal" value="' + (p.traffic_limit_bytes ? p.traffic_limit_bytes / GB : '') + '"><p class="' + HELP + '">Blank = unlimited.</p></div>'
          + '<div><label class="' + LBL + '">Price (units)</label><input id="m_punits" class="' + INP + '" inputmode="decimal" value="' + (p.price_units || 0) + '"></div>'
          + '<div><label class="' + LBL + '">Sort order</label><input id="m_psort" class="' + INP + '" inputmode="numeric" value="' + (p.sort || 0) + '"><p class="' + HELP + '">Lower numbers show first in the bot.</p></div>'
          + '<div><label class="' + LBL + '">Package type</label><select id="m_peffect" class="' + INP + '"><option value="full"' + (p.effect === 'full' ? ' selected' : '') + '>full plan</option><option value="time_only"' + (p.effect === 'time_only' ? ' selected' : '') + '>time top-up (+days)</option><option value="traffic_only"' + (p.effect === 'traffic_only' ? ' selected' : '') + '>traffic top-up (+GB)</option></select></div>'
          + '<div><label class="' + LBL + '">Offer scope</label><select id="m_pscope" class="' + INP + '"><option value="">scope: all tiers</option>'
          + tiers.map((x) => '<option value="' + x.id + '"' + (x.id === p.scope_tier_id ? ' selected' : '') + '>scope: ' + esc(x.name) + ' only</option>').join('') + '</select></div>'
          + '<div><label class="text-sm text-dark-300"><input type="checkbox" id="m_pactive" class="h-4 w-4 accent-primary"' + (p.active === false ? '' : ' checked') + '> active</label></div>'
          + '<div><label class="text-sm text-dark-300"><input type="checkbox" id="m_preset" class="h-4 w-4 accent-primary"' + (p.traffic_reset === false ? '' : ' checked') + '> reset traffic on renew</label></div>'
          + '</div>' + hwidFields(p),
          async (ovr) => {
            const gv = (id, fb) => { const v = ovr.querySelector(id).value.trim(); return v === '' ? fb : (Number(v) || 0); };
            const mode = ovr.querySelector('#m_hmode').value;
            const bbody = {name: ovr.querySelector('#m_pname').value.trim(),
              tier_id: ovr.querySelector('#m_tier').value ? Number(ovr.querySelector('#m_tier').value) : null,
              sort: Math.max(0, Number(ovr.querySelector('#m_psort').value) || 0),
              duration_days: Math.max(0, Number(ovr.querySelector('#m_pdays').value) || 0),
              traffic_limit_bytes: ovr.querySelector('#m_pgb').value.trim() === '' ? null : Math.round(Number(ovr.querySelector('#m_pgb').value) * GB),
              price_units: Number(ovr.querySelector('#m_punits').value) || 0,
              traffic_reset: ovr.querySelector('#m_preset').checked,
              effect: ovr.querySelector('#m_peffect').value,
              scope_tier_id: ovr.querySelector('#m_pscope').value ? Number(ovr.querySelector('#m_pscope').value) : null,
              active: ovr.querySelector('#m_pactive').checked,
              hwid_mode: mode, hwid_min: 0, hwid_default: 0, hwid_max: 0, hwid_free: 0, hwid_percent: 0, hwid_fixed: null};
            if (!bbody.name) throw new Error('Package name is required.');
            if (!bbody.tier_id) throw new Error('Pick a tier — every package belongs to one.');
            if (mode === 'input') { bbody.hwid_min = gv('#m_hmin', 1); bbody.hwid_default = gv('#m_hstart', 1);
              bbody.hwid_max = gv('#m_hmax', 10); bbody.hwid_free = gv('#m_hfree', 1); bbody.hwid_percent = gv('#m_hpct', 50); }
            else if (mode === 'fixed') { bbody.hwid_fixed = gv('#m_hfixedn', 1); }
            if (isNew) await j(api + '/packages', {method: 'POST', body: JSON.stringify(bbody)});
            else await j(api + '/packages/' + p.id, {method: 'PUT', body: JSON.stringify(bbody)});
            loadCatalog();
          }, isNew ? 'Add package' : 'Save package');
        const tsel = ov.querySelector('#m_tier');
        tsel.innerHTML += tiers.map((x) => '<option value="' + x.id + '"' + (x.id === (p.tier_id || null) ? ' selected' : '') + '>' + esc(x.name) + '</option>').join('');
        const hm = ov.querySelector('#m_hmode');
        hm.onchange = () => { ov.querySelector('#m_hinput').style.display = hm.value === 'input' ? '' : 'none';
          ov.querySelector('#m_hfixed').style.display = hm.value === 'fixed' ? '' : 'none'; };
      };
      const loadCatalog = async () => {
        try {
          const {tiers} = await j(api + '/tiers');
          const {packages} = await j(api + '/packages');
          const names = await squadNames();
          q('tiers').innerHTML = (tiers.map((t) =>
            '<div class="' + ROW + '" data-t="' + t.id + '"><span>' + esc(t.name)
            + (t.squad_uuid ? ' · ' + esc(names[t.squad_uuid] || t.squad_uuid.slice(0, 8)) : ' · no squad')
            + (t.active ? '' : ' · off') + '</span><span class="flex gap-1.5"><button data-te="1" class="' + BTN_M + '">Edit</button> <button data-td="1" class="' + BTN_M + '">Del</button></span></div>').join('')
            || '<i class="text-sm text-dark-300">none</i>');
          q('pkgs').innerHTML = (packages.map((p) =>
            '<div class="' + ROW + '" data-p="' + p.id + '">' + pkgBadges(p, tiers)
            + '<span class="flex gap-1.5"><button data-pe="1" class="' + BTN_M + '">Edit</button> <button data-pd="1" class="' + BTN_M + '">Del</button></span></div>').join('')
            || '<i class="text-sm text-dark-300">none</i>');
          q('tiers').querySelectorAll('[data-te]').forEach((b) => { b.onclick = () => {
            const t = tiers.find((x) => x.id === Number(b.closest('[data-t]').dataset.t)); tierModal(t, names); }; });
          q('tiers').querySelectorAll('[data-td]').forEach((b) => { b.onclick = async () => {
            const t = tiers.find((x) => x.id === Number(b.closest('[data-t]').dataset.t));
            if (!confirm('Delete tier ' + t.name + '? Packages inside must be moved out first.')) return;
            try { await j(api + '/tiers/' + t.id, {method: 'DELETE'}); loadCatalog(); }
            catch (e) { alert('Delete failed: ' + e.message); } }; });
          q('pkgs').querySelectorAll('[data-pe]').forEach((b) => { b.onclick = () => {
            const p = packages.find((x) => x.id === Number(b.closest('[data-p]').dataset.p)); pkgModal(p, tiers); }; });
          q('pkgs').querySelectorAll('[data-pd]').forEach((b) => { b.onclick = async () => {
            if (!confirm('Delete this package?')) return;
            await j(api + '/packages/' + b.closest('[data-p]').dataset.p, {method: 'DELETE'}); loadCatalog(); }; });
        } catch (e) {
          q('tiers').innerHTML = q('pkgs').innerHTML = '<span class="text-sm text-red-400">Load failed: ' + esc(e.message) + '</span>';
        }
      };
      q('addtier').onclick = async () => { const names = await squadNames(); tierModal(null, names); };
      q('addpkg').onclick = async () => { const {tiers} = await j(api + '/tiers'); pkgModal(null, tiers || []); };
      const loadUnmapped = async () => {
        try {
          const [{orphans, unplanned}, {packages}] = await Promise.all([j(api + '/unmapped'), j(api + '/packages')]);
          const opts = () => '<select data-pick="1" class="' + INP + '" style="width:auto"><option value="">— plan —</option>' + packages.filter((p) =>
            (p.effect || 'full') === 'full').map((p) =>
            '<option value="' + p.id + '">' + esc(p.name) + ' · ' + fmtU(p.price_units) + '</option>').join('')
            + '</select>';
          const row = (uuid, label) => '<div class="' + ROW + '" data-u="' + esc(uuid) + '"><span>' + label + '</span><span class="flex gap-1.5">' + opts()
            + ' <button data-assign="1" class="' + BTN_M + '">Match</button></span></div>';
          q('unmapped').innerHTML = '<h5 class="text-xs font-medium uppercase tracking-wider text-dark-300 mb-2">Your panel users with no shop plan (' + orphans.length + ')</h5><div class="space-y-1.5 mb-4">'
            + (orphans.map((o) => row(o.uuid, '👤 ' + esc(o.username)
              + ' <span class="text-dark-300">' + esc(String(o.uuid).slice(0, 8)) + '</span>')).join('') || '<i class="text-sm text-dark-300">none</i>') + '</div>'
            + '<h5 class="text-xs font-medium uppercase tracking-wider text-dark-300 mb-2">Shop subscriptions with no plan (' + unplanned.length + ')</h5><div class="space-y-1.5">'
            + (unplanned.map((s) => row(s.user_uuid, '📦 ' + esc(s.username)
              + (s.buyer_tg_id ? '' : ' <span class="text-dark-300">not linked</span>'))).join('') || '<i class="text-sm text-dark-300">none</i>') + '</div>';
          q('unmapped').querySelectorAll('[data-assign]').forEach((b) => { b.onclick = async () => {
            const div = b.closest('[data-u]');
            const pid = div.querySelector('[data-pick]').value;
            if (!pid) { alert('Pick a plan first.'); return; }
            try {
              await j(api + '/subs/assign-plan', {method: 'POST',
                body: JSON.stringify({user_uuid: div.dataset.u, package_id: Number(pid)})});
              loadUnmapped();
            } catch (e) { alert('Match failed: ' + e.message); }
          }; });
        } catch (e) { q('unmapped').innerHTML = '<span class="text-sm text-red-400">Load failed: ' + esc(e.message) + '</span>'; }
      };
      q('uload').onclick = () => loadUnmapped().catch((e) => alert('Load failed: ' + e.message));
      loadUnmapped();
      loadCatalog();
    };

    // ---- Services: categories → items.
    const renderServices = (body) => {
      body.innerHTML = '<div class="space-y-6">'
        + '<div class="' + CARD + '"><div class="flex items-center justify-between mb-4"><h2 class="' + CARD_T + '">Service categories</h2>'
        + '<button id="addscat" class="' + BTN_S + '">+ Add category</button></div>'
        + '<p class="' + HELP + ' mb-3">Buyer groups inside the Other Services menu of the bot.</p><div id="scats" class="space-y-1.5"><i class="text-sm text-dark-300">loading…</i></div></div>'
        + '<div class="' + CARD + '"><div class="flex items-center justify-between mb-4"><h2 class="' + CARD_T + '">Service items</h2>'
        + '<button id="addsvc" class="' + BTN_S + '">+ Add item</button></div><div id="svcs" class="space-y-1.5"><i class="text-sm text-dark-300">loading…</i></div></div>'
        + '</div>';
      const scatModal = (c) => {
        const isNew = !c;
        c = c || {name: '', sort: 0, active: true};
        openModal(isNew ? 'Add category' : 'Edit category', 'Buyer groups inside Other Services.',
          '<div><label class="' + LBL + '">Category name</label><div class="flex items-center gap-2"><input id="m_cname" class="' + INP + '" value="' + esc(c.name) + '">' + emojiPicker('m_cname') + '</div><p class="' + HELP + '">First step buyers pick inside Other Services.</p></div>'
          + '<div><label class="' + LBL + '">Sort order</label><input id="m_csort" class="' + INP + '" inputmode="numeric" value="' + (c.sort || 0) + '"><p class="' + HELP + '">Lower numbers show first in the bot.</p></div>'
          + '<div><label class="text-sm text-dark-300"><input type="checkbox" id="m_cactive" class="h-4 w-4 accent-primary"' + (c.active === false ? '' : ' checked') + '> active</label></div>',
          async (ov) => {
            const body = {name: ov.querySelector('#m_cname').value.trim(),
              sort: Number(ov.querySelector('#m_csort').value) || 0,
              active: ov.querySelector('#m_cactive').checked};
            if (!body.name) throw new Error('Category name is required.');
            if (isNew) await j(api + '/service-cats', {method: 'POST', body: JSON.stringify(body)});
            else await j(api + '/service-cats/' + c.id, {method: 'PUT', body: JSON.stringify(body)});
            loadServices();
          }, isNew ? 'Add category' : 'Save category');
      };
      const svcModal = (s, cats) => {
        const isNew = !s;
        s = s || {name: '', cat_id: null, price_units: 1, sort: 0, active: true, description: '', done_text: ''};
        const ov = openModal(isNew ? 'Add item' : 'Edit item', 'Standalone non-VPN sellable. Pick a category to decide where buyers find it.',
          '<div class="grid gap-3 sm:grid-cols-2">'
          + '<div><label class="' + LBL + '">Item name</label><div class="flex items-center gap-2"><input id="m_sname" class="' + INP + '" value="' + esc(s.name) + '">' + emojiPicker('m_sname') + '</div></div>'
          + '<div><label class="' + LBL + '">Price (units)</label><input id="m_sunits" class="' + INP + '" inputmode="decimal" value="' + (s.price_units || 0) + '"><p class="' + HELP + '">Multiplied by your currency rate for the buyer price.</p></div>'
          + '<div><label class="' + LBL + '">Category</label><select id="m_scat" class="' + INP + '"><option value="">No category (shows under Other)</option>'
          + cats.map((x) => '<option value="' + x.id + '"' + (x.id === s.cat_id ? ' selected' : '') + '>' + esc(x.name) + '</option>').join('') + '</select><p class="' + HELP + '">Change any time to move the item.</p></div>'
          + '<div><label class="' + LBL + '">Sort order</label><input id="m_ssort" class="' + INP + '" inputmode="numeric" value="' + (s.sort || 0) + '"><p class="' + HELP + '">Lower numbers show first in the bot.</p></div>'
          + '<div><label class="text-sm text-dark-300"><input type="checkbox" id="m_sactive" class="h-4 w-4 accent-primary"' + (s.active === false ? '' : ' checked') + '> active</label></div>'
          + '</div>'
          + '<div><label class="' + LBL + '">Description (shown before buy)</label>' + fmtBar('m_sdesc') + '<textarea id="m_sdesc" rows="2" class="' + TA + '">' + esc(s.description || '') + '</textarea></div>'
          + '<div><label class="' + LBL + '">Message sent to buyer on approval</label>' + fmtBar('m_sdone') + '<textarea id="m_sdone" rows="2" class="' + TA + '">' + esc(s.done_text || '') + '</textarea></div>',
          async (ov2) => {
            const body = {name: ov2.querySelector('#m_sname').value.trim(),
              cat_id: ov2.querySelector('#m_scat').value ? Number(ov2.querySelector('#m_scat').value) : null,
              price_units: Number(ov2.querySelector('#m_sunits').value) || 0,
              sort: Math.max(0, Number(ov2.querySelector('#m_ssort').value) || 0),
              description: ov2.querySelector('#m_sdesc').value,
              done_text: ov2.querySelector('#m_sdone').value,
              active: ov2.querySelector('#m_sactive').checked};
            if (!body.name) throw new Error('Item name is required.');
            if (isNew) await j(api + '/services', {method: 'POST', body: JSON.stringify(body)});
            else await j(api + '/services/' + s.id, {method: 'PUT', body: JSON.stringify(body)});
            loadServices();
          }, isNew ? 'Add item' : 'Save item');
        wireFmt(ov);
      };
      const loadServices = async () => {
        try {
          const {service_cats} = await j(api + '/service-cats');
          const {services} = await j(api + '/services');
          const cats = service_cats || [];
          q('scats').innerHTML = (cats.map((c) =>
            '<div class="' + ROW + '" data-c="' + c.id + '"><span>' + esc(c.name)
            + (c.active ? '' : ' · off') + '</span><span class="flex gap-1.5"><button data-ce="1" class="' + BTN_M + '">Edit</button> <button data-cd="1" class="' + BTN_M + '">Del</button></span></div>').join('')
            || '<i class="text-sm text-dark-300">none</i>');
          q('svcs').innerHTML = ((services || []).map((s) => {
            const cn = (cats.find((x) => x.id === s.cat_id) || {}).name || 'Other';
            return '<div class="' + ROW + '" data-s="' + s.id + '"><span><span class="' + pillFor('pending') + '">' + esc(cn) + '</span> '
              + esc(s.name) + ' · ' + fmtU(s.price_units) + (s.active ? '' : ' · off') + '</span><span class="flex gap-1.5"><button data-se="1" class="' + BTN_M + '">Edit</button> <button data-sd="1" class="' + BTN_M + '">Del</button></span></div>'; }).join('')
            || '<i class="text-sm text-dark-300">none</i>');
          q('scats').querySelectorAll('[data-ce]').forEach((b) => { b.onclick = () => {
            const c = cats.find((x) => x.id === Number(b.closest('[data-c]').dataset.c)); scatModal(c); }; });
          q('scats').querySelectorAll('[data-cd]').forEach((b) => { b.onclick = async () => {
            const c = cats.find((x) => x.id === Number(b.closest('[data-c]').dataset.c));
            if (!confirm('Delete category ' + c.name + '? Items become uncategorized.')) return;
            try { await j(api + '/service-cats/' + c.id, {method: 'DELETE'}); loadServices(); }
            catch (e) { alert('Delete failed: ' + e.message); } }; });
          q('svcs').querySelectorAll('[data-se]').forEach((b) => { b.onclick = () => {
            const s = (services || []).find((x) => x.id === Number(b.closest('[data-s]').dataset.s)); svcModal(s, cats); }; });
          q('svcs').querySelectorAll('[data-sd]').forEach((b) => { b.onclick = async () => {
            if (!confirm('Delete this item?')) return;
            try { await j(api + '/services/' + b.closest('[data-s]').dataset.s, {method: 'DELETE'}); loadServices(); }
            catch (e) { alert('Delete failed: ' + e.message); } }; });
        } catch (e) {
          q('scats').innerHTML = q('svcs').innerHTML = '<span class="text-sm text-red-400">Load failed: ' + esc(e.message) + '</span>';
        }
      };
      q('addscat').onclick = () => scatModal(null);
      q('addsvc').onclick = async () => { const {service_cats} = await j(api + '/service-cats'); svcModal(null, service_cats || []); };
      loadServices();
    };

    // ---- Promo codes: create + live use counts.
    const renderPromos = (body) => {
      body.innerHTML = '<div class="space-y-6">'
        + '<div class="' + CARD + '"><h2 class="' + CARD_T + '">Promo codes</h2>'
        + '<p class="' + HELP + ' mb-4">Codes kick in at buyer checkout and work only inside this shop. <b>percent</b> = % off · <b>units</b> = flat cut in shop units. Buyers by default each redeem a code once; "max uses" also caps total redemptions (blank/0 = unlimited). Expiry empty = forever. One use is burned when you approve the order.</p>'
        + '<div class="grid gap-3 sm:grid-cols-5 mb-4">'
        + '<div><label class="' + LBL + '">Code</label><input id="pr_code" class="' + INP + '" placeholder="SAVE10"></div>'
        + '<div><label class="' + LBL + '">Type</label><select id="pr_kind" class="' + INP + '"><option value="percent">% off</option><option value="units">flat units off</option></select></div>'
        + '<div><label class="' + LBL + '">Value</label><input id="pr_value" class="' + INP + '" inputmode="decimal"></div>'
        + '<div><label class="' + LBL + '">Max uses (0 = ∞)</label><input id="pr_uses" class="' + INP + '" inputmode="numeric" placeholder="0 = ∞"></div>'
        + '<div><label class="' + LBL + '">Valid until</label><input id="pr_until" type="date" class="' + INP + '"></div>'
        + '</div>'
        + '<div class="flex flex-wrap gap-2 mb-5"><button id="pr_add" class="' + BTN_P + '">Create promo</button>'
        + '<button id="pr_res" class="' + BTN_M + '">Reset list</button></div>'
        + '<div id="promos" class="space-y-1.5"><i class="text-sm text-dark-300">loading…</i></div>'
        + '</div></div>';
      const loadPromos = async () => {
        const empty = '<i>no promo codes yet</i>';
        try {
          const {promos} = await j(api + '/promos');
          q('promos').innerHTML = (promos || []).map((p) => {
            const off = p.kind === 'units'
              ? ('flat ' + esc(fmtU(p.value)) + ' off')
              : ('' + esc(fmtU(p.value)) + '% off');
            const uses = esc(p.uses || 0) + '/' + (Number(p.max_uses) > 0 ? esc(p.max_uses) : '∞');
            const until = p.valid_until ? (' · until ' + esc(String(p.valid_until).slice(0, 10))) : '';
            const dot = p.active ? '🟢' : '⚫';
            return '<div class="' + ROW + '"><span>' + dot + ' <b>' + esc(p.code) + '</b> · ' + off
              + ' · ' + uses + until + '</span><button data-dpromo="' + p.id + '" class="' + BTN_D + '">Delete</button></div>';
          }).join('') || empty;
          q('promos').querySelectorAll('[data-dpromo]').forEach((b) => { b.onclick = async () => {
            if (!confirm('Delete this promo code? Buyers can no longer apply it.')) return;
            try { await j(api + '/promos/' + b.dataset.dpromo, {method: 'DELETE'}); loadPromos(); }
            catch (e) { alert('Delete failed: ' + e.message); }
          }; });
        } catch (e) { q('promos').textContent = 'Error: ' + e.message; }
      };
      q('pr_add').onclick = async () => {
        const code = (q('pr_code').value || '').trim();
        if (!code) { alert('Promo code is required'); return; }
        const until = q('pr_until').value;
        const body2 = {code: code, kind: q('pr_kind').value || 'percent',
          value: Number(q('pr_value').value) || 0,
          max_uses: Math.max(0, Math.floor(Number(q('pr_uses').value) || 0)),
          valid_until: until ? until + 'T23:59:59Z' : null, active: true};
        try { await j(api + '/promos', {method: 'POST', body: JSON.stringify(body2)});
          q('pr_code').value = ''; q('pr_value').value = ''; q('pr_uses').value = ''; q('pr_until').value = '';
          loadPromos();
        } catch (e) { alert('Create failed: ' + e.message); }
      };
      q('pr_res').onclick = loadPromos;
      loadPromos();
    };

    // ---- Referrals: program settings + every invite with its earnings.
    const renderReferrals = (body) => {
      body.innerHTML = '<div class="space-y-6">'
        + '<div class="' + CARD + '"><h2 class="' + CARD_T + '">Program settings</h2>'
        + '<p class="' + HELP + ' mb-4">Referral % = credited to the inviter from every approved purchase of their invitee. First-order discount % = automatic discount for invitees on their very first purchase (no code needed); 0 = off. Both are shop-scoped.</p>'
        + '<div class="grid gap-3 sm:grid-cols-3">'
        + '<div><label class="' + LBL + '">Referral reward %</label><input id="refpct" inputmode="decimal" class="' + INP + '" value="' + esc((shopCache || {}).referral_percent || 0) + '"></div>'
        + '<div><label class="' + LBL + '">First-order discount % (invitees)</label><input id="campct" inputmode="decimal" class="' + INP + '" value="' + esc((shopCache || {}).campaign_percent || 0) + '"></div>'
        + '<div class="flex items-end"><button id="refsave" class="' + BTN_P + '">Save program settings</button></div>'
        + '</div><span id="refmsg" class="text-sm text-dark-300"></span>'
        + '</div>'
        + '<div class="' + CARD + '"><h2 class="' + CARD_T + '">Invites &amp; earnings</h2>'
        + '<div id="refstats" class="mb-4"></div>'
        + '<div id="reflist" class="space-y-1.5"><i class="text-sm text-dark-300">loading…</i></div>'
        + '</div></div>';
      q('refsave').onclick = async () => {
        q('refmsg').textContent = 'Saving…';
        try {
          await saveShop({
            referral_percent: Math.max(0, Math.min(100, Number(q('refpct').value) || 0)),
            campaign_percent: Math.max(0, Math.min(100, Number(q('campct').value) || 0))});
          q('refmsg').textContent = 'Saved.';
        } catch (e) { q('refmsg').textContent = 'Save failed: ' + e.message; }
      };
      const loadReferrals = async () => {
        try {
          const d = await j(api + '/referrals');
          const pairs = d.pairs || [];
          q('refstats').innerHTML = '<div class="grid gap-3 grid-cols-3"><div class="glass-card p-3"><div class="text-lg font-bold text-white">' + pairs.length + '</div><div class="text-xs text-dark-300">invited buyers</div></div>'
            + '<div class="glass-card p-3"><div class="text-lg font-bold text-white">' + fmtU(d.earned_units || 0) + '</div><div class="text-xs text-dark-300">total paid out</div></div>'
            + '<div class="glass-card p-3"><div class="text-lg font-bold text-white">' + (d.campaign_percent > 0 ? d.campaign_percent + '%' : '—') + '</div><div class="text-xs text-dark-300">first-order discount</div></div></div>';
          q('reflist').innerHTML = pairs.length ? pairs.map((p) =>
            '<div class="' + ROW + '"><span>tg <b>' + p.referrer + '</b> → <b>' + p.invited + '</b></span>'
            + '<span class="text-right"><span class="' + pillFor(p.earned > 0 ? 'approved' : 'pending') + '">' + esc(fmtU(p.earned)) + '</span>'
            + ' <span class="text-xs text-dark-300">' + p.buys + ' purchase' + (p.buys === 1 ? '' : 's') + '</span>'
            + ' <button data-b="' + p.invited + '" class="' + BTN_M + '">open</button></span></div>').join('')
            : '<i>nobody has been invited yet — buyers find the invite link in the bot\'s referral program menu</i>';
          q('reflist').querySelectorAll('[data-b]').forEach((b) => { b.onclick = () => openBuyer(Number(b.dataset.b)); });
        } catch (e) { q('reflist').textContent = 'Error: ' + e.message; }
      };
      loadReferrals();
    };

    // ---- Buyers: roster + scammer flags; rows open the buyer detail page.
    const renderBuyers = (body) => {
      body.innerHTML = '<div class="space-y-6">'
        + '<div class="' + CARD + '"><div class="flex flex-wrap items-end gap-2 mb-4"><div class="w-56"><label class="' + LBL + '" for="bq">Filter by telegram id</label>'
        + '<input id="bq" class="' + INP + '" value="' + esc(buyerFilter || '') + '"></div>'
        + '<button id="bgo" class="' + BTN_S + '">Search</button><p class="' + HELP + ' mb-1 flex-1 min-w-[16rem]">Rows open the buyer\'s full history — orders, subscriptions, credit. Click to drill in.</p></div>'
        + '<div id="blist"><i class="text-sm text-dark-300">loading…</i></div></div>'
        + '<div class="' + CARD + '"><h2 class="' + CARD_T + '">Scammer flags</h2>'
        + '<p class="' + HELP + ' mb-3">Network-wide — blocks marked buyers from receiving subscriptions in every shop using this plugin. Fake receipts, recurring abuse.</p>'
        + '<div id="scamlist" class="space-y-1.5"><i class="text-sm text-dark-300">loading…</i></div></div>'
        + '</div>';
      const loadBuyers = async () => {
        const needle = q('bq').value.trim();
        try {
          const [{buyers}, {scammers}] = await Promise.all([
            j(api + '/buyers?search=' + encodeURIComponent(needle)),
            j(api + '/scammers'),
          ]);
          buyerFilter = needle;
          q('blist').innerHTML = (buyers || []).length
            ? '<table class="table"><thead><tr><th>Buyer</th><th>Lang</th><th>Credit</th><th>Subs</th><th>Orders</th><th>Flag</th><th></th></tr></thead><tbody>'
              + buyers.map((b) => '<tr data-tg="' + b.tg_id + '"><td class="font-medium">' + b.tg_id + '</td><td>' + esc(b.lang || '—') + '</td>'
                + '<td>' + fmtU(b.credit_units) + '</td><td>' + (b.subs || 0) + '</td><td>' + (b.orders || 0) + '</td>'
                + '<td>' + (b.flagged ? '🚫 ' + esc(b.flag_reason || 'scammer') : '—') + '</td>'
                + '<td><button data-open="' + b.tg_id + '" class="' + BTN_M + '">Open</button></td></tr>').join('')
              + '</tbody></table>'
            : (needle ? '<i>no buyer matches that id</i>' : '<i>no buyers yet</i>');
          q('blist').querySelectorAll('[data-open]').forEach((b) => { b.onclick = () => openBuyer(Number(b.dataset.open)); });
          const scams = (scammers || []).filter((s) => !needle || String(s.tg_id).includes(needle));
          q('scamlist').innerHTML = scams.map((s) => '<div class="' + ROW + '"><span>tg <b>' + s.tg_id + '</b> · 🚫 '
            + esc(s.reason || 'no reason') + ' <span class="text-dark-300">(by ' + esc(s.marked_by_admin || '?') + ')</span></span>'
            + '<button data-unscam="' + s.tg_id + '" class="' + BTN_M + '">Unmark</button></div>').join('') || '<i>none</i>';
          q('scamlist').querySelectorAll('[data-unscam]').forEach((b) => { b.onclick = async () => {
            if (!confirm('Remove the network-wide scammer flag for tg ' + b.dataset.unscam + '?')) return;
            try { await j(api + '/scammers/' + b.dataset.unscam, {method: 'DELETE'}); loadBuyers(); }
            catch (e) { alert('Unmark failed: ' + e.message); }
          }; });
        } catch (e) {
          q('blist').innerHTML = '<span class="text-sm text-red-400">Load failed: ' + esc(e.message) + '</span>';
          q('scamlist').textContent = '';
        }
      };
      q('bgo').onclick = loadBuyers;
      loadBuyers();
    };

    // ---- Buyer detail: one click from list/order/referral row → full history.
    const renderBuyerDetail = (body) => {
      const tg = buyerFilter;
      body.innerHTML = '<div class="space-y-6">'
        + '<div class="flex items-center gap-2"><button id="bback" class="' + BTN_M + '">← Buyers</button></div>'
        + '<div class="' + CARD + '"><div id="bhead"><i class="text-sm text-dark-300">loading…</i></div></div>'
        + '<div id="borders"></div>'
        + '<div id="bsubdata"></div>'
        + '</div>';
      q('bback').onclick = () => { page = 'buyers'; buyerFilter = ''; go(); };
      const load = async () => {
        let d;
        try { d = await j(api + '/buyers/' + tg); }
        catch (e) { q('bhead').innerHTML = '<span class="text-sm text-red-400">Load failed: ' + esc(e.message) + '</span>'; return; }
        q('bhead').innerHTML = '<div class="flex flex-wrap items-center justify-between gap-3 mb-4">'
          + '<div><h2 class="text-lg font-semibold text-white">Buyer ' + d.tg_id + '</h2>'
          + '<p class="text-xs text-dark-300 mt-1">Language: ' + esc(d.lang || '—')
          + (d.flag ? ' · 🚫 scammer' + (d.flag.reason ? ' — ' + esc(d.flag.reason) : '') : '')
          + (d.flag ? ' <button id="bunflag" class="' + BTN_M + ' ml-1">Unmark</button>' : '') + '</p></div>'
          + '<div class="flex items-center gap-2"><span class="' + pillFor('approved') + '">credit: ' + esc(fmtU(d.credit_units)) + '</span>'
          + ' <button id="bgive" class="' + BTN_M + '">+ credit</button><button id="btake" class="' + BTN_M + '">− credit</button></div></div>';
        const adjust = async (sign) => {
          const v = prompt((sign > 0 ? 'Credit to give (shop units, floats ok):' : 'Credit to deduct:'), '');
          if (v == null || v.trim() === '') return;
          const delta = sign * (Number(v.replace(',', '.')) || 0);
          if (!delta) return;
          try {
            const r = await j(api + '/buyers/' + tg + '/credit', {method: 'POST', body: JSON.stringify({units: delta})});
            d.credit_units = r.credit_units;
            load();
          } catch (e) { alert('Adjust failed: ' + e.message); }
        };
        q('bgive').onclick = () => adjust(1);
        q('btake').onclick = () => adjust(-1);
        if (d.flag) q('bunflag').onclick = async () => {
          if (!confirm('Remove the scammer flag for tg ' + tg + '?')) return;
          try { await j(api + '/scammers/' + tg, {method: 'DELETE'}); load(); }
          catch (e) { alert('Unmark failed: ' + e.message); }
        };
        q('borders').innerHTML = '<div class="' + CARD + '"><h2 class="' + CARD_T + '">Orders (' + (d.orders || []).length + ')</h2>'
          + ((d.orders || []).length ? '<table class="table"><thead><tr><th>#</th><th>Item</th><th>Units</th><th>Promo</th><th>Status</th><th>When</th></tr></thead><tbody>'
            + d.orders.map((o) => '<tr><td class="font-medium">#' + o.id + '</td>'
              + '<td>' + (o.renew_username ? 'renew ' + esc(o.renew_username) : esc((o.package_id ? 'package #' + o.package_id : 'service #' + (o.service_id || '?')))) + '</td>'
              + '<td>' + (o.total_units != null ? esc(String(o.total_units)) + 'u' : '—') + '</td>'
              + '<td>' + (o.promo_code ? '🎟️ ' + esc(o.promo_code) : '—') + '</td>'
              + '<td><span class="' + pillFor(o.status) + '">' + esc(o.status) + '</span></td>'
              + '<td class="text-xs text-dark-300">' + esc(String(o.created_at).slice(0, 16).replace('T', ' ')) + '</td></tr>').join('')
            + '</tbody></table>' : '<i class="text-sm text-dark-300">no orders yet</i>') + '</div>';
        q('bsubdata').innerHTML = '<div class="' + CARD + '"><h2 class="' + CARD_T + '">Subscriptions (' + (d.subs || []).length + ')</h2>'
          + ((d.subs || []).length ? '<table class="table"><thead><tr><th>Account</th><th>Expire</th><th>State</th></tr></thead><tbody>'
            + d.subs.map((s) => '<tr><td class="font-medium">' + esc(s.username || ('#' + s.id)) + '</td>'
              + '<td>' + (s.is_test ? '<span class="' + pillFor('pending') + '">trial</span>' : esc(String(s.expire_at || '').slice(0, 10) || '—')) + '</td>'
              + '<td>' + (s.cancelled ? '<span class="' + pillFor('cancelled') + '">cancelled</span>' : '<span class="' + pillFor('approved') + '">active</span>') + '</td></tr>').join('')
            + '</tbody></table>' : '<i class="text-sm text-dark-300">no subscriptions yet</i>') + '</div>';
      };
      load();
    };

    // ---- Broadcast: composer + segment pick + history.
    const renderMarketing = (body) => {
      body.innerHTML = '<div class="' + CARD + '"><h2 class="' + CARD_T + '">Broadcast</h2>'
        + '<p class="' + HELP + ' mb-4">Telegram HTML reaches the shop\'s buyers as-is. Sends fan out about 25 messages per 30 seconds so Telegram never rate-limits the bot.</p>'
        + '<div class="flex flex-wrap items-end gap-3 mb-2">'
        + '<div class="w-56"><label class="' + LBL + '" for="bccat">Send to</label><select id="bccat" class="' + INP + '">'
        + '<option value="all">Everyone</option><option value="buyers">Opened the bot</option>'
        + '<option value="subs">Have a subscription</option><option value="expired">EXPIRED subscription</option>'
        + '<option value="noorders">No purchase yet</option><option value="invited">Invited (referral)</option></select></div>'
        + '<div><label class="' + LBL + '" for="bcphoto">Photo (≤5MB, optional)</label><input id="bcphoto" type="file" accept="image/*" class="' + INP + ' w-64"></div>'
        + '<span id="bcsize" class="text-xs text-dark-300"></span></div>'
        + '<div><label class="' + LBL + '" for="bctext">Message</label>' + fmtBar('bctext') + '<textarea id="bctext" rows="5" class="' + TA + '" placeholder="You can use Telegram HTML: <b>bold</b>, <a href=&quot;…&quot;>links</a>, <code>…</code>…"></textarea></div>'
        + '<div class="flex items-center gap-3 mt-3"><button id="bcsend" class="' + BTN_P + '">Queue broadcast</button>'
        + '<span id="bcstat" class="text-sm text-dark-300"></span></div>'
        + '<div class="mt-6 border-t border-[var(--glass-border)] pt-4"><h3 class="' + SEC_T + '">History</h3><div id="bcstat2" class="text-sm"><i class="text-dark-300">loading…</i></div></div>'
        + '<div class="mt-6 border-t border-[var(--glass-border)] pt-4"><h3 class="' + SEC_T + '">Tip</h3><p class="' + HELP + '">Promo code + broadcast is a strong combo: announce <code>WELCOME15</code>, buyers type the code at checkout and pay 15% less.</p></div>'
        + '</div>';
      wireFmt(el);
      q('bcphoto').onchange = () => {
        const f = q('bcphoto').files[0];
        q('bcsize').textContent = f ? (Math.round(f.size / 1024) + ' KB' + (f.size > 5 * 1024 * 1024 ? ' — TOO BIG, 5MB max' : '')) : '';
      };
      const loadBc = async () => {
        try {
          const {broadcasts} = await j(api + '/broadcasts');
          q('bcstat2').innerHTML = (broadcasts || []).map((x) =>
            '#' + x.id + ' · ' + esc(x.status) + ' · ' + x.done + '/' + x.total + ' sent'
            + (x.failed ? ' · ' + x.failed + ' failed' : '')).join('<br>') || '<i>no broadcasts yet</i>';
        } catch (e) { q('bcstat2').textContent = 'status unavailable'; }
      };
      q('bcsend').onclick = async () => {
        const f = q('bcphoto').files[0];
        if (f && f.size > 5 * 1024 * 1024) { alert('Photo must be under 5MB'); return; }
        const fd = new FormData();
        fd.append('text', q('bctext').value);
        if (q('bccat')) fd.append('target', q('bccat').value);
        if (f) fd.append('photo', f);
        q('bcstat').textContent = 'Queueing…';
        try {
          const h = {};
          const m = document.cookie.match(/(?:^|; )rw_csrf=([^;]*)/);
          if (m) h['X-CSRF-Token'] = decodeURIComponent(m[1]);
          const r = await fetch(api + '/broadcast', {method: 'POST', headers: h, body: fd}).then(async (x) => {
            if (!x.ok) throw new Error((await x.text()).slice(0, 200));
            return x.json();
          });
          q('bcstat').textContent = 'Queued #' + r.id + ' for ' + r.recipients + ' buyers (' + r.target + ') — fans out ~25 per 30s.';
          q('bctext').value = ''; q('bcphoto').value = ''; q('bcsize').textContent = '';
          loadBc();
        } catch (e) { q('bcstat').textContent = 'Failed: ' + e.message; }
      };
      loadBc();
    };

    // ---- Settings: bot connection, texts, external API, plugin settings (admin).
    const renderSettings = (body) => {
      if (!shopCache) {
        body.innerHTML = '<div class="' + CARD + '"><h2 class="' + CARD_T + '">Shop setup</h2>'
          + '<p class="text-sm text-dark-300 mb-4">Create your shop: telegram bot token, admin id and a URL prefix. Everything else unlocks after this.</p>'
          + '<div class="grid gap-3 sm:grid-cols-2">'
          + '<div><label class="' + LBL + '" for="prefix">URL prefix</label><input id="prefix" class="' + INP + '" placeholder="vip"><p class="' + HELP + '">Each shop gets a unique prefix used in bot addresses.</p></div>'
          + '<div><label class="' + LBL + '" for="tgid">Admin TG id</label><input id="tgid" inputmode="numeric" class="' + INP + '" placeholder="123456789"></div>'
          + '<div><label class="' + LBL + '" for="token">Bot token</label><input id="token" class="' + INP + '" placeholder="123456:ABC-DEF…"><p class="' + HELP + '">From @BotFather. Save then press Connect bot.</p></div>'
          + '<div><label class="' + LBL + '" for="lang">Bot language</label><select id="lang" class="' + INP + '">'
          + '<option value="en">English</option><option value="fa">فارسی</option><option value="ru">Русский</option><option value="zh">中文</option></select></div>'
          + '<div><label class="' + LBL + '" for="currency">Currency label</label><input id="currency" class="' + INP + '" placeholder="Toman"></div>'
          + '<div><label class="' + LBL + '" for="rate">1 unit costs this much currency</label><input id="rate" inputmode="decimal" class="' + INP + '" placeholder="100000"><p class="' + HELP + '">Leave blank to sell flat in panel units.</p></div>'
          + '<div><label class="' + LBL + '" for="cardnum">Payment destination</label><input id="cardnum" class="' + INP + '" placeholder="Card 6037-… / crypto wallet"><p class="' + HELP + '">Shown as receipts payment dest. Supports Telegram HTML.</p></div>'
          + '<div><label class="' + LBL + '" for="naghours">Nag pending buyers after (hours, 0 = off)</label><input id="naghours" inputmode="numeric" class="' + INP + '"></div>'
          + '</div>'
          + '<div class="flex items-center gap-3 mt-4"><label class="flex items-center gap-2 text-sm"><input type="checkbox" id="teston" class="h-4 w-4 accent-primary"> Trial subscription enabled</label>'
          + '<label class="flex items-center gap-2 text-sm"><input type="checkbox" id="tierint" class="h-4 w-4 accent-primary"> Internal tiers (skip public tiers)</label></div>'
          + (adminMode ? '' : '<p class="' + HELP + ' mt-2">Other settings (referral %, tutorial texts, how-to buttons) are superadmin-only or live on their own pages.</p>')
          + '<div class="flex items-center gap-3 mt-5"><button id="save" class="' + BTN_P + '">Save shop</button><span id="sec" class="text-sm text-dark-300"></span></div>'
          + '<div class="flex items-center gap-3 mt-3"><button id="connect" class="' + BTN_S + '">Connect bot</button>'
          + '<button id="disconnect" class="' + BTN_S + '">Disconnect bot</button></div>'
          + '</div>';
      } else {
        body.innerHTML = '<div class="space-y-6">'
          + '<div class="' + CARD + '"><h2 class="' + CARD_T + '">Bot connection</h2>'
          + '<div class="grid gap-3 sm:grid-cols-2">'
          + '<div><label class="' + LBL + '" for="prefix">URL prefix</label><input id="prefix" class="' + INP + '"></div>'
          + '<div><label class="' + LBL + '" for="tgid">Admin TG id</label><input id="tgid" inputmode="numeric" class="' + INP + '"></div>'
          + '<div><label class="' + LBL + '" for="token">New bot token (optional)</label><input id="token" class="' + INP + '" placeholder="paste to replace"><p class="' + HELP + '">Leave blank to keep the current one. Change → Save → Connect.</p></div>'
          + '<div><label class="' + LBL + '" for="lang">Bot language</label><select id="lang" class="' + INP + '">'
          + '<option value="en">English</option><option value="fa">فارسی</option><option value="ru">Русский</option><option value="zh">中文</option></select></div>'
          + '</div>'
          + '<div class="flex items-center gap-3 mt-4"><button id="save_conn" class="' + BTN_P + '">Save connection</button><span id="sec" class="text-sm text-dark-300"></span></div>'
          + '<div class="flex items-center gap-3 mt-3"><button id="connect" class="' + BTN_S + '">Connect bot</button>'
          + '<button id="disconnect" class="' + BTN_S + '">Disconnect bot</button></div>'
          + '<p id="sec" class="' + HELP + ' mt-2"></p></div>'
          + '<div class="' + CARD + '"><h2 class="' + CARD_T + '">Pricing</h2>'
          + '<p class="' + HELP + ' mb-4">Currency, pricing, test settings, device mode, pending nag hours.</p>'
          + '<div class="grid gap-3 sm:grid-cols-2">'
          + '<div><label class="' + LBL + '" for="currency">Currency label</label><input id="currency" class="' + INP + '" placeholder="Toman"></div>'
          + '<div><label class="' + LBL + '" for="rate">1 unit costs this much currency</label><input id="rate" inputmode="decimal" class="' + INP + '"><p class="' + HELP + '">Leave blank to sell flat in panel units.</p></div>'
          + '<div><label class="' + LBL + '" for="cardnum">Payment destination</label><input id="cardnum" class="' + INP + '"></div>'
          + '<div><label class="' + LBL + '" for="naghours">Nag pending buyers after (hours, 0 = off)</label><input id="naghours" inputmode="numeric" class="' + INP + '"></div>'
          + '</div>'
          + '<div class="grid gap-3 sm:grid-cols-2 mt-4">'
          + '<div><label class="flex items-center gap-2 text-sm"><input type="checkbox" id="teston" class="h-4 w-4 accent-primary"> Trial subscription enabled</label></div>'
          + '<div><label class="flex items-center gap-2 text-sm"><input type="checkbox" id="tierint" class="h-4 w-4 accent-primary"> Internal tiers (skip public tiers)</label></div>'
          + '<div class="flex items-center gap-3 mt-3"><button id="save_price" class="' + BTN_P + '">Save pricing</button><span id="msg_price" class="text-sm text-dark-300"></span></div>'
          + '</div>'
          + '</div>'
          + '<div class="' + CARD + '"><h2 class="' + CARD_T + '">Bot texts</h2>'
          + '<div class="mb-3 w-64"><label class="' + LBL + '" for="bosel">Which text</label><select id="bosel" class="' + INP + '" data-cur="welcome">'
          + '<option value="welcome">Welcome</option><option value="thanks">Receipt thanks</option><option value="approved">Approved</option>'
          + '<option value="denied">Denied</option><option value="referral">Referral program</option><option value="support">Support</option></select>'
          + '<p id="bolab" class="' + HELP + '"></p></div>'
          + '<div id="bovars"></div>'
          + fmtBar('botext') + '<textarea id="botext" rows="4" class="' + TA + '"></textarea>'
          + '<div class="flex items-center gap-2 mt-2"><button id="bodef" class="' + BTN_M + '" title="Load the bundled default for the selected bot language">Reset to default</button></div>'
          + '<p class="' + HELP + '" id="bostate"></p>'
          + '<div class="flex items-center gap-3 mt-2"><button id="save_text" class="' + BTN_P + '">Save bot texts</button><span id="msg_text" class="text-sm text-dark-300"></span></div>'
          + '<div class="' + CARD + '" id="extcard"><h2 class="' + CARD_T + '">External API — reuse the bot catalog elsewhere</h2><div class="max-w-xl">'
          + '<p class="text-xs text-dark-300 mb-3">Read-only JSON of exactly what buyers see (tiers, packages, services, converted prices) for your own website or widget. Send it as <code>X-Shop-Key</code> header or <code>?key=</code>. Anyone holding the key can read the catalog — rotate it if it leaks.</p>'
          + '<button id="xshow" class="' + BTN_M + '">Show my key</button><div id="xout" class="text-sm mt-3 break-all"></div></div>'
          + (adminMode ? '<div id="plugsec"><i class="text-sm text-dark-300">plugin settings…</i></div>' : '')
          + '</div>';
      }
      // Common shop fields live once, filled from the cache.
      if (shopCache) {
        q('prefix').value = shopCache.prefix || ''; q('tgid').value = shopCache.admin_tg_id || '';
        q('currency').value = shopCache.currency_label || ''; q('rate').value = shopCache.unit_rate || '';
        q('cardnum').value = shopCache.card_text || '';
        q('teston').checked = !!shopCache.test_enabled;
        q('tierint').checked = !!shopCache.tiers_internal;
        q('naghours').value = shopCache.pending_nag_hours == null ? 6 : shopCache.pending_nag_hours;
        q('lang').value = ['en', 'fa', 'ru', 'zh'].includes(shopCache.lang) ? shopCache.lang : 'en';
      }
      botextInit();
      const save = async () => {
        if (q('savmsg')) q('savmsg').textContent = 'Saving…';
        try {
          const body = {prefix: q('prefix').value, admin_tg_id: Number(q('tgid').value), lang: q('lang').value,
            card_text: q('cardnum').value.trim(), currency_label: q('currency').value,
            unit_rate: q('rate').value ? Number(q('rate').value) : null,
            test_enabled: q('teston').checked, tiers_internal: q('tierint').checked,
            pending_nag_hours: Math.max(0, Math.min(168, Number(q('naghours').value) || 0))};
          if (shopCache) {
            body.welcome_text = BOTXT.welcome; body.thank_you_text = BOTXT.thanks;
            body.approved_text = BOTXT.approved; body.denied_text = BOTXT.denied;
            body.referral_text = BOTXT.referral; body.support_text = BOTXT.support;
          }
          if (q('token').value) body.bot_token = q('token').value;
          const {r, w} = await saveShop(body);
          if (q('sec')) q('sec').textContent = w.ok ? 'Saved + bot connected ✓ ' + w.url
            : (w.error ? 'Saved. Bot NOT connected: ' + w.error + ' — press Connect bot to retry.'
              : 'Saved. Webhook secret: ' + r.webhook_secret);
          if (q('savmsg')) q('savmsg').textContent = 'Saved.';
          q('token').value = '';
          shopCache = Object.assign(shopCache || {}, {currency_label: body.currency_label, unit_rate: body.unit_rate});
        } catch (e) {
          const t = q('savmsg') || q('sec');
          if (t) t.textContent = 'Save failed: ' + e.message; else alert('Save failed: ' + e.message);
        }
      };
      q('save_conn').onclick = async () => {
        if (q('sec')) q('sec').textContent = 'Saving…';
        try {
          const body = {prefix: q('prefix').value, admin_tg_id: Number(q('tgid').value), lang: q('lang').value || 'en'};
          if (q('token').value) body.bot_token = q('token').value;
          const {r, w} = await saveShop(body);
          if (q('sec')) q('sec').textContent = w.ok ? 'Saved + bot connected ✓ ' + w.url
            : (w.error ? 'Saved. Bot NOT connected: ' + w.error + ' — press Connect bot to retry.'
              : 'Saved. Webhook secret: ' + r.webhook_secret);
          q('token').value = '';
        } catch (e) { if (q('sec')) q('sec').textContent = 'Save failed: ' + e.message; else alert('Save failed: ' + e.message); }
      };
      q('save_price').onclick = async () => {
        if (q('msg_price')) q('msg_price').textContent = 'Saving…';
        try {
          const body = {card_text: q('cardnum').value.trim(), currency_label: q('currency').value,
            unit_rate: q('rate').value ? Number(q('rate').value) : null,
            test_enabled: q('teston').checked, tiers_internal: q('tierint').checked,
            pending_nag_hours: Math.max(0, Math.min(168, Number(q('naghours').value) || 0)), lang: q('lang').value || 'en'};
          const {r, w} = await saveShop(body);
          const msg = (w && w.error) ? ('Saved. Webhook note: ' + w.error) : 'Saved.';
          if (q('msg_price')) q('msg_price').textContent = msg;
        } catch (e) { if (q('msg_price')) q('msg_price').textContent = 'Save failed: ' + e.message; }
      };
      q('save_text').onclick = async () => {
        if (q('msg_text')) q('msg_text').textContent = 'Saving…';
        try {
          const body = {lang: q('lang').value || 'en'};
          const cur = (q('bosel') && q('bosel').dataset.cur) || 'welcome';
          if (q('botext')) BOTXT[cur] = q('botext').value;
          if (shopCache) {
            body.welcome_text = BOTXT.welcome; body.thank_you_text = BOTXT.thanks;
            body.approved_text = BOTXT.approved; body.denied_text = BOTXT.denied;
            body.referral_text = BOTXT.referral; body.support_text = BOTXT.support;
          }
          const {r, w} = await saveShop(body);
          const msg = (w && w.error) ? ('Saved. Webhook note: ' + w.error) : 'Saved.';
          if (q('msg_text')) q('msg_text').textContent = msg;
        } catch (e) { if (q('msg_text')) q('msg_text').textContent = 'Save failed: ' + e.message; }
      };
      q('connect').onclick = async () => {
        if (q('sec')) q('sec').textContent = 'Connecting…';
        try {
          const r = await j(api + '/shops/mine/webhook', {method: 'POST'});
          const w = r.webhook || {}, s = r.status || {};
          const msg = w.ok ? ('Connected ✓ ' + w.url + (s.pending ? ' · ' + s.pending + ' queued' : '')
            + (s.last_error ? ' · last error: ' + s.last_error : ''))
            : ('Connect failed: ' + (w.error || 'unknown') + ' — ask superadmin to set the panel public URL, or enable polling mode.');
          if (q('sec')) q('sec').textContent = msg;
          await loadShop();
        } catch (e) { if (q('sec')) q('sec').textContent = 'Connect failed: ' + e.message; }
      };
      q('disconnect').onclick = async () => {
        if (!confirm('Disconnect the bot from this shop? Texts, catalog, buyers and credit stay. The bot stops responding until you connect a token again.')) return;
        if (q('sec')) q('sec').textContent = 'Disconnecting…';
        try {
          const r = await j(api + '/shops/mine/disconnect', {method: 'POST'});
          q('token').value = '';
          if (q('sec')) q('sec').textContent = r.disconnected ? 'Bot disconnected.' : 'No bot was connected.';
          await loadShop();
        } catch (e) { if (q('sec')) q('sec').textContent = 'Disconnect failed: ' + e.message; }
      };
      // External API key card.
      const xrender = (d) => {
        q('xout').innerHTML = d.enabled
          ? ('Endpoint: <code>' + esc(d.url) + '</code><br>Key: <code>' + esc(d.key || '') + '</code><br>'
            + 'Example: <code>' + esc(d.url) + '?key=' + esc(d.key || '') + '</code><br><br>'
            + '<button id="xrot" class="' + BTN_M + '">Rotate (old key dies)</button> <button id="xrev" class="' + BTN_M + '">Revoke</button><div id="xmsg" class="text-xs text-dark-300 mt-2"></div>')
          : 'Disabled. <button id="xrot" class="' + BTN_M + '">Generate key</button><div id="xmsg" class="text-xs text-dark-300 mt-2"></div>';
        const rot = q('xrot');
        if (rot) rot.onclick = async () => {
          const m = q('xmsg'); if (m) m.textContent = 'Working…';
          try {
            const r = await j(api + '/shops/mine/apikey/rotate', {method: 'POST'});
            xrender({enabled: true, key: r.key, url: r.url});
          } catch (e) { if (m) m.textContent = 'Failed: ' + e.message; }
        };
        const rev = q('xrev');
        if (rev) rev.onclick = async () => {
          if (!confirm('Revoke the external API key? Websites using it stop working.')) return;
          await j(api + '/shops/mine/apikey', {method: 'DELETE'});
          xrender({enabled: false});
        };
      };
      q('xshow').onclick = async () => {
        q('xout').textContent = 'Loading…';
        try { xrender(await j(api + '/shops/mine/apikey')); }
        catch (e) { q('xout').textContent = 'Create and save your shop first.'; }
      };
      // Superadmin-only plugin settings + danger zone, nested inside the page.
      if (adminMode) renderPluginSettings(q('plugsec'));
      wireFmt(el);
    };
    let BOTXT = {welcome: '', thanks: '', approved: '', denied: '', referral: '', support: ''};
    const botextInit = () => {
      const BOTEXT_VARS = {welcome: ['shop'], thanks: ['order'],
        approved: ['username', 'dot', 'status', 'plan', 'price', 'when', 'traffic', 'hwid', 'url'],
        denied: ['reason', 'username', 'plan'], referral: ['percent', 'link', 'invitee_discount'], support: []};
      if (!shopCache) { if (q('botext')) q('botext').value = ''; return; }
      BOTXT = {welcome: shopCache.welcome_text || '', thanks: shopCache.thank_you_text || '',
        approved: shopCache.approved_text || '', denied: shopCache.denied_text || '',
        referral: shopCache.referral_text || '', support: shopCache.support_text || ''};
      const botextShow = (key) => {
        q('botext').value = BOTXT[key] || '';
        q('bolab').textContent = {welcome: 'First message a buyer gets on /start (shows the shop menu).',
          thanks: 'Sent right after the buyer submits a receipt.',
          approved: 'The subscription delivery message. {url} only filled for new purchases — renewals reuse this text without it.',
          denied: 'Receipt rejected.',
          referral: 'Explains the referral program; buyers open this from the Referral menu.',
          support: 'Support section header message — usually a contact or rules text.'}[key] || '';
        q('bovars').innerHTML = varBar('botext', BOTEXT_VARS[key] || []);
        wireFmt(el);
      };
      q('bosel').onchange = () => {
        BOTXT[q('bosel').dataset.cur || 'welcome'] = q('botext').value;
        const k = q('bosel').value;
        q('bosel').dataset.cur = k;
        botextShow(k);
      };
      q('bodef').onclick = () => {
        const map = {welcome: 'welcome', thanks: 'thank_you', approved: 'approved',
          denied: 'denied', referral: 'referral', support: 'support'};
        const k = q('bosel').dataset.cur || 'welcome';
        const lng = (q('lang') && q('lang').value) || 'en';
        j(api + '/shops/default-texts?lang=' + encodeURIComponent(lng)).then(({defaults}) => {
          const v = defaults ? defaults[map[k]] : undefined;
          if (v !== undefined) { q('botext').value = v; BOTXT[k] = v; }
        }).catch(() => {});
      };
      q('bosel').dataset.cur = 'welcome';
      botextShow('welcome');
    };
    const renderPluginSettings = (host) => {
      let d;
      j(api + '/plugin-settings').then((info) => {
        d = info || {};
        // How-to buttons editor state: [{label, url, inline}] parsed from the
        // stored `Label | URL || Label | URL` string (line = own bot row).
        const tbState = [];
        try {
          (d.tutorial_buttons || '').split('\n').forEach((line) => {
            line.split('||').forEach((chunk, ci) => {
              if (!chunk.includes('|')) {
                if (chunk.trim()) tbState.push({label: chunk.trim(), url: '', inline: ci > 0});
                return;
              }
              const parts = chunk.split('|');
              const label = (parts[0] || '').trim();
              const url = (parts.slice(1).join('|') || '').trim();
              if (label || url) tbState.push({label: label, url: url, inline: ci > 0});
            });
          });
        } catch (e) { /* empty editor on any parse surprise */ }
        const tbSerialize = () => {
          let out = '';
          tbState.forEach((b, i) => {
            if (i > 0) out += b.inline ? ' || ' : '\n';
            out += (b.label || '').trim() + ' | ' + (b.url || '').trim();
          });
          return out;
        };
        host.innerHTML = '<div class="' + CARD + '"><h2 class="' + CARD_T + '">Plugin settings <span class="text-xs font-normal text-dark-300">superadmin only · applies to every shop</span></h2>'
          + '<p class="text-sm text-dark-300 mb-5">Version: ' + esc(d.version) + ' · Shops: ' + d.counts.shops
          + ' · Pending orders: ' + d.counts.pending_orders + ' · Subscriptions: ' + d.counts.subs + '</p>'
          + '<div class="space-y-5">'
          + '<div class="flex items-start gap-3"><input type="checkbox" id="poll" class="h-4 w-4 mt-1 accent-primary" ' + (d.polling_enabled ? 'checked' : '') + '>'
          + '<div><label for="poll" class="text-sm text-white">Polling mode</label>'
          + '<p class="' + HELP + '">For servers without public HTTPS. Webhook delivery is preferred — leave this off when the panel has a public URL.</p></div></div>'
          + '<div><label class="' + LBL + '" for="tutorial">How-to tutorial</label>' + fmtBar('tutorial')
          + '<textarea id="tutorial" rows="4" class="' + TA + '" placeholder="How buyers use the bot — every shop shows this. Blank hides the button.">' + esc(d.tutorial_text || '') + '</textarea></div>'
          + '<div><label class="' + LBL + '">How-to buttons (optional)</label>'
          + '<div id="tb_rows" class="space-y-2"></div>'
          + '<div class="mt-2 flex items-center gap-2"><button type="button" id="tb_add" class="' + BTN_M + '">+ Add button</button></div>'
          + '<p class="' + HELP + '">Each entry becomes one row in the bot, above the Back button. “Inline” places the button side-by-side with the one above it instead of starting a new row. Label and URL only — the text and its buttons stay the same message; only http(s) URLs are accepted.</p></div>'
          + '<div><span class="' + LBL + '">Test subscription size (every shop, one trial per person across all shops)</span>'
          + '<div class="grid gap-4 sm:grid-cols-4">'
          + '<div><label class="' + LBL + '" for="tdays">Duration (days)</label><input id="tdays" inputmode="numeric" class="' + INP + '" value="' + esc(d.test_duration_days == null ? 1 : d.test_duration_days) + '"></div>'
          + '<div><label class="' + LBL + '" for="tgb">Traffic GB (0 = unlimited)</label><input id="tgb" inputmode="numeric" class="' + INP + '" value="' + esc(d.test_traffic_gb == null ? 1 : d.test_traffic_gb) + '"></div>'
          + '<div><label class="' + LBL + '" for="tsquad">Test squad</label><select id="tsquad" class="' + INP + '"><option value="">panel default</option></select><p class="' + HELP + '">Trial accounts join the selected internal squad.</p></div>'
          + '<div><label class="' + LBL + '" for="thwid">HWID limit (blank = panel default)</label><input id="thwid" inputmode="numeric" class="' + INP + '" value="' + esc(d.test_hwid == null ? '' : d.test_hwid) + '"></div>'
          + '</div></div>'
          + '<div><label class="' + LBL + '" for="base">Panel public URL</label>'
          + '<input id="base" class="' + INP + '" placeholder="https://panel.example.com" value="' + esc(d.public_base_url || '') + '">'
          + '<p class="' + HELP + '">HTTPS origin — the plugin points each bot at itself, no manual setWebhook needed.</p></div>'
          + '<div class="flex items-center gap-3"><button id="psave" class="' + BTN_P + '">Save settings</button><span id="pmsg" class="text-sm text-dark-300"></span></div>'
          + '</div>'
          + '<div class="mt-8 border-t border-[var(--glass-border)] pt-5"><h3 class="text-sm font-semibold text-red-400 mb-2">Danger zone</h3>'
          + '<p class="text-xs text-dark-300 mb-3">Delete every shop, order, subscription, buyer, flag, broadcast and plugin setting. The database tables stay, but everything in them is gone. This cannot be undone.</p>'
          + '<button id="preset" class="' + BTN_D + '">Reset all PerShop data</button></div>'
          + '</div>';
        j(api + '/squads').then(({squads}) => {
          const sel = q('tsquad');
          (squads || []).forEach((s) => {
            const o = document.createElement('option');
            o.value = s.uuid; o.textContent = s.name || s.uuid;
            if (s.uuid === (d.test_squad_uuid || '')) o.selected = true;
            sel.appendChild(o);
          });
          if (d.test_squad_uuid && ![...sel.options].some((o) => o.value === d.test_squad_uuid)) {
            const o = document.createElement('option');
            o.value = d.test_squad_uuid; o.textContent = d.test_squad_uuid + ' (removed?)'; o.selected = true;
            sel.appendChild(o);
          }
        }).catch(() => {});
        const tbRender = () => {
          const box = q('tb_rows');
          if (!box) return;
          box.innerHTML = '';
          if (!tbState.length) {
            box.innerHTML = '<p class="text-xs text-dark-300">No buttons yet — buyers get just the tutorial text and Back.</p>';
          }
          tbState.forEach((b, i) => {
            const line = document.createElement('div');
            line.className = 'flex items-center gap-2';
            const dis = i === 0 ? 'disabled title="The first button starts its own row"' : (b.inline ? 'checked' : '');
            line.innerHTML = '<input id="tbl' + i + '" value="' + esc(b.label) + '" placeholder="Label (button text)" class="' + INP + '">'
              + '<input id="tbu' + i + '" value="' + esc(b.url) + '" placeholder="https://…" class="' + INP + '">'
              + '<label class="flex whitespace-nowrap items-center gap-1 text-xs text-dark-100" title="Same row as the button above"' + (i === 0 ? ' hidden' : '') + '><input type="checkbox" id="tbi' + i + '" class="h-4 w-4 accent-primary" ' + dis + '> inline</label>'
              + '<button type="button" id="tbd' + i + '" class="' + BTN_M + '" title="Remove this button">✕</button>';
            box.appendChild(line);
            q('tbl' + i).addEventListener('change', () => { tbState[i].label = q('tbl' + i).value; });
            q('tbu' + i).addEventListener('change', () => { tbState[i].url = q('tbu' + i).value; });
            if (i > 0) q('tbi' + i).addEventListener('change', () => { tbState[i].inline = q('tbi' + i).checked; });
            q('tbd' + i).addEventListener('click', () => { tbState.splice(i, 1); tbRender(); });
          });
        };
        tbRender();
        q('tb_add').addEventListener('click', () => { tbState.push({label: '', url: '', inline: false}); tbRender(); });
        q('psave').onclick = async () => {
          try {
            await j(api + '/plugin-settings', {method: 'PUT', body: JSON.stringify({polling_enabled: q('poll').checked,
              tutorial_text: q('tutorial').value, tutorial_buttons: tbSerialize(),
              test_duration_days: Number(q('tdays').value) || 1,
              test_traffic_gb: Number(q('tgb').value) || 0, test_squad_uuid: q('tsquad').value.trim(),
              test_hwid: q('thwid').value.trim() === '' ? null : Number(q('thwid').value) || 0, public_base_url: q('base').value.trim()})});
            q('pmsg').textContent = 'Saved.';
          } catch (e) { q('pmsg').textContent = 'Save failed: ' + e.message; }
        };
        q('preset').onclick = async () => {
          if (!confirm('Delete ALL PerShop data — every shop, order, subscription, buyer, flag and setting?')) return;
          if (!confirm('Last chance. This cannot be undone. Really reset everything?')) return;
          q('pmsg').textContent = 'Resetting…';
          try {
            const r = await j(api + '/admin/reset', {method: 'DELETE'});
            q('pmsg').textContent = 'Reset done (' + (r.wiped || []).length + ' tables wiped).';
            await loadShop();
            go();
          } catch (e) { q('pmsg').textContent = 'Reset failed: ' + e.message; }
        };
        wireFmt(host);
      }).catch(() => {
        host.innerHTML = '<div class="' + CARD + ' text-sm text-dark-300">Plugin settings require superadmin rights.</div>';
      });
    };

    // ============================== BOOT ==============================
    const boot = () => {
      el.innerHTML = '<div class="space-y-6">'
        + '<div class="page-header"><div class="flex items-center gap-2 min-w-0">'
        + ICON + '<div><h1 class="page-header-title">PerShop</h1>'
        + '<p class="text-muted-foreground mt-1 text-sm">Telegram shops for panel admins</p></div>'
        + '</div></div>'
        + '<div class="flex flex-col md:flex-row gap-4 md:gap-6">'
        + '<nav id="nav" class="flex md:flex-col gap-1 overflow-x-auto md:overflow-visible md:w-56 md:shrink-0 md:sticky md:top-6" style="align-self:start;padding-bottom:2px"></nav>'
        + '<div class="flex-1 min-w-0" id="pagebody"></div>'
        + '</div></div>';
      go();
    };
    // Superadmin probe decides whether Settings shows the plugin-level section;
    // owners get shop pages only. Shop cache feeds currency formatting + partial saves.
    loadShop(true).then(() =>
      j(api + '/plugin-settings').then(() => { adminMode = true; boot(); })
        .catch(() => { adminMode = false; boot(); }));
  },
  unmount(el) { el.innerHTML = ''; }
};
