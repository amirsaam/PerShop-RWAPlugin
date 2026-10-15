window.rwaPluginUI = window.rwaPluginUI || {};
window.rwaPluginUI['pershop'] = {
  mount(el) {
    const api = '/api/v2/plugins/pershop';
    const q = (id) => el.querySelector('#' + id);
    const j = async (url, opts) => {
      const r = await fetch(url, Object.assign({headers: {'Content-Type': 'application/json'}}, opts || {}));
      if (!r.ok) { const e = new Error(await r.text()); e.status = r.status; throw e; }
      return r.json();
    };
    const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    const shell = (inner) => '<div style="padding:16px;font-family:sans-serif;max-width:960px"><h2>RWA-PerShop</h2>' + inner + '</div>';

    // Panel 5.x mounts this module once on the single route /plugins/:pluginId,
    // so Settings/Shops/Orders are in-module tabs, not separate URLs.
    el.innerHTML = shell('<div><button data-tab="shops">Shops</button> <button data-tab="orders">Orders</button> <button data-tab="settings">Plugin Settings</button></div><div id="tabbody" style="margin-top:12px">Loading…</div>');

    const renderSettings = () => {
      q('tabbody').innerHTML = '<div id="s">Loading…</div>';
      j(api + '/plugin-settings').then((d) => {
        q('s').innerHTML = '<p>Version: ' + esc(d.version) + ' · Shops: ' + d.counts.shops
          + ' · Pending: ' + d.counts.pending_orders + ' · Subs: ' + d.counts.subs + '</p>'
          + '<label><input type="checkbox" id="poll" ' + (d.polling_enabled ? 'checked' : '') + '> Polling mode (for servers without public HTTPS; webhook is preferred)</label><br><br>'
          + '<h4>How-to tutorial (shown in every shop bot; blank hides the button)</h4>'
          + '<textarea id="tutorial" rows="4" cols="60" placeholder="how to use tutorial">' + esc(d.tutorial_text || '') + '</textarea><br><br>'
          + '<h4>Test subscription (size for all shops; each owner toggles it on/off)</h4>'
          + '<label>Duration days <input id="tdays" size="4" inputmode="numeric" value="' + esc(d.test_duration_days == null ? 1 : d.test_duration_days) + '"></label> '
          + '<label>Traffic GB (0 = unlimited) <input id="tgb" size="4" inputmode="numeric" value="' + esc(d.test_traffic_gb == null ? 1 : d.test_traffic_gb) + '"></label> '
          + '<label>Squad UUID (blank = panel default) <input id="tsquad" size="36" placeholder="internal squad uuid" value="' + esc(d.test_squad_uuid || '') + '"></label> '
          + '<label>HWID limit (blank = panel default) <input id="thwid" size="4" inputmode="numeric" value="' + esc(d.test_hwid == null ? '' : d.test_hwid) + '"></label><br><br>'
          + '<h4>Panel public URL (https origin — the plugin points each bot at itself, no manual setWebhook)</h4>'
          + '<input id="base" size="50" placeholder="https://panel.example.com" value="' + esc(d.public_base_url || '') + '"><br><br>'
          + '<button id="save">Save</button> <span id="msg"></span>';
        q('save').onclick = async () => {
          await j(api + '/plugin-settings', {method: 'PUT', body: JSON.stringify({polling_enabled: q('poll').checked,
            tutorial_text: q('tutorial').value, test_duration_days: Number(q('tdays').value) || 1,
            test_traffic_gb: Number(q('tgb').value) || 0, test_squad_uuid: q('tsquad').value.trim(),
            test_hwid: q('thwid').value.trim() === '' ? null : Number(q('thwid').value) || 0, public_base_url: q('base').value.trim()})});
          q('msg').textContent = 'Saved.';
        };
      }).catch((e) => { q('s').textContent = e.status === 403 ? 'Superadmin only.' : 'Error: ' + e.message; });
    };

    const renderShops = () => {
      q('tabbody').innerHTML = '<div id="stats" style="font-size:13px;margin-bottom:8px"></div><div id="all"></div><h3>My shop</h3><div id="mine" style="display:grid;gap:8px;max-width:520px">'
        + '<input id="prefix" placeholder="prefix"><input id="tgid" placeholder="admin telegram id" inputmode="numeric">'
        + '<select id="lang"><option value="en">English</option><option value="fa">فارسی</option><option value="ru">Русский</option><option value="zh">简体中文</option></select>'
        + '<input id="token" placeholder="bot token (blank keeps current)" type="password">'
        + '<input id="card" placeholder="payment/card text"><input id="welcome" placeholder="welcome text">'
        + '<input id="currency" placeholder="currency label, e.g. تومان">'
        + '<input id="rate" placeholder="unit rate, e.g. 250000 (1 unit = ? currency)" inputmode="decimal">'
        + '<textarea id="thanks" placeholder="thank-you text (after receipt)"></textarea>'
        + '<textarea id="approved" placeholder="approved text — {username} {expire} {url}"></textarea>'
        + '<textarea id="denied" placeholder="denied text — {reason}"></textarea>'
        + '<label><input type="checkbox" id="teston"> Test subscription enabled (free trial, one per person across all shops)</label>'
        + '<textarea id="referral" placeholder="referral program text (blank hides the button)"></textarea>'
        + '<input id="refpct" placeholder="referral reward % of each invited purchase, credited to inviter (0=off)" inputmode="decimal">'
        + '<textarea id="support" placeholder="support text, e.g. contact @username (blank hides the button)"></textarea>'
        + '<input id="naghours" placeholder="pending receipt nudge every N hours (0=off)" value="6" inputmode="numeric">'
        + '<button id="save">Save shop</button> <button id="connect">Connect bot</button><div id="sec" style="font-size:12px;color:#888"></div></div>'
        + '<h3>Catalog — tiers → packages</h3><div id="tiers"></div><div id="tierdetail" style="margin-top:12px"></div>'
        + '<h3>Other Services — categories → items (non-VPN, no duration/traffic/hwid)</h3><div id="scats"></div><div id="scatdetail" style="margin-top:12px"></div>'
        + '<h3>Plan matching — users without a plan</h3><div id="unmapped"><button id="uload">Load users</button></div>'
        + '<h3>Broadcast — send to all buyers</h3><div id="bc" style="display:grid;gap:8px;max-width:520px">'
        + '<div><button data-t="b">Bold</button> <button data-t="i">Italic</button> <button data-t="u">Underline</button> <button data-t="s">Strike</button> <button data-t="code">Code</button> <button data-t="link">Link</button></div>'
        + '<textarea id="bctext" rows="4" placeholder="Message for EVERY buyer, in your buyers language. HTML tags allowed: <b> <i> <u> <s> <code> <a href=&quot;...&quot;>"></textarea>'
        + '<input id="bcphoto" type="file" accept="image/*"><div id="bcsize" style="font-size:12px;color:#888"></div>'
        + '<button id="bcsend">Queue broadcast</button><div id="bcstat" style="font-size:12px"></div></div>'
        + '<h3>Buyer lookup — balances & flags (settles "I had credit!" disputes)</h3><div><input id="bq" placeholder="telegram id (blank = all)" inputmode="numeric"> <button id="bgo">Look up</button><div id="bout" style="font-size:12px;margin-top:8px"></div></div>'
        + '<h3>External API — reuse the bot catalog elsewhere</h3><div id="xapi" style="display:grid;gap:8px;max-width:520px">'
        + '<div style="font-size:12px">Read-only JSON of exactly what buyers see (tiers, packages, services, converted prices) for your own website or widget. Send it as <code>X-Shop-Key</code> header or <code>?key=</code>. Anyone holding the key can read the catalog — rotate it if it leaks.</div>'
        + '<button id="xshow">Show my key</button><div id="xout" style="font-size:12px;word-break:break-all"></div></div>';
      let shopCache = null, statsCache = null;
      const renderStats = () => {
        if (!statsCache) return;
        const s = statsCache;
        q('stats').innerHTML = '<b>Overview</b> · active subs: ' + (s.subs_active || 0)
          + ' · pending: ' + (s.orders_pending || 0)
          + ' · approved: ' + (s.orders_approved || 0) + ' (' + (s.units_sold || 0) + 'u)'
          + ' · denied: ' + (s.orders_denied || 0) + ' · withdrawn: ' + (s.orders_withdrawn || 0)
          + ' · trials: ' + (s.tests_claimed || 0) + ' · linked: ' + (s.subs_linked || 0)
          + ' · manual: ' + (s.subs_manual || 0)
          + ' · referral paid: ' + fmtU(s.referral_earned || 0);
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
      j(api + '/shops/mine').then(({shop}) => {
        if (!shop) return;
        shopCache = shop;
        renderStats();
        q('prefix').value = shop.prefix || ''; q('tgid').value = shop.admin_tg_id || '';
        q('card').value = shop.card_text || ''; q('welcome').value = shop.welcome_text || '';
        q('currency').value = shop.currency_label || ''; q('rate').value = shop.unit_rate || '';
        q('thanks').value = shop.thank_you_text || ''; q('approved').value = shop.approved_text || '';
        q('denied').value = shop.denied_text || '';
        q('teston').checked = !!shop.test_enabled;
        q('referral').value = shop.referral_text || ''; q('support').value = shop.support_text || '';
        q('refpct').value = shop.referral_percent || 0;
        q('naghours').value = shop.pending_nag_hours == null ? 6 : shop.pending_nag_hours;
        q('lang').value = ['en', 'fa', 'ru', 'zh'].includes(shop.lang) ? shop.lang : 'en';
        q('sec').textContent = 'Bot endpoint: POST ' + api + '/tg/' + shop.id + ' — press Connect bot to point Telegram at it.';
        loadTiers(); loadScats();
      }).catch(() => {});
      q('connect').onclick = async () => {
        q('sec').textContent = 'Connecting…';
        try {
          const r = await j(api + '/shops/mine/webhook', {method: 'POST'});
          const w = r.webhook || {}, s = r.status || {};
          q('sec').textContent = w.ok ? ('Connected ✓ ' + w.url + (s.pending ? ' · ' + s.pending + ' queued' : '')
            + (s.last_error ? ' · last error: ' + s.last_error : ''))
            : ('Connect failed: ' + (w.error || 'unknown') + ' — ask superadmin to set the panel public URL, or enable polling mode.');
        } catch (e) { q('sec').textContent = 'Connect failed: ' + e.message; }
      };
      q('save').onclick = async () => {
        try {
          const body = {prefix: q('prefix').value, admin_tg_id: Number(q('tgid').value), lang: q('lang').value,
            card_text: q('card').value, welcome_text: q('welcome').value,
            currency_label: q('currency').value, unit_rate: q('rate').value ? Number(q('rate').value) : null,
            thank_you_text: q('thanks').value, approved_text: q('approved').value, denied_text: q('denied').value,
            test_enabled: q('teston').checked, referral_text: q('referral').value, support_text: q('support').value,
            referral_percent: Math.max(0, Math.min(100, Number(q('refpct').value) || 0)),
            pending_nag_hours: Math.max(0, Math.min(168, Number(q('naghours').value) || 0))};
          if (q('token').value) body.bot_token = q('token').value;
          const r = await j(api + '/shops/mine', {method: 'PUT', body: JSON.stringify(body)});
          const w = r.webhook || {};
          q('sec').textContent = w.ok ? 'Saved + bot connected ✓ ' + w.url
            : (w.error ? 'Saved. Bot NOT connected: ' + w.error + ' — press Connect bot to retry.'
              : 'Saved. Webhook secret: ' + r.webhook_secret);
          shopCache = Object.assign(shopCache || {}, {currency_label: body.currency_label, unit_rate: body.unit_rate});
        } catch (e) { alert('Save failed: ' + e.message); }
      };
      q('bgo').onclick = async () => {
        try {
          const {buyers} = await j(api + '/buyers?search=' + encodeURIComponent(q('bq').value.trim()));
          q('bout').innerHTML = buyers.map((b) => 'tg <b>' + b.tg_id + '</b> · balance ' + esc(fmtU(b.credit_units))
            + ' · lang ' + esc(b.lang) + ' · subs ' + b.subs + (b.flagged ? ' · 🚫 scammer' : '')).join('<br>') || '<i>none</i>';
        } catch (e) { q('bout').textContent = 'Error: ' + e.message; }
      };
      const bcTags = {b: ['<b>', '</b>'], i: ['<i>', '</i>'], u: ['<u>', '</u>'],
        s: ['<s>', '</s>'], code: ['<code>', '</code>'], link: ['<a href="URL">', '</a>']};
      q('bc').querySelectorAll('[data-t]').forEach((b) => { b.onclick = () => {
        const t = q('bctext'), k = b.dataset.t;
        let tag = bcTags[k];
        if (k === 'link') { const u = prompt('Link URL:'); if (!u) return; tag = ['<a href="' + u + '">', '</a>']; }
        const s = t.selectionStart || 0, e = t.selectionEnd || 0, v = t.value;
        const sel = v.slice(s, e) || (k === 'link' ? 'text' : 'text');
        t.value = v.slice(0, s) + tag[0] + sel + tag[1] + v.slice(e);
        t.focus();
      }; });
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
          const r = await fetch(api + '/broadcast', {method: 'POST', body: fd}).then(async (x) => {
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
            + 'Example: <code>' + esc(d.url) + '?key=' + esc(d.key || '') + '</code><br>'
            + '<button id="xrot">Rotate (old key dies)</button> <button id="xrev">Revoke</button><div id="xmsg"></div>')
          : 'Disabled. <button id="xrot">Generate key</button><div id="xmsg"></div>';
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
          '<button data-t="' + t.id + '">' + esc(t.name) + (t.active ? '' : ' (off)')
          + (t.squad_uuid ? ' · ' + esc(names[t.squad_uuid] || t.squad_uuid.slice(0, 8)) : ' · no squad') + '</button>').join(' ')
          + ' <input id="newtier" placeholder="new tier name" size="18"> <button id="addtier">Add tier</button>';
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
          '<button data-c="' + c.id + '">' + esc(c.name) + (c.active ? '' : ' (off)') + '</button>').join(' ')
          + ' <input id="newscat" placeholder="new category name" size="18"> <button id="addscat">Add category</button>'
          + ' <button id="lensvc" title="Show items with no category">Uncategorized items</button>';
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
        d.innerHTML = '<h4>' + esc(title) + '</h4>'
          + (id == null ? '' : '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">'
            + '<input id="cname" value="' + esc(title) + '" size="16">'
            + '<input id="csort" placeholder="sort" value="' + ((service_cats.find((x) => x.id === id) || {}).sort || 0) + '" size="4" inputmode="numeric">'
            + '<label><input type="checkbox" id="cactive" ' + ((service_cats.find((x) => x.id === id) || {}).active === false ? '' : 'checked') + '> active</label>'
            + '<button id="csave">Save category</button><button id="cdel">Delete category</button></div>')
          + '<h5>Items</h5><div id="svcs">' + (items.map((s) =>
            '<div data-s="' + s.id + '">' + esc(s.name) + ' · ' + fmtU(s.price_units)
            + (s.active ? '' : ' (off)')
            + ' <button data-se="1">Edit</button> <button data-sd="1">Del</button></div>').join('') || '<i>none</i>') + '</div>'
          + '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">'
          + '<input id="sname" placeholder="item name" size="14"><input id="sunits" placeholder="price (units)" size="12" value="1" inputmode="decimal">'
          + '<label><input type="checkbox" id="sactive" checked> active</label>'
          + '<input id="sdesc" placeholder="description shown before buy" size="30">'
          + '<input id="sdone" placeholder="message sent to buyer on approval" size="30">'
          + '<button id="sadd">Add item</button></div>';
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
          try { await j(api + '/services/' + b.parentElement.dataset.s, {method: 'DELETE'}); }
          catch (e) { alert('Delete failed: ' + e.message); }
          showScat(id); }; });
        d.querySelectorAll('[data-se]').forEach((b) => { b.onclick = async () => {
          const s = items.find((x) => x.id === Number(b.parentElement.dataset.s));
          q('sname').value = s.name; q('sunits').value = s.price_units || 0;
          q('sactive').checked = s.active; q('sdesc').value = s.description || '';
          q('sdone').value = s.done_text || '';
          q('sadd').textContent = 'Save as new (delete old to replace)';
        }; });
      };
      const loadUnmapped = async () => {
        const [{orphans, unplanned}, {packages}] = await Promise.all([j(api + '/unmapped'), j(api + '/packages')]);
        const opts = () => '<select data-pick="1"><option value="">— plan —</option>' + packages.filter((p) =>
          (p.effect || 'full') === 'full').map((p) =>
          '<option value="' + p.id + '">' + esc(p.name) + ' · ' + fmtU(p.price_units) + '</option>').join('')
          + '</select>';
        const row = (uuid, label) => '<div data-u="' + esc(uuid) + '">' + label + ' ' + opts()
          + ' <button data-assign="1">Match</button></div>';
        q('unmapped').innerHTML = '<h5>Your panel users with no shop plan (' + orphans.length + ')</h5>'
          + (orphans.map((o) => row(o.uuid, '👤 ' + esc(o.username)
            + ' <span style="color:#888">' + esc(String(o.uuid).slice(0, 8)) + '</span>')).join('') || '<i>none</i>')
          + '<h5>Shop subscriptions with no plan (' + unplanned.length + ')</h5>'
          + (unplanned.map((s) => row(s.user_uuid, '📦 ' + esc(s.username)
            + (s.buyer_tg_id ? '' : ' <span style="color:#888">not linked</span>'))).join('') || '<i>none</i>');
        q('unmapped').querySelectorAll('[data-assign]').forEach((b) => { b.onclick = async () => {
          const div = b.parentElement;
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
        d.innerHTML = '<h4>' + esc(t.name) + '</h4>'
          + '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">'
          + '<input id="tname" value="' + esc(t.name) + '" size="16">'
          + '<span id="tsquadwrap"><select id="tsquad"><option value="">loading squads…</option></select></span>'
          + '<input id="thwid" placeholder="hwid default (blank=none)" value="' + (t.hwid_default == null ? '' : t.hwid_default) + '" size="20" inputmode="numeric">'
          + '<input id="tsort" placeholder="sort" value="' + t.sort + '" size="4" inputmode="numeric">'
          + '<label><input type="checkbox" id="tactive" ' + (t.active ? 'checked' : '') + '> active</label>'
          + '<button id="tsave">Save tier</button><button id="tdel">Delete tier</button></div>'
          + '<h5>Packages</h5><div id="pkgs">' + (pkgs.map((p) =>
            '<div data-p="' + p.id + '">' + esc(p.name) + ' · '
            + (Number(p.duration_days) === 0 ? 'never expires · ' : p.duration_days + 'd · ')
            + (p.traffic_limit_bytes ? (p.traffic_limit_bytes / 1073741824) + 'GB · ' : 'unlimited · ')
            + fmtU(p.price_units) + ' · hwid:' + esc(p.hwid_mode || 'default')
            + ' · ' + esc({full: 'full plan', time_only: 'time top-up', traffic_only: 'traffic top-up'}[p.effect] || 'full plan')
            + (p.scope_tier_id ? ' · only ' + esc((tiers.find((x) => x.id === p.scope_tier_id) || {}).name || ('tier ' + p.scope_tier_id)) : '')
            + (p.traffic_reset === false ? ' · topup' : '') + (p.active ? '' : ' (off)')
            + ' <button data-pe="1">Edit</button> <button data-pd="1">Del</button></div>').join('') || '<i>none</i>') + '</div>'
          + '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">'
          + '<input id="pname" placeholder="name" size="14"><input id="pdays" placeholder="days (0=never expires)" size="5" value="30" inputmode="numeric">'
          + '<input id="pgb" placeholder="GB (blank=unlimited)" size="14" inputmode="decimal">'
          + '<input id="punits" placeholder="price (units)" size="12" value="1" inputmode="decimal">'
          + '<label><input type="checkbox" id="pactive" checked> active</label>'
          + '<label title="ON: renew resets the cap to this package. OFF: leftover rolls over, package traffic is added on top."><input type="checkbox" id="preset" checked> reset traffic on renew</label></div>'
          + '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;align-items:center">'
          + '<select id="peffect" title="full plan: time+traffic. time top-up: +days only, needs days>0. traffic top-up: +GB only, needs a cap."><option value="full">full plan</option><option value="time_only">time top-up (+days)</option><option value="traffic_only">traffic top-up (+GB)</option></select> '
          + '<select id="pscope" title="top-up offers: which tier\u2019s subs see this offer. All tiers = everyone."><option value="">scope: all tiers</option>'
          + tiers.map((x) => '<option value="' + x.id + '"' + (x.id === id ? ' selected' : '') + '>scope: ' + esc(x.name) + ' only</option>').join('')
          + '</select> '
          + '<select id="phmode"><option value="default">hwid: panel default</option><option value="input">hwid: buyer input</option><option value="fixed">hwid: fixed by admin</option><option value="unlimited">hwid: unlimited</option></select> '
          + '<input id="phnums" placeholder="min,default,max,free,percent[,fixed value]" size="32" value="1,1,10,1,50">'
          + '<button id="padd">Add package</button></div>';
        q('tsave').onclick = async () => {
          await j(api + '/tiers/' + id, {method: 'PUT', body: JSON.stringify({name: q('tname').value,
            squad_uuid: q('tsquad').value.trim() || null,
            hwid_default: q('thwid').value === '' ? null : Number(q('thwid').value),
            sort: Number(q('tsort').value) || 0, active: q('tactive').checked})});
          loadTiers();
        };
        try {
          const {squads} = await j(api + '/squads');
          const wrap = document.getElementById('tsquadwrap');
          if (wrap && squads && squads.length) {
            const cur = t.squad_uuid || '';
            wrap.innerHTML = '<select id="tsquad">'
              + (cur && !squads.some((s) => s.uuid === cur)
                ? '<option value="' + esc(cur) + '">current: ' + esc(cur.slice(0, 8)) + '…</option>' : '')
              + '<option value="">— pick a squad —</option>'
              + squads.map((s) => '<option value="' + esc(s.uuid) + '"'
                + (s.uuid === cur ? ' selected' : '') + '>' + esc(s.name) + '</option>').join('')
              + '</select>';
          } else if (wrap) {
            wrap.innerHTML = '<input id="tsquad" placeholder="squad uuid" value="'
              + esc(t.squad_uuid || '') + '" size="36">';
          }
        } catch (e) {
          const wrap = document.getElementById('tsquadwrap');
          if (wrap) wrap.innerHTML = '<input id="tsquad" placeholder="squad uuid" value="'
            + esc(t.squad_uuid || '') + '" size="36">';
        }
        q('tdel').onclick = async () => {
          if (!confirm('Delete tier ' + t.name + '?')) return;
          try { await j(api + '/tiers/' + id, {method: 'DELETE'}); q('tierdetail').innerHTML = ''; loadTiers(); }
          catch (e) { alert('Delete failed: ' + e.message); }
        };
        const GB = 1073741824;
        const hwidBody = () => {
          const n = q('phnums').value.split(',').map((s) => Number(s.trim()));
          return {hwid_mode: q('phmode').value, hwid_min: n[0] || 0, hwid_default: n[1] || 0,
            hwid_max: n[2] || 0, hwid_free: n[3] || 0, hwid_percent: n[4] || 0,
            hwid_fixed: q('phmode').value === 'fixed' ? (n[5] || 0) : null}; };
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
          await j(api + '/packages/' + b.parentElement.dataset.p, {method: 'DELETE'}); showTier(id); }; });
        d.querySelectorAll('[data-pe]').forEach((b) => { b.onclick = async () => {
          const p = pkgs.find((x) => x.id === Number(b.parentElement.dataset.p));
          q('pname').value = p.name; q('pdays').value = p.duration_days;
          q('pgb').value = p.traffic_limit_bytes ? p.traffic_limit_bytes / GB : '';
          q('punits').value = p.price_units || 0;
          q('pactive').checked = p.active;
          q('preset').checked = p.traffic_reset !== false;
          q('peffect').value = p.effect || 'full';
          q('pscope').value = p.scope_tier_id || '';
          q('phmode').value = p.hwid_mode || 'default';
          q('phnums').value = [p.hwid_min, p.hwid_default, p.hwid_max, p.hwid_free, p.hwid_percent, p.hwid_fixed || 0].join(',');
          q('padd').textContent = 'Save as new (delete old to replace)';
        }; });
      };
    };

    const renderOrders = () => {
      q('tabbody').innerHTML = '<div><button data-s="pending">Pending</button> <button data-s="approved">Approved</button> <button data-s="denied">Denied</button> <button data-s="withdrawn">Withdrawn</button></div><div id="o" style="margin-top:12px"></div>';
      const load = async (status) => {
        const {orders} = await j(api + '/orders?status=' + status);
        q('o').innerHTML = orders.map((o) =>
          '<div data-id="' + o.id + '">#' + o.id + ' ' + esc(o.package_name) + ' · @' + esc(o.buyer_username) + (o.buyer_flagged ? ' 🚫 <button data-a="unscam" data-tg="' + o.buyer_tg_id + '">Unmark</button>' : '')
          + (o.total_units != null ? ' · ' + esc(String(o.total_units)) + 'u' : '')
          + (o.renew_username ? ' · renew ' + esc(o.renew_username) : '')
          + (o.status === 'withdraw_requested' ? ' <i>withdraw requested — reason: ' + esc(o.withdraw_reason || '') + ' · refund to: ' + esc(o.withdraw_card || '') + ' (decide in the bot DM)</i>'
            : status === 'pending' && o.status === 'pending' ? ' <button data-a="ok">Approve</button> <button data-a="no">Deny</button>' : ' <i>' + o.status + '</i>') + '</div>').join('') || '<i>none</i>';
        q('o').querySelectorAll('button').forEach((b) => { b.onclick = async () => {
          const id = b.parentElement.dataset.id;
          if (b.dataset.a === 'ok') await j(api + '/orders/' + id + '/approve', {method: 'POST'});
          else if (b.dataset.a === 'unscam') await j(api + '/scammers/' + b.dataset.tg, {method: 'DELETE'});
          else { const r = prompt('Decline reason:'); if (!r) return;
            await j(api + '/orders/' + id + '/deny', {method: 'POST', body: JSON.stringify({reason: r})}); }
          load(status);
        }; });
      };
      el.querySelectorAll('[data-s]').forEach((b) => { b.onclick = () => load(b.dataset.s); });
      load('pending');
    };

    const pages = {shops: renderShops, orders: renderOrders, settings: renderSettings};
    el.querySelectorAll('[data-tab]').forEach((b) => { b.onclick = () => pages[b.dataset.tab](); });
    renderShops();
  },
  unmount(el) { el.innerHTML = ''; }
};
