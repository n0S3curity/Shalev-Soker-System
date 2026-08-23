/* ════════════════════════════════════════════════════════════════════
   The survey form: fill, edit, attachments, signature.
   ════════════════════════════════════════════════════════════════════ */
window.SurveyForm = (function () {
  'use strict';

  const $ = UI.$;
  const el = UI.el;

  const TEXT_FIELDS = ['f_bn', 'f_bid', 'f_rn', 'f_role', 'f_rph', 'f_bph',
                       'f_addr', 'f_bto', 'f_ysz', 'f_emp', 'f_notes'];
  const OPTION_KEYS = ['sector', 'kitchen', 'yard', 'wet', 'cardboard', 'decl_given', 'decl_ret'];
  const NEGATIVE_VALUES = ['no', 'לא מפנה'];

  let catalog = null;
  let state = null;
  let lookupTimer = null;

  function blankState() {
    return {
      surveyId: null,
      opts: {},
      containers: [],
      images: [],
      docs: [],
      signature: null,
      canEdit: true,
      ownerName: '',
      ownerEmail: ''
    };
  }

  /* ── Catalogue ─────────────────────────────────────────────────── */
  async function loadCatalog() {
    if (catalog) return catalog;
    catalog = await API.get('/api/surveys/meta/catalog');

    const select = $('f_bt');
    catalog.business_types.forEach((name) => {
      select.appendChild(el('option', { value: name, text: name }));
    });

    const types = $('container-types');
    UI.clear(types);
    catalog.container_order.forEach((name) => {
      types.appendChild(el('div', {
        class: 'ck', 'data-ctype': name, text: name,
        onclick: () => toggleContainerType(name)
      }));
    });
    return catalog;
  }

  const hasVolumes = (name) => catalog.no_volume_types.indexOf(name) < 0;

  /* ── Option buttons ────────────────────────────────────────────── */
  function setOption(key, value) {
    if (!state.canEdit) return;
    state.opts[key] = value;
    paintOptions();
    UI.show($('yard-size-field'), state.opts.yard === 'yes');
    UI.show($('emp-field'), state.opts.decl_ret === 'yes');
    updateProgress();
  }

  function paintOptions() {
    UI.qsa('.rr[data-opt]').forEach((group) => {
      const key = group.getAttribute('data-opt');
      UI.qsa('.rb', group).forEach((button) => {
        const value = button.getAttribute('data-val');
        const active = state.opts[key] === value;
        button.className = 'rb' + (active ? (NEGATIVE_VALUES.indexOf(value) >= 0 ? ' off' : ' on') : '');
      });
    });
  }

  /* ── Containers ────────────────────────────────────────────────── */
  function typeSelected(name) {
    const node = UI.qs('.ck[data-ctype="' + CSS.escape(name) + '"]');
    return !!node && node.classList.contains('sel');
  }

  function markType(name, selected) {
    const node = UI.qs('.ck[data-ctype="' + CSS.escape(name) + '"]');
    if (node) node.classList.toggle('sel', selected);
  }

  function toggleContainerType(name) {
    if (!state.canEdit) return;
    if (typeSelected(name)) {
      state.containers = state.containers.filter((entry) => entry.ctype !== name);
      markType(name, false);
    } else {
      markType(name, true);
      if (!hasVolumes(name)) {
        state.containers.push({ ctype: name, vol: null, qty: null, freq: '', freqOther: '', ownership: '', usage: '' });
      }
    }
    renderContainers();
  }

  function toggleVolume(ctype, vol) {
    if (!state.canEdit) return;
    const index = state.containers.findIndex((e) => e.ctype === ctype && e.vol === vol);
    if (index >= 0) {
      state.containers.splice(index, 1);
      if (!state.containers.some((e) => e.ctype === ctype)) markType(ctype, false);
    } else {
      state.containers.push({ ctype: ctype, vol: vol, qty: null, freq: '', freqOther: '', ownership: '', usage: '' });
    }
    renderContainers();
  }

  function findEntry(ctype, vol) {
    return state.containers.find((e) => e.ctype === ctype && (e.vol || null) === (vol || null));
  }

  function setEntryField(ctype, vol, field, value, rerender) {
    const entry = findEntry(ctype, vol);
    if (!entry) return;
    entry[field] = value;
    if (rerender) renderContainers();
  }

  function renderContainers() {
    const wrap = $('container-config');
    UI.clear(wrap);
    const disabled = !state.canEdit;

    catalog.container_order.forEach((name) => {
      if (!typeSelected(name)) return;
      const entries = state.containers.filter((e) => e.ctype === name);
      const block = el('div', { class: 'cblk' }, [
        el('div', { class: 'cblk-head', text: '🗑️ ' + name })
      ]);
      const body = el('div', { class: 'cblk-body' });

      if (hasVolumes(name)) {
        const chosen = entries.map((e) => e.vol);
        const row = el('div', { class: 'vol-sel-row' });
        (catalog.container_volumes[name] || []).forEach((vol) => {
          row.appendChild(el('div', {
            class: 'vbtn' + (chosen.indexOf(vol) >= 0 ? ' sel' : ''),
            text: vol + ' ל׳',
            onclick: disabled ? null : () => toggleVolume(name, vol)
          }));
        });
        body.appendChild(el('div', {}, [
          el('div', { class: 'lbl', text: 'בחר נפחים (ניתן לבחור מספר):' }),
          row
        ]));
      }

      entries.forEach((entry) => {
        body.appendChild(renderEntryCard(name, entry, disabled));
      });

      block.appendChild(body);
      wrap.appendChild(block);
    });
  }

  function renderEntryCard(name, entry, disabled) {
    const label = entry.vol ? name + ' ' + entry.vol + ' ל׳' : name;

    const title = el('div', { class: 'vcfg-title' }, [el('span', { text: '⚙️ ' + label })]);
    if (entry.vol && !disabled) {
      title.appendChild(el('button', {
        class: 'vcfg-remove', type: 'button', text: '×',
        title: 'הסר נפח', onclick: () => toggleVolume(name, entry.vol)
      }));
    }

    const qtyGrid = el('div', { class: 'qty-grid' });
    for (let n = 1; n <= 10; n++) {
      qtyGrid.appendChild(el('div', {
        class: 'qb' + (entry.qty === n ? ' sel' : ''), text: String(n),
        onclick: disabled ? null : () => setEntryField(name, entry.vol, 'qty', n, true)
      }));
    }

    const freqSelect = el('select', { class: 'sel', disabled: disabled || null }, [
      el('option', { value: '', text: '-- בחר --' })
    ]);
    catalog.freq_options.forEach((option) => {
      const node = el('option', { value: option, text: option });
      if (entry.freq === option) node.selected = true;
      freqSelect.appendChild(node);
    });
    freqSelect.addEventListener('change', function () {
      setEntryField(name, entry.vol, 'freq', this.value, true);
    });

    const freqOther = el('div', { class: entry.freq === 'אחר' ? 'slide' : 'hidden' }, [
      el('input', {
        class: 'inp', placeholder: 'פרט תדירות…', value: entry.freqOther || '',
        maxlength: '120', disabled: disabled || null,
        oninput: (event) => setEntryField(name, entry.vol, 'freqOther', event.target.value, false)
      })
    ]);

    return el('div', { class: 'vcfg' }, [
      title,
      el('div', { class: 'lbl', text: 'כמות' }),
      qtyGrid,
      el('div', { class: 'cfg-sec' }, [
        el('div', { class: 'f' }, [
          el('label', { class: 'lbl', text: 'תדירות פינוי' }), freqSelect, freqOther
        ]),
        el('div', { class: 'f' }, [
          el('label', { class: 'lbl', text: 'בעלות' }),
          optionRow(catalog.ownership_options, ['🏛️ רשות', '👤 פרטי'], entry.ownership,
                    (value) => setEntryField(name, entry.vol, 'ownership', value, true), disabled)
        ]),
        el('div', { class: 'f' }, [
          el('label', { class: 'lbl', text: 'שימוש' }),
          optionRow(catalog.usage_options, ['👥 משותף', '👤 אישי'], entry.usage,
                    (value) => setEntryField(name, entry.vol, 'usage', value, true), disabled)
        ])
      ])
    ]);
  }

  function optionRow(values, labels, current, onPick, disabled) {
    const row = el('div', { class: 'rr' });
    values.forEach((value, index) => {
      row.appendChild(el('div', {
        class: 'rb' + (current === value ? ' on' : ''),
        text: labels[index] || value,
        onclick: disabled ? null : () => onPick(value)
      }));
    });
    return row;
  }

  /* ── Attachments ───────────────────────────────────────────────── */
  async function uploadFile(file, kind) {
    const form = new FormData();
    form.append('file', file, file.name || 'file');
    form.append('kind', kind);
    return API.post('/api/files', form);
  }

  async function addImages(files) {
    const list = Array.prototype.slice.call(files);
    if (!list.length) return;
    UI.busy(true, 'מעלה תמונות…');
    try {
      for (const original of list) {
        try {
          const prepared = await UI.downscaleImage(original);
          const ref = await uploadFile(prepared, 'image');
          state.images.push(ref);
        } catch (err) {
          UI.toast((original.name || 'תמונה') + ': ' + err.message, 'err');
        }
      }
      renderImages();
    } finally {
      UI.busy(false);
    }
  }

  async function addDocs(files) {
    const list = Array.prototype.slice.call(files);
    if (!list.length) return;
    UI.busy(true, 'מעלה קבצים…');
    try {
      for (const file of list) {
        try {
          const ref = await uploadFile(file, 'doc');
          state.docs.push(ref);
        } catch (err) {
          UI.toast((file.name || 'קובץ') + ': ' + err.message, 'err');
        }
      }
      renderDocs();
    } finally {
      UI.busy(false);
    }
  }

  function fileUrl(id, download) {
    return '/api/files/' + encodeURIComponent(id) + (download ? '?download=true' : '');
  }

  function renderImages() {
    const wrap = $('image-list');
    UI.clear(wrap);
    state.images.forEach((ref, index) => {
      const image = el('img', { src: fileUrl(ref.id), alt: ref.name || 'תמונה', loading: 'lazy' });
      image.addEventListener('click', () => UI.lightbox(fileUrl(ref.id)));
      const cell = el('div', { class: 'thumb' }, [image]);
      if (state.canEdit) {
        cell.appendChild(el('button', {
          class: 'thumb-x', type: 'button', text: '×', title: 'הסר',
          onclick: async () => {
            const ok = await UI.confirm('הסרת תמונה', 'להסיר את התמונה מהסקר?', 'הסר');
            if (!ok) return;
            state.images.splice(index, 1);
            renderImages();
          }
        }));
      }
      wrap.appendChild(cell);
    });
  }

  function renderDocs() {
    const wrap = $('doc-list');
    UI.clear(wrap);
    state.docs.forEach((ref, index) => {
      const row = el('div', { class: 'fi' }, [
        el('span', { text: '📄' }),
        el('span', { class: 'fn', text: ref.name || 'קובץ' }),
        el('span', { class: 'fsz', text: UI.formatSize(ref.size) }),
        el('a', { class: 'fdl', href: fileUrl(ref.id), target: '_blank', rel: 'noopener noreferrer', text: 'פתח' })
      ]);
      if (state.canEdit) {
        row.appendChild(el('button', {
          class: 'fx', type: 'button', text: '×',
          onclick: async () => {
            const ok = await UI.confirm('הסרת קובץ', 'להסיר את הקובץ מהסקר?', 'הסר');
            if (!ok) return;
            state.docs.splice(index, 1);
            renderDocs();
          }
        }));
      }
      wrap.appendChild(row);
    });
  }

  /* ── Signature ─────────────────────────────────────────────────── */
  const signature = {
    canvas: null, ctx: null, drawing: false, dirty: false,

    setup() {
      this.canvas = $('sig-canvas');
      this.ctx = this.canvas.getContext('2d');
      this.ctx.strokeStyle = '#173404';
      this.ctx.lineWidth = 2.5;
      this.ctx.lineCap = 'round';
      this.ctx.lineJoin = 'round';

      const point = (event) => {
        const rect = this.canvas.getBoundingClientRect();
        const source = event.touches ? event.touches[0] : event;
        return {
          x: (source.clientX - rect.left) * (this.canvas.width / rect.width),
          y: (source.clientY - rect.top) * (this.canvas.height / rect.height)
        };
      };
      const start = (event) => {
        if (!state || !state.canEdit || state.signature) return;
        this.drawing = true; this.dirty = true;
        const p = point(event);
        this.ctx.beginPath(); this.ctx.moveTo(p.x, p.y);
      };
      const move = (event) => {
        if (!this.drawing) return;
        const p = point(event);
        this.ctx.lineTo(p.x, p.y); this.ctx.stroke();
      };
      const end = () => { this.drawing = false; };

      this.canvas.addEventListener('mousedown', start);
      this.canvas.addEventListener('mousemove', move);
      this.canvas.addEventListener('mouseup', end);
      this.canvas.addEventListener('mouseleave', end);
      this.canvas.addEventListener('touchstart', (e) => { e.preventDefault(); start(e); }, { passive: false });
      this.canvas.addEventListener('touchmove', (e) => { e.preventDefault(); move(e); }, { passive: false });
      this.canvas.addEventListener('touchend', end);
    },

    clear() {
      this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
      this.dirty = false;
      UI.show($('sig-ok'), false);
    },

    async save() {
      if (!this.dirty) return UI.toast('חתום על הלוח לפני השמירה', 'err');
      UI.busy(true, 'שומר חתימה…');
      try {
        const blob = await new Promise((resolve) => this.canvas.toBlob(resolve, 'image/png'));
        const ref = await uploadFile(new File([blob], 'signature.png', { type: 'image/png' }), 'signature');
        state.signature = ref;
        paintSignature();
        UI.toast('החתימה נשמרה', 'ok');
      } catch (err) {
        UI.toast(err.message, 'err');
      } finally {
        UI.busy(false);
      }
    }
  };

  function paintSignature() {
    const saved = !!state.signature;
    UI.show($('sig-saved'), saved);
    UI.show($('sig-draw'), !saved && state.canEdit);
    UI.show($('sig-lock'), saved);
    if (saved) {
      $('sig-image').src = fileUrl(state.signature.id);
      UI.show($('sig-admin-reset-wrap'), !!(state.surveyId && App.user && App.user.role === 'admin'));
    }
    if (!saved && !state.canEdit) {
      UI.show($('sig-saved'), false);
      UI.show($('sig-draw'), false);
    }
  }

  async function resetSignature() {
    const ok = await UI.confirm('איפוס חתימה',
      'החתימה תימחק לצמיתות ויהיה אפשר להחתים מחדש. להמשיך?', 'אפס חתימה');
    if (!ok) return;
    UI.busy(true, 'מאפס…');
    try {
      await API.post('/api/surveys/' + encodeURIComponent(state.surveyId) + '/signature/reset', {});
      state.signature = null;
      signature.clear();
      paintSignature();
      UI.toast('החתימה אופסה', 'ok');
    } catch (err) {
      UI.toast(err.message, 'err');
    } finally {
      UI.busy(false);
    }
  }

  /* ── Progress ──────────────────────────────────────────────────── */
  function value(id) { return ($(id).value || '').trim(); }

  function updateProgress() {
    let filled = 0;
    if (value('f_bn')) filled++;
    if (value('f_bid')) filled++;
    if (value('f_addr')) filled++;
    if (state.opts.sector) filled++;
    if (state.opts.decl_given) filled++;
    const percent = Math.round((filled / 5) * 100);
    $('progress-fill').style.width = percent + '%';
    $('progress-pct').textContent = percent + '%';
  }

  /* ── Read only mode ────────────────────────────────────────────── */
  function applyEditability() {
    const disabled = !state.canEdit;
    TEXT_FIELDS.forEach((id) => { $(id).disabled = disabled; });
    $('f_bt').disabled = disabled;
    UI.show($('form-footer'), !disabled);
    UI.show($('readonly-banner'), disabled);
    if (disabled) $('ro-owner').textContent = state.ownerName || state.ownerEmail || 'משתמש אחר';
    UI.qsa('.media-actions .btn').forEach((button) => { button.disabled = disabled; });
    $('uz-doc').style.display = disabled ? 'none' : '';
    document.body.classList.toggle('readonly', disabled);
  }

  /* ── Load / reset ──────────────────────────────────────────────── */
  function reset(keepSearch) {
    state = blankState();
    TEXT_FIELDS.forEach((id) => { $(id).value = ''; });
    $('f_bt').value = '';
    $('f_city').value = App.city || '';
    UI.show($('bt-other'), false);
    UI.show($('yard-size-field'), false);
    UI.show($('emp-field'), false);
    UI.show($('status-bar'), false);
    UI.qsa('.ck').forEach((node) => node.classList.remove('sel'));
    if (!keepSearch) $('search-biznum').value = '';
    paintOptions();
    renderContainers();
    renderImages();
    renderDocs();
    signature.clear();
    paintSignature();
    applyEditability();
    updateProgress();
    $('form-mode').textContent = 'סקר חדש';
    $('btn-save').textContent = '💾 שמור סקר';
  }

  function fill(survey) {
    state = blankState();
    state.surveyId = survey.id;
    state.canEdit = survey.can_edit;
    state.ownerName = survey.owner_name;
    state.ownerEmail = survey.owner_email;
    state.containers = (survey.containers || []).map((entry) => ({
      ctype: entry.ctype, vol: entry.vol || null, qty: entry.qty || null,
      freq: entry.freq || '', freqOther: entry.freqOther || '',
      ownership: entry.ownership || '', usage: entry.usage || ''
    }));
    state.images = survey.images || [];
    state.docs = survey.docs || [];
    state.signature = survey.signature || null;

    $('f_bn').value = survey.biz_name || '';
    $('f_bid').value = survey.biznum || '';
    $('f_rn').value = survey.rep_name || '';
    $('f_role').value = survey.role || '';
    $('f_rph').value = survey.rep_phone || '';
    $('f_bph').value = survey.biz_phone || '';
    $('f_addr').value = survey.address || '';
    $('f_city').value = survey.city || '';
    $('f_bt').value = survey.biz_type_std || '';
    UI.show($('bt-other'), survey.biz_type_std === 'אחר');
    if (survey.biz_type_std === 'אחר') $('f_bto').value = survey.biz_type || '';
    $('f_ysz').value = survey.yard_size || '';
    $('f_emp').value = survey.emp_count || '';
    $('f_notes').value = survey.notes || '';

    OPTION_KEYS.forEach((key) => { if (survey[key]) state.opts[key] = survey[key]; });
    paintOptions();
    UI.show($('yard-size-field'), state.opts.yard === 'yes');
    UI.show($('emp-field'), state.opts.decl_ret === 'yes');

    UI.qsa('.ck').forEach((node) => {
      const name = node.getAttribute('data-ctype');
      node.classList.toggle('sel', state.containers.some((e) => e.ctype === name));
    });

    renderContainers();
    renderImages();
    renderDocs();
    signature.clear();
    paintSignature();
    applyEditability();
    updateProgress();

    $('form-mode').textContent = state.canEdit ? 'עריכת סקר' : 'צפייה בסקר';
    $('btn-save').textContent = '💾 עדכן סקר';

    if (survey.city && survey.city !== App.city) App.setCity(survey.city, false);
  }

  async function open(surveyId) {
    UI.busy(true, 'טוען סקר…');
    try {
      const survey = await API.get('/api/surveys/' + encodeURIComponent(surveyId));
      await loadCatalog();
      fill(survey);
      $('search-biznum').value = survey.biznum || '';
      setStatus(survey.can_edit
        ? '✏️ עריכת סקר קיים — ' + (survey.biz_name || '')
        : '🔒 צפייה בלבד — נערך על ידי ' + (survey.owner_name || survey.owner_email),
        survey.can_edit ? 'st-found' : 'st-info');
      App.go('form');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      UI.toast(err.message, 'err');
    } finally {
      UI.busy(false);
    }
  }

  function setStatus(text, cssClass, extra) {
    const bar = $('status-bar');
    UI.clear(bar);
    bar.className = 'st-bar ' + cssClass;
    bar.appendChild(document.createTextNode(text));
    (extra || []).forEach((node) => bar.appendChild(node));
    UI.show(bar, true);
  }

  /* ── Business number lookup ────────────────────────────────────── */
  async function lookup(raw) {
    const biznum = (raw || '').trim();
    if (biznum.length < 3) { UI.show($('status-bar'), false); return; }
    try {
      const result = await API.get('/api/surveys/lookup?biznum=' + encodeURIComponent(biznum));
      const branches = result.branches || [];
      if (!branches.length) {
        setStatus('🆕 מספר עסק חדש — ימולא כסקר חדש', 'st-new');
        return;
      }
      const buttons = branches.map((branch) => el('button', {
        class: 'btn btn-s btn-mini',
        text: branch.city + ' · ' + (branch.address || '—'),
        onclick: () => open(branch.id)
      }));
      setStatus('✅ קיימים ' + branches.length + ' סניפים למספר הזה: ', 'st-found', buttons);
    } catch (err) {
      /* lookup is advisory only */
    }
  }

  /* ── Save ──────────────────────────────────────────────────────── */
  function collect() {
    const standardType = $('f_bt').value;
    return {
      city: $('f_city').value || App.city,
      biz_name: value('f_bn'),
      biznum: value('f_bid'),
      rep_name: value('f_rn'),
      role: value('f_role'),
      rep_phone: value('f_rph'),
      biz_phone: value('f_bph'),
      address: value('f_addr'),
      biz_type: standardType === 'אחר' ? value('f_bto') : standardType,
      biz_type_std: standardType,
      sector: state.opts.sector || '',
      kitchen: state.opts.kitchen || '',
      yard: state.opts.yard || '',
      yard_size: value('f_ysz'),
      containers: state.containers.map((entry) => ({
        ctype: entry.ctype,
        vol: entry.vol || null,
        qty: entry.qty || null,
        freq: entry.freq || '',
        freqOther: entry.freqOther || '',
        ownership: entry.ownership || '',
        usage: entry.usage || ''
      })),
      wet: state.opts.wet || '',
      cardboard: state.opts.cardboard || '',
      decl_given: state.opts.decl_given || '',
      decl_ret: state.opts.decl_ret || '',
      emp_count: value('f_emp'),
      notes: value('f_notes'),
      image_ids: state.images.map((ref) => ref.id),
      doc_ids: state.docs.map((ref) => ref.id),
      signature_id: state.signature ? state.signature.id : null
    };
  }

  async function save() {
    if (!state.canEdit) return;
    if (!value('f_bn') || !value('f_bid') || !value('f_addr')) {
      return UI.toast('יש למלא שם עסק, מספר ח.פ וכתובת', 'err');
    }
    if (!$('f_city').value) return UI.toast('בחר רשות מקומית לפני השמירה', 'err');

    const payload = collect();
    $('btn-save').disabled = true;
    UI.busy(true, 'שומר…');
    try {
      let saved;
      if (state.surveyId) {
        saved = await API.put('/api/surveys/' + encodeURIComponent(state.surveyId), payload);
        UI.toast('הסקר עודכן ✅', 'ok');
      } else {
        saved = await API.post('/api/surveys', payload);
        UI.toast('הסקר נשמר ✅', 'ok');
      }
      fill(saved);
      App.refreshCounts();
    } catch (err) {
      UI.toast(err.message, 'err');
    } finally {
      $('btn-save').disabled = false;
      UI.busy(false);
    }
  }

  /* ── Wiring ────────────────────────────────────────────────────── */
  function bind() {
    signature.setup();
    state = blankState();

    UI.qsa('.rr[data-opt]').forEach((group) => {
      const key = group.getAttribute('data-opt');
      UI.qsa('.rb', group).forEach((button) => {
        button.addEventListener('click', () => setOption(key, button.getAttribute('data-val')));
      });
    });

    ['f_bn', 'f_bid', 'f_addr'].forEach((id) => {
      $(id).addEventListener('input', updateProgress);
    });

    $('f_bt').addEventListener('change', function () {
      UI.show($('bt-other'), this.value === 'אחר');
    });

    $('search-biznum').addEventListener('input', function () {
      clearTimeout(lookupTimer);
      const raw = this.value;
      lookupTimer = setTimeout(() => lookup(raw), 420);
    });
    $('btn-clear-search').addEventListener('click', () => reset(false));
    $('btn-form-reset').addEventListener('click', async () => {
      const ok = await UI.confirm('ניקוי הטופס', 'כל הנתונים שהוזנו ולא נשמרו יימחקו. להמשיך?', 'נקה');
      if (ok) reset(false);
    });
    $('btn-save').addEventListener('click', save);

    $('btn-pick-image').addEventListener('click', () => $('input-image').click());
    $('input-image').addEventListener('change', function () {
      addImages(this.files); this.value = '';
    });
    $('uz-doc').addEventListener('click', () => { if (state.canEdit) $('input-doc').click(); });
    $('input-doc').addEventListener('change', function () {
      addDocs(this.files); this.value = '';
    });

    $('btn-camera').addEventListener('click', async () => {
      if (!UI.camera.supported()) {
        // Older mobile browsers: fall back to the native camera intent
        $('input-image').setAttribute('capture', 'environment');
        $('input-image').click();
        return;
      }
      try {
        const photo = await UI.camera.open();
        if (photo) await addImages([photo]);
      } catch (err) {
        UI.toast('אין גישה למצלמה. אשר את ההרשאה בדפדפן.', 'err');
        $('input-image').setAttribute('capture', 'environment');
        $('input-image').click();
      }
    });

    $('btn-sig-clear').addEventListener('click', () => signature.clear());
    $('btn-sig-save').addEventListener('click', () => signature.save());
    $('btn-sig-reset').addEventListener('click', resetSignature);
  }

  return {
    bind: bind,
    loadCatalog: loadCatalog,
    reset: reset,
    open: open,
    get state() { return state; }
  };
})();
