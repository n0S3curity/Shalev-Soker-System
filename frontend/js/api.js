/* ════════════════════════════════════════════════════════════════════
   Thin fetch wrapper.
   - always same-origin, credentials included (HttpOnly session cookie)
   - echoes the CSRF cookie back in a header on every state change
   - surfaces the server's Hebrew error message as an Error
   ════════════════════════════════════════════════════════════════════ */
window.API = (function () {
  'use strict';

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)sv_csrf=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
  }

  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.status = status;
    }
  }

  async function request(method, path, body, options) {
    options = options || {};
    const headers = {};
    let payload = null;

    if (body instanceof FormData) {
      payload = body;
    } else if (body !== undefined && body !== null) {
      headers['Content-Type'] = 'application/json';
      payload = JSON.stringify(body);
    }
    if (method !== 'GET' && method !== 'HEAD') {
      headers['X-CSRF-Token'] = csrfToken();
    }

    let response;
    try {
      response = await fetch(path, {
        method: method,
        headers: headers,
        body: payload,
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'error'
      });
    } catch (err) {
      throw new ApiError('אין חיבור לשרת. בדוק את החיבור לאינטרנט.', 0);
    }

    if (response.status === 401 && !options.allowAnonymous) {
      document.dispatchEvent(new CustomEvent('session-expired'));
      throw new ApiError('פג תוקף החיבור. התחבר מחדש.', 401);
    }
    if (options.raw) {
      if (!response.ok) throw new ApiError(await errorMessage(response), response.status);
      return response;
    }
    if (response.status === 204) return null;

    if (!response.ok) {
      throw new ApiError(await errorMessage(response), response.status);
    }
    const type = response.headers.get('content-type') || '';
    return type.indexOf('application/json') >= 0 ? response.json() : response.text();
  }

  async function errorMessage(response) {
    try {
      const data = await response.json();
      if (typeof data.detail === 'string') return data.detail;
      if (Array.isArray(data.detail) && data.detail.length) {
        return data.detail[0].msg || 'נתונים לא תקינים';
      }
    } catch (err) { /* not json */ }
    if (response.status === 429) return 'יותר מדי בקשות. המתן רגע ונסה שוב.';
    if (response.status === 413) return 'הקובץ גדול מדי.';
    return 'שגיאה בשרת (' + response.status + ')';
  }

  return {
    ApiError: ApiError,
    csrfToken: csrfToken,
    get: (path, options) => request('GET', path, null, options),
    post: (path, body, options) => request('POST', path, body, options),
    put: (path, body, options) => request('PUT', path, body, options),
    patch: (path, body, options) => request('PATCH', path, body, options),
    del: (path, options) => request('DELETE', path, null, options),

    /* Streams a generated file straight to the browser's download list. */
    async download(path, fallbackName) {
      const response = await request('GET', path, null, { raw: true });
      const blob = await response.blob();
      const disposition = response.headers.get('content-disposition') || '';
      let name = fallbackName;
      const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/i);
      if (utf8) {
        try { name = decodeURIComponent(utf8[1]); } catch (err) { /* keep fallback */ }
      }
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = name;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    }
  };
})();
