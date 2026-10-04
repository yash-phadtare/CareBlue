/* Keep entered clinical data in the current page when a submission fails. */
window.addEventListener('load', () => {
  const identity = document.querySelector?.('meta[name="form-identity"]')?.content || '';
  function addSessionRecovery(summary) {
    if (!identity) return;
    const actions = document.createElement('div');
    actions.className = 'md-cluster md-session-recovery';
    const signin = document.createElement('a');
    signin.className = 'md-btn md-btn--outlined';
    signin.textContent = 'Sign in securely';
    signin.href = document.body.dataset.loginUrl || '/login';
    signin.target = '_blank'; signin.rel = 'noopener';
    const restore = document.createElement('button');
    restore.type = 'button'; restore.className = 'md-btn md-btn--filled';
    restore.textContent = 'I’ve signed in — restore saving';
    const status = document.createElement('p');
    status.setAttribute('role', 'status');
    status.textContent = 'Sign in with the same account in the new tab, then return here. Keep this form open; your entries stay on this page.';
    restore.addEventListener('click', async () => {
      restore.disabled = true;
      status.textContent = 'Checking your sign-in…';
      try {
        const response = await fetch('/session/recover', {headers: {'X-Form-Identity': identity}, cache: 'no-store'});
        const result = await response.json();
        if (!response.ok || !result.csrf_token) {
          status.textContent = result.error || 'Sign-in could not be verified. Try again.';
          return;
        }
        document.querySelectorAll('input[name="csrf_token"]').forEach(field => { field.value = result.csrf_token; });
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) meta.content = result.csrf_token;
        summary.replaceChildren();
        summary.removeAttribute('id');
        summary.className = 'md-error-summary md-server-errors md-session-restored';
        const message = document.createElement('p');
        message.textContent = 'Sign-in restored. Your entries are unchanged. Review them, then submit again when ready.';
        summary.append(message); summary.focus();
        window.dispatchEvent(new Event('careblue:session-restored'));
      } catch (_) {
        status.textContent = 'Unable to check sign-in. Your entries are still here. Check your connection and try again.';
      } finally { restore.disabled = false; }
    });
    actions.append(signin, restore, status); summary.append(actions);
  }
  window.addEventListener('careblue:session-expired', () => {
    if (document.getElementById('sessionRecovery')) return;
    const summary = document.createElement('div');
    summary.id = 'sessionRecovery'; summary.className = 'md-error-summary';
    summary.setAttribute('role', 'alert'); summary.tabIndex = -1;
    const title = document.createElement('strong'); title.textContent = 'Your session needs to be restored';
    summary.append(title); addSessionRecovery(summary);
    (document.getElementById('mainContent') || document.body).prepend(summary); summary.focus();
  });
  let fieldNumber = 0;
  const labelDynamicFields = () => {
    document.querySelectorAll('.md-field').forEach(wrap => {
      const label = wrap.querySelector('label');
      const field = wrap.querySelector('input,select,textarea');
      if (label && field) {
        if (!field.id) field.id = 'dynamic-field-' + (++fieldNumber);
        label.htmlFor = field.dataset.focusTarget || field.id;
        if (field.required && !label.querySelector('.req')) {
          const mark=document.createElement('span');mark.className='req';mark.textContent=' *';mark.setAttribute('aria-hidden','true');label.append(mark);
        }
      }
    });
  };
  labelDynamicFields();
  new MutationObserver(labelDynamicFields).observe(document.body, {childList:true, subtree:true});
  const medicineSearches = new WeakMap();
  document.addEventListener('input', event => {
    const field = event.target;
    if (!field.name?.startsWith('medicine_name_')) return;
    const state=medicineSearches.get(field) || {revision:0};
    clearTimeout(state.timer);state.controller?.abort();state.revision++;
    medicineSearches.set(field,state);
    const query=field.value.trim(),revision=state.revision;
    if(query.length<2 || field.disabled){
      document.getElementById(field.getAttribute('list'))?.replaceChildren();
      return;
    }
    state.timer=setTimeout(async () => {
      state.controller=new AbortController();
      try {
        const response = await fetch('/admin/lookup/medicines?q=' + encodeURIComponent(query),{signal:state.controller.signal});
        if (!response.ok) return;
        const medicines = await response.json();
        if (revision !== state.revision || field.isConnected === false || !Array.isArray(medicines)) return;
        const id = field.id + '__catalogue';
        let list = document.getElementById(id);
        if (!list) { list = document.createElement('datalist'); list.id = id; field.after(list); field.setAttribute('list', id); }
        list.replaceChildren();
        medicines.forEach(medicine => {
          const option = document.createElement('option');
          option.value = medicine.name;
          option.label = [medicine.strength, medicine.stock_qty + ' in stock'].filter(Boolean).join(' · ');
          list.append(option);
        });
      } catch (_) { /* Free-text prescribing remains available. */ }
    },180);
  });
  const snapshots = new WeakMap();
  const changedForms = new Set();
  function activeForm(form) {
    const dialog=form.closest?.('.md-dialog-backdrop');
    return form.isConnected !== false && (!dialog || dialog.classList.contains('open'));
  }
  const postForms = [...document.querySelectorAll('form')].filter(form => (form.getAttribute('method') || 'get').toUpperCase() === 'POST');
  function signature(form) {
    return JSON.stringify([...form.querySelectorAll('input,select,textarea')].filter(field => field.name && !['csrf_token','idempotency_key','version'].includes(field.name)).map(field => {
      if (field.type === 'file') return [field.name, [...(field.files || [])].map(file => [file.name,file.size,file.lastModified])];
      return [field.name, field.value, /^(checkbox|radio)$/.test(field.type) ? field.checked : null];
    }));
  }
  function updateDirty(form) {
    if (!snapshots.has(form)) return;
    const changed = signature(form) !== snapshots.get(form);
    if (changed) changedForms.add(form); else changedForms.delete(form);
    if (form.toggleAttribute) form.toggleAttribute('data-unsaved', changed);
  }
  postForms.forEach(form => snapshots.set(form, signature(form)));
  const trackChange = event => {
    const form = event.target.closest('form');
    if (form) updateDirty(form);
  };
  document.addEventListener('input', trackChange);
  document.addEventListener('change', trackChange);
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-fill-input]');
    if (!button) return;
    const field = document.getElementById(button.dataset.fillInput);
    if (!field || field.form !== button.closest('form')) return;
    field.value = button.dataset.fillValue;
    field.dispatchEvent(new Event('input', {bubbles:true}));
    field.dispatchEvent(new Event('change', {bubbles:true}));
    field.focus();
  });
  new MutationObserver(() => postForms.forEach(updateDirty)).observe(document.body, {childList:true, subtree:true});
  window.addEventListener('beforeunload', e => {
    postForms.forEach(updateDirty);
    if ([...changedForms].some(activeForm)) { e.preventDefault(); e.returnValue = ''; }
  });
  document.querySelectorAll('form').forEach(form => {
    if ((form.getAttribute('method') || 'get').toUpperCase() !== 'POST') return;
    form.addEventListener('submit', async e => {
      if (e.defaultPrevented) return;
      e.preventDefault();
      if (form.dataset.submitting === '1') return;
      const data = new FormData(form);
      const actionURL = form.getAttribute('action') || location.href;
      if (e.submitter?.name) data.set(e.submitter.name, e.submitter.value);
      form.dataset.submitting = '1';
      form.setAttribute('aria-busy', 'true');
      const controls = [...form.querySelectorAll('input,select,textarea,button')];
      const disabled = controls.map(control => control.disabled);
      controls.forEach(control => control.disabled = true);
      const submitter = e.submitter;
      const originalLabel = submitter?.getAttribute('aria-label');
      const pendingMessage = form.dataset.submitMessage || (form.hasAttribute('data-auth-flow') ? 'Please wait…' : 'Saving changes…');
      if (submitter) {
        submitter.classList.add('is-saving');
        submitter.setAttribute('aria-label', pendingMessage);
      }
      const status = document.createElement('p');
      status.className = 'md-save-status'; status.setAttribute('role', 'status');
      status.textContent = pendingMessage;
      if (form.append) form.append(status);
      form.querySelector('.md-server-errors')?.remove();
      try {
        const headers = {'X-Requested-With':'CareBlue'};
        if (identity && !form.hasAttribute('data-auth-flow')) headers['X-Form-Identity'] = identity;
        const response = await fetch(actionURL, {method:'POST', body:data, headers});
        const type = response.headers.get('Content-Type') || '';
        let errors = [], html, destination, sessionExpired = false;
        if (type.includes('application/json')) {
          const result = await response.json();
          if (!response.ok) errors.push(result.error || 'The operation could not be completed.');
          destination = result.redirect;
          if (result.session_expired) {
            sessionExpired = true;
            errors = ['Your session expired or needs to be refreshed. Your entries are still here.'];
          }
        } else {
          html = new DOMParser().parseFromString(await response.text(), 'text/html');
          const notices = html.querySelectorAll('.md-snackbar--danger, .md-snackbar--warning');
          errors = [...notices].map(n => n.querySelector('span')?.textContent || n.textContent.replace('Dismiss','').trim());
          if (!response.ok && !errors.length) errors.push(html.querySelector('[role="alert"]')?.textContent || 'The operation could not be completed. Your entries are still here.');
          // Keep the original form in memory while the user restores their session.
          if (response.redirected && new URL(response.url).pathname.includes('/login') && !form.hasAttribute('data-auth-flow') && !location.pathname.includes('/login')) {
            sessionExpired = true;
            errors = ['Your session expired. Your entries are still here.'];
          }
        }
        if (errors.length) {
          const summary = document.createElement('div');
          summary.className = 'md-error-summary md-server-errors';
          summary.setAttribute('role','alert'); summary.tabIndex = -1;
          const title = document.createElement('strong'); title.textContent = 'Please review before saving';
          summary.append(title);
          errors.forEach(text => { const p = document.createElement('p'); p.textContent = text; summary.append(p); });
          if (sessionExpired) addSessionRecovery(summary);
          form.prepend(summary); summary.focus();
          // A 422 prescription response includes a valid current CSRF token, never passwords.
          const token = html?.querySelector('input[name="csrf_token"]');
          if (!sessionExpired && token && form.elements.csrf_token) form.elements.csrf_token.value = token.value;
          return;
        }
        snapshots.set(form, signature(form));
        updateDirty(form);
        if ([...changedForms].some(activeForm)) window.mdNotify?.('Saved. Other forms on this page still have unsaved changes.', 'info');
        location.assign(destination || response.url || actionURL);
      } catch (error) {
        window.mdNotify?.('Connection lost. Your entries are still here. Check whether the change was saved before retrying.', 'danger');
      } finally {
        delete form.dataset.submitting;
        form.removeAttribute('aria-busy');
        status.remove();
        if (submitter) {
          submitter.classList.remove('is-saving');
          if (originalLabel === null) submitter.removeAttribute('aria-label');
          else submitter.setAttribute('aria-label', originalLabel);
        }
        controls.forEach((control,i) => control.disabled = disabled[i]);
      }
    });
  });
  // Complete menu keyboard navigation and return focus to the trigger.
  document.querySelectorAll('[data-md-menu]').forEach(trigger => {
    const menu = document.getElementById(trigger.dataset.mdMenu);
    if (!menu) return;
    trigger.addEventListener('keydown', e => {
      if (e.key === 'ArrowDown') {
        e.preventDefault(); if (!menu.classList.contains('open')) trigger.click();
        menu.querySelector('[role^="menuitem"]')?.focus({preventScroll:true});
      }
    });
    menu.addEventListener('keydown', e => {
      const items = [...menu.querySelectorAll('[role^="menuitem"]')];
      const index = items.indexOf(document.activeElement);
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault(); items[(index + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length]?.focus({preventScroll:true});
      } else if (e.key === 'Escape') {
        menu.classList.remove('open'); trigger.setAttribute('aria-expanded','false'); trigger.focus();
      }
    });
  });
});
