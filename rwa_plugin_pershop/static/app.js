window.rwaPluginUI = window.rwaPluginUI || {};
window.rwaPluginUI['pershop'] = {
  mount(el) {
    const api = '/api/v2/plugins/pershop';
    const q = (id) => el.querySelector('#' + id);
    const j = async (url, opts) => {
      const o = Object.assign({headers: {'Content-Type': 'application/json'}}, opts || {});
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

    // ---- Panel look: Tailwind theme tokens + legacy panel classes (.card/.table/glass-card).
    // shadcn React components are host-only; these class strings reproduce their look.
    const BTN_P = 'inline-flex h-9 items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-50';
    const BTN_S = 'inline-flex h-9 items-center justify-center rounded-md bg-secondary px-4 py-2 text-sm font-medium text-secondary-foreground shadow-sm hover:bg-secondary/80 disabled:pointer-events-none disabled:opacity-50';
    const BTN_D = 'inline-flex h-9 items-center justify-center rounded-md bg-destructive px-4 py-2 text-sm font-medium text-destructive-foreground shadow-sm hover:bg-destructive/90 disabled:pointer-events-none disabled:opacity-50';
    const BTN_M = 'inline-flex h-8 items-center justify-center rounded-md border border-input bg-background px-3 text-xs font-medium text-foreground shadow-sm hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50';
    const INP = 'flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm text-foreground shadow-sm';
    const TA = 'flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground shadow-sm';
    const LBL = 'mb-1.5 block text-sm font-medium text-white';
    const HELP = 'mt-1 text-[11px] text-dark-400';
    const CARD = 'glass-card p-5';
    const CARD_T = 'text-base font-semibold text-white mb-4';
    const ROW = 'flex items-center justify-between gap-2 rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground';
    const PILL = 'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ';
    const pillFor = (s) => PILL + ({pending: 'bg-amber-500/15 text-amber-400',
      withdraw_requested: 'bg-orange-500/15 text-orange-400', approved: 'bg-emerald-500/15 text-emerald-400',
      denied: 'bg-red-500/15 text-red-400', withdrawn: 'bg-gray-500/15 text-gray-400',
      cancelled: 'bg-gray-500/15 text-gray-400'}[s] || 'bg-gray-500/15 text-gray-400');

    // ---- Rich-text helpers: every buyer message sends as Telegram HTML, so all
    // message fields share one formatting toolbar; fields with {variables} also
    // get insert buttons. Variables only work where listed — anywhere else they
    // would be sent literally.
    const FMT_TAGS = {b: ['<b>', '</b>'], i: ['<i>', '</i>'], u: ['<u>', '</u>'],
      s: ['<s>', '</s>'], code: ['<code>', '</code>'], link: ['<a href="URL">', '</a>']};
    const FMT_NAMES = {b: 'Bold', i: 'Italic', u: 'Underline', s: 'Strike', code: 'Code', link: 'Link'};
    const fmtBar = (id) => '<div class="flex flex-wrap gap-1.5 mb-2">' + Object.keys(FMT_TAGS).map((t) =>
      '<button data-fmt="' + t + '" data-for="' + id + '" class="' + BTN_M + '">' + FMT_NAMES[t] + '</button>').join('')
      + '<button data-br data-for="' + id + '" class="' + BTN_M + '" title="Insert a blank line">⏎ Break</button></div>';
    const varBar = (id, vars) => (vars && vars.length)
      ? '<div class="flex flex-wrap items-center gap-1.5 mb-2"><span class="text-[11px] text-dark-400">Insert:</span>' + vars.map((v) =>
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
    const ICON = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-5 h-5 text-emerald-400 shrink-0"><path d="m2 7 4.41-4.41A2 2 0 0 1 7.83 2h8.34a2 2 0 0 1 1.42.59L22 7"/><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><path d="M15 22v-4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4"/><path d="M2 7h20"/></svg>';

    // Panel 5.x mounts this module once on the single route /plugins/:pluginId,
    // so sections are in-module tabs, not separate URLs.
    const renderShell = (tabs, active) => {
      el.innerHTML = '<div class="space-y-6">'
        + '<div class="flex items-center justify-between gap-3"><div class="flex items-center gap-2 min-w-0">'
        + ICON + '<h1 class="text-xl sm:text-2xl font-bold text-white truncate">PerShop</h1>'
        + '</div><span class="text-sm text-dark-300 shrink-0">Telegram shops for panel admins</span></div>'
        + '<div class="inline-flex h-9 items-center justify-center rounded-lg bg-muted p-1 text-muted-foreground">'
        + tabs.map((t) => '<button data-tab="' + t + '" class="inline-flex items-center justify-center whitespace-nowrap rounded-md px-3 py-1 text-sm font-medium transition-all'
          + (t === active ? ' bg-background text-foreground shadow' : ' hover:text-foreground') + '">'
          + {shops: 'Shops', orders: 'Orders', settings: 'Plugin Settings'}[t] + '</button>').join('')
        + '</div><div id="tabbody"></div></div>';
    };

    const renderSettings = (info) => {
      renderSettingsBody(info);
    };

    // Refetch on every open: the first probe result goes stale after a save.
    const openSettings = () => {
      q('tabbody').innerHTML = '<div class="' + CARD + '"><span class="text-sm text-dark-300">Loading…</span></div>';
      j(api + '/plugin-settings').then(renderSettingsBody).catch((e) => {
        q('tabbody').innerHTML = '<div class="' + CARD + ' text-sm text-dark-300">Unavailable: ' + esc(e.message) + '</div>';
      });
    };

    const renderSettingsBody = (info) => {
      const d = info || {};
      q('tabbody').innerHTML = '<div class="' + CARD + '">'
        + '<h2 class="' + CARD_T + '">Plugin Settings <span class="text-xs font-normal text-dark-400">superadmin only · applies to every shop</span></h2>'
        + '<p class="text-sm text-dark-300 mb-5">Version: ' + esc(d.version) + ' · Shops: ' + d.counts.shops
        + ' · Pending orders: ' + d.counts.pending_orders + ' · Subscriptions: ' + d.counts.subs + '</p>'
        + '<div class="space-y-5">'
        + '<div class="flex items-start gap-3"><input type="checkbox" id="poll" class="h-4 w-4 mt-1 accent-primary" ' + (d.polling_enabled ? 'checked' : '') + '>'
        + '<div><label for="poll" class="text-sm text-white">Polling mode</label>'
        + '<p class="' + HELP + '">For servers without public HTTPS. Webhook delivery is preferred — leave this off when the panel has a public URL.</p></div></div>'
        + '<div><label class="' + LBL + '" for="tutorial">How-to tutorial</label>' + fmtBar('tutorial')
        + '<textarea id="tutorial" rows="4" class="' + TA + '" placeholder="How buyers use the bot — every shop shows this. Blank hides the button.">' + esc(d.tutorial_text || '') + '</textarea></div>'
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
        + '<div class="flex items-center gap-3"><button id="save" class="' + BTN_P + '">Save settings</button><span id="msg" class="text-sm text-dark-300"></span></div>'
        + '</div>'
        + '<div class="mt-8 border-t border-input pt-5"><h3 class="text-sm font-semibold text-red-400 mb-2">Danger zone</h3>'
        + '<p class="text-xs text-dark-400 mb-3">Delete every shop, order, subscription, buyer, flag, broadcast and plugin setting. The database tables stay, but everything in them is gone. This cannot be undone.</p>'
        + '<button id="reset" class="' + BTN_D + '">Reset all PerShop data</button></div>'
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
      q('save').onclick = async () => {
        try {
          await j(api + '/plugin-settings', {method: 'PUT', body: JSON.stringify({polling_enabled: q('poll').checked,
            tutorial_text: q('tutorial').value, test_duration_days: Number(q('tdays').value) || 1,
            test_traffic_gb: Number(q('tgb').value) || 0, test_squad_uuid: q('tsquad').value.trim(),
            test_hwid: q('thwid').value.trim() === '' ? null : Number(q('thwid').value) || 0, public_base_url: q('base').value.trim()})});
          try {
            renderSettingsBody(await j(api + '/plugin-settings'));
            q('msg').textContent = 'Saved.';
          } catch (e2) { q('msg').textContent = 'Saved, but refresh failed: ' + e2.message; }
        } catch (e) { q('msg').textContent = 'Save failed: ' + e.message; }
      };
      q('reset').onclick = async () => {
        if (!confirm('Delete ALL PerShop data — every shop, order, subscription, buyer, flag and setting?')) return;
        if (!confirm('Last chance. This cannot be undone. Really reset everything?')) return;
        q('msg').textContent = 'Resetting…';
        try {
          const r = await j(api + '/admin/reset', {method: 'DELETE'});
          q('msg').textContent = 'Reset done (' + (r.wiped || []).length + ' tables wiped).';
          openSettings();
        } catch (e) { q('msg').textContent = 'Reset failed: ' + e.message; }
      };
      wireFmt(el);
    };

    const renderShops = (isAdmin) => {
      q('tabbody').innerHTML = '<div id="stats" class="grid grid-cols-2 sm:grid-cols-4 gap-3"></div>'
        + (isAdmin ? '<div class="' + CARD + ' mt-6"><h2 class="' + CARD_T + '">All shops</h2><div id="all" class="text-sm text-dark-300">Loading…</div></div>' : '')
        + '<div class="' + CARD + ' mt-6"><h2 class="' + CARD_T + '">My shop</h2><div id="mine" class="grid gap-4 sm:grid-cols-2">'
        + '<div><label class="' + LBL + '" for="prefix">Username prefix</label><input id="prefix" class="' + INP + '" placeholder="e.g. vip — every account name starts with this"><p class="' + HELP + '">Accounts are named prefix-telegramusername, numbered when taken.</p></div>'
        + '<div><label class="' + LBL + '" for="tgid">Admin Telegram ID</label><input id="tgid" inputmode="numeric" class="' + INP + '" placeholder="your numeric id — order alerts arrive here"></div>'
        + '<div><label class="' + LBL + '" for="lang">Bot language</label><select id="lang" class="' + INP + '"><option value="en">English</option><option value="fa">فارسی</option><option value="ru">Русский</option><option value="zh">简体中文</option></select></div>'
        + '<div><label class="' + LBL + '" for="token">Bot token</label><input id="token" type="password" class="' + INP + '" placeholder="paste a BotFather token to connect — blank leaves it as is"></div>'
        + '<div><label class="' + LBL + '" for="currency">Currency label</label><input id="currency" class="' + INP + '" placeholder="shown after every price, e.g. تومان"></div>'
        + '<div><label class="' + LBL + '" for="rate">Unit rate</label><input id="rate" inputmode="decimal" class="' + INP + '" placeholder="how much 1 unit costs, e.g. 250000"></div>'
        + '<div><label class="' + LBL + '" for="refpct">Referral reward %</label><input id="refpct" inputmode="decimal" class="' + INP + '" placeholder="0 = off"><p class="' + HELP + '">% of each invited purchase, gifted to the inviter as shop credit.</p></div>'
        + '<div><label class="' + LBL + '" for="naghours">Pending-receipt nudge (hours)</label><input id="naghours" inputmode="numeric" class="' + INP + '" placeholder="6"><p class="' + HELP + '">0 = off — remind me when a receipt waits longer than this.</p></div>'
        + '<div class="sm:col-span-2"><label class="' + LBL + '" for="bosel">Bot texts</label>'
        + '<select id="bosel" class="' + INP + ' max-w-xs mb-2"><option value="card">Payment / card text</option><option value="welcome">Welcome text</option><option value="thanks">Thank-you text (after receipt)</option><option value="approved">Approved text</option><option value="denied">Denied text</option><option value="referral">Referral program text (blank hides the button)</option><option value="support">Support text (blank hides the button)</option></select>'
        + fmtBar('botext') + '<div id="bovars"></div><textarea id="botext" rows="4" class="' + TA + '"></textarea></div>'
        + '<div class="sm:col-span-2 flex items-start gap-3"><input type="checkbox" id="teston" class="h-4 w-4 mt-1 accent-primary"><div><label for="teston" class="text-sm text-white">Test subscription enabled</label><p class="' + HELP + '">Free trial, one per person across all shops.</p></div></div>'
        + '</div><div class="flex flex-wrap items-center gap-2 mt-4"><button id="save" class="' + BTN_P + '">Save shop</button><button id="connect" class="' + BTN_S + '">Connect bot</button><button id="disconnect" class="' + BTN_M + '">Disconnect bot</button><span id="sec" class="text-xs text-dark-400"></span></div></div>'
        + '<div class="' + CARD + ' mt-6"><h2 class="' + CARD_T + '">Catalog — tiers → packages</h2><div id="tiers" class="flex flex-wrap gap-2"></div><div id="tierdetail" class="mt-4"></div></div>'
        + '<div class="' + CARD + ' mt-6"><h2 class="' + CARD_T + '">Other Services — categories → items</h2><p class="text-xs text-dark-400 mb-3">Non-VPN sellables. No duration, traffic or device limits.</p><div id="scats" class="flex flex-wrap gap-2"></div><div id="scatdetail" class="mt-4"></div></div>'
        + '<div class="' + CARD + ' mt-6"><h2 class="' + CARD_T + '">Plan matching — users without a plan</h2><div id="unmapped"><button id="uload" class="' + BTN_M + '">Load users</button></div></div>'
        + '<div class="' + CARD + ' mt-6"><h2 class="' + CARD_T + '">Broadcast — send to all buyers</h2><div class="grid gap-3 max-w-xl">'
        + fmtBar('bctext')
        + '<textarea id="bctext" rows="4" class="' + TA + '" placeholder="Message for EVERY buyer, in your buyers language. HTML tags allowed: <b> <i> <u> <s> <code> <a href=&quot;...&quot;>"></textarea>'
        + '<input id="bcphoto" type="file" accept="image/*" class="text-sm text-dark-300"><div id="bcsize" class="text-xs text-dark-400"></div>'
        + '<div><button id="bcsend" class="' + BTN_P + '">Queue broadcast</button></div><div id="bcstat" class="text-xs text-dark-300"></div></div></div>'
        + '<div class="' + CARD + ' mt-6"><h2 class="' + CARD_T + '">Buyers — scammer flags &amp; credit balances</h2>'
        + '<p class="text-xs text-dark-400 mb-3">Network-wide scammer flags are shared across all shops on this panel. Credit balances are per-shop.</p>'
        + '<div class="flex gap-2 max-w-xl mb-4"><input id="bq" inputmode="numeric" class="' + INP + '" placeholder="filter by telegram id"> <button id="bgo" class="' + BTN_S + '">Filter</button></div>'
        + '<h3 class="text-sm font-semibold text-white mb-2">Flagged as scammer</h3><div id="scamlist" class="text-sm text-dark-300 mb-4"><i>loading…</i></div>'
        + '<h3 class="text-sm font-semibold text-white mb-2">Credit balances</h3><div id="creditlist" class="text-sm text-dark-300"><i>loading…</i></div></div>'
        + '<div class="' + CARD + ' mt-6"><h2 class="' + CARD_T + '">External API — reuse the bot catalog elsewhere</h2><div class="max-w-xl">'
        + '<p class="text-xs text-dark-400 mb-3">Read-only JSON of exactly what buyers see (tiers, packages, services, converted prices) for your own website or widget. Send it as <code>X-Shop-Key</code> header or <code>?key=</code>. Anyone holding the key can read the catalog — rotate it if it leaks.</p>'
        + '<button id="xshow" class="' + BTN_M + '">Show my key</button><div id="xout" class="text-sm mt-3 break-all"></div></div></div>';
      let shopCache = null, statsCache = null;
      const renderStats = () => {
        if (!statsCache) return;
        const s = statsCache;
        const cell = (v, l) => '<div class="glass-card p-4"><div class="text-xl font-bold text-white">' + v + '</div><div class="text-xs text-dark-400 mt-0.5">' + l + '</div></div>';
        q('stats').innerHTML = cell(s.subs_active || 0, 'Active subs') + cell(s.orders_pending || 0, 'Pending orders')
          + cell((s.orders_approved || 0) + ' · ' + (s.units_sold || 0) + 'u', 'Approved · units sold')
          + cell(s.orders_denied || 0, 'Denied') + cell(s.orders_withdrawn || 0, 'Withdrawn')
          + cell(s.tests_claimed || 0, 'Trials') + cell(s.subs_linked || 0, 'Linked')
          + cell(fmtU(s.referral_earned || 0), 'Referral paid');
      };
      j(api + '/stats').then(({stats}) => {
        statsCache = stats || {};
        renderStats();
      }).catch(() => {});
      const fmtU = (u) => {
        if (u == null) return '';
        if (shopCache && shopCache.unit_rate && shopCache.currency_label)
          return (Number(u) * Number(shopCache.unit_rate)).toLocaleString('en-US') + ' ' + shopCache.currency_label;
        return u + (shopCache && shopCache.currency_label ? ' ' + shopCache.currency_label : ' units');
      };
      if (isAdmin) {
        j(api + '/shops/all').then(({shops}) => {
          q('all').innerHTML = shops.length ? '<table class="table"><thead><tr><th>Shop</th><th>Owner</th><th>Prefix</th><th>Bot</th></tr></thead><tbody>'
            + shops.map((s) => '<tr><td>#' + s.id + '</td><td>' + esc(s.owner_admin) + '</td><td><code>' + esc(s.prefix || '—') + '</code></td>'
              + '<td>' + (s.webhook_configured ? '<span class="' + pillFor('approved') + '">connected</span>' : '<span class="' + pillFor('pending') + '">not connected</span>') + '</td></tr>').join('')
            + '</tbody></table>' : '<i>no shops yet</i>';
        }).catch(() => { q('all').textContent = 'Unavailable.'; });
      }
      const BOTEXT_VARS = {approved: ['username', 'expire', 'url'], denied: ['reason']};
      let botextCache = {};
      const botextShow = (key) => {
        q('botext').value = botextCache[key] || '';
        q('bovars').innerHTML = varBar('botext', BOTEXT_VARS[key] || [])
          + (key === 'approved' ? '<p class="' + HELP + '">{url} is only filled on new purchases — renewals reuse this text without it.</p>' : '');
        wireFmt(el);
      };
      q('bosel').onchange = () => {
        botextCache[q('bosel').dataset.cur || 'card'] = q('botext').value;
        const k = q('bosel').value;
        q('bosel').dataset.cur = k;
        botextShow(k);
      };
      const loadMine = () => j(api + '/shops/mine').then(({shop}) => {
        if (!shop) {
          const note = '<i>Save your shop above first — tiers, packages and services unlock after setup.</i>';
          q('tiers').innerHTML = note; q('scats').innerHTML = note;
          return;
        }
        shopCache = shop;
        renderStats();
        q('prefix').value = shop.prefix || ''; q('tgid').value = shop.admin_tg_id || '';
        botextCache = {card: shop.card_text || '', welcome: shop.welcome_text || '',
          thanks: shop.thank_you_text || '', approved: shop.approved_text || '',
          denied: shop.denied_text || '', referral: shop.referral_text || '',
          support: shop.support_text || ''};
        q('bosel').value = 'card'; q('bosel').dataset.cur = 'card'; botextShow('card');
        q('currency').value = shop.currency_label || ''; q('rate').value = shop.unit_rate || '';
        q('teston').checked = !!shop.test_enabled;
        q('refpct').value = shop.referral_percent || 0;
        q('naghours').value = shop.pending_nag_hours == null ? 6 : shop.pending_nag_hours;
        q('lang').value = ['en', 'fa', 'ru', 'zh'].includes(shop.lang) ? shop.lang : 'en';
        q('sec').textContent = (shop.has_token ? '' : 'No bot connected — paste a token, Save, then Connect. ')
          + 'Bot endpoint: POST ' + api + '/tg/' + shop.id;
        loadTiers(); loadScats();
      }).catch(() => {});
      loadMine();
      q('connect').onclick = async () => {
        q('sec').textContent = 'Connecting…';
        try {
          const r = await j(api + '/shops/mine/webhook', {method: 'POST'});
          const w = r.webhook || {}, s = r.status || {};
          q('sec').textContent = w.ok ? ('Connected ✓ ' + w.url + (s.pending ? ' · ' + s.pending + ' queued' : '')
            + (s.last_error ? ' · last error: ' + s.last_error : ''))
            : ('Connect failed: ' + (w.error || 'unknown') + ' — ask superadmin to set the panel public URL, or enable polling mode.');
          loadMine();
        } catch (e) { q('sec').textContent = 'Connect failed: ' + e.message; }
      };
      q('disconnect').onclick = async () => {
        if (!confirm('Disconnect the bot from this shop? Texts, catalog, buyers and credit stay. The bot stops responding until you connect a token again.')) return;
        q('sec').textContent = 'Disconnecting…';
        try {
          const r = await j(api + '/shops/mine/disconnect', {method: 'POST'});
          q('token').value = '';
          q('sec').textContent = r.disconnected ? 'Bot disconnected.' : 'No bot was connected.';
          loadMine();
        } catch (e) { q('sec').textContent = 'Disconnect failed: ' + e.message; }
      };
      q('save').onclick = async () => {
        try {
          botextCache[q('bosel').dataset.cur || 'card'] = q('botext').value;
          const body = {prefix: q('prefix').value, admin_tg_id: Number(q('tgid').value), lang: q('lang').value,
            card_text: botextCache.card, welcome_text: botextCache.welcome,
            currency_label: q('currency').value, unit_rate: q('rate').value ? Number(q('rate').value) : null,
            thank_you_text: botextCache.thanks, approved_text: botextCache.approved, denied_text: botextCache.denied,
            test_enabled: q('teston').checked, referral_text: botextCache.referral, support_text: botextCache.support,
            referral_percent: Math.max(0, Math.min(100, Number(q('refpct').value) || 0)),
            pending_nag_hours: Math.max(0, Math.min(168, Number(q('naghours').value) || 0))};
          if (q('token').value) body.bot_token = q('token').value;
          const r = await j(api + '/shops/mine', {method: 'PUT', body: JSON.stringify(body)});
          const w = r.webhook || {};
          q('sec').textContent = w.ok ? 'Saved + bot connected ✓ ' + w.url
            : (w.error ? 'Saved. Bot NOT connected: ' + w.error + ' — press Connect bot to retry.'
              : 'Saved. Webhook secret: ' + r.webhook_secret);
          q('token').value = '';
          shopCache = Object.assign(shopCache || {}, {currency_label: body.currency_label, unit_rate: body.unit_rate});
          loadMine();
        } catch (e) { alert('Save failed: ' + e.message); }
      };
      const loadBuyers = async () => {
        const needle = q('bq').value.trim();
        try {
          const [{scammers}, {buyers}] = await Promise.all([
            j(api + '/scammers'),
            j(api + '/buyers?search=' + encodeURIComponent(needle)),
          ]);
          const scams = (scammers || []).filter((s) => !needle || String(s.tg_id).includes(needle));
          q('scamlist').innerHTML = scams.map((s) => '<div class="' + ROW + '"><span>tg <b>' + s.tg_id + '</b> · 🚫 '
            + esc(s.reason || 'no reason') + ' <span class="text-dark-400">(by ' + esc(s.marked_by_admin || '?') + ')</span></span>'
            + '<button data-unscam="' + s.tg_id + '" class="' + BTN_M + '">Unmark</button></div>').join('') || '<i>none</i>';
          const credited = (buyers || []).filter((b) => Number(b.credit_units) > 0);
          q('creditlist').innerHTML = credited.map((b) => '<div class="' + ROW + '"><span>tg <b>' + b.tg_id + '</b> · balance '
            + esc(fmtU(b.credit_units)) + ' · lang ' + esc(b.lang) + ' · subs ' + b.subs + (b.flagged ? ' · 🚫 scammer' : '') + '</span></div>').join('')
            || (needle ? '<i>this buyer holds no credit — disputes settled, nothing owed</i>' : '<i>nobody holds credit right now</i>');
          q('scamlist').querySelectorAll('[data-unscam]').forEach((b) => { b.onclick = async () => {
            if (!confirm('Remove the network-wide scammer flag for tg ' + b.dataset.unscam + '?')) return;
            try { await j(api + '/scammers/' + b.dataset.unscam, {method: 'DELETE'}); loadBuyers(); }
            catch (e) { alert('Unmark failed: ' + e.message); }
          }; });
        } catch (e) {
          q('scamlist').textContent = 'Error: ' + e.message;
          q('creditlist').textContent = '';
        }
      };
      q('bgo').onclick = loadBuyers;
      loadBuyers();
      wireFmt(el);
      q('bcphoto').onchange = () => {
        const f = q('bcphoto').files[0];
        q('bcsize').textContent = f ? (Math.round(f.size / 1024) + ' KB' + (f.size > 5 * 1024 * 1024 ? ' — TOO BIG, 5MB max' : '')) : '';
      };
      const loadBc = async () => {
        try {
          const {broadcasts} = await j(api + '/broadcasts');
          q('bcstat').innerHTML = (broadcasts || []).map((x) =>
            '#' + x.id + ' · ' + esc(x.status) + ' · ' + x.done + '/' + x.total + ' sent'
            + (x.failed ? ' · ' + x.failed + ' failed' : '')).join('<br>') || '<i>no broadcasts yet</i>';
        } catch (e) { q('bcstat').textContent = 'status unavailable'; }
      };
      q('bcsend').onclick = async () => {
        const f = q('bcphoto').files[0];
        if (f && f.size > 5 * 1024 * 1024) { alert('Photo must be under 5MB'); return; }
        const fd = new FormData();
        fd.append('text', q('bctext').value);
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
          q('bcstat').textContent = 'Queued #' + r.id + ' for ' + r.recipients + ' buyers — fans out ~25 per 30s.';
          q('bctext').value = ''; q('bcphoto').value = ''; q('bcsize').textContent = '';
          loadBc();
        } catch (e) { q('bcstat').textContent = 'Failed: ' + e.message; }
      };
      loadBc();
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
      const loadTiers = async () => {
        const {tiers} = await j(api + '/tiers');
        let names = {};
        try { const r = await j(api + '/squads'); (r.squads || []).forEach((s) => { names[s.uuid] = s.name; }); } catch (e) {}
        q('tiers').innerHTML = tiers.map((t) =>
          '<button data-t="' + t.id + '" class="' + BTN_M + '">' + esc(t.name) + (t.active ? '' : ' (off)')
          + (t.squad_uuid ? ' · ' + esc(names[t.squad_uuid] || t.squad_uuid.slice(0, 8)) : ' · no squad') + '</button>').join('')
          + '<span class="flex gap-2"><input id="newtier" class="' + INP + '" style="width:auto" placeholder="new tier name"> <button id="addtier" class="' + BTN_M + '">Add tier</button></span>';
        q('tiers').querySelectorAll('[data-t]').forEach((b) => { b.onclick = () => showTier(Number(b.dataset.t)); });
        q('addtier').onclick = async () => {
          if (!q('newtier').value.trim()) return;
          const r = await j(api + '/tiers', {method: 'POST', body: JSON.stringify({name: q('newtier').value.trim()})});
          loadTiers(); showTier(r.id);
        };
      };
      const loadScats = async () => {
        const {service_cats} = await j(api + '/service-cats');
        q('scats').innerHTML = (service_cats || []).map((c) =>
          '<button data-c="' + c.id + '" class="' + BTN_M + '">' + esc(c.name) + (c.active ? '' : ' (off)') + '</button>').join('')
          + '<span class="flex gap-2"><input id="newscat" class="' + INP + '" style="width:auto" placeholder="new category name"> <button id="addscat" class="' + BTN_M + '">Add category</button>'
          + '<button id="lensvc" title="Show items with no category" class="' + BTN_M + '">Uncategorized items</button></span>';
        q('scats').querySelectorAll('[data-c]').forEach((b) => { b.onclick = () => showScat(Number(b.dataset.c)); });
        q('addscat').onclick = async () => {
          if (!q('newscat').value.trim()) return;
          const r = await j(api + '/service-cats', {method: 'POST', body: JSON.stringify({name: q('newscat').value.trim()})});
          loadScats(); showScat(r.id);
        };
        q('lensvc').onclick = () => showScat(null);
      };
      const showScat = async (id) => {
        const {service_cats} = await j(api + '/service-cats');
        const {services} = await j(api + '/services');
        const items = services.filter((s) => (s.cat_id || null) === (id || null));
        const title = id == null ? 'Uncategorized' : ((service_cats.find((x) => x.id === id) || {}).name || '');
        if (id != null && !service_cats.some((x) => x.id === id)) return;
        const d = q('scatdetail');
        d.innerHTML = '<h4 class="text-sm font-semibold text-white mb-2">' + esc(title) + '</h4>'
          + (id == null ? '' : '<div class="grid gap-3 sm:grid-cols-2 mb-3">'
            + '<div><label class="' + LBL + '" for="cname">Category name</label><input id="cname" class="' + INP + '" value="' + esc(title) + '"><p class="' + HELP + '">First step buyers pick inside Other Services.</p></div>'
            + '<div><label class="' + LBL + '" for="csort">Sort order</label><input id="csort" class="' + INP + '" value="' + ((service_cats.find((x) => x.id === id) || {}).sort || 0) + '" inputmode="numeric"><p class="' + HELP + '">Lower numbers show first in the bot.</p></div>'
            + '</div><div class="flex gap-2 items-center mb-3">'
            + '<label class="text-sm text-dark-300"><input type="checkbox" id="cactive" class="h-4 w-4 accent-primary" ' + ((service_cats.find((x) => x.id === id) || {}).active === false ? '' : 'checked') + '> active</label>'
            + '<button id="csave" class="' + BTN_M + '">Save category</button><button id="cdel" class="' + BTN_M + '">Delete category</button></div>')
          + '<h5 class="text-xs font-medium uppercase tracking-wider text-dark-400 mb-2">Items</h5><div id="svcs" class="space-y-1.5">' + (items.map((s) =>
            '<div class="' + ROW + '" data-s="' + s.id + '"><span>' + esc(s.name) + ' · ' + fmtU(s.price_units)
            + (s.active ? '' : ' (off)') + '</span><span class="flex gap-1.5"><button data-se="1" class="' + BTN_M + '">Edit</button> <button data-sd="1" class="' + BTN_M + '">Del</button></span></div>').join('') || '<i class="text-sm text-dark-400">none</i>') + '</div>'
          + '<div class="grid gap-3 mt-3 sm:grid-cols-2">'
          + '<div><label class="' + LBL + '" for="sname">Item name</label><input id="sname" class="' + INP + '"><p class="' + HELP + '">Shown to buyers inside this category.</p></div>'
          + '<div><label class="' + LBL + '" for="sunits">Price (units)</label><input id="sunits" class="' + INP + '" value="1" inputmode="decimal"><p class="' + HELP + '">Multiplied by your currency rate for the buyer price.</p></div>'
          + '<div><label class="' + LBL + '" for="sdesc">Description (shown before buy)</label>' + fmtBar('sdesc') + '<textarea id="sdesc" rows="2" class="' + TA + '"></textarea></div>'
          + '<div><label class="' + LBL + '" for="sdone">Message sent to buyer on approval</label>' + fmtBar('sdone') + '<textarea id="sdone" rows="2" class="' + TA + '"></textarea></div>'
          + '</div><div class="flex gap-2 items-center mt-3"><label class="text-sm text-dark-300 self-center"><input type="checkbox" id="sactive" class="h-4 w-4 accent-primary" checked> active</label>'
          + '<button id="sadd" class="' + BTN_P + '">Add item</button></div>';
        if (id != null) {
          q('csave').onclick = async () => {
            await j(api + '/service-cats/' + id, {method: 'PUT', body: JSON.stringify({name: q('cname').value,
              sort: Number(q('csort').value) || 0, active: q('cactive').checked})});
            loadScats();
          };
          q('cdel').onclick = async () => {
            if (!confirm('Delete category ' + title + '? Items become uncategorized.')) return;
            try { await j(api + '/service-cats/' + id, {method: 'DELETE'}); q('scatdetail').innerHTML = ''; loadScats(); }
            catch (e) { alert('Delete failed: ' + e.message); }
          };
        }
        q('sadd').onclick = async () => {
          await j(api + '/services', {method: 'POST', body: JSON.stringify({name: q('sname').value,
            cat_id: id, price_units: Number(q('sunits').value) || 0,
            description: q('sdesc').value, done_text: q('sdone').value, active: q('sactive').checked})});
          showScat(id);
        };
        d.querySelectorAll('[data-sd]').forEach((b) => { b.onclick = async () => {
          try { await j(api + '/services/' + b.closest('[data-s]').dataset.s, {method: 'DELETE'}); }
          catch (e) { alert('Delete failed: ' + e.message); }
          showScat(id); }; });
        d.querySelectorAll('[data-se]').forEach((b) => { b.onclick = async () => {
          const s = items.find((x) => x.id === Number(b.closest('[data-s]').dataset.s));
          q('sname').value = s.name; q('sunits').value = s.price_units || 0;
          q('sactive').checked = s.active; q('sdesc').value = s.description || '';
          q('sdone').value = s.done_text || '';
          q('sadd').textContent = 'Save as new (delete old to replace)';
        }; });
        wireFmt(d);
      };
      const loadUnmapped = async () => {
        const [{orphans, unplanned}, {packages}] = await Promise.all([j(api + '/unmapped'), j(api + '/packages')]);
        const opts = () => '<select data-pick="1" class="' + INP + '" style="width:auto"><option value="">— plan —</option>' + packages.filter((p) =>
          (p.effect || 'full') === 'full').map((p) =>
          '<option value="' + p.id + '">' + esc(p.name) + ' · ' + fmtU(p.price_units) + '</option>').join('')
          + '</select>';
        const row = (uuid, label) => '<div class="' + ROW + '" data-u="' + esc(uuid) + '"><span>' + label + '</span><span class="flex gap-1.5">' + opts()
          + ' <button data-assign="1" class="' + BTN_M + '">Match</button></span></div>';
        q('unmapped').innerHTML = '<h5 class="text-xs font-medium uppercase tracking-wider text-dark-400 mb-2">Your panel users with no shop plan (' + orphans.length + ')</h5><div class="space-y-1.5 mb-4">'
          + (orphans.map((o) => row(o.uuid, '👤 ' + esc(o.username)
            + ' <span class="text-dark-400">' + esc(String(o.uuid).slice(0, 8)) + '</span>')).join('') || '<i class="text-sm text-dark-400">none</i>') + '</div>'
          + '<h5 class="text-xs font-medium uppercase tracking-wider text-dark-400 mb-2">Shop subscriptions with no plan (' + unplanned.length + ')</h5><div class="space-y-1.5">'
          + (unplanned.map((s) => row(s.user_uuid, '📦 ' + esc(s.username)
            + (s.buyer_tg_id ? '' : ' <span class="text-dark-400">not linked</span>'))).join('') || '<i class="text-sm text-dark-400">none</i>') + '</div>';
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
      };
      q('uload').onclick = () => { loadUnmapped().catch((e) => alert('Load failed: ' + e.message)); };
      const showTier = async (id) => {
        const {tiers} = await j(api + '/tiers');
        const t = tiers.find((x) => x.id === id);
        if (!t) return;
        const {packages} = await j(api + '/packages');
        const pkgs = packages.filter((p) => p.tier_id === id);
        const d = q('tierdetail');
        d.innerHTML = '<h4 class="text-sm font-semibold text-white mb-2">' + esc(t.name) + '</h4>'
          + '<div class="grid gap-3 sm:grid-cols-2 mb-3">'
          + '<div><label class="' + LBL + '" for="tname">Tier name</label><input id="tname" class="' + INP + '" value="' + esc(t.name) + '"><p class="' + HELP + '">Shown to buyers as the first step of the buy flow.</p></div>'
          + '<div><label class="' + LBL + '" for="tsquad">Internal squad</label><span id="tsquadwrap"><select id="tsquad" class="' + INP + '"><option value="">loading squads…</option></select></span><p class="' + HELP + '">Sold accounts join this squad. Lists the squads you can see.</p></div>'
          + '<div><label class="' + LBL + '" for="thwid">HWID default</label><input id="thwid" class="' + INP + '" placeholder="blank = panel default" value="' + (t.hwid_default == null ? '' : t.hwid_default) + '" inputmode="numeric"><p class="' + HELP + '">Fallback device limit for packages set to panel default.</p></div>'
          + '<div><label class="' + LBL + '" for="tsort">Sort order</label><input id="tsort" class="' + INP + '" value="' + t.sort + '" inputmode="numeric"><p class="' + HELP + '">Lower numbers show first in the bot.</p></div>'
          + '</div><div class="flex gap-2 items-center mb-3">'
          + '<label class="text-sm text-dark-300"><input type="checkbox" id="tactive" class="h-4 w-4 accent-primary" ' + (t.active ? 'checked' : '') + '> active</label>'
          + '<button id="tsave" class="' + BTN_M + '">Save tier</button><button id="tdel" class="' + BTN_M + '">Delete tier</button></div>'
          + '<h5 class="text-xs font-medium uppercase tracking-wider text-dark-400 mb-2">Packages</h5><div id="pkgs" class="space-y-1.5">' + (pkgs.map((p) =>
            '<div class="' + ROW + '" data-p="' + p.id + '"><span>' + esc(p.name) + ' · '
            + (Number(p.duration_days) === 0 ? 'never expires · ' : p.duration_days + 'd · ')
            + (p.traffic_limit_bytes ? (p.traffic_limit_bytes / 1073741824) + 'GB · ' : 'unlimited · ')
            + fmtU(p.price_units) + ' · hwid:' + esc(p.hwid_mode || 'default')
            + ' · ' + esc({full: 'full plan', time_only: 'time top-up', traffic_only: 'traffic top-up'}[p.effect] || 'full plan')
            + (p.scope_tier_id ? ' · only ' + esc((tiers.find((x) => x.id === p.scope_tier_id) || {}).name || ('tier ' + p.scope_tier_id)) : '')
            + (p.traffic_reset === false ? ' · topup' : '') + (p.active ? '' : ' (off)')
            + '</span><span class="flex gap-1.5"><button data-pe="1" class="' + BTN_M + '">Edit</button> <button data-pd="1" class="' + BTN_M + '">Del</button></span></div>').join('') || '<i class="text-sm text-dark-400">none</i>') + '</div>'
          + '<div class="grid gap-3 sm:grid-cols-2 mt-3">'
          + '<div><label class="' + LBL + '" for="pname">Package name</label><input id="pname" class="' + INP + '"><p class="' + HELP + '">Shown to buyers inside this tier.</p></div>'
          + '<div><label class="' + LBL + '" for="pdays">Duration (days)</label><input id="pdays" class="' + INP + '" value="30" inputmode="numeric"><p class="' + HELP + '">0 = never expires (∞).</p></div>'
          + '<div><label class="' + LBL + '" for="pgb">Traffic cap (GB)</label><input id="pgb" class="' + INP + '" inputmode="decimal"><p class="' + HELP + '">Blank = unlimited.</p></div>'
          + '<div><label class="' + LBL + '" for="punits">Price (units)</label><input id="punits" class="' + INP + '" value="1" inputmode="decimal"><p class="' + HELP + '">Multiplied by your currency rate for the buyer price.</p></div>'
          + '<div><label class="' + LBL + '" for="peffect">Package type</label>'
          + '<select id="peffect" class="' + INP + '"><option value="full">full plan</option><option value="time_only">time top-up (+days)</option><option value="traffic_only">traffic top-up (+GB)</option></select>'
          + '<p class="' + HELP + '">Full plan: time + traffic. Time top-up adds days only (needs days &gt; 0). Traffic top-up adds GB only (needs a cap).</p></div>'
          + '<div><label class="' + LBL + '" for="pscope">Offer scope</label>'
          + '<select id="pscope" class="' + INP + '"><option value="">scope: all tiers</option>'
          + tiers.map((x) => '<option value="' + x.id + '"' + (x.id === id ? ' selected' : '') + '>scope: ' + esc(x.name) + ' only</option>').join('')
          + '</select><p class="' + HELP + '">Top-up offers: which tier\u2019s subs see this offer. Full plans ignore scope.</p></div>'
          + '<div><label class="text-sm text-dark-300"><input type="checkbox" id="pactive" class="h-4 w-4 accent-primary" checked> active</label><p class="' + HELP + '">Off hides the package from the bot.</p></div>'
          + '<div><label class="text-sm text-dark-300"><input type="checkbox" id="preset" class="h-4 w-4 accent-primary" checked> reset traffic on renew</label><p class="' + HELP + '">ON: renew resets the cap to this package. OFF: leftover rolls over, package traffic is added on top.</p></div>'
          + '</div>'
          + '<div class="mt-3"><label class="' + LBL + '" for="phmode">Device limit (HWID)</label>'
          + '<select id="phmode" class="' + INP + '" style="width:auto"><option value="default">hwid: panel default</option><option value="input">hwid: buyer input</option><option value="fixed">hwid: fixed by admin</option><option value="unlimited">hwid: unlimited</option></select> '
          + '<p class="' + HELP + '">Panel default: leave to the panel. Buyer input: the buyer picks the device count, extra devices cost extra. Fixed: every buyer gets the same count. Unlimited: no device limit.</p>'
          + '<div id="phinput" class="grid grid-cols-2 sm:grid-cols-5 gap-2 mt-2">'
          + '<div><label class="' + LBL + '" for="phmin">Min</label><input id="phmin" class="' + INP + '" value="1" inputmode="numeric"></div>'
          + '<div><label class="' + LBL + '" for="phstart">Start value</label><input id="phstart" class="' + INP + '" value="1" inputmode="numeric"></div>'
          + '<div><label class="' + LBL + '" for="phmax">Max</label><input id="phmax" class="' + INP + '" value="10" inputmode="numeric"></div>'
          + '<div><label class="' + LBL + '" for="phfree">Free incl.</label><input id="phfree" class="' + INP + '" value="1" inputmode="numeric"></div>'
          + '<div><label class="' + LBL + '" for="phpct">Extra %</label><input id="phpct" class="' + INP + '" value="50" inputmode="numeric"></div>'
          + '</div>'
          + '<div id="phfixed" class="mt-2" style="display:none"><label class="' + LBL + '" for="phfixedn">Fixed device count</label><input id="phfixedn" class="' + INP + '" style="width:8rem" value="1" inputmode="numeric"></div>'
          + '</div>'
          + '<button id="padd" class="' + BTN_P + ' mt-3">Add package</button>';
        q('tsave').onclick = async () => {
          await j(api + '/tiers/' + id, {method: 'PUT', body: JSON.stringify({name: q('tname').value,
            squad_uuid: q('tsquad').value.trim() || null,
            hwid_default: q('thwid').value === '' ? null : Number(q('thwid').value),
            sort: Number(q('tsort').value) || 0, active: q('tactive').checked})});
          loadTiers();
        };
        try {
          const {squads} = await j(api + '/squads');
          const wrap = el.querySelector('#tsquadwrap');
          if (wrap && squads && squads.length) {
            const cur = t.squad_uuid || '';
            wrap.innerHTML = '<select id="tsquad" class="' + INP + '">'
              + (cur && !squads.some((s) => s.uuid === cur)
                ? '<option value="' + esc(cur) + '">current: ' + esc(cur.slice(0, 8)) + '…</option>' : '')
              + '<option value="">— pick a squad —</option>'
              + squads.map((s) => '<option value="' + esc(s.uuid) + '"'
                + (s.uuid === cur ? ' selected' : '') + '>' + esc(s.name) + '</option>').join('')
              + '</select>';
          } else if (wrap) {
            wrap.innerHTML = '<input id="tsquad" class="' + INP + '" placeholder="squad uuid" value="'
              + esc(t.squad_uuid || '') + '">';
          }
        } catch (e) {
          const wrap = el.querySelector('#tsquadwrap');
          if (wrap) wrap.innerHTML = '<input id="tsquad" class="' + INP + '" placeholder="squad uuid" value="'
            + esc(t.squad_uuid || '') + '">';
        }
        q('tdel').onclick = async () => {
          if (!confirm('Delete tier ' + t.name + '?')) return;
          try { await j(api + '/tiers/' + id, {method: 'DELETE'}); q('tierdetail').innerHTML = ''; loadTiers(); }
          catch (e) { alert('Delete failed: ' + e.message); }
        };
        const GB = 1073741824;
        const gv = (id, fb) => { const v = q(id).value.trim(); return v === '' ? fb : (Number(v) || 0); };
        const hwidBody = () => {
          const mode = q('phmode').value;
          const b = {hwid_mode: mode, hwid_min: 0, hwid_default: 0,
            hwid_max: 0, hwid_free: 0, hwid_percent: 0, hwid_fixed: null};
          if (mode === 'input') { b.hwid_min = gv('phmin', 1); b.hwid_default = gv('phstart', 1);
            b.hwid_max = gv('phmax', 10); b.hwid_free = gv('phfree', 1); b.hwid_percent = gv('phpct', 50); }
          else if (mode === 'fixed') { b.hwid_fixed = gv('phfixedn', 1); }
          return b; };
        const syncHwid = () => { const m = q('phmode').value;
          q('phinput').style.display = m === 'input' ? '' : 'none';
          q('phfixed').style.display = m === 'fixed' ? '' : 'none'; };
        q('phmode').onchange = syncHwid;
        q('padd').onclick = async () => {
          await j(api + '/packages', {method: 'POST', body: JSON.stringify(Object.assign({name: q('pname').value,
            tier_id: id, duration_days: Math.max(0, Number(q('pdays').value) || 0),
            traffic_limit_bytes: q('pgb').value === '' ? null : Math.round(Number(q('pgb').value) * GB),
            price_units: Number(q('punits').value) || 0, traffic_reset: q('preset').checked,
            effect: q('peffect').value,
            scope_tier_id: q('pscope').value ? Number(q('pscope').value) : null,
            active: q('pactive').checked}, hwidBody()))});
          showTier(id);
        };
        d.querySelectorAll('[data-pd]').forEach((b) => { b.onclick = async () => {
          await j(api + '/packages/' + b.closest('[data-p]').dataset.p, {method: 'DELETE'}); showTier(id); }; });
        d.querySelectorAll('[data-pe]').forEach((b) => { b.onclick = async () => {
          const p = pkgs.find((x) => x.id === Number(b.closest('[data-p]').dataset.p));
          q('pname').value = p.name; q('pdays').value = p.duration_days;
          q('pgb').value = p.traffic_limit_bytes ? p.traffic_limit_bytes / GB : '';
          q('punits').value = p.price_units || 0;
          q('pactive').checked = p.active;
          q('preset').checked = p.traffic_reset !== false;
          q('peffect').value = p.effect || 'full';
          q('pscope').value = p.scope_tier_id || '';
          q('phmode').value = p.hwid_mode || 'default';
          q('phmin').value = p.hwid_min == null ? 1 : p.hwid_min;
          q('phstart').value = p.hwid_default == null ? 1 : p.hwid_default;
          q('phmax').value = p.hwid_max == null ? 10 : p.hwid_max;
          q('phfree').value = p.hwid_free == null ? 1 : p.hwid_free;
          q('phpct').value = p.hwid_percent == null ? 50 : p.hwid_percent;
          q('phfixedn').value = p.hwid_fixed || 1;
          syncHwid();
          q('padd').textContent = 'Save as new (delete old to replace)';
        }; });
      };
    };

    const renderOrders = () => {
      q('tabbody').innerHTML = '<div class="inline-flex h-9 items-center justify-center rounded-lg bg-muted p-1 text-muted-foreground">'
        + ['pending', 'approved', 'denied', 'withdrawn'].map((s) =>
          '<button data-s="' + s + '" class="inline-flex items-center justify-center whitespace-nowrap rounded-md px-3 py-1 text-sm font-medium capitalize transition-all'
          + (s === 'pending' ? ' bg-background text-foreground shadow' : ' hover:text-foreground') + '">' + s + '</button>').join('')
        + '</div><div id="o" class="mt-4"></div>';
      const load = async (status) => {
        const {orders} = await j(api + '/orders?status=' + status);
        q('o').innerHTML = orders.length ? '<div class="' + CARD + ' !p-0 overflow-hidden"><table class="table"><thead><tr><th>Order</th><th>Item</th><th>Buyer</th><th>Amount</th><th>Type</th><th>Status</th><th></th></tr></thead><tbody>'
          + orders.map((o) =>
            '<tr data-id="' + o.id + '"><td class="font-medium">#' + o.id + '</td><td>' + esc(o.package_name) + '</td>'
            + '<td>@' + esc(o.buyer_username) + (o.buyer_flagged ? ' 🚫 <button data-a="unscam" data-tg="' + o.buyer_tg_id + '" class="' + BTN_M + '">Unmark</button>' : '') + '</td>'
            + '<td>' + (o.total_units != null ? esc(String(o.total_units)) + 'u' : '—') + '</td>'
            + '<td>' + (o.renew_username ? 'renew ' + esc(o.renew_username) : 'new') + '</td>'
            + '<td>' + (o.status === 'withdraw_requested' ? '<span class="' + pillFor('withdraw_requested') + '">withdraw requested</span><div class="text-xs text-dark-400 mt-1">reason: ' + esc(o.withdraw_reason || '') + ' · refund to: ' + esc(o.withdraw_card || '') + ' (decide in the bot DM)</div>'
              : '<span class="' + pillFor(o.status) + '">' + esc(o.status) + '</span>') + '</td>'
            + '<td class="whitespace-nowrap">' + (status === 'pending' && o.status === 'pending'
              ? '<button data-a="ok" class="' + BTN_M + '">Approve</button> <button data-a="no" class="' + BTN_M + '">Deny</button>' : '') + '</td></tr>').join('')
          + '</tbody></table></div>' : '<i class="text-sm text-dark-400">none</i>';
        q('o').querySelectorAll('button').forEach((b) => { b.onclick = async () => {
          const id = b.closest('[data-id]').dataset.id;
          if (b.dataset.a === 'ok') await j(api + '/orders/' + id + '/approve', {method: 'POST'});
          else if (b.dataset.a === 'unscam') await j(api + '/scammers/' + b.dataset.tg, {method: 'DELETE'});
          else { const r = prompt('Decline reason:'); if (!r) return;
            await j(api + '/orders/' + id + '/deny', {method: 'POST', body: JSON.stringify({reason: r})}); }
          load(status);
        }; });
      };
      el.querySelectorAll('[data-s]').forEach((b) => { b.onclick = (ev) => {
        el.querySelectorAll('[data-s]').forEach((x) => { x.className = x.className.replace(' bg-background text-foreground shadow', ' hover:text-foreground'); });
        ev.currentTarget.className += ' bg-background text-foreground shadow';
        load(b.dataset.s); }; });
      load('pending');
    };

    // Superadmin probe: /plugin-settings is pershop_system:manage (superadmin-only).
    // Admins get the settings tab first + an all-shops overview; shop owners get shops/orders.
    j(api + '/plugin-settings').then((info) => {
      const tabs = ['settings', 'shops', 'orders'];
      renderShell(tabs, 'settings');
      const pages = {shops: () => renderShops(true), orders: renderOrders, settings: openSettings};
      el.querySelectorAll('[data-tab]').forEach((b) => { b.onclick = () => {
        el.querySelectorAll('[data-tab]').forEach((x) => { x.className = x.className.replace(' bg-background text-foreground shadow', ''); });
        b.className += ' bg-background text-foreground shadow';
        pages[b.dataset.tab](); }; });
      openSettings();
    }).catch(() => {
      const tabs = ['shops', 'orders'];
      renderShell(tabs, 'shops');
      const pages = {shops: () => renderShops(false), orders: renderOrders};
      el.querySelectorAll('[data-tab]').forEach((b) => { b.onclick = () => {
        el.querySelectorAll('[data-tab]').forEach((x) => { x.className = x.className.replace(' bg-background text-foreground shadow', ''); });
        b.className += ' bg-background text-foreground shadow';
        pages[b.dataset.tab](); }; });
      renderShops(false);
    });
  },
  unmount(el) { el.innerHTML = ''; }
};
