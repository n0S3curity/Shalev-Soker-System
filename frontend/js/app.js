/* ════════════════════════════════════════════════════════════════════
   Bootstrap, navigation and shared application state.

   Screen flow
     login → admin → admin panel → pick a municipality → data + export
     login → user  → pick a municipality → fill / edit surveys
   ════════════════════════════════════════════════════════════════════ */
window.App = (function () {
  'use strict';

  const $ = UI.$;
  const el = UI.el;
  const CITY_KEY = 'sv_city';

  const TABS = {
    dashboard: { label: '📊 לוח בקרה', panel: 'panel-dashboard', admin: true },
    city:      { label: '🏙️ רשות',     panel: 'panel-city' },
    form:      { label: '📋 סקר',       panel: 'panel-form' },
    records:   { label: '🗂️ רשומות',   panel: 'panel-records' },
    users:     { label: '👥 משתמשים',  panel: 'panel-users', admin: true },
    cities:    { label: '🗺️ רשויות',   panel: 'panel-cities', admin: true },
    export:    { label: '📊 ייצוא',     panel: 'panel-export', admin: true },
    settings:  { label: '⚙️ הגדרות',   panel: 'panel-settings', admin: true },
    account:   { label: '🔑 הסיסמה שלי', panel: 'panel-account' }
  };

  const USER_ORDER = ['city', 'form', 'records', 'account'];
  const ADMIN_ORDER = ['dashboard', 'city', 'form', 'records', 'users', 'cities',
                       'export', 'settings', 'account'];

  const app = {
    user: null,
    city: null,
    adminEmail: '',
    cities: [],
    current: null
  };

  /* ── Navigation ────────────────────────────────────────────────── */
  function buildNav() {
    const nav = $('nav');
    UI.clear(nav);
    const order = app.user.role === 'admin' ? ADMIN_ORDER : USER_ORDER;
    order.forEach((key) => {
      nav.appendChild(el('button', {
        class: 'nav-t', 'data-tab': key, text: TABS[key].label,
        onclick: () => go(key)
      }));
    });
  }

  function go(tab) {
    if (!TABS[tab]) return;
    if (TABS[tab].admin && app.user.role !== 'admin') return;

    app.current = tab;
    Object.keys(TABS).forEach((key) => {
      UI.show($(TABS[key].panel), key === tab);
    });
    UI.qsa('.nav-t').forEach((button) => {
      button.classList.toggle('act', button.getAttribute('data-tab') === tab);
    });
    window.scrollTo({ top: 0 });

    if (tab === 'records') Records.load();
    if (tab === 'users') Admin.loadUsers();
    if (tab === 'cities') Admin.loadCities();
    if (tab === 'settings') { Admin.loadSettings(); Admin.loadAudit(); }
    if (tab === 'dashboard') Admin.loadDashboard();
    if (tab === 'city') renderCityPicker();
    if (tab === 'export') Admin.renderExportChips(app.cities);
    if (tab === 'account') Admin.loadAccount();
  }

  /* ── Cities ────────────────────────────────────────────────────── */
  async function loadCities() {
    app.cities = await API.get('/api/cities');

    ['records-city', 'export-city'].forEach((id) => {
      const select = $(id);
      const previous = select.value;
      UI.clear(select);
      select.appendChild(el('option', { value: '', text: id === 'export-city' ? 'כל הערים' : 'כל הערים' }));
      app.cities.forEach((city) => {
        select.appendChild(el('option', { value: city.name, text: city.name }));
      });
      if (previous) select.value = previous;
    });

    // A city that was deactivated or renamed must not stay selected
    if (app.city && !app.cities.some((city) => city.name === app.city)) {
      setCity(null, false);
    }
    renderCityPicker();
    refreshCounts();
  }

  function renderCityPicker() {
    const grid = $('city-grid');
    if (!grid) return;
    UI.clear(grid);

    if (!app.cities.length) {
      grid.appendChild(UI.emptyState('🏙️',
        app.user.role === 'admin'
          ? 'אין רשויות. הוסף רשות במסך "רשויות".'
          : 'אין רשויות פעילות. פנה למנהל המערכת.'));
      return;
    }

    app.cities.forEach((city) => {
      const button = el('button', {
        class: 'city-btn' + (city.name === app.city ? ' sel' : ''),
        onclick: () => { setCity(city.name, true); }
      }, [
        document.createTextNode(city.name),
        el('small', { text: city.survey_count + ' סקרים' })
      ]);
      grid.appendChild(button);
    });
  }

  function setCity(name, navigate) {
    app.city = name;
    if (name) localStorage.setItem(CITY_KEY, name);
    else localStorage.removeItem(CITY_KEY);

    $('f_city').value = name || '';
    refreshCounts();
    renderCityPicker();

    if (navigate) {
      SurveyForm.reset(false);
      go('form');
      UI.toast('העיר הפעילה: ' + name, 'ok');
    }
  }

  function refreshCounts() {
    const chip = $('tb-city');
    if (!app.city) {
      chip.textContent = 'בחר רשות';
      return;
    }
    const record = app.cities.find((city) => city.name === app.city);
    chip.textContent = app.city + (record ? ' (' + record.survey_count + ')' : '');
  }

  /* ── Session ───────────────────────────────────────────────────── */
  async function startSession(user) {
    app.user = user;

    UI.show($('screen-login'), false);
    UI.show($('screen-boot'), false);
    UI.show($('app'), true);

    $('tb-user').textContent = user.name || user.email;

    try {
      const me = await API.get('/api/auth/me');
      app.adminEmail = me.admin_email || '';
    } catch (err) { /* non fatal */ }

    buildNav();
    await SurveyForm.loadCatalog();
    await loadCities();

    const stored = localStorage.getItem(CITY_KEY);
    if (stored && app.cities.some((city) => city.name === stored)) {
      app.city = stored;
      $('f_city').value = stored;
      refreshCounts();
    }

    if (user.role === 'admin') {
      go('dashboard');
    } else if (app.city) {
      SurveyForm.reset(false);
      go('form');
    } else {
      go('city');
    }
  }

  /* ── Boot ──────────────────────────────────────────────────────── */
  async function boot() {
    UI.bindGlobal();
    SurveyForm.bind();
    Records.bind();
    Admin.bind();

    $('btn-logout').addEventListener('click', async () => {
      const ok = await UI.confirm('יציאה', 'להתנתק מהמערכת?', 'התנתק');
      if (ok) Auth.logout();
    });
    $('tb-city').addEventListener('click', () => go('city'));

    document.addEventListener('session-expired', () => {
      UI.toast('פג תוקף החיבור. מתחבר מחדש…', 'err');
      setTimeout(() => location.reload(), 1600);
    });

    // Already signed in?
    try {
      const me = await API.get('/api/auth/me', { allowAnonymous: true });
      app.adminEmail = me.admin_email || '';
      await startSession({ email: me.email, name: me.name, role: me.role });
      return;
    } catch (err) { /* not signed in - show the login screen */ }

    UI.show($('screen-boot'), false);
    UI.show($('screen-login'), true);
    try {
      await Auth.init(startSession);
    } catch (err) {
      $('signin-error').textContent = 'שגיאה בטעינת מסך ההתחברות: ' + err.message;
    }
  }

  document.addEventListener('DOMContentLoaded', boot);

  return {
    go: go,
    setCity: setCity,
    loadCities: loadCities,
    refreshCounts: refreshCounts,
    get user() { return app.user; },
    get city() { return app.city; },
    get cities() { return app.cities; },
    get adminEmail() { return app.adminEmail; }
  };
})();
