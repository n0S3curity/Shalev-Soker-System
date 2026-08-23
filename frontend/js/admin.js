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
    UI.show($('new-credentials'), true);
    $('new-credentials').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  async function copyCredentials() {
    const text = 'כתובת: ' + window.location.origin +
                 '\nשם משתמש: ' + $('cred-email').textContent +
                 '\nסיסמה חד-פעמית: ' + $('cred-password').textContent;
    try {
      await navigator.clipboard.writeText(text);
      UI.toast('פרטי הכניסה הועתקו', 'ok');
    } catch (err) {
      UI.toast('ההעתקה נכשלה — סמן והעתק ידנית', 'err');
    }
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
  async function loadDashboard() {
    try {
      const stats = await API.get('/api/stats');

      const grid = $('stat-grid');
      UI.clear(grid);
      [['סקרים', stats.total_surveys], ['סוקרים פעילים', stats.active_users],
       ['רשויות פעילות', stats.active_cities]].forEach(([label, number]) => {
        grid.appendChild(el('div', { class: 'stat' }, [
          el('div', { class: 'stat-n', text: String(number) }),
          el('div', { class: 'stat-l', text: label })
        ]));
      });

      renderBars($('stat-cities'), stats.by_city.map((r) => [r.city, r.count]));
      renderBars($('stat-users'), stats.by_user.map((r) => [r.user, r.count]));
    } catch (err) {
      UI.toast(err.message, 'err');
    }
  }

  function renderBars(container, rows) {
    UI.clear(container);
    if (!rows.length) {
      container.appendChild(UI.emptyState('📊', 'אין נתונים'));
      return;
    }
    const max = Math.max.apply(null, rows.map((r) => r[1])) || 1;
    rows.forEach(([name, count]) => {
      container.appendChild(el('div', { class: 'bar-row' }, [
        el('div', { class: 'bar-name', text: name }),
        el('div', { class: 'bar-track' }, [
          el('div', { class: 'bar-fill', style: 'width:' + Math.round((count / max) * 100) + '%' })
        ]),
        el('div', { class: 'bar-n', text: String(count) })
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
