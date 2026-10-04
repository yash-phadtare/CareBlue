/* CareBlue interactions — vanilla JS, no frameworks */
(function () {
  'use strict';

  function refreshIcons() {
    try { if (window.lucide) lucide.createIcons({ attrs: { 'stroke-width': 1.75 } }); } catch (e) { /* noop */ }
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
    if (type === 'success' || type === 'info') setTimeout(function () { if (el.isConnected) dismissSnack(el); }, 8000);
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
    const restoreFocus = !show && sb.contains(document.activeElement);
    document.querySelector('.md-main')?.toggleAttribute('inert', show);
    document.body.classList.toggle('drawer-open', show);
    if (show) sb.querySelector('a,button')?.focus();
    else if (restoreFocus) btn?.focus();
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
      if (t && !a.hasAttribute('aria-label')) a.setAttribute('aria-label', t.textContent.trim());
    });
  }

  /* ---------- Slot selection (global, used by inline onclick) ---------- */
  window.selectSlot = function (element) {
    if (!element || element.classList.contains('booked') || element.disabled) return;
    document.querySelectorAll('.md-slot').forEach((s) => { s.classList.remove('selected'); s.setAttribute('aria-selected', 'false'); s.tabIndex = -1; });
    element.classList.add('selected');
    element.setAttribute('aria-selected', 'true');
    element.tabIndex = 0;
    const hidden = document.getElementById('time_slot');
    if (hidden) {
      hidden.value = element.dataset.start || '';
      hidden.dispatchEvent(new Event('change', { bubbles: true }));
    }
    document.querySelectorAll('.md-slot-grid').forEach(grid => {
      if (!grid.contains(element)) {
        const first=grid.querySelector('.md-slot:not(:disabled)');
        if(first)first.tabIndex=0;
      }
    });
  };

  /* ---------- Dialogs with focus trap + restoration ---------- */
  let lastDialogTrigger = null;
  const overlaySiblings = new WeakMap();
  function isolateOverlay(root, show) {
    if (show) {
      const siblings = [];
      let node = root;
      while (node.parentElement) {
        [...node.parentElement.children].forEach(sibling => {
          if (sibling === node || sibling.matches('script,style,.md-snack-stack')) return;
          siblings.push([sibling, sibling.inert]); sibling.inert = true;
        });
        node = node.parentElement;
        if (node === document.body) break;
      }
      overlaySiblings.set(root, siblings);
    } else {
      (overlaySiblings.get(root) || []).forEach(([node, previous]) => node.inert = previous);
      overlaySiblings.delete(root);
    }
  }
  function focusables(root) {
    return [...root.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')]
      .filter((el) => el.offsetParent !== null || el === document.activeElement);
  }
  function openDialog(id, trigger) {
    const bd = document.querySelector(`[data-md-dialog="${CSS.escape(id)}"]`);
    if (!bd) return;
    window.toggleSidebar(false);
    if (trigger) lastDialogTrigger = trigger;
    else if (document.activeElement instanceof HTMLElement) lastDialogTrigger = document.activeElement;
    bd.classList.add('open');
    isolateOverlay(bd, true);
    document.body.style.overflow = 'hidden';
    const dlg = bd.querySelector('.md-dialog');
    if (dlg) {
      dlg.setAttribute('tabindex', '-1');
      setTimeout(() => {
        const f = focusables(dlg);
        (f.find(el => el.matches('input,select,textarea')) || f[0] || dlg).focus({ preventScroll: true });
      }, 30);
    }
  }
  function closeDialog(bd) {
    if (!bd) return;
    if (bd.querySelector('form[data-submitting="1"]')) return;
    bd.classList.remove('open');
    isolateOverlay(bd, false);
    if (!document.querySelector('.md-palette-backdrop.open')) document.body.style.overflow = '';
    if (lastDialogTrigger && document.contains(lastDialogTrigger)) {
      lastDialogTrigger.focus({ preventScroll: true });
      lastDialogTrigger = null;
    }
  }
  window.mdOpenDialog = openDialog;
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const open = document.querySelector('.md-dialog-backdrop.open .md-dialog, .md-palette-backdrop.open .md-palette, .md-drawer.mobile-open');
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
  const menuAnchors = new WeakMap();
  function positionMenu(menu, trigger) {
    const rect = trigger.getBoundingClientRect();
    menuAnchors.set(menu, {trigger, x:rect.x, y:rect.y});
    menu.style.position = 'fixed';
    menu.style.right = 'auto';
    const width = menu.offsetWidth, height = menu.offsetHeight;
    const left = Math.max(16, Math.min(rect.right - width, window.innerWidth - width - 16));
    const below = rect.bottom + 8;
    const top = below + height <= window.innerHeight - 16 ? below : Math.max(16, rect.top - height - 8);
    menu.style.left = left + 'px'; menu.style.top = top + 'px';
  }
  window.addEventListener('resize', () => { closeAllMenus(null); if (window.innerWidth >= 1024) window.toggleSidebar(false); });
  document.addEventListener('scroll', e => {
    if (e.target.closest?.('.md-menu')) return;
    document.querySelectorAll('.md-menu.open').forEach(menu => {
      const anchor = menuAnchors.get(menu);
      if (!anchor) return;
      const rect = anchor.trigger.getBoundingClientRect();
      if (Math.abs(rect.x-anchor.x)>1 || Math.abs(rect.y-anchor.y)>1) closeAllMenus(null);
    });
  }, true);

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
          const res = await fetch('/session/refresh', {method:'POST', headers:{'X-CSRF-Token':document.querySelector('meta[name="csrf-token"]').content}});
          if (res.ok) { dismissSnack(el); arm(); return; }
        } catch (e) { /* noop */ }
        dismissSnack(el);
        window.dispatchEvent(new Event('careblue:session-expired'));
      });
      el.appendChild(stay);
      const dis = document.createElement('button');
      dis.type = 'button'; dis.textContent = 'Dismiss'; dis.setAttribute('aria-label', 'Dismiss notification');
      dis.addEventListener('click', () => dismissSnack(el));
      el.appendChild(dis);
      (stack || document.body).appendChild(el);
      if (window.lucide) { try { lucide.createIcons({ attrs: { 'stroke-width': 1.75 } }); } catch (e) {} }
    }
    function arm() {
      warned = false;
      clearTimeout(arm.t);
      arm.t = setTimeout(showWarning, Math.max((lifetimeSec - WARN_BEFORE) * 1000, 60000));
    }
    window.addEventListener('careblue:session-restored', arm);
    arm();
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
    return document.querySelector('.md-nav-link[href*="/admin/view_patients"]') ? '/admin/view_patients?search=' : null;
  }
  function renderPalette(filter) {
    const list = document.getElementById('paletteList');
    if (!list) return;
    const q = (filter || '').trim().toLowerCase();
    const hits = paletteActions.filter((a) => !q || a.label.toLowerCase().includes(q)).slice(0, 8);
    paletteIndex = 0;
    list.innerHTML = hits.map((a, i) =>
      `<button type="button" id="palette-option-${i}" class="md-palette-item${i === 0 ? ' active' : ''}" role="option" aria-selected="${i === 0}" data-href="${a.href}"><i data-lucide="arrow-right"></i><span>${a.label.replace(/</g, '&lt;')}</span></button>`
    ).join('') || `<div class="md-caption palette-empty">No matching page.${patientSearchURL() ? ' Press Enter to search patients.' : ' Try another page name.'}</div>`;
    refreshIcons();
    const input = document.getElementById('paletteInput');
    if (hits.length) input?.setAttribute('aria-activedescendant', 'palette-option-0');
    else input?.removeAttribute('aria-activedescendant');
    list.querySelectorAll('.md-palette-item').forEach((b) => b.addEventListener('click', () => { window.location.href = b.dataset.href; }));
  }
  let lastPaletteTrigger;
  function openPalette() {
    lastPaletteTrigger = document.activeElement;
    collectActions();
    const bd = document.getElementById('paletteBackdrop');
    if (!bd) return;
    window.toggleSidebar(false);
    bd.classList.add('open');
    isolateOverlay(bd, true);
    document.body.style.overflow = 'hidden';
    const input = document.getElementById('paletteInput');
    input.value = '';
    renderPalette('');
    setTimeout(() => input.focus(), 30);
  }
  function closePalette() {
    const bd = document.getElementById('paletteBackdrop');
    if (!bd) return;
    const wasOpen = bd.classList.contains('open');
    bd.classList.remove('open');
    isolateOverlay(bd, false);
    if (wasOpen) lastPaletteTrigger?.focus();
    if (!document.querySelector('.md-dialog-backdrop.open')) document.body.style.overflow = '';
  }
  function movePalette(dir) {
    const items = [...document.querySelectorAll('#paletteList .md-palette-item')];
    if (!items.length) return;
    paletteIndex = (paletteIndex + dir + items.length) % items.length;
    items.forEach((b, i) => { b.classList.toggle('active', i === paletteIndex); b.setAttribute('aria-selected', i === paletteIndex ? 'true' : 'false'); });
    items[paletteIndex].scrollIntoView({ block: 'nearest' });
    document.getElementById('paletteInput')?.setAttribute('aria-activedescendant', items[paletteIndex].id);
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
          } else if (e.key === 'Home' || e.key === 'End') {
            e.preventDefault();select(btns[e.key==='Home'?0:btns.length-1],true);
          }
        });
      });
      if (btns.length) select(btns[0], false);
    });
  }

  /* ---------- Mobile card tables: copy header text to data-label ---------- */
  function initCardTables(scope) {
    scope.querySelectorAll('table.md-table').forEach((table) => {
      if (table.hasAttribute('data-mobile-cards')) table.classList.add('md-table--cards');
      table.setAttribute('role', 'table');
      table.querySelectorAll('thead,tbody').forEach(group => group.setAttribute('role','rowgroup'));
      table.querySelectorAll('tr').forEach(row => row.setAttribute('role','row'));
      table.querySelectorAll('th').forEach(cell => cell.setAttribute('role','columnheader'));
      const heads = [...table.querySelectorAll('thead th')].map((th) => th.textContent.trim());
      table.querySelectorAll('thead th').forEach((th, i) => {
        th.dataset.label = heads[i];
        if (heads[i] === '#') th.classList.add('md-index-cell');
      });
      table.querySelectorAll('tbody tr').forEach((tr) => {
        [...tr.children].forEach((td, i) => {
          td.setAttribute('role','cell');
          if (heads[i] === '#') td.classList.add('md-index-cell');
          if (/^(Patient|Medicine|Item|Doctor|When|Actions|Open|Timeline|Detail|Hospital|Workspace|Ward \/ bed)$/i.test(heads[i] || '') || td.colSpan > 1) td.classList.add('md-card-cell--wide');
          if (!td.hasAttribute('data-label')) td.setAttribute('data-label', heads[i] || '');
          if (table.hasAttribute('data-mobile-cards') && td.colSpan === 1 && heads[i]) {
            const label = document.createElement('span'); label.className = 'md-cell-label';
            label.setAttribute('aria-hidden','true'); label.textContent = td.dataset.label;
            td.prepend(label);
          }
        });
      });
      if (!table.querySelector('caption')) {
        const cap = document.createElement('caption');
        cap.className = 'md-sr-only';
        cap.textContent = table.closest('.md-card')?.querySelector('.md-card-head h2')?.textContent.trim() || document.querySelector('h1')?.textContent.trim() || 'Records';
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
    field.setAttribute('aria-invalid', String(!ok));
    const proxy = document.getElementById(field.dataset.focusTarget);
    proxy?.setAttribute('aria-invalid', String(!ok));
    if (err) {
      err.id = err.id || (field.id || field.name) + '__error';
      const ids = new Set((field.getAttribute('aria-describedby') || '').split(' ').filter(Boolean));
      ids.add(err.id); field.setAttribute('aria-describedby', [...ids].join(' '));
      proxy?.setAttribute('aria-describedby', [...ids].join(' '));
    }
    if (err) err.textContent = ok ? '' : (field.validationMessage || `${fieldLabel(field)} is required.`);
    return ok;
  }
  function revealField(field) {
    let details=field.closest('details');
    while(details){details.open=true;details=details.parentElement?.closest('details');}
    (document.getElementById(field.dataset.focusTarget)||field).focus({preventScroll:false});
  }
  let staticFieldNumber = 0;
  function initValidation(scope) {
    scope.querySelectorAll('form').forEach((form) => {
      if ((form.getAttribute('method') || 'get').toUpperCase() === 'GET' || form.querySelector('[data-no-validate]')) return;
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
      form.querySelectorAll('.md-field').forEach((wrap, i) => {
        const label = wrap.querySelector('label'); const field = wrap.querySelector('input,select,textarea');
        if (label && field) { field.id = field.id || 'field_' + (field.name || i) + '_' + (++staticFieldNumber); label.htmlFor = field.dataset.focusTarget || field.id; }
        const help = wrap.querySelector('.md-help');
        if (help && field) {
          help.id = help.id || field.id + '__help';
          const ids = new Set((field.getAttribute('aria-describedby') || '').split(' ').filter(Boolean));
          ids.add(help.id); field.setAttribute('aria-describedby', [...ids].join(' '));
          document.getElementById(field.dataset.focusTarget)?.setAttribute('aria-describedby', [...ids].join(' '));
        }
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
            a.addEventListener('click', (ev) => { ev.preventDefault(); revealField(f); });
            li.appendChild(a);
            ul.appendChild(li);
          });
          summary.appendChild(ul);
          form.prepend(summary);
          summary.focus({ preventScroll: false });
          revealField(invalid[0]);
        }
      });
    });
  }

  /* ---------- Searchable select enhancement (patient/doctor/visit) ---------- */
  function initSearchableSelects(scope) {
    scope.querySelectorAll('select[data-searchable], #patient_id, #doctor_id, #appointment_id').forEach((sel) => {
      if (sel.dataset.enhanced) return;
      sel.dataset.enhanced = '1';
      const wrap = document.createElement('div');
      wrap.className = 'md-picker';
      sel.parentElement.insertBefore(wrap, sel);
      const input = document.createElement('input');
      input.type = 'text';
      input.id = (sel.id || sel.name) + '__search';
      input.autocomplete = 'off';
      input.setAttribute('role', 'combobox');
      input.setAttribute('aria-autocomplete', 'list');
      input.setAttribute('aria-expanded', 'false');
      input.setAttribute('aria-required', String(sel.required));
      input.placeholder = sel.dataset.searchPlaceholder || (sel.id === 'doctor_id' ? 'Name or specialty…' : sel.id === 'patient_id' ? 'Name or phone number…' : 'Search…');
      sel.dataset.focusTarget = input.id;
      const label = sel.id && scope.querySelector('label[for="' + CSS.escape(sel.id) + '"]');
      if (label) label.htmlFor = input.id;
      else input.setAttribute('aria-label', sel.getAttribute('aria-label') || sel.name);
      sel.classList.add('md-sr-only');
      sel.tabIndex = -1;
      sel.setAttribute('aria-hidden', 'true');
      wrap.append(input, sel);
      const list = document.createElement('div');
      list.id = input.id + '__options';
      list.className = 'md-picker-list';
      list.setAttribute('role', 'listbox');
      input.setAttribute('aria-controls', list.id);
      wrap.append(list);
      let options = [], active = -1, timer, revision = 0;
      const sources = {patient_id: 'patients', doctor_id: 'doctors', appointment_id: 'visits'};
      const source = ['patients','doctors','visits'].includes(sel.dataset.searchSource) ? sel.dataset.searchSource : sources[sel.id];
      function sync() { input.value = sel.value ? (sel.selectedOptions[0]?.text || '') : ''; }
      function close() {
        list.classList.remove('open');
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
      }
      function choose(option) {
        if(sel.disabled)return;
        clearTimeout(timer); ++revision; input.removeAttribute('aria-busy');
        sel.value = option.value;
        sync(); close();
        sel.dispatchEvent(new Event('change', {bubbles:true}));
      }
      function highlight(index) {
        active = Math.max(0, Math.min(index, options.length - 1));
        [...list.children].forEach((node, i) => node.setAttribute('aria-selected', String(i === active)));
        if (options.length) {
          input.setAttribute('aria-activedescendant', list.children[active].id);
          list.children[active].scrollIntoView({block:'nearest'});
        }
      }
      function render(q, remoteOptions, pending=false) {
        options = remoteOptions || [...sel.options].filter(o => !o.disabled && (!q || [o.text,o.dataset.searchDetail||''].join(' ').toLowerCase().includes(q.toLowerCase())));
        list.replaceChildren();
        options.forEach((option, i) => {
          const node = document.createElement('div');
          node.id = list.id + '_' + i;
          node.setAttribute('role', 'option');
          node.setAttribute('aria-selected', 'false');
          const primary=document.createElement('span');primary.textContent=option.text;node.append(primary);
          if(option.dataset.searchDetail){const detail=document.createElement('small');detail.className='md-picker-detail';detail.textContent=option.dataset.searchDetail;node.append(detail);}
          node.addEventListener('pointerdown', e => { e.preventDefault(); choose(option); });
          list.append(node);
        });
        if (!options.length) {
          const empty = document.createElement('p');
          empty.textContent = pending ? 'Searching…' : 'No matches. Try another search.';
          empty.setAttribute('role','status');
          list.append(empty);
        }
        active = -1;
        list.classList.add('open');
        input.setAttribute('aria-expanded', 'true');
      }
      async function search(q) {
        const rev = ++revision;
        if (!source) { render(q); return; }
        input.setAttribute('aria-busy', 'true');
        try {
          const url = new URL('/admin/lookup/' + source, window.location.origin);
          url.searchParams.set('q', q);
          const patient = document.getElementById('patient_id');
          if (source === 'visits' && patient?.value) url.searchParams.set('patient_id', patient.value);
          const res = await fetch(url);
          if (!res.ok) throw new Error('Search unavailable');
          const rows = await res.json();
          if (!Array.isArray(rows)) throw new Error('Invalid search results');
          if (rev !== revision) return;
          [...sel.options].filter(o => o.value && o.value !== sel.value).forEach(o => o.remove());
          const matches=rows.map(row => {
            const existing=[...sel.options].find(o=>o.value===String(row.id));
            const option = existing || new Option(source === 'patients' ? row.label + ' · #' + row.id : row.label, String(row.id));
            option.dataset.searchDetail=row.detail||'';
            if (row.patient_id) option.dataset.patient = row.patient_id;
            if (row.patient_name) option.dataset.patientName = row.patient_name;
            if (row.fee_cents !== undefined) option.dataset.fee = row.fee_cents;
            if(!existing)sel.add(option);
            return option;
          });
          if(!q){const placeholder=[...sel.options].find(o=>!o.value);if(placeholder)matches.unshift(placeholder);}
          render(q,matches);
        } catch (error) {
          if (rev === revision) { render(q); mdNotify('Search could not refresh. Retry when connected.', 'warning'); }
        } finally { if (rev === revision) input.removeAttribute('aria-busy'); }
      }
      input.addEventListener('focus', () => search(''));
      input.addEventListener('input', () => {
        ++revision; // Invalidate a focus search before the debounced request starts.
        clearTimeout(timer);
        if (sel.value) { sel.value = ''; sel.dispatchEvent(new Event('change', {bubbles:true})); }
        render(input.value,null,!!source);
        timer = setTimeout(() => search(input.value), 180);
      });
      input.addEventListener('keydown', e => {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
          e.preventDefault(); if (!list.classList.contains('open')) render('');
          highlight(active + (e.key === 'ArrowDown' ? 1 : -1));
        } else if (e.key === 'Enter' && list.classList.contains('open')) {
          e.preventDefault(); if (active >= 0 && options[active]) choose(options[active]);
        } else if (e.key === 'Escape') { clearTimeout(timer); ++revision; input.removeAttribute('aria-busy'); close(); sync(); }
        else if (e.key === 'Tab') { clearTimeout(timer); ++revision; input.removeAttribute('aria-busy'); close(); }
      });
      input.addEventListener('blur', () => { clearTimeout(timer); ++revision; input.removeAttribute('aria-busy'); setTimeout(() => { close(); sync(); }, 100); });
      sel.addEventListener('change', () => { if (document.activeElement !== input) sync(); });
      sync();
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    refreshIcons();
    labelRailLinks();
    try {
      if (localStorage.getItem('md-nav-collapsed') === '1' && window.innerWidth >= 1024)
        document.body.classList.add('md-nav-collapsed');
    } catch (e) { /* noop */ }

    /* Snackbars: pause the auto-dismiss while hovered/focused, resume after */
    document.querySelectorAll('.md-snackbar').forEach((el) => {
      const persistent = el.matches('.md-snackbar--danger,.md-snackbar--warning');
      let remaining = 8000;
      let startedAt = Date.now();
      let t = persistent ? null : setTimeout(() => dismissSnack(el), remaining);
      const pause = () => { if (persistent) return; clearTimeout(t); remaining -= Date.now() - startedAt; };
      const resume = () => {
        if (persistent) return;
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
        if (menu && willOpen) { menu.classList.add('open'); positionMenu(menu, btn); btn.setAttribute('aria-expanded', 'true'); }
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
            const patientURL = patientSearchURL();
            if (patientURL) window.location.href = patientURL + encodeURIComponent(input.value.trim());
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
      if (el.closest('.md-dialog-backdrop')) return; // The dialog already asks for explicit confirmation.
      const msg = el.getAttribute('data-confirm');
      if (el.tagName === 'FORM') {
        el.addEventListener('submit', (e) => {
          if (el.dataset.confirmed === '1') { delete el.dataset.confirmed; return; }
          e.preventDefault();
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
    document.querySelectorAll('.md-icon-btn[aria-label]:not([data-tip])').forEach(btn => btn.dataset.tip = btn.getAttribute('aria-label'));
    const tip = document.createElement('div');
    tip.className = 'md-tooltip'; tip.id = 'controlTooltip'; tip.setAttribute('role', 'tooltip'); tip.hidden = true;
    document.body.append(tip);
    let tipOwner = null;
    function hideTip(control) {
      if (control && control !== tipOwner) return;
      document.querySelectorAll('[aria-describedby="controlTooltip"]').forEach(el => el.removeAttribute('aria-describedby'));
      tipOwner = null;
      tip.hidden = true;
    }
    document.querySelectorAll('[data-tip],.md-nav-link').forEach(control => {
      const showTip = () => {
        if (control.matches('.md-nav-link') && !document.body.classList.contains('md-nav-collapsed')) return;
        hideTip();
        tipOwner = control;
        tip.textContent = control.dataset.tip || control.dataset.label; tip.hidden = false;
        const box = control.getBoundingClientRect();
        const left = control.matches('.md-nav-link') ? box.right + 12 : box.left + box.width / 2 - tip.offsetWidth / 2;
        tip.style.left = Math.max(8, Math.min(left, innerWidth - tip.offsetWidth - 8)) + 'px';
        tip.style.top = Math.min(innerHeight - tip.offsetHeight - 8, box.bottom + 8) + 'px';
        control.setAttribute('aria-describedby', tip.id);
      };
      control.addEventListener('mouseenter', showTip); control.addEventListener('focus', showTip);
      control.addEventListener('mouseleave', () => { if (document.activeElement !== control) hideTip(control); });
      control.addEventListener('blur', () => hideTip(control)); control.addEventListener('click', () => hideTip(control));
    });
    window.addEventListener('blur', () => hideTip());
    document.addEventListener('keydown', event => { if (event.key === 'Escape') hideTip(); });

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

    document.addEventListener('click', e => {
      const button = e.target.closest('[data-action], [data-slot-select]');
      if (!button) return;
      const actions = {'print': () => window.print(), 'reload': () => location.reload(),
        'back': () => history.back(), 'sidebar': () => toggleSidebar(), 'close-sidebar': () => toggleSidebar(false),
        'collapse-nav': () => toggleNavCollapse(), 'expand-nav': () => toggleNavCollapse(false)};
      if (button.hasAttribute('data-slot-select')) selectSlot(button);
      else actions[button.dataset.action]?.();
    });
    /* Slot keyboard activation */
    document.addEventListener('keydown', (e) => {
      const t = e.target;
      if (t && t.classList && t.classList.contains('md-slot') && (e.key === 'Enter' || e.key === ' ')) {
        e.preventDefault();
        window.selectSlot(t);
      }
      if (t?.classList?.contains('md-slot') && ['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(e.key)) {
        e.preventDefault();
        const slots = [...t.closest('.md-slot-grid').querySelectorAll('.md-slot:not(:disabled)')];
        const index = slots.indexOf(t);
        const columns = getComputedStyle(t.parentElement).gridTemplateColumns.split(' ').length;
        const delta = {ArrowLeft:-1, ArrowRight:1, ArrowUp:-columns, ArrowDown:columns}[e.key] || 0;
        const next = e.key === 'Home' ? 0 : e.key === 'End' ? slots.length - 1 : Math.max(0, Math.min(index + delta, slots.length - 1));
        slots[next]?.focus(); window.selectSlot(slots[next]);
      }
    });

    initCardTables(document);
    initSearchableSelects(document);
    initValidation(document);
    initTabs(document);
    initPagination(document);
  });
})();
