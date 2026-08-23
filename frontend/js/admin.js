/* ════════════════════════════════════════════════════════════════════
   Admin panel: users, cities, exports, daily-report settings, dashboard.
   ════════════════════════════════════════════════════════════════════ */
window.Admin = (function () {
  'use strict';

  const $ = UI.$;
  const el = UI.el;

  /* ── Users ─────────────────────────────────────────────────────── */
  async function loadUsers() {
    const body = $('users-body');
    UI.clear(body);
    try {
      const users = await API.get('/api/users');
      if (!users.length) {
        body.appendChild(UI.emptyState('👥', 'אין משתמשים'));
        return;
      }
      users.forEach((user) => body.appendChild(userRow(user)));
    } catch (err) {
      body.appendChild(UI.emptyState('⚠️', err.message));
    }
  }

  function userRow(user) {
    const isOwner = user.email === App.adminEmail;
    const isSelf = App.user && user.email === App.user.email;

    const title = el('div', { class: 'rb2' }, [el('span', { text: user.name || user.email })]);
    if (user.role === 'admin') title.appendChild(el('span', { class: 'tag tag-admin', text: 'מנהל' }));
    if (isOwner) title.appendChild(el('span', { class: 'tag tag-admin', text: 'בעלים' }));
    if (!user.active) title.appendChild(el('span', { class: 'tag tag-off', text: 'מושבת' }));
    if (user.locked) title.appendChild(el('span', { class: 'tag tag-off', text: '🔒 נעול' }));
    if (user.must_change_password) {
      title.appendChild(el('span', { class: 'tag tag-warn', text: 'סיסמה חד-פעמית' }));
    }

    const actions = el('div', { class: 'ra' });

    // Password reset is available for every account, including the owner.
    actions.appendChild(el('button', {
      class: 'btn btn-s btn-mini', text: '🔑', title: 'הנפק סיסמה חד-פעמית חדשה',
      onclick: () => resetUserPassword(user)
    }));

    if (user.locked) {
      actions.appendChild(el('button', {
        class: 'btn btn-s btn-mini', text: '🔓', title: 'שחרר נעילה',
        onclick: () => unlockUser(user)
      }));
    }

    if (!isOwner) {
      actions.appendChild(el('button', {
        class: 'btn btn-s btn-mini',
        text: user.active ? '⏸️' : '▶️',
        title: user.active ? 'השבת גישה' : 'הפעל גישה',
        onclick: () => patchUser(user.id, { active: !user.active },
                                user.active ? 'הגישה הושבתה' : 'הגישה הופעלה')
      }));
      if (!isSelf) {
        actions.appendChild(el('button', {
          class: 'btn btn-s btn-mini',
          text: user.role === 'admin' ? '⬇️' : '⬆️',
          title: user.role === 'admin' ? 'הפוך לסוקר' : 'הפוך למנהל',
          onclick: async () => {
            const target = user.role === 'admin' ? 'user' : 'admin';
            const ok = await UI.confirm('שינוי הרשאה',
              target === 'admin'
                ? 'להעניק ל־' + user.email + ' הרשאות מנהל מלאות?'
                : 'להסיר מ־' + user.email + ' את הרשאות המנהל?', 'שנה');
            if (ok) await patchUser(user.id, { role: target }, 'ההרשאה עודכנה');
          }
        }));
        actions.appendChild(el('button', {
          class: 'btn btn-d btn-mini', text: '🗑️', title: 'מחק משתמש',
          onclick: () => removeUser(user)
        }));
      }
    }

    return el('div', { class: 'rr2' }, [
      el('div', { class: 'rm' }, [
        title,
        el('div', { class: 'rd', text: user.email }),
        el('div', {
          class: 'rmeta',
          text: 'סקרים: ' + user.survey_count +
                ' · כניסה אחרונה: ' + (UI.formatDate(user.last_login) || 'טרם נכנס')
        })
      ]),
      actions
    ]);
  }

  /* Shows a freshly issued one-time password. It arrives exactly once, in the
     create/reset response, so it is rendered rather than re-fetched. */
  function showCredentials(email, password) {
    $('cred-email').textContent = email;
    $('cred-password').textContent = password;
    UI.show($('cred-fallback'), false);
    UI.show($('new-credentials'), true);
    $('new-credentials').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  /* The text handed to the new user. Instructions travel with the password so
     the admin can paste one message into WhatsApp or email and be done. */
  function credentialsMessage() {
    return [
      'מערכת סקרים עירוניים — פרטי כניסה',
      '',
      'כתובת: ' + window.location.origin,
      'שם משתמש: ' + $('cred-email').textContent,
      'סיסמה חד-פעמית: ' + $('cred-password').textContent,
      '',
      'איך נכנסים:',
      '1. פתח את הכתובת שלמעלה בדפדפן.',
      '2. הזן את שם המשתמש והסיסמה החד-פעמית.',
      '3. בכניסה הראשונה תתבקש לבחור סיסמה קבועה.',
      '4. אחרי הכניסה בחר רשות מקומית, ואז ייפתח טופס הסקר.',
      '',
      'הסיסמה החד-פעמית משמשת לכניסה אחת בלבד ואינה ניתנת לשחזור.'
    ].join(String.fromCharCode(10));
  }

  async function copyCredentials() {
    const copied = await UI.copyText(credentialsMessage());
    if (copied) {
      UI.toast('פרטי הכניסה וההוראות הועתקו', 'ok');
      return;
    }
    // Both clipboard paths refused. Show the message and put the caret around
    // it so the admin only has to press Ctrl+C.
    const box = $('cred-fallback');
    box.textContent = credentialsMessage();
    UI.show(box, true);
    UI.selectText(box);
    UI.toast('הדפדפן חוסם את ההעתקה — הטקסט מסומן, העתק עם Ctrl+C', 'info');
  }

  async function resetUserPassword(user) {
    const ok = await UI.confirm('איפוס סיסמה',
      'תונפק סיסמה חד-פעמית חדשה עבור ' + user.email + '. ' +
      'הסיסמה הנוכחית תפסיק לעבוד וכל החיבורים הפעילים של המשתמש ינותקו. להמשיך?', 'אפס סיסמה');
    if (!ok) return;
    UI.busy(true, 'מאפס…');
    try {
      const result = await API.post('/api/users/' + encodeURIComponent(user.id) + '/reset-password', {});
      showCredentials(result.email, result.one_time_password);
      UI.toast('הונפקה סיסמה חד-פעמית חדשה', 'ok');
      loadUsers();
    } catch (err) {
      UI.toast(err.message, 'err');
    } finally {
      UI.busy(false);
    }
  }

  async function unlockUser(user) {
    UI.busy(true, 'משחרר…');
    try {
      await API.post('/api/users/' + encodeURIComponent(user.id) + '/unlock', {});
      UI.toast('הנעילה שוחררה', 'ok');
      loadUsers();
    } catch (err) {
      UI.toast(err.message, 'err');
    } finally {
      UI.busy(false);
    }
  }

  async function patchUser(id, changes, message) {
    UI.busy(true, 'מעדכן…');
    try {
      await API.patch('/api/users/' + encodeURIComponent(id), changes);
      UI.toast(message, 'ok');
      loadUsers();
    } catch (err) {
      UI.toast(err.message, 'err');
    } finally {
      UI.busy(false);
    }
  }

  async function removeUser(user) {
    const ok = await UI.confirm('מחיקת משתמש',
      'הגישה של ' + user.email + ' תבוטל מיידית. הסקרים שמילא יישארו במערכת. להמשיך?', 'מחק');
    if (!ok) return;
    UI.busy(true, 'מוחק…');
    try {
      const result = await API.del('/api/users/' + encodeURIComponent(user.id));
      UI.toast('המשתמש נמחק. נשמרו ' + result.kept_surveys + ' סקרים.', 'ok');
      loadUsers();
    } catch (err) {
      UI.toast(err.message, 'err');
    } finally {
      UI.busy(false);
    }
  }

  async function addUser() {
    const email = $('nu-email').value.trim().toLowerCase();
    if (!email || email.indexOf('@') < 1) return UI.toast('הכנס כתובת מייל תקינה', 'err');
    UI.busy(true, 'מוסיף…');
    try {
      const created = await API.post('/api/users', {
        email: email,
        name: $('nu-name').value.trim(),
        role: $('nu-role').value,
        active: true
      });
      $('nu-email').value = '';
      $('nu-name').value = '';
      showCredentials(created.email, created.one_time_password);
      UI.toast('המשתמש נוצר — מסור לו את הסיסמה החד-פעמית', 'ok');
      loadUsers();
    } catch (err) {
      UI.toast(err.message, 'err');
    } finally {
      UI.busy(false);
    }
  }

  /* ── Cities ────────────────────────────────────────────────────── */
  async function loadCities() {
    const body = $('cities-body');
    UI.clear(body);
    try {
      const cities = await API.get('/api/cities?include_inactive=true');
      if (!cities.length) {
        body.appendChild(UI.emptyState('🏙️', 'אין ערים. הוסף רשות ראשונה.'));
        return;
      }
      cities.forEach((city) => body.appendChild(cityRow(city)));
    } catch (err) {
      body.appendChild(UI.emptyState('⚠️', err.message));
    }
  }

  function cityRow(city) {
    const title = el('div', { class: 'rb2' }, [el('span', { text: city.name })]);
    if (!city.active) title.appendChild(el('span', { class: 'tag tag-off', text: 'לא פעילה' }));

    return el('div', { class: 'rr2' }, [
      el('div', { class: 'rm' }, [
        title,
        el('div', { class: 'rmeta', text: city.survey_count + ' סקרים' })
      ]),
      el('div', { class: 'ra' }, [
        el('button', {
          class: 'btn btn-s btn-mini', text: '✏️', title: 'שנה שם',
          onclick: () => renameCity(city)
        }),
        el('button', {
          class: 'btn btn-s btn-mini',
          text: city.active ? '⏸️' : '▶️',
          title: city.active ? 'הפוך ללא פעילה' : 'הפעל',
          onclick: async () => {
            UI.busy(true, 'מעדכן…');
            try {
              await API.patch('/api/cities/' + encodeURIComponent(city.id), { active: !city.active });
              UI.toast('העיר עודכנה', 'ok');
              await refreshCityLists();
            } catch (err) { UI.toast(err.message, 'err'); } finally { UI.busy(false); }
          }
        }),
        el('button', {
          class: 'btn btn-d btn-mini', text: '🗑️', title: 'מחק',
          onclick: () => removeCity(city)
        })
      ])
    ]);
  }

  async function renameCity(city) {
    const name = window.prompt('שם חדש לרשות:', city.name);
    if (name === null) return;
    const trimmed = name.trim();
    if (!trimmed || trimmed === city.name) return;
    UI.busy(true, 'משנה שם…');
    try {
      await API.patch('/api/cities/' + encodeURIComponent(city.id), { name: trimmed });
      UI.toast('השם עודכן בכל הסקרים המשויכים', 'ok');
      await refreshCityLists();
    } catch (err) {
      UI.toast(err.message, 'err');
    } finally {
      UI.busy(false);
    }
  }

  async function removeCity(city) {
    const ok = await UI.confirm('מחיקת רשות',
      city.survey_count
        ? 'לרשות משויכים ' + city.survey_count + ' סקרים, ולכן היא תועבר למצב לא פעילה במקום להימחק. להמשיך?'
        : 'למחוק את "' + city.name + '" מהרשימה?',
      'המשך');
    if (!ok) return;
    UI.busy(true, 'מוחק…');
    try {
      const result = await API.del('/api/cities/' + encodeURIComponent(city.id));
      UI.toast(result.message || 'הרשות נמחקה', 'ok');
      await refreshCityLists();
    } catch (err) {
      UI.toast(err.message, 'err');
    } finally {
      UI.busy(false);
    }
  }

  async function addCity() {
    const name = $('nc-name').value.trim();
    if (!name) return UI.toast('הכנס שם רשות', 'err');
    UI.busy(true, 'מוסיף…');
    try {
      await API.post('/api/cities', { name: name });
      $('nc-name').value = '';
      UI.toast('הרשות נוספה', 'ok');
      await refreshCityLists();
    } catch (err) {
      UI.toast(err.message, 'err');
    } finally {
      UI.busy(false);
    }
  }

  async function refreshCityLists() {
    await App.loadCities();
    loadCities();
  }

  /* ── Export ────────────────────────────────────────────────────── */
  function renderExportChips(cities) {
    const chips = $('export-chips');
    UI.clear(chips);
    cities.forEach((city) => {
      chips.appendChild(el('div', { class: 'chip', text: city.name + ' · ' + city.survey_count }));
    });
  }

  async function exportWorkbook(kind) {
    const city = $('export-city').value;
    const params = new URLSearchParams();
    if (city) params.set('city', city);
    if (kind === 'full') params.set('images', $('export-images').checked ? 'true' : 'false');

    UI.busy(true, 'מכין את הקובץ…');
    try {
      await API.download('/api/export/' + kind + '?' + params.toString(),
                         (kind === 'full' ? 'full-data' : 'calculation') + '.xlsx');
      UI.toast('הקובץ הורד ✅', 'ok');
    } catch (err) {
      UI.toast(err.message, 'err');
    } finally {
      UI.busy(false);
    }
  }

  /* ── Settings ──────────────────────────────────────────────────── */
  async function loadSettings() {
    try {
      const config = await API.get('/api/settings');
      $('set-enabled').checked = config.daily_email_enabled;
      $('set-time').value = config.daily_email_time;
      $('set-scope').value = config.daily_email_scope;
      $('set-recipient').value = config.daily_email_recipient;
      UI.show($('smtp-warn'), !config.smtp_configured);
      $('set-enabled').disabled = !config.smtp_configured;
      $('btn-send-now').disabled = !config.smtp_configured;

      const parts = ['אזור זמן: ' + config.timezone];
      if (config.last_sent_at) parts.push('נשלח לאחרונה: ' + UI.formatDate(config.last_sent_at));
      if (config.last_send_status && config.last_send_status !== 'ok') {
        parts.push('סטטוס אחרון: ' + config.last_send_status);
      }
      $('set-status').textContent = parts.join(' · ');
    } catch (err) {
      UI.toast(err.message, 'err');
    }
  }

  async function saveSettings() {
    UI.busy(true, 'שומר…');
    try {
      await API.patch('/api/settings', {
        daily_email_enabled: $('set-enabled').checked,
        daily_email_time: $('set-time').value,
        daily_email_scope: $('set-scope').value,
        daily_email_recipient: $('set-recipient').value.trim().toLowerCase()
      });
      UI.toast('ההגדרות נשמרו', 'ok');
      loadSettings();
    } catch (err) {
      UI.toast(err.message, 'err');
    } finally {
      UI.busy(false);
    }
  }

  async function sendNow() {
    const ok = await UI.confirm('שליחת דוח',
      'שני קבצי האקסל יישלחו עכשיו לכתובת שבהגדרות. להמשיך?', 'שלח');
    if (!ok) return;
    UI.busy(true, 'שולח מייל…');
    try {
      const result = await API.post('/api/settings/send-now', {});
      UI.toast('נשלח ל־' + result.recipient + ' (' + result.records + ' סקרים)', 'ok');
      loadSettings();
    } catch (err) {
      UI.toast(err.message, 'err');
    } finally {
      UI.busy(false);
    }
  }

  async function changePassword() {
    const current = $('pw-current').value;
    const next = $('pw-new').value;
    const confirmation = $('pw-new2').value;
    if (!current || !next) return UI.toast('מלא את כל השדות', 'err');
    if (next !== confirmation) return UI.toast('הסיסמאות החדשות אינן תואמות', 'err');
    if (next === current) return UI.toast('הסיסמה החדשה זהה לנוכחית', 'err');

    UI.busy(true, 'מעדכן…');
    try {
      await API.post('/api/auth/password', { current_password: current, new_password: next });
      ['pw-current', 'pw-new', 'pw-new2'].forEach((id) => { $(id).value = ''; });
      UI.toast('הסיסמה עודכנה', 'ok');
    } catch (err) {
      UI.toast(err.message, 'err');
    } finally {
      UI.busy(false);
    }
  }

  function loadAccount() {
    $('account-identity').textContent =
      App.user ? (App.user.name || App.user.email) + ' · ' + App.user.email : '';
    $('account-rules').textContent = Auth.rulesText();
  }

  async function loadAudit() {
    const body = $('audit-body');
    UI.clear(body);
    try {
      const result = await API.get('/api/audit?limit=60');
      if (!result.items.length) {
        body.appendChild(UI.emptyState('🧾', 'אין רשומות'));
        return;
      }
      result.items.forEach((item) => {
        body.appendChild(el('div', { class: 'audit-row' }, [
          el('span', { class: 'audit-act' + (item.success ? '' : ' audit-fail'), text: item.action }),
          document.createTextNode(' · ' + item.actor + (item.target ? ' → ' + item.target : '')),
          el('div', { class: 'audit-meta', text: UI.formatDate(item.ts) + ' · ' + (item.ip || '') })
        ]));
      });
    } catch (err) {
      body.appendChild(UI.emptyState('⚠️', err.message));
    }
  }

  /* ── Dashboard ─────────────────────────────────────────────────── */
  const YES_NO_LABEL = { yes: 'כן', no: 'לא', '': 'לא נענה' };
  const nf = (n) => Number(n || 0).toLocaleString('he-IL');

  function pct(part, whole) {
    if (!whole) return 0;
    return Math.round((part / whole) * 100);
  }

  function relabel(rows, map) {
    return (rows || []).map((r) => ({
      label: map[r.label] !== undefined ? map[r.label] : (r.label || '—'),
      count: r.count
    }));
  }

  async function loadDashboard() {
    try {
      const stats = await API.get('/api/stats');
      renderKpis(stats);
      renderTrend($('stat-trend'), stats.by_day);

      renderDonut($('stat-sector'), relabel(stats.by_sector, { '': 'לא סווג' }));
      renderDonut($('stat-completeness'), [
        { label: 'עם חתימה', count: stats.signed_surveys },
        { label: 'ללא חתימה', count: stats.total_surveys - stats.signed_surveys }
      ]);
      renderDonut($('stat-wet'), relabel(stats.by_wet, { '': 'לא נענה' }));
      renderDonut($('stat-cardboard'), relabel(stats.by_cardboard, { '': 'לא נענה' }));

      renderBars($('stat-cities'), stats.by_city.map((r) => [r.city, r.count]));
      renderBars($('stat-biztypes'),
        relabel(stats.by_biz_type, { '': 'לא צוין' }).map((r) => [r.label, r.count]));
      renderBars($('stat-containers'),
        (stats.by_container || []).map((r) => [r.label, r.qty, r.count + ' סקרים']));
      renderBars($('stat-users'), stats.by_user.map((r) => [r.user, r.count]));

      renderFacilities($('stat-facilities'), stats);
      renderUserTable($('stat-user-table'), stats.users || []);
    } catch (err) {
      UI.toast(err.message, 'err');
    }
  }

  function renderKpis(stats) {
    const grid = $('stat-grid');
    UI.clear(grid);
    const signedPct = pct(stats.signed_surveys, stats.total_surveys);
    const photoPct = pct(stats.surveys_with_images, stats.total_surveys);
    [
      ['סה״כ סקרים', nf(stats.total_surveys), null],
      ['נוספו היום', nf(stats.surveys_today), null],
      ['ב־7 הימים האחרונים', nf(stats.surveys_7d), null],
      ['ב־30 הימים האחרונים', nf(stats.surveys_30d), null],
      ['חתומים', signedPct + '%', nf(stats.signed_surveys) + ' מתוך ' + nf(stats.total_surveys)],
      ['עם תמונות', photoPct + '%', nf(stats.surveys_with_images) + ' סקרים'],
      ['כלי אצירה', nf(stats.container_units), nf(stats.container_rows) + ' שורות'],
      ['קבצים מצורפים', nf(stats.total_images + stats.total_docs),
       nf(stats.total_images) + ' תמונות · ' + nf(stats.total_docs) + ' מסמכים'],
      ['סוקרים פעילים', nf(stats.active_users), null],
      ['רשויות פעילות', nf(stats.active_cities), null]
    ].forEach(([label, value, sub]) => {
      grid.appendChild(el('div', { class: 'stat' }, [
        el('div', { class: 'stat-n', text: String(value) }),
        el('div', { class: 'stat-l', text: label }),
        sub ? el('div', { class: 'stat-s', text: sub }) : null
      ]));
    });
  }

  /* Area + line chart of the last 30 days. Drawn as inline SVG on a 0..100
     viewBox so it scales to any column width without a resize listener. */
  function renderTrend(container, days) {
    UI.clear(container);
    const rows = days || [];
    if (!rows.length) {
      container.appendChild(UI.emptyState('📈', 'אין נתונים'));
      return;
    }

    const W = 100, H = 34, PAD = 2;
    const max = Math.max.apply(null, rows.map((r) => r.count)) || 1;
    const step = rows.length > 1 ? (W - PAD * 2) / (rows.length - 1) : 0;
    // RTL: the oldest day belongs on the right, so x runs backwards
    const xAt = (i) => W - PAD - i * step;
    const yAt = (v) => H - PAD - (v / max) * (H - PAD * 2);

    const points = rows.map((r, i) => xAt(i).toFixed(2) + ',' + yAt(r.count).toFixed(2));
    const area = 'M' + xAt(0).toFixed(2) + ',' + (H - PAD) +
                 ' L' + points.join(' L') +
                 ' L' + xAt(rows.length - 1).toFixed(2) + ',' + (H - PAD) + ' Z';

    const chart = UI.svg('svg', {
      class: 'chart', viewBox: '0 0 ' + W + ' ' + H,
      preserveAspectRatio: 'none', role: 'img',
      'aria-label': 'סקרים לפי יום ב־30 הימים האחרונים'
    }, [
      UI.svg('path', { class: 'chart-area', d: area }),
      UI.svg('polyline', { class: 'chart-line', points: points.join(' ') })
    ]);
    rows.forEach((r, i) => {
      if (!r.count) return;
      chart.appendChild(UI.svg('circle', {
        class: 'chart-dot', cx: xAt(i).toFixed(2), cy: yAt(r.count).toFixed(2), r: '0.55'
      }, [UI.svg('title', { text: r.day + ' · ' + r.count })]));
    });
    container.appendChild(chart);

    const first = rows[0].day, last = rows[rows.length - 1].day;
    const total = rows.reduce((sum, r) => sum + r.count, 0);
    container.appendChild(el('div', { class: 'chart-legend' }, [
      el('span', { text: last }),
      el('span', { class: 'chart-legend-mid', text: 'סה״כ ' + nf(total) + ' סקרים · שיא ' + nf(max) + ' ביום' }),
      el('span', { text: first })
    ]));
  }

  /* Donut with a legend. Segments use evenly spaced tints of the accent so
     the palette stays calm rather than a rainbow of hues. */
  function renderDonut(container, rows) {
    UI.clear(container);
    const data = (rows || []).filter((r) => r.count > 0);
    const total = data.reduce((sum, r) => sum + r.count, 0);
    if (!total) {
      container.appendChild(UI.emptyState('◔', 'אין נתונים'));
      return;
    }

    const R = 15.9155;          // circumference 100, so lengths are percentages
    const ring = UI.svg('svg', {
      class: 'donut', viewBox: '0 0 42 42', role: 'img',
      'aria-label': data.map((r) => r.label + ' ' + r.count).join(', ')
    }, [
      UI.svg('circle', { class: 'donut-track', cx: '21', cy: '21', r: String(R) })
    ]);

    let offset = 25;            // start at 12 o'clock
    data.forEach((row, index) => {
      const share = (row.count / total) * 100;
      ring.appendChild(UI.svg('circle', {
        class: 'donut-seg', cx: '21', cy: '21', r: String(R),
        'stroke-dasharray': share.toFixed(2) + ' ' + (100 - share).toFixed(2),
        'stroke-dashoffset': String(offset),
        style: 'stroke:var(--seg-' + (index % 5 + 1) + ')'
      }, [UI.svg('title', { text: row.label + ': ' + row.count })]));
      offset -= share;
    });
    ring.appendChild(UI.svg('text', {
      class: 'donut-mid', x: '21', y: '21', 'text-anchor': 'middle',
      'dominant-baseline': 'central', text: nf(total)
    }));

    const legend = el('div', { class: 'donut-legend' });
    data.forEach((row, index) => {
      legend.appendChild(el('div', { class: 'dl-row' }, [
        el('span', { class: 'dl-dot', style: 'background:var(--seg-' + (index % 5 + 1) + ')' }),
        el('span', { class: 'dl-name', text: row.label }),
        el('span', { class: 'dl-val', text: nf(row.count) + ' · ' + pct(row.count, total) + '%' })
      ]));
    });
    container.appendChild(el('div', { class: 'donut-wrap' }, [ring, legend]));
  }

  function renderFacilities(container, stats) {
    UI.clear(container);
    [['🍽️ מטבח פעיל', stats.by_kitchen], ['🌳 חצר בעסק', stats.by_yard],
     ['📄 הצהרה נמסרה', stats.by_decl_given], ['📬 הצהרה הוחזרה', stats.by_decl_returned]
    ].forEach(([title, rows]) => {
      const data = relabel(rows, YES_NO_LABEL);
      const total = data.reduce((sum, r) => sum + r.count, 0) || 1;
      const block = el('div', { class: 'mini-stat' }, [
        el('div', { class: 'mini-t', text: title })
      ]);
      data.forEach((row) => {
        block.appendChild(el('div', { class: 'mini-row' }, [
          el('span', { class: 'mini-n', text: row.label }),
          el('div', { class: 'bar-track' }, [
            el('div', { class: 'bar-fill', style: 'width:' + pct(row.count, total) + '%' })
          ]),
          el('span', { class: 'mini-v', text: nf(row.count) })
        ]));
      });
      container.appendChild(block);
    });
  }

  /* Per surveyor breakdown. Everything the admin needs to see who is active,
     who has stalled and whose surveys are missing signatures. */
  function renderUserTable(table, people) {
    UI.clear(table);
    if (!people.length) {
      table.appendChild(el('caption', { class: 'dt-empty', text: 'אין נתונים' }));
      return;
    }

    const head = el('tr', {}, ['סוקר', 'סה״כ', '7 ימים', '30 יום', 'חתומים', 'תמונות',
                               'רשויות', 'סקר אחרון', 'כניסה אחרונה']
      .map((label) => el('th', { text: label })));
    table.appendChild(el('thead', {}, [head]));

    const body = el('tbody');
    people.forEach((person) => {
      const who = el('div', { class: 'dt-who' }, [
        el('span', { class: 'dt-name', text: person.name })
      ]);
      if (person.role === 'admin') who.appendChild(el('span', { class: 'tag tag-admin', text: 'מנהל' }));
      if (!person.active) who.appendChild(el('span', { class: 'tag tag-off', text: 'מושבת' }));

      body.appendChild(el('tr', {}, [
        el('td', {}, [who, el('div', { class: 'dt-mail', text: person.email })]),
        el('td', { class: 'num strong', text: nf(person.total) }),
        el('td', { class: 'num', text: nf(person.d7) }),
        el('td', { class: 'num', text: nf(person.d30) }),
        el('td', { class: 'num', text: person.total ? pct(person.signed, person.total) + '%' : '—' }),
        el('td', { class: 'num', text: nf(person.images) }),
        el('td', { class: 'cities', title: person.cities.join(', '),
                   text: person.cities.length ? person.cities.join(', ') : '—' }),
        el('td', { text: UI.formatDate(person.last_survey_at) || '—' }),
        el('td', { text: UI.formatDate(person.last_login) || 'טרם נכנס' })
      ]));
    });
    table.appendChild(body);
  }

  /* rows are [name, value] or [name, value, note] */
  function renderBars(container, rows) {
    UI.clear(container);
    if (!rows.length) {
      container.appendChild(UI.emptyState('📊', 'אין נתונים'));
      return;
    }
    const max = Math.max.apply(null, rows.map((r) => r[1])) || 1;
    rows.forEach(([name, count, note]) => {
      container.appendChild(el('div', { class: 'bar-row' }, [
        el('div', { class: 'bar-name', title: name, text: name }),
        el('div', { class: 'bar-track' }, [
          el('div', { class: 'bar-fill', style: 'width:' + Math.round((count / max) * 100) + '%' })
        ]),
        el('div', { class: 'bar-n', text: nf(count) }),
        note ? el('div', { class: 'bar-note', text: note }) : null
      ]));
    });
  }

  /* ── Wiring ────────────────────────────────────────────────────── */
  function bind() {
    $('btn-add-user').addEventListener('click', addUser);
    $('nu-email').addEventListener('keydown', (e) => { if (e.key === 'Enter') addUser(); });
    $('btn-add-city').addEventListener('click', addCity);
    $('nc-name').addEventListener('keydown', (e) => { if (e.key === 'Enter') addCity(); });
    $('btn-export-full').addEventListener('click', () => exportWorkbook('full'));
    $('btn-export-calc').addEventListener('click', () => exportWorkbook('calc'));
    $('btn-save-settings').addEventListener('click', saveSettings);
    $('btn-send-now').addEventListener('click', sendNow);
    $('btn-change-pw').addEventListener('click', changePassword);
    $('btn-copy-cred').addEventListener('click', copyCredentials);
  }

  return {
    bind: bind,
    loadAccount: loadAccount,
    loadUsers: loadUsers,
    loadCities: loadCities,
    loadSettings: loadSettings,
    loadAudit: loadAudit,
    loadDashboard: loadDashboard,
    renderExportChips: renderExportChips
  };
})();
