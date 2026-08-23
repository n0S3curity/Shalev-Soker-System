/* ════════════════════════════════════════════════════════════════════
   Email + password sign-in.

   Four states share one card:
     step-signin  email + password
     step-first   forced replacement of the admin-issued one-time password
     step-forgot  request a reset link
     step-reset   choose a new password from an emailed link (?reset=TOKEN)

   There is no registration path here, by design.
   ════════════════════════════════════════════════════════════════════ */
window.Auth = (function () {
  'use strict';

  const $ = UI.$;
  const STEPS = ['step-signin', 'step-first', 'step-forgot', 'step-reset'];

  let onSignedIn = null;
  let changeToken = null;   // short lived, only during the first-login exchange
  let resetToken = null;    // taken from the URL, never persisted
  let config = { password_min_length: 10, password_reset_available: false };

  function showStep(id) {
    STEPS.forEach((step) => UI.show($(step), step === id));
    clearMessages();
    const subtitle = {
      'step-signin': 'כניסה עם כתובת מייל וסיסמה',
      'step-first': 'בחירת סיסמה קבועה',
      'step-forgot': 'שחזור סיסמה',
      'step-reset': 'בחירת סיסמה חדשה'
    }[id];
    $('login-subtitle').textContent = subtitle || '';
  }

  function clearMessages() {
    ['signin-error', 'first-error', 'forgot-error', 'reset-error'].forEach((id) => {
      const node = $(id);
      if (node) node.textContent = '';
    });
    ['forgot-ok', 'reset-ok'].forEach((id) => UI.show($(id), false));
  }

  function fail(id, message) {
    $(id).textContent = message || '';
  }

  function succeed(id, message) {
    const node = $(id);
    node.textContent = message;
    UI.show(node, true);
  }

  function rulesText() {
    return 'לפחות ' + config.password_min_length +
           ' תווים, הכוללים אות באנגלית וספרה. אל תשתמש בכתובת המייל שלך.';
  }

  /* Local pre-check so the user is not bounced by the server for typos.
     The server enforces the real policy regardless. */
  function localPasswordProblem(password, confirmation) {
    if (!password) return 'הכנס סיסמה';
    if (password.length < config.password_min_length) {
      return 'הסיסמה חייבת להכיל לפחות ' + config.password_min_length + ' תווים';
    }
    if (!/[A-Za-z]/.test(password)) return 'הסיסמה חייבת להכיל אות באנגלית';
    if (!/\d/.test(password)) return 'הסיסמה חייבת להכיל ספרה';
    if (confirmation !== undefined && password !== confirmation) return 'הסיסמאות אינן תואמות';
    return null;
  }

  /* ── step 1: sign in ────────────────────────────────────────────── */
  async function signIn(event) {
    if (event) event.preventDefault();
    const email = $('login-email').value.trim().toLowerCase();
    const password = $('login-password').value;
    if (!email || !password) return fail('signin-error', 'מלא כתובת מייל וסיסמה');

    fail('signin-error', '');
    $('btn-signin').disabled = true;
    UI.busy(true, 'מתחבר…');
    try {
      const result = await API.post('/api/auth/login', { email: email, password: password },
                                    { allowAnonymous: true });
      if (result.status === 'ok') {
        $('login-password').value = '';
        onSignedIn(result.user);
        return;
      }
      // One-time password accepted; force the replacement now.
      changeToken = result.change_token;
      $('first-email-label').textContent = result.email || email;
      $('login-password').value = '';
      showStep('step-first');
      setTimeout(() => $('first-password').focus(), 60);
    } catch (err) {
      fail('signin-error', err.message);
    } finally {
      $('btn-signin').disabled = false;
      UI.busy(false);
    }
  }

  /* ── step 2: replace the one-time password ──────────────────────── */
  async function submitFirstPassword(event) {
    if (event) event.preventDefault();
    const password = $('first-password').value;
    const confirmation = $('first-password2').value;

    const problem = localPasswordProblem(password, confirmation);
    if (problem) return fail('first-error', problem);
    if (!changeToken) {
      showStep('step-signin');
      return fail('signin-error', 'פג תוקף הבקשה. התחבר שוב עם הסיסמה החד-פעמית.');
    }

    fail('first-error', '');
    $('btn-first').disabled = true;
    UI.busy(true, 'שומר סיסמה…');
    try {
      const result = await API.post('/api/auth/first-login', {
        change_token: changeToken,
        new_password: password,
        confirm_password: confirmation
      }, { allowAnonymous: true });
      changeToken = null;
      $('first-password').value = '';
      $('first-password2').value = '';
      UI.toast('הסיסמה נשמרה', 'ok');
      onSignedIn(result.user);
    } catch (err) {
      fail('first-error', err.message);
      if (err.status === 401) { changeToken = null; }
    } finally {
      $('btn-first').disabled = false;
      UI.busy(false);
    }
  }

  /* ── step 3: forgot ─────────────────────────────────────────────── */
  async function submitForgot(event) {
    if (event) event.preventDefault();
    const email = $('forgot-email').value.trim().toLowerCase();
    if (!email) return fail('forgot-error', 'הכנס כתובת מייל');

    clearMessages();
    $('btn-forgot').disabled = true;
    UI.busy(true, 'שולח…');
    try {
      const result = await API.post('/api/auth/forgot', { email: email }, { allowAnonymous: true });
      succeed('forgot-ok', result.message || 'אם הכתובת קיימת, נשלח אליה קישור.');
    } catch (err) {
      fail('forgot-error', err.message);
    } finally {
      $('btn-forgot').disabled = false;
      UI.busy(false);
    }
  }

  /* ── step 4: reset from the emailed link ────────────────────────── */
  async function submitReset(event) {
    if (event) event.preventDefault();
    const password = $('reset-password').value;
    const confirmation = $('reset-password2').value;

    const problem = localPasswordProblem(password, confirmation);
    if (problem) return fail('reset-error', problem);

    clearMessages();
    $('btn-reset').disabled = true;
    UI.busy(true, 'מעדכן סיסמה…');
    try {
      const result = await API.post('/api/auth/reset', {
        token: resetToken,
        new_password: password,
        confirm_password: confirmation
      }, { allowAnonymous: true });
      succeed('reset-ok', result.message || 'הסיסמה עודכנה.');
      $('reset-password').value = '';
      $('reset-password2').value = '';
      resetToken = null;
      setTimeout(() => showStep('step-signin'), 2200);
    } catch (err) {
      fail('reset-error', err.message);
    } finally {
      $('btn-reset').disabled = false;
      UI.busy(false);
    }
  }

  /* ── wiring ─────────────────────────────────────────────────────── */
  function bindPasswordEyes() {
    UI.qsa('.pw-eye').forEach((button) => {
      button.addEventListener('click', () => {
        const field = $(button.getAttribute('data-target'));
        if (!field) return;
        const revealed = field.type === 'text';
        field.type = revealed ? 'password' : 'text';
        button.textContent = revealed ? '👁️' : '🙈';
      });
    });
  }

  /* The reset token must not linger in the address bar or in history. */
  function takeResetTokenFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const token = params.get('reset');
    if (!token) return null;
    params.delete('reset');
    const query = params.toString();
    window.history.replaceState({}, document.title,
      window.location.pathname + (query ? '?' + query : ''));
    return token;
  }

  async function init(callback) {
    onSignedIn = callback;

    try {
      config = await API.get('/api/auth/config', { allowAnonymous: true });
    } catch (err) { /* fall back to defaults */ }

    $('first-rules').textContent = rulesText();
    $('reset-rules').textContent = rulesText();

    $('step-signin').addEventListener('submit', signIn);
    $('step-first').addEventListener('submit', submitFirstPassword);
    $('step-forgot').addEventListener('submit', submitForgot);
    $('step-reset').addEventListener('submit', submitReset);

    $('link-forgot').addEventListener('click', () => {
      $('forgot-email').value = $('login-email').value.trim();
      showStep('step-forgot');
      if (!config.password_reset_available) {
        $('forgot-note').textContent =
          'שחזור בדוא"ל אינו מוגדר במערכת. פנה למנהל המערכת כדי לקבל סיסמה חד-פעמית חדשה.';
      }
    });
    UI.qsa('[data-back]').forEach((button) => {
      button.addEventListener('click', () => showStep(button.getAttribute('data-back')));
    });

    bindPasswordEyes();

    resetToken = takeResetTokenFromUrl();
    if (resetToken) {
      showStep('step-reset');
      setTimeout(() => $('reset-password').focus(), 60);
    } else {
      showStep('step-signin');
      setTimeout(() => $('login-email').focus(), 60);
    }
  }

  async function logout() {
    try { await API.post('/api/auth/logout', {}, { allowAnonymous: true }); } catch (err) { /* ignore */ }
    location.reload();
  }

  return { init: init, logout: logout, rulesText: () => rulesText() };
})();
