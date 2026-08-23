/* ════════════════════════════════════════════════════════════════════
   Shared UI helpers: DOM lookup, safe rendering, toasts, modals,
   camera capture and client side image downscaling.

   Nothing here ever writes user data through innerHTML - every dynamic
   string goes through text nodes or esc(), which keeps a malicious
   business name from becoming script (defence in depth behind the CSP).
   ════════════════════════════════════════════════════════════════════ */
window.UI = (function () {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const qs = (sel, root) => (root || document).querySelector(sel);
  const qsa = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));

  function esc(value) {
    if (value === null || value === undefined) return '';
    return String(value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function show(el, visible) {
    if (!el) return;
    el.classList.toggle('hidden', !visible);
  }

  function clear(el) {
    while (el && el.firstChild) el.removeChild(el.firstChild);
  }

  /* Builds an element without ever parsing user text as HTML. */
  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    Object.keys(attrs || {}).forEach((key) => {
      const value = attrs[key];
      if (value === null || value === undefined || value === false) return;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key.slice(0, 2) === 'on' && typeof value === 'function') {
        node.addEventListener(key.slice(2), value);
      } else node.setAttribute(key, value);
    });
    (children || []).forEach((child) => {
      if (child === null || child === undefined) return;
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    });
    return node;
  }

  /* ── Theme ──────────────────────────────────────────────────────── */
  /* Three states so the user can go back to following the OS setting:
     auto -> light -> dark -> auto. The stylesheet carries the light palette
     on :root, the dark one behind both a prefers-color-scheme query and a
     [data-theme="dark"] attribute, so "auto" is simply the absence of the
     attribute. */
  const THEME_KEY = 'sv_theme';
  const THEME_ORDER = ['auto', 'light', 'dark'];
  const THEME_FACE = {
    auto: { icon: '\u25D0', label: '\u05ea\u05e6\u05d5\u05d2\u05d4: \u05dc\u05e4\u05d9 \u05d4\u05de\u05e2\u05e8\u05db\u05ea' },
    light: { icon: '\u2600', label: '\u05ea\u05e6\u05d5\u05d2\u05d4: \u05d1\u05d4\u05d9\u05e8\u05d4' },
    dark: { icon: '\u263D', label: '\u05ea\u05e6\u05d5\u05d2\u05d4: \u05db\u05d4\u05d4' }
  };
  const BAR_COLOR = { light: '#F7F6F3', dark: '#191A17' };

  const theme = {
    mode: 'auto',
    query: null,

    read() {
      try {
        const stored = localStorage.getItem(THEME_KEY);
        return THEME_ORDER.indexOf(stored) >= 0 ? stored : 'auto';
      } catch (err) {
        return 'auto';
      }
    },

    resolved() {
      if (this.mode !== 'auto') return this.mode;
      return this.query && this.query.matches ? 'dark' : 'light';
    },

    apply() {
      const root = document.documentElement;
      if (this.mode === 'auto') root.removeAttribute('data-theme');
      else root.setAttribute('data-theme', this.mode);

      const resolved = this.resolved();
      root.style.colorScheme = resolved;

      // A stored choice has to beat the media scoped meta tags in the head,
      // so drop their media attribute and drive both from here.
      qsa('meta[name="theme-color"]').forEach((tag) => {
        tag.removeAttribute('media');
        tag.setAttribute('content', BAR_COLOR[resolved]);
      });

      const button = $('btn-theme');
      if (button) {
        const face = THEME_FACE[this.mode];
        button.textContent = face.icon;
        button.title = face.label;
        button.setAttribute('aria-label', face.label);
      }
    },

    cycle() {
      this.mode = THEME_ORDER[(THEME_ORDER.indexOf(this.mode) + 1) % THEME_ORDER.length];
      try { localStorage.setItem(THEME_KEY, this.mode); } catch (err) { /* private mode */ }
      this.apply();
      toast(THEME_FACE[this.mode].label, 'info');
    },

    init() {
      this.mode = this.read();
      if (window.matchMedia) {
        this.query = window.matchMedia('(prefers-color-scheme: dark)');
        const onChange = () => { if (this.mode === 'auto') this.apply(); };
        if (this.query.addEventListener) this.query.addEventListener('change', onChange);
        else if (this.query.addListener) this.query.addListener(onChange);
      }
      this.apply();
    }
  };

  /* ── SVG ────────────────────────────────────────────────────────── */
  const SVG_NS = 'http://www.w3.org/2000/svg';

  /* Same shape as el(), but in the SVG namespace. createElement() would
     produce an unknown HTML element that never renders. */
  function svg(tag, attrs, children) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach((key) => {
      const value = attrs[key];
      if (value === null || value === undefined || value === false) return;
      if (key === 'text') node.textContent = value;
      else if (key.slice(0, 2) === 'on' && typeof value === 'function') {
        node.addEventListener(key.slice(2), value);
      } else node.setAttribute(key, value);
    });
    (children || []).forEach((child) => {
      if (child === null || child === undefined) return;
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    });
    return node;
  }

  /* ── Clipboard ──────────────────────────────────────────────────── */
  /* navigator.clipboard only exists in a secure context, so over plain HTTP
     it is undefined and the copy silently fails. Fall back to a throwaway
     textarea plus execCommand, which has no such restriction. */
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (err) { /* fall through to the legacy path */ }
    }
    try {
      const area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.style.position = 'fixed';
      area.style.top = '0';
      area.style.insetInlineStart = '-9999px';
      document.body.appendChild(area);
      area.focus();
      area.select();
      area.setSelectionRange(0, area.value.length);
      const copied = document.execCommand('copy');
      document.body.removeChild(area);
      return copied;
    } catch (err) {
      return false;
    }
  }

  /* Last resort when both copy paths fail: put the caret around the text so
     the user only has to press Ctrl+C. */
  function selectText(node) {
    if (!node) return;
    try {
      const range = document.createRange();
      range.selectNodeContents(node);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    } catch (err) { /* selection is a nicety, never fatal */ }
  }

  /* ── Toast ──────────────────────────────────────────────────────── */
  let toastTimer = null;
  function toast(message, kind) {
    const node = $('toast');
    node.textContent = message;
    node.className = 'toast ' + (kind === 'ok' ? 't-ok' : kind === 'info' ? 't-info' : 't-err');
    node.style.display = 'block';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.style.display = 'none'; }, 3600);
  }

  /* ── Busy overlay ───────────────────────────────────────────────── */
  let busyDepth = 0;
  function busy(on, text) {
    busyDepth = Math.max(0, busyDepth + (on ? 1 : -1));
    if (text) $('busy-text').textContent = text;
    show($('busy'), busyDepth > 0);
  }

  /* ── Confirm dialog ─────────────────────────────────────────────── */
  function confirmBox(title, text, confirmLabel) {
    return new Promise((resolve) => {
      const modal = $('confirm-modal');
      $('confirm-title').textContent = title;
      $('confirm-text').textContent = text;
      $('confirm-yes').textContent = confirmLabel || 'אישור';
      show(modal, true);

      function done(result) {
        show(modal, false);
        $('confirm-yes').removeEventListener('click', yes);
        $('confirm-no').removeEventListener('click', no);
        resolve(result);
      }
      function yes() { done(true); }
      function no() { done(false); }
      $('confirm-yes').addEventListener('click', yes);
      $('confirm-no').addEventListener('click', no);
    });
  }

  /* ── Lightbox ───────────────────────────────────────────────────── */
  function lightbox(src) {
    $('lightbox-img').src = src;
    show($('lightbox'), true);
  }

  /* ── Formatting ─────────────────────────────────────────────────── */
  function formatDate(value) {
    if (!value) return '';
    const date = new Date(value);
    if (isNaN(date.getTime())) return '';
    return date.toLocaleString('he-IL', {
      day: '2-digit', month: '2-digit', year: 'numeric',
      hour: '2-digit', minute: '2-digit'
    });
  }

  function formatSize(bytes) {
    if (!bytes) return '';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
  }

  /* ── Image downscaling before upload ────────────────────────────── */
  const MAX_EDGE = 2200;

  function downscaleImage(file) {
    return new Promise((resolve) => {
      if (!file.type || file.type.indexOf('image/') !== 0) return resolve(file);

      const url = URL.createObjectURL(file);
      const image = new Image();
      image.onload = function () {
        URL.revokeObjectURL(url);
        let { width, height } = image;
        if (Math.max(width, height) <= MAX_EDGE && file.size < 1.5 * 1024 * 1024) {
          return resolve(file);
        }
        const scale = Math.min(1, MAX_EDGE / Math.max(width, height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.round(width * scale);
        canvas.height = Math.round(height * scale);
        canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height);
        canvas.toBlob((blob) => {
          if (!blob) return resolve(file);
          const name = (file.name || 'photo').replace(/\.[^.]+$/, '') + '.jpg';
          resolve(new File([blob], name, { type: 'image/jpeg' }));
        }, 'image/jpeg', 0.85);
      };
      image.onerror = function () { URL.revokeObjectURL(url); resolve(file); };
      image.src = url;
    });
  }

  /* ── In-page camera ─────────────────────────────────────────────── */
  const camera = {
    stream: null,
    facing: 'environment',
    resolver: null,

    supported() {
      return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    },

    async open() {
      if (!this.supported()) throw new Error('unsupported');
      await this.start();
      show($('camera-modal'), true);
      return new Promise((resolve) => { this.resolver = resolve; });
    },

    async start() {
      await this.stop();
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: this.facing, width: { ideal: 1920 }, height: { ideal: 1080 } },
        audio: false
      });
      const video = $('camera-video');
      video.srcObject = this.stream;
      await video.play().catch(() => {});
    },

    async stop() {
      if (this.stream) {
        this.stream.getTracks().forEach((track) => track.stop());
        this.stream = null;
      }
      const video = $('camera-video');
      if (video) video.srcObject = null;
    },

    async flip() {
      this.facing = this.facing === 'environment' ? 'user' : 'environment';
      try { await this.start(); } catch (err) { toast('לא ניתן להחליף מצלמה', 'err'); }
    },

    shoot() {
      const video = $('camera-video');
      const canvas = $('camera-canvas');
      canvas.width = video.videoWidth || 1280;
      canvas.height = video.videoHeight || 720;
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => {
        const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        this.finish(blob ? new File([blob], 'photo-' + stamp + '.jpg', { type: 'image/jpeg' }) : null);
      }, 'image/jpeg', 0.9);
    },

    async finish(file) {
      await this.stop();
      show($('camera-modal'), false);
      if (this.resolver) { this.resolver(file); this.resolver = null; }
    }
  };

  function bindGlobal() {
    theme.init();
    $('camera-close').addEventListener('click', () => camera.finish(null));
    $('camera-shoot').addEventListener('click', () => camera.shoot());
    $('camera-flip').addEventListener('click', () => camera.flip());
    $('lightbox-close').addEventListener('click', () => show($('lightbox'), false));
    $('lightbox').addEventListener('click', (event) => {
      if (event.target.id === 'lightbox') show($('lightbox'), false);
    });
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      show($('lightbox'), false);
      if (!$('camera-modal').classList.contains('hidden')) camera.finish(null);
    });
  }

  return {
    $: $, qs: qs, qsa: qsa, el: el, esc: esc, show: show, clear: clear,
    svg: svg, copyText: copyText, selectText: selectText,
    theme: theme,
    toast: toast, busy: busy, confirm: confirmBox, lightbox: lightbox,
    formatDate: formatDate, formatSize: formatSize,
    downscaleImage: downscaleImage, camera: camera, bindGlobal: bindGlobal,
    emptyState: function (icon, text) {
      return el('div', { class: 'empty' }, [
        el('div', { class: 'empty-i', text: icon }),
        el('div', { text: text })
      ]);
    }
  };
})();
