const pw = document.getElementById('password');
const pw2 = document.getElementById('confirm_password');
const rules = { len: document.querySelector('#pwCheck [data-rule="len"]'), match: document.querySelector('#pwCheck [data-rule="match"]') };
function checkPw() {
  rules.len.dataset.ok = pw.value.length >= 12 && pw.value.length <= 128 ? '1' : '0';
  rules.match.dataset.ok = (pw2.value.length > 0 && pw.value === pw2.value) ? '1' : '0';
  pw2.setCustomValidity(pw2.value && pw.value !== pw2.value ? 'Passwords do not match.' : '');
}
pw.addEventListener('input', checkPw);
pw2.addEventListener('input', checkPw);
checkPw();
const slug = document.getElementById('slug');
const hospitalName = document.getElementById('hospital_name');
const workspacePreview = document.getElementById('workspace-preview');
let customSlug = Boolean(slug.value);
function updateWorkspacePreview() {
  if (workspacePreview) workspacePreview.textContent = '/h/' + (slug.value || 'your-workspace');
}
slug.addEventListener('input', () => {
  customSlug = true;
  slug.value = slug.value.toLowerCase();
  updateWorkspacePreview();
});
hospitalName.addEventListener('input', () => {
  if (customSlug) return;
  const suggestion = hospitalName.value.normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0,50).replace(/-+$/g, '');
  slug.value = suggestion.length >= 3 ? suggestion : '';
  slug.dispatchEvent(new Event('change', {bubbles:true}));
  updateWorkspacePreview();
});
updateWorkspacePreview();
