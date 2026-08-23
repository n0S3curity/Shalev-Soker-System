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
