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

  const TABS = {
    dashboard: { label: '📊 לוח בקרה', panel: 'panel-dashboard', admin: true },
    city:      { label: '🏙️ בחירת רשות', panel: 'panel-city' },
    form:      { label: '📋 סקר',       panel: 'panel-form' },
    records:   { label: '🗂️ רשומות',   panel: 'panel-records' },
    users:     { label: '👥 משתמשים',  panel: 'panel-users', admin: true },
    cities:    { label: '🗺️ רשויות',   panel: 'panel-cities', admin: true },
    export:    { label: '📊 ייצוא לאקסל', panel: 'panel-export', admin: true },
    settings:  { label: '⚙️ הגדרות',   panel: 'panel-settings', admin: true },
    account:   { label: '🔑 הסיסמה שלי', panel: 'panel-account' }
  };

  const USER_ORDER = ['city', 'form', 'records', 'account'];
  const ADMIN_ORDER = ['dashboard', 'city', 'form', 'records', 'users', 'cities',
                       'export', 'settings', 'account'];

  const app = {
    user: null,
    city: null,
    // true while the city on screen was picked for the form the user is
    // on right now, which is what separates "just chose one" from
    // "came back to an idle tab". Cleared on leaving the survey tab.
    cityPicked: false,
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

    if (app.current === 'form' && tab !== 'form') app.cityPicked = false;
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
    if (tab === 'form') { expireIdleCity(); applyFormGate(); }
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
    applyFormGate();
    refreshCounts();
  }

  /* One renderer for both pickers: the "רשות" tab and the survey gate. */
  function fillCityGrid(grid) {
    if (!grid) return;
    UI.clear(grid);

    if (!app.cities.length) {
      grid.appendChild(UI.emptyState('🏙️',
        app.user && app.user.role === 'admin'
          ? 'אין רשויות. הוסף רשות במסך "רשויות".'
          : 'אין רשויות פעילות. פנה למנהל המערכת.'));
      return;
    }

    app.cities.forEach((city) => {
      grid.appendChild(el('button', {
        class: 'city-btn' + (city.name === app.city ? ' sel' : ''),
        onclick: () => { setCity(city.name, true); }
      }, [
        document.createTextNode(city.name),
        el('small', { text: city.survey_count + ' סקרים' })
      ]));
    });
  }

  function renderCityPicker() {
    fillCityGrid($('city-grid'));
    fillCityGrid($('form-city-grid'));
  }

  /* A survey has to belong to a municipality, so the form itself stays behind
     a chooser until one is active. Before this gate existed you could fill the
     whole form and only be told at save time - and picking a city at that
     point reset everything that had been typed. */
  /* Starting a new survey always means confirming the authority again. The
     city is kept only while there is live work on screen - part filled input,
     or a saved record being viewed - so a surveyor mid-form is never
     interrupted, but an idle return to the tab goes back to the chooser. */
  function expireIdleCity() {
    if (!app.city) return;
    if (app.cityPicked) return;
    if (SurveyForm.isNewAndDirty()) return;
    if (SurveyForm.state && SurveyForm.state.surveyId) return;
    setCity(null, false);
  }

  function applyFormGate() {
    const missing = !app.city;
    UI.show($('form-city-gate'), missing);
    UI.show($('form-live'), !missing);
  }

  function setCity(name, navigate) {
    app.city = name;
    if (!name) app.cityPicked = false;

    $('f_city').value = name || '';
    refreshCounts();
    renderCityPicker();
    applyFormGate();

    if (!navigate) return;
    app.cityPicked = true;

    // Choosing a city must never throw away work in progress. A part filled
    // new survey keeps everything and simply adopts the city; only a loaded
    // existing record starts over, because that record belongs to its own city.
    const kept = SurveyForm.isNewAndDirty();
    if (!kept) SurveyForm.reset(false);
    applyFormGate();
    go('form');
    UI.toast(kept
      ? 'הרשות הוחלפה ל' + name + ' — הנתונים שמילאת נשמרו'
      : 'הרשות הפעילה: ' + name, 'ok');
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

    // The active city is deliberately not remembered between sessions. It used
    // to be restored from localStorage, which silently pre-filled yesterday's
    // city and made it easy to file a survey under the wrong authority.
    go(user.role === 'admin' ? 'dashboard' : 'city');
  }

  /* ── Boot ──────────────────────────────────────────────────────── */
  async function boot() {
    UI.bindGlobal();
    // Older builds cached the active city here. Nothing reads it any more,
    // so clear it rather than leave stale state in the browser.
    try { localStorage.removeItem('sv_city'); } catch (err) { /* private mode */ }
    SurveyForm.bind();
    Records.bind();
    Admin.bind();

    $('btn-logout').addEventListener('click', async () => {
      const ok = await UI.confirm('יציאה', 'להתנתק מהמערכת?', 'התנתק');
      if (ok) Auth.logout();
    });
    $('tb-city').addEventListener('click', () => go('city'));
    $('btn-theme').addEventListener('click', () => UI.theme.cycle());

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
