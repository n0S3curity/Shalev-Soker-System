/* ════════════════════════════════════════════════════════════════════
   Records list. Everyone sees every survey; the edit button only shows
   where the server says can_edit is true.
   ════════════════════════════════════════════════════════════════════ */
window.Records = (function () {
  'use strict';

  const $ = UI.$;
  const el = UI.el;
  const PAGE_SIZE = 40;

  let page = 1;
  let searchTimer = null;

  function query() {
    const params = new URLSearchParams();
    const term = $('records-search').value.trim();
    const city = $('records-city').value;
    if (term) params.set('q', term);
    if (city) params.set('city', city);
    if ($('records-mine').checked) params.set('mine', 'true');
    params.set('page', String(page));
    params.set('page_size', String(PAGE_SIZE));
    return params.toString();
  }

  async function load() {
    const body = $('records-body');
    UI.clear(body);
    body.appendChild(UI.emptyState('⏳', 'טוען…'));

    try {
      const result = await API.get('/api/surveys?' + query());
      render(result);
    } catch (err) {
      UI.clear(body);
      body.appendChild(UI.emptyState('⚠️', err.message));
    }
  }

  function render(result) {
    const body = $('records-body');
    UI.clear(body);
    $('records-count').textContent = result.total
      ? 'סה״כ ' + result.total + ' סקרים' : '';

    if (!result.items.length) {
      body.appendChild(UI.emptyState('🗂️', 'לא נמצאו סקרים'));
      renderPager(result);
      return;
    }

    const groups = {};
    result.items.forEach((item) => {
      const city = item.city || '—';
      (groups[city] = groups[city] || []).push(item);
    });

    Object.keys(groups).sort().forEach((city) => {
      const card = el('div', { class: 'cb2' }, [
        el('div', { class: 'ch2', text: '🏙️ ' + city + ' (' + groups[city].length + ')' })
      ]);
      groups[city].forEach((item) => card.appendChild(row(item)));
      body.appendChild(card);
    });

    renderPager(result);
  }

  function row(item) {
    const title = el('div', { class: 'rb2' }, [
      el('span', { text: item.biz_name || item.biznum }),
      el('span', { class: 'tag', text: item.biznum })
    ]);
    if (item.can_edit) title.appendChild(el('span', { class: 'tag tag-mine', text: 'ניתן לעריכה' }));
    if (item.has_signature) title.appendChild(el('span', { class: 'tag', text: '✍️' }));
    if (item.image_count) title.appendChild(el('span', { class: 'tag', text: '📷 ' + item.image_count }));
    if (item.doc_count) title.appendChild(el('span', { class: 'tag', text: '📎 ' + item.doc_count }));

    const details = [item.address, item.biz_type, item.container_summary]
      .filter(Boolean).join(' · ');

    const actions = el('div', { class: 'ra' }, [
      el('button', {
        class: 'btn btn-s btn-mini', title: item.can_edit ? 'ערוך' : 'צפה',
        text: item.can_edit ? '✏️' : '👁️',
        onclick: () => SurveyForm.open(item.id)
      })
    ]);
    if (item.can_edit) {
      actions.appendChild(el('button', {
        class: 'btn btn-d btn-mini', text: '🗑️', title: 'מחק',
        onclick: () => remove(item)
      }));
    }

    return el('div', { class: 'rr2' }, [
      el('div', { class: 'rm' }, [
        title,
        el('div', { class: 'rd', text: details }),
        el('div', {
          class: 'rmeta',
          text: 'מילא: ' + (item.owner_name || item.owner_email || '—') +
                ' · עודכן: ' + UI.formatDate(item.updated_at)
        })
      ]),
      actions
    ]);
  }

  function renderPager(result) {
    const pager = $('records-pager');
    UI.clear(pager);
    const pages = Math.ceil(result.total / result.page_size) || 1;
    if (pages <= 1) return;

    pager.appendChild(el('button', {
      class: 'btn btn-s btn-sm', text: '→ הקודם', disabled: page <= 1 || null,
      onclick: () => { page = Math.max(1, page - 1); load(); }
    }));
    pager.appendChild(el('span', { class: 'rec-count', text: page + ' / ' + pages }));
    pager.appendChild(el('button', {
      class: 'btn btn-s btn-sm', text: 'הבא ←', disabled: page >= pages || null,
      onclick: () => { page = Math.min(pages, page + 1); load(); }
    }));
  }

  async function remove(item) {
    const ok = await UI.confirm(
      'מחיקת סקר',
      'הסקר של "' + (item.biz_name || item.biznum) + '" יוסר מהרשימות ומקבצי הייצוא. להמשיך?',
      'מחק'
    );
    if (!ok) return;
    UI.busy(true, 'מוחק…');
    try {
      await API.del('/api/surveys/' + encodeURIComponent(item.id));
      UI.toast('הסקר נמחק', 'ok');
      load();
      App.refreshCounts();
    } catch (err) {
      UI.toast(err.message, 'err');
    } finally {
      UI.busy(false);
    }
  }

  function bind() {
    $('records-search').addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { page = 1; load(); }, 380);
    });
    $('records-city').addEventListener('change', () => { page = 1; load(); });
    $('records-mine').addEventListener('change', () => { page = 1; load(); });
  }

  return {
    bind: bind,
    load: () => { page = 1; load(); },
    reload: load
  };
})();
