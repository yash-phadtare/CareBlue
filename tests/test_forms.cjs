// Exercise the actual form handler with a small DOM substitute and controlled responses.
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const assert = require('node:assert/strict');
const source = readFileSync(require('node:path').join(__dirname, '../static/js/forms.js'), 'utf8');

function createHarness({result, htmlRedirect = false, authFlow = true, shadowAttributes = false, secondForm = false, responseWait, failingRequest = false, hiddenDialog = false, identity = '', recoveryResult = {csrf_token:'restored-csrf'}, recoveryOK = true}) {
  const callbacks = {};
  const summaries = [];
  const destinations = [];
  const requests = [];
  const meta = {content:'test-csrf'};
  const dispatched = [];
  const form = {
    method: 'POST', action: 'http://localhost/register', dataset: {},
    elements: {name: {name:'name', value: 'New Owner'}, csrf_token: {value: 'test-csrf'}},
    addEventListener: (name, callback) => { callbacks[name] = callback; },
    querySelector: () => null, querySelectorAll: selector => selector === 'input,select,textarea' ? [form.elements.name] : selector === 'input,select,textarea,button' ? [form.elements.name,form.elements.csrf_token] : [],
    closest: () => hiddenDialog ? {classList:{contains:()=>false}} : null,
    setAttribute() {}, removeAttribute() {},
    getAttribute: name => name === 'method' ? 'POST' : name === 'action' ? 'http://localhost/register' : null,
    hasAttribute: name => name === 'data-auth-flow' && authFlow,
    prepend: element => summaries.push(element),
  };
  const secondary = {...form, dataset:{}, elements:{name:{name:'name',value:'Other Owner'}},
    addEventListener() {},
    querySelectorAll: selector => selector === 'input,select,textarea' ? [secondary.elements.name] : [],
  };
  const emptyPage = {querySelectorAll: () => [], querySelector: () => null};
  if (shadowAttributes) { form.method = {value:'Cash'}; form.action = {value:'sign'}; }
  const context = {
    window: {addEventListener: (name, callback) => { callbacks[name] = callback; }, dispatchEvent: event => dispatched.push(event.type)},
    document: {
      body: {dataset:{loginUrl:'/h/hospital-1/login'}}, addEventListener() {},
      querySelector: selector => selector === 'meta[name="form-identity"]' ? {content:identity} : meta,
      querySelectorAll: selector => selector === 'form' ? (secondForm ? [form,secondary] : [form]) : selector === 'input[name="csrf_token"]' ? [form.elements.csrf_token] : [],
      createElement: tag => ({tag, children: [], events: {}, addEventListener(name, handler) {this.events[name]=handler;}, append(...items) { this.children.push(...items); }, replaceChildren() {this.children=[];}, setAttribute() {}, removeAttribute() {}, focus() {}, remove() {}}),
    },
    location: {pathname: '/register', href: 'http://localhost/register', assign: url => destinations.push(url)},
    MutationObserver: class { observe() {} },
    FormData: class { set() {} },
    URL,
    Event,
    DOMParser: class { parseFromString() { return emptyPage; } },
    fetch: async url => { requests.push(url); if(url === '/session/recover') return {ok:recoveryOK,json:async()=>recoveryResult}; if(responseWait)await responseWait; if(failingRequest)throw new Error('Network failure'); return {ok: true, redirected: htmlRedirect,
      url: 'http://localhost/h/new-hospital/login',
      headers: {get: () => htmlRedirect ? 'text/html' : 'application/json'},
      json: async () => result, text: async () => '<form></form>',
    }; },
  };
  vm.runInNewContext(source, context);
  callbacks.load();
  return {form, secondary, summaries, destinations, requests, meta, dispatched,
    restore: () => summaries[0].children.find(child=>child.tag==='div').children.find(child=>child.tag==='button').events.click(),
    wouldWarn: () => { let warned=false; callbacks.beforeunload({preventDefault(){warned=true;}}); return warned; },
    submit: () => callbacks.submit({preventDefault() {}, defaultPrevented: false})};
}

test('successful registration navigates to workspace sign-in', async () => {
  const harness = createHarness({result: {redirect: '/h/new-hospital/login'}});
  await harness.submit();
  assert.deepEqual(harness.destinations, ['/h/new-hospital/login']);
  assert.equal(harness.summaries.length, 0);
  assert.equal(harness.form.dataset.submitting, undefined);
});

test('same-account recovery refreshes CSRF without losing changes or automatically submitting', async () => {
  const harness=createHarness({result:{session_expired:true},authFlow:false,identity:'signed-original-account'});
  harness.form.elements.name.value='Unsaved patient';
  await harness.submit();
  const actions=harness.summaries[0].children.find(child=>child.tag==='div');
  assert.equal(actions.children[0].href,'/h/hospital-1/login');
  assert.equal(actions.children[0].rel,'noopener');
  await harness.restore();
  assert.equal(harness.form.elements.name.value,'Unsaved patient');
  assert.equal(harness.form.elements.csrf_token.value,'restored-csrf');
  assert.equal(harness.meta.content,'restored-csrf');
  assert.equal(harness.wouldWarn(),true);
  assert.deepEqual(harness.requests,['http://localhost/register','/session/recover']);
  assert.equal(harness.destinations.length,0);
  assert.deepEqual(harness.dispatched,['careblue:session-restored']);
});

