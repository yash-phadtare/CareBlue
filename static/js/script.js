/* CareBlue interactions — vanilla JS, no frameworks */
(function () {
  'use strict';

  function refreshIcons() {
    try { if (window.lucide) lucide.createIcons({ attrs: { 'stroke-width': 1.5 } }); } catch (e) { /* noop */ }
  }

  /* ---------- Snackbar helper (replaces native alert) ---------- */
  function mdNotify(message, type) {
    type = type || 'info';
    var stack = document.querySelector('.md-snack-stack');
    if (!stack) { return; }
    var icons = { success: 'check', danger: 'x', warning: 'triangle-alert', info: 'info' };
    var el = document.createElement('div');
    el.className = 'md-snackbar md-snackbar--' + type;
    el.setAttribute('role', type === 'danger' ? 'alert' : 'status');
    var icon = document.createElement('i');
    icon.setAttribute('data-lucide', icons[type] || 'info');
    var span = document.createElement('span');
    span.textContent = message;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = 'Dismiss';
    btn.setAttribute('aria-label', 'Dismiss notification');
    btn.setAttribute('data-md-dismiss', '');
    btn.addEventListener('click', function () { dismissSnack(el); });
    el.appendChild(icon);
    el.appendChild(span);
    el.appendChild(btn);
    stack.appendChild(el);
    refreshIcons();
    setTimeout(function () { if (el.isConnected) dismissSnack(el); }, 6000);
  }
  window.mdNotify = mdNotify;

  /* ---------- Mobile drawer ---------- */
  window.toggleSidebar = function (force) {
    const sb = document.getElementById('sidebar');
    const scrim = document.getElementById('sidebarScrim');
    const btn = document.querySelector('.md-menu-btn');
    if (!sb) return;
    const show = typeof force === 'boolean' ? force : !sb.classList.contains('mobile-open');
    sb.classList.toggle('mobile-open', show);
    if (scrim) scrim.classList.toggle('show', show);
    if (btn) btn.setAttribute('aria-expanded', show ? 'true' : 'false');
  };

  /* ---------- Collapsible rail (desktop, persisted) ---------- */
  window.toggleNavCollapse = function (force) {
    const collapsed = typeof force === 'boolean' ? force : !document.body.classList.contains('md-nav-collapsed');
    document.body.classList.toggle('md-nav-collapsed', collapsed);
    try { localStorage.setItem('md-nav-collapsed', collapsed ? '1' : '0'); } catch (e) { /* noop */ }
    document.querySelectorAll('.md-collapse-btn').forEach((b) => b.setAttribute('aria-expanded', collapsed ? 'false' : 'true'));
    labelRailLinks();
  };
  function labelRailLinks() {
    document.querySelectorAll('.md-nav-link').forEach((a) => {
      const t = a.querySelector('.md-nav-text');
      if (t && !a.hasAttribute('data-label')) a.setAttribute('data-label', t.textContent.trim());
    });
  }

  /* ---------- Slot selection (global, used by inline onclick) ---------- */
  window.selectSlot = function (element) {
    if (!element || element.classList.contains('booked') || element.disabled) return;
    document.querySelectorAll('.md-slot').forEach((s) => { s.classList.remove('selected'); s.setAttribute('aria-selected', 'false'); });
    element.classList.add('selected');
    element.setAttribute('aria-selected', 'true');
    const hidden = document.getElementById('time_slot');
    if (hidden) {
      hidden.value = element.dataset.start || '';
      hidden.dispatchEvent(new Event('change', { bubbles: true }));
    }
    const btn = document.getElementById('submitBtn');
    if (btn) btn.disabled = !hidden?.value;
    if (typeof window.mdUpdateSteps === 'function') window.mdUpdateSteps();
  };

  /* ---------- Dialogs with focus trap + restoration ---------- */
  let lastDialogTrigger = null;
  function focusables(root) {
    return [...root.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')]
      .filter((el) => el.offsetParent !== null || el === document.activeElement);
  }
  function openDialog(id, trigger) {
    const bd = document.querySelector(`[data-md-dialog="${CSS.escape(id)}"]`);
    if (!bd) return;
    if (trigger) lastDialogTrigger = trigger;
    else if (document.activeElement instanceof HTMLElement) lastDialogTrigger = document.activeElement;
    bd.classList.add('open');
    document.body.style.overflow = 'hidden';
    const dlg = bd.querySelector('.md-dialog');
    if (dlg) {
      dlg.setAttribute('tabindex', '-1');
      setTimeout(() => {
        const f = focusables(dlg);
        (f[0] || dlg).focus({ preventScroll: true });
      }, 30);
    }
  }
  function closeDialog(bd) {
    if (!bd) return;
    bd.classList.remove('open');
    if (!document.querySelector('.md-palette-backdrop.open')) document.body.style.overflow = '';
    if (lastDialogTrigger && document.contains(lastDialogTrigger)) {
      lastDialogTrigger.focus({ preventScroll: true });
      lastDialogTrigger = null;
    }
  }
  window.mdOpenDialog = openDialog;
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const open = document.querySelector('.md-dialog-backdrop.open .md-dialog');
    if (!open) return;
    const f = focusables(open);
    if (!f.length) { e.preventDefault(); open.focus(); return; }
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });

  /* ---------- Snackbars ---------- */
  function dismissSnack(el) {
    el.style.opacity = '0';
    el.style.transform = 'translateY(8px)';
    setTimeout(() => el.remove(), 250);
  }

  /* ---------- Dropdown menus ---------- */
  function closeAllMenus(except) {
    document.querySelectorAll('.md-menu.open').forEach((m) => {
      if (m !== except) {
        m.classList.remove('open');
        const t = document.querySelector(`[data-md-menu="${m.id}"]`);
        if (t) t.setAttribute('aria-expanded', 'false');
      }
    });
  }

  /* ---------- Session-timeout warning + keep-alive ---------- */
  (function initSessionWatchdog() {
    const lifetimeSec = parseInt(document.body.dataset.sessionLifetime || '0', 10);
    if (!lifetimeSec || lifetimeSec <= 0) return;
    const WARN_BEFORE = 5 * 60; // warn 5 minutes before expiry
    const stack = document.querySelector('.md-snack-stack');
    let warned = false;
    function showWarning() {
      if (warned || !document.body.contains(document.querySelector('.md-shell'))) return;
      warned = true;
      const el = document.createElement('div');
      el.className = 'md-snackbar md-snackbar--warning';
      el.setAttribute('role', 'alert');
      el.innerHTML = '<i data-lucide="alarm-clock"></i><span>Your session expires in 5 minutes. Save your work.</span>';
      const stay = document.createElement('button');
      stay.type = 'button';
      stay.textContent = 'Stay signed in';
      stay.addEventListener('click', async () => {
        try {
          const res = await fetch('/session/refresh');
          if (res.ok) { dismissSnack(el); arm(); return; }
        } catch (e) { /* noop */ }
        window.location.href = '/login';
      });
      el.appendChild(stay);
      const dis = document.createElement('button');
      dis.type = 'button'; dis.textContent = 'Dismiss'; dis.setAttribute('aria-label', 'Dismiss notification');
      dis.addEventListener('click', () => dismissSnack(el));
      el.appendChild(dis);
      (stack || document.body).appendChild(el);
      if (window.lucide) { try { lucide.createIcons({ attrs: { 'stroke-width': 1.5 } }); } catch (e) {} }
    }
    function arm() {
      warned = false;
      clearTimeout(arm.t);
      arm.t = setTimeout(showWarning, Math.max((lifetimeSec - WARN_BEFORE) * 1000, 60000));
    }
    arm();
    ['click', 'keydown'].forEach((ev) => document.addEventListener(ev, () => {
      if (warned) return;
      clearTimeout(arm.t);
      arm.t = setTimeout(showWarning, Math.max((lifetimeSec - WARN_BEFORE) * 1000, 60000));
    }, { passive: true }));
  })();
  /* ---------- Command palette ---------- */
  const paletteActions = [];
  function collectActions() {
    paletteActions.length = 0;
    document.querySelectorAll('.md-nav-link').forEach((a) => {
      const label = (a.textContent || '').trim().replace(/\s+/g, ' ');
      if (a.href && label) paletteActions.push({ label, href: a.href, icon: (a.querySelector('svg,i') || {}).outerHTML || '' });
    });
    document.querySelectorAll('#quickMenu a').forEach((a) => {
      const label = (a.textContent || '').trim();
      if (a.href && label && !paletteActions.some((p) => p.href === a.href))
        paletteActions.push({ label, href: a.href, icon: '' });
    });
  }
  let paletteIndex = 0;
  function patientSearchURL() {
    const doctorLink = document.querySelector('.md-nav-link[href*="/doctor/patients"]');
    if (doctorLink) return '/doctor/patients?search=';
    return '/admin/view_patients?search=';
  }
  function renderPalette(filter) {
    const list = document.getElementById('paletteList');
    if (!list) return;
    const q = (filter || '').trim().toLowerCase();
    const hits = paletteActions.filter((a) => !q || a.label.toLowerCase().includes(q)).slice(0, 8);
    paletteIndex = 0;
    list.innerHTML = hits.map((a, i) =>
      `<button type="button" class="md-palette-item${i === 0 ? ' active' : ''}" role="option" aria-selected="${i === 0}" data-href="${a.href}"><i data-lucide="arrow-right"></i><span>${a.label.replace(/</g, '&lt;')}</span></button>`
    ).join('') || `<div class="md-caption" style="padding:0.8rem">No matching page — press Enter to search patients.</div>`;
    refreshIcons();
    list.querySelectorAll('.md-palette-item').forEach((b) => b.addEventListener('click', () => { window.location.href = b.dataset.href; }));
  }
  function openPalette() {
    collectActions();
    const bd = document.getElementById('paletteBackdrop');
    if (!bd) return;
    bd.classList.add('open');
    document.body.style.overflow = 'hidden';
    const input = document.getElementById('paletteInput');
    input.value = '';
    renderPalette('');
    setTimeout(() => input.focus(), 30);
  }
  function closePalette() {
    const bd = document.getElementById('paletteBackdrop');
    if (!bd) return;
    bd.classList.remove('open');
    if (!document.querySelector('.md-dialog-backdrop.open')) document.body.style.overflow = '';
  }
  function movePalette(dir) {
    const items = [...document.querySelectorAll('#paletteList .md-palette-item')];
    if (!items.length) return;
    paletteIndex = (paletteIndex + dir + items.length) % items.length;
    items.forEach((b, i) => { b.classList.toggle('active', i === paletteIndex); b.setAttribute('aria-selected', i === paletteIndex ? 'true' : 'false'); });
    items[paletteIndex].scrollIntoView({ block: 'nearest' });
  }

  /* ---------- Tabs ---------- */
  function initTabs(scope) {
    scope.querySelectorAll('[data-md-tabs]').forEach((tabs) => {
      const btns = [...tabs.querySelectorAll('[role="tab"]')];
      const panels = btns.map((b) => document.getElementById(b.getAttribute('aria-controls'))).filter(Boolean);
      function select(btn, focus) {
        btns.forEach((b) => { const on = b === btn; b.setAttribute('aria-selected', on ? 'true' : 'false'); b.tabIndex = on ? 0 : -1; if (focus && on) b.focus(); });
        panels.forEach((p) => { p.hidden = p.id !== btn.getAttribute('aria-controls'); });
      }
      btns.forEach((b, i) => {
        b.addEventListener('click', () => select(b, false));
        b.addEventListener('keydown', (e) => {
          if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
            e.preventDefault();
            select(btns[(i + (e.key === 'ArrowRight' ? 1 : btns.length - 1)) % btns.length], true);
          }
        });
      });
      if (btns.length) select(btns[0], false);
    });
  }

  /* ---------- Mobile card tables: copy header text to data-label ---------- */
  function initCardTables(scope) {
    scope.querySelectorAll('table.md-table').forEach((table) => {
      table.classList.add('md-table--cards');
      const heads = [...table.querySelectorAll('thead th')].map((th) => th.textContent.trim());
      table.querySelectorAll('tbody tr').forEach((tr) => {
        [...tr.children].forEach((td, i) => {
          if (!td.hasAttribute('data-label')) td.setAttribute('data-label', heads[i] || '');
        });
      });
      if (!table.querySelector('caption')) {
        const cap = document.createElement('caption');
        cap.className = 'md-sr-only';
        cap.textContent = 'Data table';
        table.prepend(cap);
      }
    });
  }

  /* ---------- Client pagination (shareable via ?pg= / hash, server upgrade path noted) ---------- */
  function initPagination(scope) {
    scope.querySelectorAll('table.js-paginate').forEach((table, ti) => {
      const size = parseInt(table.dataset.pageSize || '10', 10);
      const rows = [...table.querySelectorAll('tbody tr')];
      if (rows.length <= size) return;
      const key = 'pg' + (table.dataset.pgKey || ti);
      const params = new URLSearchParams(window.location.search);
      let page = Math.max(0, parseInt(params.get(key) || '0', 10) || 0);
      const pages = Math.ceil(rows.length / size);
      page = Math.min(page, pages - 1);
      const bar = document.createElement('div');
      bar.className = 'md-pager';
      bar.setAttribute('role', 'navigation');
      bar.setAttribute('aria-label', 'Table pages');
      bar.innerHTML = `<span class="md-pager-label" role="status"></span><span class="md-cluster"><button type="button" class="md-btn md-btn--outlined md-btn--sm" data-pg="prev">← Prev</button><button type="button" class="md-btn md-btn--outlined md-btn--sm" data-pg="next">Next →</button></span>`;
      table.closest('.md-table-wrap, .md-table-scroll, .md-card')?.appendChild(bar)
        || table.parentElement.appendChild(bar);
      const label = bar.querySelector('.md-pager-label');
      const prev = bar.querySelector('[data-pg="prev"]');
      const next = bar.querySelector('[data-pg="next"]');
      function draw(syncUrl) {
        rows.forEach((r, i) => { r.style.display = (i >= page * size && i < page * size + size) ? '' : 'none'; });
        label.textContent = `Showing ${page * size + 1}–${Math.min(page * size + size, rows.length)} of ${rows.length}`;
        prev.disabled = page === 0;
        next.disabled = page >= pages - 1;
        if (syncUrl) {
          const u = new URL(window.location.href);
          u.searchParams.set(key, String(page));
          window.history.replaceState(null, '', u);
        }
      }
      prev.addEventListener('click', () => { if (page > 0) { page--; draw(true); } });
      next.addEventListener('click', () => { if (page < pages - 1) { page++; draw(true); } });
      draw(false);
    });
  }

  /* ---------- Consistent form validation: errors, summary, focus ---------- */
  function fieldLabel(field) {
    const id = field.id;
    if (id) {
      const l = document.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (l) return l.textContent.replace(/\*.*$/, '').trim();
    }
    const wrap = field.closest('.md-field');
    const l = wrap ? wrap.querySelector('label') : null;
    return (l ? l.textContent : (field.name || 'This field')).replace(/\*.*$/, '').trim() || 'This field';
  }
  function ensureErrorText(field) {
    const wrap = field.closest('.md-field');
    if (!wrap) return null;
    let err = wrap.querySelector('.md-error-text');
    if (!err) {
      err = document.createElement('p');
      err.className = 'md-error-text';
      err.setAttribute('role', 'alert');
      wrap.appendChild(err);
    }
    return err;
  }
  function validateField(field) {
    const wrap = field.closest('.md-field');
    const err = ensureErrorText(field);
    const ok = field.checkValidity();
    if (wrap) wrap.classList.toggle('md-field--invalid', !ok);
    if (err) err.textContent = ok ? '' : (field.validationMessage || `${fieldLabel(field)} is required.`);
    return ok;
  }
  function initValidation(scope) {
    scope.querySelectorAll('form').forEach((form) => {
      if (form.method.toUpperCase() === 'GET' || form.querySelector('[data-no-validate]')) return;
      // Mark required fields + live validation
      form.querySelectorAll('[required]').forEach((f) => {
        const wrap = f.closest('.md-field');
        const lab = wrap ? wrap.querySelector('label') : document.querySelector(`label[for="${f.id}"]`);
        if (lab && !lab.querySelector('.req')) {
          const s = document.createElement('span');
          s.className = 'req';
          s.setAttribute('aria-hidden', 'true');
          s.textContent = ' *';
          lab.appendChild(s);
          lab.setAttribute('title', 'Required');
        }
        f.addEventListener('blur', () => { if (form.classList.contains('was-validated')) validateField(f); });
        f.addEventListener('input', () => { if (form.classList.contains('was-validated')) validateField(f); });
        f.addEventListener('change', () => { if (form.classList.contains('was-validated')) validateField(f); });
      });
      form.setAttribute('novalidate', '');
      form.addEventListener('submit', (e) => {
        form.querySelectorAll('.md-error-summary').forEach((s) => s.remove());
        const invalid = [...form.querySelectorAll('input, select, textarea')].filter((f) => !f.checkValidity());
        form.classList.add('was-validated');
        invalid.forEach(validateField);
        if (invalid.length) {
          e.preventDefault();
          e.stopPropagation();
          const summary = document.createElement('div');
          summary.className = 'md-error-summary';
          summary.setAttribute('role', 'alert');
          summary.setAttribute('tabindex', '-1');
          summary.innerHTML = `<strong>${invalid.length} field${invalid.length > 1 ? 's need' : ' needs'} attention</strong>`;
          const ul = document.createElement('ul');
          invalid.slice(0, 5).forEach((f) => {
            const li = document.createElement('li');
            const a = document.createElement('a');
            a.href = '#';
            a.textContent = `${fieldLabel(f)} — ${f.validationMessage || 'required'}`;
            a.addEventListener('click', (ev) => { ev.preventDefault(); f.focus(); });
            li.appendChild(a);
            ul.appendChild(li);
          });
          summary.appendChild(ul);
          form.prepend(summary);
          summary.focus({ preventScroll: false });
          invalid[0].focus({ preventScroll: false });
          mdNotify('Please fix the highlighted fields.', 'danger');
        }
      });
    });
  }

  /* ---------- Searchable select enhancement (patient/doctor/visit) ---------- */
  function initSearchableSelects(scope) {
    scope.querySelectorAll('select[data-searchable], #patient_id, #doctor_id, #appointment_id').forEach((sel) => {
      if (sel.dataset.enhanced || sel.options.length < 4) return;
      sel.dataset.enhanced = '1';
      sel.setAttribute('aria-label', sel.getAttribute('aria-label') || 'Type to filter options');
      const wrap = document.createElement('div');
      wrap.className = 'md-picker';
      sel.parentElement.insertBefore(wrap, sel);
      wrap.appendChild(sel);
      const list = document.createElement('div');
      list.className = 'md-picker-list';
      list.setAttribute('role', 'listbox');
      wrap.appendChild(list);
      let items = [];
      function render(filter) {
        const q = (filter || '').toLowerCase();
        list.innerHTML = '';
        items = [...sel.options].filter((o) => !q || o.text.toLowerCase().includes(q)).slice(0, 30);
        if (!items.length) {
          list.innerHTML = '<div class="md-caption" style="padding:0.6rem 0.8rem">No matches.</div>';
          return;
        }
        items.forEach((o, i) => {
          const b = document.createElement('button');
          b.type = 'button';
          b.textContent = o.text;
          b.setAttribute('role', 'option');
          if (o.value === sel.value) b.classList.add('active');
          b.addEventListener('click', () => {
            sel.value = o.value;
            sel.dispatchEvent(new Event('change', { bubbles: true }));
            list.classList.remove('open');
          });
          list.appendChild(b);
        });
      }
      render('');
      sel.addEventListener('focus', () => { render(''); list.classList.add('open'); });
      sel.addEventListener('keydown', (e) => {
        // Typing filters the dropdown like a search
        if (e.key.length === 1) {
          list.classList.add('open');
          const q = (sel.dataset.q || '') + e.key;
          sel.dataset.q = q;
          clearTimeout(sel._qt);
          sel._qt = setTimeout(() => { sel.dataset.q = ''; }, 800);
          render(q);
        } else if (e.key === 'Escape') {
          list.classList.remove('open');
        }
      });
      document.addEventListener('click', (e) => { if (!wrap.contains(e.target)) list.classList.remove('open'); });
    });
  }

  /* ---------- Booking steps progress ---------- */
  function initSteps() {
    const steps = document.querySelector('.md-steps[data-steps]') || document.querySelector('.md-steps');
    if (!steps || !document.getElementById('appointmentForm')) return;
    const spans = [...steps.querySelectorAll('span')];
    if (spans.length < 3) return;
    spans.forEach((s) => s.classList.add('md-step'));
    window.mdUpdateSteps = function () {
      const p = !!document.getElementById('patient_id')?.value && !!document.getElementById('doctor_id')?.value;
      const d = !!document.getElementById('date')?.value;
      const t = !!document.getElementById('time_slot')?.value;
      spans.forEach((s) => s.classList.remove('on', 'done'));
      if (t) { spans[0].classList.add('done'); spans[1].classList.add('done'); spans[2].classList.add('on'); }
      else if (d && p) { spans[0].classList.add('done'); spans[1].classList.add('done'); spans[2].classList.add('on'); spans[2].scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
      else if (p) { spans[0].classList.add('done'); spans[1].classList.add('on'); }
      else { spans[0].classList.add('on'); }
    };
    window.mdUpdateSteps();
  }

  document.addEventListener('DOMContentLoaded', function () {
    refreshIcons();
    labelRailLinks();
    try {
      if (localStorage.getItem('md-nav-collapsed') === '1' && window.innerWidth >= 992)
        document.body.classList.add('md-nav-collapsed');
    } catch (e) { /* noop */ }

    /* Snackbars: pause the auto-dismiss while hovered/focused, resume after */
    document.querySelectorAll('.md-snackbar').forEach((el) => {
      let remaining = 6000;
      let startedAt = Date.now();
      let t = setTimeout(() => dismissSnack(el), remaining);
      const pause = () => { clearTimeout(t); remaining -= Date.now() - startedAt; };
      const resume = () => {
        clearTimeout(t);
        startedAt = Date.now();
        t = setTimeout(() => dismissSnack(el), Math.max(remaining, 1500));
      };
      el.addEventListener('mouseenter', pause);
      el.addEventListener('focusin', pause);
      el.addEventListener('mouseleave', resume);
      el.addEventListener('focusout', resume);
      el.querySelectorAll('[data-md-dismiss]').forEach((b) => b.addEventListener('click', () => { clearTimeout(t); dismissSnack(el); }));
    });

    /* Menus */
    document.querySelectorAll('[data-md-menu]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const menu = document.getElementById(btn.getAttribute('data-md-menu'));
        const willOpen = menu && !menu.classList.contains('open');
        closeAllMenus(menu);
        if (menu && willOpen) { menu.classList.add('open'); btn.setAttribute('aria-expanded', 'true'); }
        else btn.setAttribute('aria-expanded', 'false');
      });
    });
    document.addEventListener('click', (e) => {
      if (!e.target.closest('.md-menu-wrap')) closeAllMenus(null);
      if (e.target.closest('[data-md-palette-open]')) closeAllMenus(null);
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        window.toggleSidebar(false);
        closeAllMenus(null);
        closePalette();
        document.querySelectorAll('.md-dialog-backdrop.open').forEach(closeDialog);
      }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
    });

    /* Palette */
    document.querySelectorAll('[data-md-palette-open]').forEach((b) => b.addEventListener('click', openPalette));
    const pbd = document.getElementById('paletteBackdrop');
    if (pbd) {
      pbd.addEventListener('click', (e) => { if (e.target === pbd) closePalette(); });
      const input = document.getElementById('paletteInput');
      input.addEventListener('input', () => renderPalette(input.value));
      input.addEventListener('keydown', (e) => {
        if (e.key === 'ArrowDown') { e.preventDefault(); movePalette(1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); movePalette(-1); }
        else if (e.key === 'Enter') {
          const items = [...document.querySelectorAll('#paletteList .md-palette-item')];
          if (items.length && (input.value.trim() === '' || document.querySelector('#paletteList .md-palette-item.active'))) {
            const active = document.querySelector('#paletteList .md-palette-item.active') || items[0];
            window.location.href = active.dataset.href;
          } else if (input.value.trim() !== '') {
            window.location.href = patientSearchURL() + encodeURIComponent(input.value.trim());
          }
        }
      });
    }

    /* Dialogs */
    document.querySelectorAll('[data-md-open]').forEach((btn) => {
      btn.addEventListener('click', () => openDialog(btn.getAttribute('data-md-open'), btn));
    });
    document.querySelectorAll('.md-dialog-backdrop').forEach((bd) => {
      bd.addEventListener('click', (e) => { if (e.target === bd) closeDialog(bd); });
      bd.querySelectorAll('[data-md-close]').forEach((b) => b.addEventListener('click', () => closeDialog(bd)));
    });

    /* Destructive / clinical confirmations: data-confirm="message" */
    document.querySelectorAll('form[data-confirm], button[data-confirm], a[data-confirm]').forEach((el) => {
      const msg = el.getAttribute('data-confirm');
      if (el.tagName === 'FORM') {
        el.addEventListener('submit', (e) => {
          if (el.dataset.confirmed === '1') { delete el.dataset.confirmed; return; }
          e.preventDefault();
          mdNotify(msg || 'Please confirm this action.', 'warning');
          if (window.confirm(msg || 'Are you sure?')) { el.dataset.confirmed = '1'; el.requestSubmit(); }
        });
      } else {
        el.addEventListener('click', (e) => {
          if (!window.confirm(msg || 'Are you sure?')) e.preventDefault();
        });
      }
    });

    /* Icon-button tooltips: promote title → floating tip (keeps aria-label) */
    document.querySelectorAll('.md-icon-btn[title]').forEach((btn) => {
      if (!btn.hasAttribute('aria-label')) btn.setAttribute('aria-label', btn.getAttribute('title'));
      btn.setAttribute('data-tip', btn.getAttribute('title'));
      btn.removeAttribute('title');
    });

    /* Password toggles */
    document.querySelectorAll('[data-pass-toggle]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const input = document.getElementById(btn.getAttribute('data-pass-toggle'));
        if (!input) return;
        const show = input.type === 'password';
        input.type = show ? 'text' : 'password';
        btn.setAttribute('aria-pressed', show ? 'true' : 'false');
        btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
        btn.innerHTML = show
          ? '<i data-lucide="eye-off"></i>'
          : '<i data-lucide="eye"></i>';
        refreshIcons();
        input.focus();
      });
    });

    /* Slot keyboard activation */
    document.addEventListener('keydown', (e) => {
      const t = e.target;
      if (t && t.classList && t.classList.contains('md-slot') && (e.key === 'Enter' || e.key === ' ')) {
        e.preventDefault();
        window.selectSlot(t);
      }
    });

    initCardTables(document);
    initValidation(document);
    initSearchableSelects(document);
    initSteps();
    initTabs(document);
    initPagination(document);
  });
})();
