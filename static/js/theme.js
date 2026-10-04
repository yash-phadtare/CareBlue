/* Apply a device preference before styles paint. This stores appearance only. */
(function () {
  'use strict';
  const key = 'careblue-theme';
  const root = document.documentElement;
  const device = window.matchMedia('(prefers-color-scheme: dark)');
  const valid = value => ['light', 'dark', 'system'].includes(value);
  let preference = 'system';
  try { const saved = localStorage.getItem(key); if (valid(saved)) preference = saved; } catch (_) { /* Device mode works without storage. */ }
  function apply() {
    const resolved = preference === 'system' ? (device.matches ? 'dark' : 'light') : preference;
    root.dataset.theme = resolved;
    root.dataset.themePreference = preference;
    document.querySelectorAll('[data-theme-option]').forEach(button => {
      button.setAttribute('aria-checked', String(button.dataset.themeOption === preference));
    });
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta && document.body) meta.content = getComputedStyle(root).getPropertyValue('--md-background').trim();
    window.dispatchEvent(new CustomEvent('careblue:theme', {detail: {preference, resolved}}));
  }
  function set(value) {
    if (!valid(value)) return;
    preference = value;
    try { localStorage.setItem(key, value); } catch (_) { /* Current page can still change theme. */ }
    apply();
  }
  window.CareBlueTheme = {set, get preference() { return preference; }};
  device.addEventListener('change', () => { if (preference === 'system') apply(); });
  window.addEventListener('storage', event => {
    if (event.key !== key) return;
    preference = valid(event.newValue) ? event.newValue : 'system'; apply();
  });
  document.addEventListener('DOMContentLoaded', apply);
  document.addEventListener('click', event => {
    const option = event.target.closest('[data-theme-option]');
    if (option) set(option.dataset.themeOption);
  });
  apply();
})();
