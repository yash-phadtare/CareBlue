const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/js/theme.js'), 'utf8');

function environment(saved, dark = false, blocked = false) {
  const windowEvents = {}, documentEvents = {}, deviceEvents = {}, writes = [];
  const options = ['light', 'dark', 'system'].map(value => ({dataset: {themeOption: value}, attrs: {}, setAttribute(key, val) {this.attrs[key] = val;}}));
  const root = {dataset: {}};
  const meta = {};
  const device = {matches: dark, addEventListener(type, handler) {deviceEvents[type] = handler;}};
  const context = {
    window: {matchMedia: () => device, addEventListener(type, handler) {windowEvents[type] = handler;}, dispatchEvent() {}},
    document: {documentElement: root, body: {}, querySelectorAll: () => options, querySelector: () => meta, addEventListener(type, handler) {documentEvents[type] = handler;}},
    localStorage: {getItem() {if (blocked) throw Error('Storage unavailable'); return saved;}, setItem(key, value) {if (blocked) throw Error('Storage unavailable'); writes.push([key, value]);}},
    getComputedStyle: () => ({getPropertyValue: () => root.dataset.theme === 'dark' ? '#141218' : '#fffbfe'}),
    CustomEvent: class {constructor(type, details) {this.type = type; this.detail = details.detail;}},
  };
  vm.runInNewContext(source, context);
  return {root, meta, device, writes, options, theme: context.window.CareBlueTheme,
    deviceChange(dark) {device.matches = dark; deviceEvents.change();},
    storageChange(value, key = 'careblue-theme') {windowEvents.storage({key, newValue: value});},
    click(value) {documentEvents.click({target: {closest: () => options.find(option => option.dataset.themeOption === value)}});},
  };
}

test('device preference applies before DOM readiness and follows device changes', () => {
  const env = environment(null, true);
  assert.equal(env.root.dataset.theme, 'dark');
  assert.equal(env.theme.preference, 'system');
  env.deviceChange(false);
  assert.equal(env.root.dataset.theme, 'light');
  assert.equal(env.meta.content, '#fffbfe');
  assert.deepEqual(env.writes, []);
});
test('saved explicit themes persist and ignore device changes', () => {
  const env = environment('light', true);
  env.deviceChange(true);
  assert.equal(env.root.dataset.theme, 'light');
  env.click('dark');
  assert.equal(env.root.dataset.theme, 'dark');
  assert.equal(env.options[1].attrs['aria-checked'], 'true');
  assert.equal(env.options[0].attrs['aria-checked'], 'false');
  assert.deepEqual(env.writes, [['careblue-theme', 'dark']]);
});
test('system setting resumes device tracking and rejects unknown settings', () => {
  const env = environment('light', true);
  env.theme.set('system');
  assert.equal(env.root.dataset.theme, 'dark');
  env.theme.set('invalid');
  assert.equal(env.theme.preference, 'system');
  assert.equal(env.writes.length, 1);
});
test('theme works when browser storage is blocked', () => {
  const env = environment(null, false, true);
  env.theme.set('dark');
  assert.equal(env.root.dataset.theme, 'dark');
  assert.equal(env.theme.preference, 'dark');
});
test('cross-tab changes update appearance without writing clinical data or other preferences', () => {
  const env = environment('light', true);
  env.storageChange('dark', 'unrelated');
  assert.equal(env.theme.preference, 'light');
  env.storageChange('dark');
  assert.equal(env.root.dataset.theme, 'dark');
  env.storageChange(null);
  assert.equal(env.theme.preference, 'system');
  assert.equal(env.root.dataset.theme, 'dark');
  assert.deepEqual(env.writes, []);
});
test('malformed stored preferences fall back to the device theme', () => {
  assert.equal(environment('unexpected', true).root.dataset.theme, 'dark');
});