test('wrong-account recovery keeps the old token and form for another attempt', async () => {
  const harness=createHarness({result:{session_expired:true},authFlow:false,identity:'signed-original-account',recoveryOK:false,recoveryResult:{error:'Use the same account and hospital.'}});
  await harness.submit();await harness.restore();
  assert.equal(harness.form.elements.csrf_token.value,'test-csrf');
  assert.equal(harness.form.elements.name.value,'New Owner');
  assert.equal(harness.dispatched.length,0);
  assert.equal(harness.destinations.length,0);
  const actions=harness.summaries[0].children.find(child=>child.tag==='div');
  assert.equal(actions.children.find(child=>child.tag==='button').disabled,false);
  assert.match(actions.children.find(child=>child.tag==='p').textContent,/same account/);
});

test('actual session expiry preserves the form and shows an error', async () => {
  const harness = createHarness({result: {redirect: '/login', session_expired: true}, authFlow: false});
  await harness.submit();
  assert.equal(harness.destinations.length, 0);
  assert.equal(harness.form.elements.name.value, 'New Owner');
  assert.match(harness.summaries[0].children[1].textContent, /session expired/);
});

test('HTML registration redirects also navigate to sign-in', async () => {
  const harness = createHarness({htmlRedirect: true});
  await harness.submit();
  assert.deepEqual(harness.destinations, ['http://localhost/h/new-hospital/login']);
  assert.equal(harness.summaries.length, 0);
});

test('HTML redirects from protected forms still preserve unsaved entries', async () => {
  const harness = createHarness({htmlRedirect: true, authFlow: false});
  await harness.submit();
  assert.equal(harness.destinations.length, 0);
  assert.equal(harness.form.elements.name.value, 'New Owner');
  assert.match(harness.summaries[0].children[1].textContent, /session expired/);
});

test('fields named method and action do not shadow the form submission attributes', async () => {
  const harness = createHarness({result: {redirect: '/saved'},shadowAttributes:true});
  await harness.submit();
  assert.deepEqual(harness.requests, ['http://localhost/register']);
  assert.deepEqual(harness.destinations, ['/saved']);
});

test('reverting a form to its original values removes the unsaved warning', () => {
  const harness=createHarness({result:{redirect:'/saved'}});
  assert.equal(harness.wouldWarn(),false);
  harness.form.elements.name.value='Changed owner';
  assert.equal(harness.wouldWarn(),true);
  harness.form.elements.name.value='New Owner';
  assert.equal(harness.wouldWarn(),false);
});

test('saving one form preserves the warning for another edited form', async () => {
  const harness=createHarness({result:{redirect:'/saved'},secondForm:true});
  harness.form.elements.name.value='Saved owner';
  harness.secondary.elements.name.value='Unsaved owner';
  assert.equal(harness.wouldWarn(),true);
  await harness.submit();
  assert.deepEqual(harness.destinations,['/saved']);
  assert.equal(harness.wouldWarn(),true);
  harness.secondary.elements.name.value='Other Owner';
  assert.equal(harness.wouldWarn(),false);
});

test('saving locks editable fields until the response and restores prior disabled states', async () => {
  let finish;
  const responseWait=new Promise(resolve=>{finish=resolve});
  const harness=createHarness({result:{redirect:'/saved'},responseWait});
  harness.form.elements.name.disabled=false;
  harness.form.elements.csrf_token.disabled=true;
  const saving=harness.submit();
  assert.equal(harness.form.elements.name.disabled,true);
  assert.equal(harness.form.dataset.submitting,'1');
  assert.equal(harness.destinations.length,0);
  finish();await saving;
  assert.equal(harness.form.elements.name.disabled,false);
  assert.equal(harness.form.elements.csrf_token.disabled,true);
  assert.deepEqual(harness.destinations,['/saved']);
});

test('network failure restores editing and retains entered values', async () => {
  const harness=createHarness({result:{},failingRequest:true});
  harness.form.elements.name.disabled=false;
  harness.form.elements.name.value='Unsent edit';
  await harness.submit();
  assert.equal(harness.form.elements.name.disabled,false);
  assert.equal(harness.form.elements.name.value,'Unsent edit');
  assert.equal(harness.form.dataset.submitting,undefined);
  assert.equal(harness.destinations.length,0);
  assert.equal(harness.wouldWarn(),true);
});

test('cancelled closed dialog drafts do not trigger an invisible-form warning', () => {
  const harness=createHarness({result:{},hiddenDialog:true});
  harness.form.elements.name.value='Cancelled staff draft';
  assert.equal(harness.wouldWarn(),false);
});
