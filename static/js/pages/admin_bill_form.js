document.addEventListener('DOMContentLoaded', () => {
  const box = document.getElementById('itemsContainer');
  const count = document.getElementById('item_count');
  const visitSel = document.getElementById('appointment_id');
  const patientSel = document.getElementById('patient_id');
  let itemSequence=0;
  // Full visit list cached once; the dropdown only ever shows visits
  // belonging to the chosen patient (plus "No linked visit").
  const allVisits = new Map();
  const cacheVisits = () => [...visitSel.options].forEach(o=>{if(o.value)allVisits.set(o.value,{value:o.value,text:o.text,patient:o.dataset.patient||'',patientName:o.dataset.patientName||'',fee:o.dataset.fee||'0'});});
  cacheVisits();
  const filterVisits = () => {
    cacheVisits();
    const pid = patientSel.value;
    const current = visitSel.value;
    visitSel.innerHTML = '';
    const none = document.createElement('option');
    none.value = '';
    none.textContent = 'No linked visit';
    visitSel.appendChild(none);
    [...allVisits.values()]
      .filter((v) => v.value && (!pid || v.patient === pid))
      .forEach((v) => {
        const o = document.createElement('option');
        o.value = v.value;
        o.textContent = v.text;
        o.dataset.patient = v.patient;
        o.dataset.patientName = v.patientName;
        o.dataset.fee = v.fee;
        visitSel.appendChild(o);
      });
    // Keep the selection only if it still belongs to the chosen patient
    visitSel.value = [...visitSel.options].some((o) => o.value === current) ? current : '';
  };
  // Choosing a patient narrows the visit list; choosing a visit sets its patient
  patientSel.addEventListener('change', filterVisits);
  visitSel.addEventListener('change', () => {
    const opt = visitSel.selectedOptions[0];
    if (opt && opt.dataset.patient) {
      if (![...patientSel.options].some(option=>option.value===opt.dataset.patient)) {
        const patientOption=document.createElement('option');
        patientOption.value=opt.dataset.patient;
        patientOption.textContent=(opt.dataset.patientName || 'Patient')+' · #'+opt.dataset.patient;
        patientSel.appendChild(patientOption);
      }
      patientSel.value = opt.dataset.patient;
      patientSel.dispatchEvent(new Event('change',{bubbles:true}));
    }
  });
  const selected = visitSel.selectedOptions[0];
  if (selected?.dataset.patient) patientSel.value=selected.dataset.patient;
  filterVisits();
  const renumber = () => {
    box.querySelectorAll('.md-med').forEach((row, i) => {
      const n = i + 1;
      row.setAttribute('data-n', 'Item-' + n);
      row.querySelectorAll('input').forEach((inp) => { inp.name = inp.name.replace(/_\d+$/, '') + '_' + n; });
    });
    count.value = box.querySelectorAll('.md-med').length;
  };
  document.getElementById('addItem').addEventListener('click', () => {
    const n = box.querySelectorAll('.md-med').length + 1;
    count.value = n;
    const div = document.createElement('div');
    div.className = 'md-med';
    div.setAttribute('data-n', 'Item-' + n);
    const fieldId='bill-extra-'+(++itemSequence);
    div.innerHTML = `<div class="md-med-grid bill-item-grid"><div class="md-field"><label for="${fieldId}-label">Extra charge</label><input id="${fieldId}-label" name="item_label_${n}" maxlength="120" placeholder="e.g. X-ray chest"></div><div class="md-field"><label for="${fieldId}-amount">Amount (₹)</label><input id="${fieldId}-amount" name="item_amount_${n}" type="number" min="0.01" step="0.01" placeholder="0.00"></div><div><button type="button" class="md-btn md-btn--text md-btn--sm removeItem item-remove">Remove</button></div></div>`;
    box.appendChild(div);
    div.querySelector('input').focus();
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.removeItem')) return;
    const row=e.target.closest('.md-med');
    const focusTarget=(row.nextElementSibling || row.previousElementSibling)?.querySelector('input') || document.getElementById('addItem');
    row.remove();
    renumber();
    updatePreview();
    focusTarget.focus();
  });
  const currency = cents => '₹'+(cents/100).toFixed(2);
  let lastVisit;
  function updatePreview() {
    const fee=Number(visitSel.selectedOptions[0]?.dataset.fee || 0);
    let total=fee;
    box.querySelectorAll('.md-med').forEach(row=>{
      const label=row.querySelector('[name^=item_label_]');
      const amount=row.querySelector('[name^=item_amount_]');
      const filled=!!(label.value.trim()||amount.value);
      label.required=filled;amount.required=filled;
      const value=Number(amount.value);
      if(Number.isFinite(value)&&value>0)total+=Math.round(value*100);
    });
    document.getElementById('consultationFeePreview').textContent=visitSel.value?'Consultation fee included: '+currency(fee):'Link a visit to include its consultation fee.';
    document.getElementById('billTotalPreview').textContent=currency(total);
    const extras=document.getElementById('billExtras');
    if(extras && lastVisit!==visitSel.value){extras.open=!visitSel.value||[...box.querySelectorAll('input')].some(input=>input.value);lastVisit=visitSel.value;}
    const extrasLabel=document.getElementById('billExtrasLabel');if(extrasLabel)extrasLabel.textContent=visitSel.value?'Extra charges (optional)':'Bill charges';
    const paid=document.getElementById('pay_now').checked;
    const fields=document.getElementById('paymentFields');fields.hidden=!paid;fields.disabled=!paid;
    document.getElementById('payment_amount').placeholder=currency(total);
    document.getElementById('payment_amount').max=(total/100).toFixed(2);
    const create=document.getElementById('createBillButton');
    create.textContent=paid?'Create bill & record payment':'Create bill';
    create.disabled=!patientSel.value||total<=0;
    const hint=document.getElementById('billReadyHint');if(hint)hint.hidden=!create.disabled;
  }
  document.getElementById('billForm').addEventListener('input',updatePreview);
  document.getElementById('billForm').addEventListener('change',updatePreview);
  document.getElementById('billForm').addEventListener('submit',updatePreview,true);
  updatePreview();
});
