document.addEventListener('DOMContentLoaded', () => {
  const form=document.getElementById('prescriptionForm');
  const box=document.getElementById('medicinesContainer');
  const count=document.getElementById('medicine_count');
  const picker=document.getElementById('prescriptionTemplate');
  const templates=JSON.parse(document.getElementById('prescriptionTemplateData').textContent);
  const previous=JSON.parse(document.getElementById('previousPrescriptionData').textContent);
  const patientHistory=document.getElementById('clinicalPatientHistory');
  if(patientHistory&&window.matchMedia('(min-width: 1100px)').matches)patientHistory.open=true;
  const saveState=document.querySelector('#prescriptionSaveState span');
  const saveStateRoot=saveState?.closest('#prescriptionSaveState');
  let initialEntries;
  let fieldNumber=0;
  function entries() {
    return JSON.stringify([...new FormData(form)].filter(([key])=>!['csrf_token','version','patient_version'].includes(key)));
  }
  function updateSaveState() {
    if(!saveState || initialEntries===undefined)return;
    const unsaved=entries()!==initialEntries;
    const version=Number(form.querySelector('[name=version]').value);
    const saved=form.dataset.savedStatus||'New';
    const baseline=version ? saved+' · v'+version : 'New · not saved';
    const saving=form.dataset.submitting==='1';
    const message=saving ? 'Saving your changes…' : unsaved ? baseline+' · Unsaved changes' : baseline;
    if(saveState.textContent!==message)saveState.textContent=message;
    saveStateRoot?.classList.toggle('md-badge--warn',unsaved&&!saving);
    saveStateRoot?.classList.toggle('md-badge--done',!unsaved&&!saving&&['Signed','Amended'].includes(saved));
    saveStateRoot?.classList.toggle('md-badge--neutral',saving||(!unsaved&&!['Signed','Amended'].includes(saved)));
  }
  function medicineSummary(row) {
    const entry=row.closest('.md-medicine-entry');
    if(!entry)return;
    const value=key=>row.querySelector('[name^="medicine_'+key+'_"]')?.value.trim()||'';
    const name=value('name'),strength=value('strength');
    entry.querySelector('[data-medicine-title]').textContent=(name||row.dataset.n)+(strength?' · '+strength:'');
    const core=['dosage','frequency','route','duration'].map(value);
    entry.querySelector('[data-medicine-overview]').textContent=core.some(Boolean) ? core.map((text,index)=>text||['Dose not entered','Frequency not entered','Route not entered','Duration not entered'][index]).join(' · ') : 'Enter treatment details';
    const meal=value('meal');
    const times=['morning','afternoon','evening'].filter(key=>row.querySelector('[name^="medicine_'+key+'_"]')?.checked).map(key=>key[0].toUpperCase()+key.slice(1));
    const extras=[...(['before','after'].includes(meal)?[meal[0].toUpperCase()+meal.slice(1)+' meal']:[]),...times];
    const extraSummary=entry.querySelector('[data-medicine-extra-summary]');
    extraSummary.textContent=extras.join(' · ');extraSummary.hidden=!extras.length;
  }
  function renumber() {
    box.querySelectorAll('.md-med').forEach((row,i)=>{
      const n=i+1;row.dataset.n='Medicine '+n;
      row.querySelectorAll('input,select').forEach(input=>input.name=input.name.replace(/_\d+$/,'')+'_'+n);
      row.querySelector('.removeMedicine')?.setAttribute('aria-label','Remove medicine '+n);
      medicineSummary(row);
    });
    count.value=box.querySelectorAll('.md-med').length;
    document.getElementById('noMedicines').hidden=Number(count.value)>0;
  }
  function add(medicine={}) {
    const row=document.getElementById('medicineRowTemplate').content.firstElementChild.cloneNode(true);
    row.querySelectorAll('input,select').forEach(input=>{
      input.removeAttribute('id');
      const key=input.name.replace(/^medicine_/,'').replace(/_\d+$/,'');
      if(input.type==='checkbox')input.checked=medicine[key]==='1';
      else input.value=medicine[key] || (key==='meal'?'any':'');
    });
    row.open=true;
    row.querySelectorAll('.md-field').forEach(wrap=>{
      const input=wrap.querySelector('input,select');const label=wrap.querySelector('label');
      if(input&&label){input.id=input.id||'clinical-rx-field-'+(++fieldNumber);label.htmlFor=input.id;}
    });
    box.append(row);renumber();return row;
  }
  function rules(signing=false) {
    box.querySelectorAll('.md-med').forEach(row=>{
      const filled=[...row.querySelectorAll('input:not([type=checkbox])')].some(input=>input.value.trim());
      row.querySelectorAll('input').forEach(input=>{
        const key=input.name.replace(/^medicine_/,'').replace(/_\d+$/,'');
        input.required=filled && (['name','dosage','frequency'].includes(key) || (signing && ['route','duration'].includes(key)));
      });
    });
  }
  function changed() { rules();form.querySelector('[name=reviewed]').checked=false;form.dispatchEvent(new Event('change',{bubbles:true})); }
  function addBlankMedicine() {
    box.querySelectorAll('.md-medicine-entry').forEach(entry=>{
      if(['name','dosage','frequency','route','duration'].every(key=>entry.querySelector('[name^="medicine_'+key+'_"]')?.value.trim()))entry.open=false;
    });
    const row=add();changed();row.querySelector('input').focus();
  }
  document.getElementById('addMedicine').addEventListener('click',addBlankMedicine);
  box.addEventListener('click',event=>{
    if(!event.target.closest('.removeMedicine'))return;
    const row=event.target.closest('.md-medicine-entry')||event.target.closest('.md-med');
    const adjacent=row.nextElementSibling||row.previousElementSibling;
    row.remove();renumber();changed();
    if(adjacent?.matches('.md-medicine-entry'))adjacent.open=true;
    (adjacent?.querySelector('input')||document.getElementById('addMedicine')).focus();
  });
  form.addEventListener('input',event=>{
    if(/^(medicine_|diagnosis|instructions|patient_allergies|patient_medical_history)/.test(event.target.name||''))form.querySelector('[name=reviewed]').checked=false;
    rules();
    const medicineRow=event.target.closest('.md-med');if(medicineRow)medicineSummary(medicineRow);
    if(event.target.id==='patient_allergies')document.getElementById('allergySummary').textContent=event.target.value || 'Not recorded — confirm with the patient.';
    if(event.target.id==='patient_medical_history')document.getElementById('medicalHistorySummary').textContent=event.target.value.length>240 ? event.target.value.slice(0,237)+'…' : event.target.value || 'No medical history recorded.';
    updateSaveState();
  });
  form.addEventListener('change',event=>{if((event.target.name||'').startsWith('medicine_')){form.querySelector('[name=reviewed]').checked=false;const row=event.target.closest('.md-med');if(row)medicineSummary(row);}updateSaveState();});
  function submitRules(action) {
    const signing=['sign','print','next'].includes(action);rules(signing);
    const review=form.querySelector('[name=reviewed]');review.required=signing;
  }
  form.addEventListener('click',event=>{
    const submitter=event.target.closest('button[type=submit][name=action]');
    if(submitter)submitRules(submitter.value);
  },true);
  form.addEventListener('submit',event=>{
    submitRules(event.submitter?.value);
  },true);
  function apply(data) {
    if(!data)return;
    const hasEntries=form.querySelector('#diagnosis').value.trim() || form.querySelector('#instructions').value.trim() || [...box.querySelectorAll('input:not([type=checkbox])')].some(input=>input.value.trim());
    if(hasEntries && !window.confirm('Replace the current diagnosis, medicines and advice with these entries?')) {picker.value='';return;}
    form.querySelector('#diagnosis').value=data.diagnosis || '';
    form.querySelector('#instructions').value=data.instructions || '';
    document.getElementById('clinicalAdvice').open=Boolean(data.instructions);
    box.replaceChildren();(data.medicines||[]).forEach((medicine,index)=>{const row=add(medicine);row.open=index===0;});renumber();changed();form.querySelector('#diagnosis').focus();
  }
  picker.addEventListener('change',()=>apply(templates.find(item=>String(item.id)===picker.value)));
  document.getElementById('reusePrescription')?.addEventListener('click',()=>apply(previous));
  document.getElementById('savePrescriptionTemplate').addEventListener('click',async event=>{
    const status=document.getElementById('templateSaveStatus');
    const name=document.getElementById('template_name').value.trim();
    if(!name){status.textContent='Enter a template name.';document.getElementById('template_name').focus();return;}
    const data=new FormData();
    for(const [key,value] of new FormData(form))if(['csrf_token','diagnosis','instructions'].includes(key)||key.startsWith('medicine_'))data.append(key,value);
    data.set('template_name',name);event.target.disabled=true;status.textContent='Saving template…';
    try {
      const response=await fetch('/doctor/prescription-templates',{method:'POST',body:data,headers:{'X-Requested-With':'XMLHttpRequest'}});
      const result=await response.json();if(!response.ok)throw new Error(result.error||'Could not save the template.');
      templates.push(result);picker.add(new Option(result.name,result.id));status.textContent='Template saved.';
      document.querySelector('[data-md-dialog=saveTemplateDialog] [data-md-close]').click();window.mdNotify?.('Template saved to your account.','success');
    } catch(error){status.textContent=error.message;}
    finally{event.target.disabled=false;}
  });
  form.addEventListener('keydown',event=>{
    if((event.ctrlKey||event.metaKey)&&event.key==='Enter'){
      event.preventDefault();if(event.shiftKey){if(form.dataset.submitting!=='1')addBlankMedicine();return;}
      const draft=form.querySelector('[name=action][value=save]');
      if(draft){submitRules('save');HTMLFormElement.prototype.requestSubmit.call(form,draft);}
      else window.mdNotify?.('Review the amendment and use Sign amendment.','info');
    }
  });
  document.getElementById('reviewPrescription').addEventListener('click',()=>{
    const review=document.querySelector('.md-clinical-review');
    review.scrollIntoView({block:'start',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
    (form.querySelector('#amendment_reason')||form.querySelector('[name=reviewed]')).focus({preventScroll:true});
  });
  renumber();rules();initialEntries=entries();updateSaveState();
  new MutationObserver(updateSaveState).observe(form,{attributes:true,attributeFilter:['data-submitting']});
});
