document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('appointmentForm');
  const patient = document.getElementById('patient_id');
  const doctor = document.getElementById('doctor_id');
  const date = document.getElementById('date');
  const time = document.getElementById('time_slot');
  const box = document.getElementById('time_slots');
  const submit = document.getElementById('submitBtn');
  const readyHint=document.getElementById('bookingReadyHint');
  const slotStatus=document.getElementById('slotStatus');
  const firstFree=document.getElementById('firstFreeTime');
  let revision = 0;
  const timed = () => form.querySelector('[name=booking_mode]:checked').value === 'timed';
  const isNew = () => form.querySelector('[name=patient_mode]:checked').value === 'new';
  const escape = value => String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function validate() {
    const patientReady=isNew() ? form.querySelector('#patient_name').value.trim() : patient.value;
    const reason=!patientReady ? (isNew() ? 'Enter the patient’s name to continue.' : 'Choose a patient to continue.') : !doctor.value ? 'Choose a doctor to continue.' : timed() && (!date.value || !time.value) ? 'Choose a date and time to continue.' : '';
    submit.disabled=!!reason || form.dataset.submitting==='1';
    readyHint.textContent=reason;readyHint.hidden=!reason;
  }
  function syncSelection() {
    box.querySelectorAll('.md-slot-grid').forEach(grid=>{
      const slots=[...grid.querySelectorAll('.md-slot')];
      const chosen=slots.find(slot=>!slot.disabled && slot.dataset.start===time.value);
      const tabStop=chosen || slots.find(slot=>!slot.disabled);
      slots.forEach(slot=>{
        const selected=slot===chosen;
        slot.classList.toggle('selected',selected);
        slot.setAttribute('aria-selected',String(selected));
        slot.tabIndex=slot===tabStop?0:-1;
      });
    });
  }
  function slotGrid(slots,label) {
    return '<div class="md-slot-grid" role="listbox" aria-label="'+label+'">'+slots.map(slot=>{
      return `<button type="button" class="md-slot${slot.booked?' booked':''}" role="option" tabindex="-1" aria-selected="false" data-start="${escape(slot.start)}" ${slot.booked?'disabled aria-disabled="true"':'data-slot-select'}><span class="md-slot-time">${escape(slot.start)}</span>${slot.booked?'<small>Booked</small>':''}</button>`;
    }).join('')+'</div>';
  }
  function syncModes() {
    const newFields = document.getElementById('newPatientFields');
    const timedFields = document.getElementById('timedBooking');
    newFields.hidden = !isNew(); newFields.disabled = !isNew();
    document.getElementById('existingPatientFields').hidden = isNew();
    patient.disabled = isNew();
    const proxy = document.getElementById('patient_id__search');
    if (proxy) proxy.disabled = isNew();
    timedFields.hidden = !timed(); timedFields.disabled = !timed();
    submit.textContent = timed() ? 'Schedule visit' : 'Add to queue';
    validate(); loadSlots();
  }
  async function loadSlots() {
    const token = ++revision;
    firstFree.disabled=true;
    box.removeAttribute('aria-busy');
    box.replaceChildren();
    if (!timed() || !doctor.value || !date.value) { slotStatus.textContent = 'Choose a doctor and date to see suggestions.'; return; }
    box.setAttribute('aria-busy','true');slotStatus.textContent='Loading suggested times…';
    try {
      const response = await fetch(`/admin/get_doctor_slots/${encodeURIComponent(doctor.value)}/${encodeURIComponent(date.value)}`);
      if (!response.ok) throw new Error('Could not load suggestions');
      const data = await response.json();
      if (token !== revision) return;
      if (data.error) { slotStatus.textContent=data.error; return; }
      if(!Array.isArray(data.slots) || !Array.isArray(data.booked_slots))throw new Error('Invalid suggestions');
      const slots=data.slots.map(slot=>({...slot,booked:data.booked_slots.includes(slot.start)}));
      const available=slots.filter(slot=>!slot.booked);
      const shortlist=available.slice(0,8);
      const visible=new Set(shortlist.map(slot=>slot.start));
      const more=slots.filter(slot=>!visible.has(slot.start));
      box.innerHTML=shortlist.length ? slotGrid(shortlist,'Earliest available suggestions') : '';
      if(more.length)box.insertAdjacentHTML('beforeend','<details class="md-expansion md-slot-more"><summary>More times · '+more.length+'</summary><div class="md-expansion-body">'+slotGrid(more,'More suggested times')+'</div></details>');
      slotStatus.textContent=available.length ? `Showing ${shortlist.length} earliest available suggestions. You can also enter any time.` : 'No free suggestions. You can enter another time above.';
      firstFree.disabled=!available.length;
      syncSelection();
    } catch (_) { if (token===revision) slotStatus.textContent='Suggestions could not load. You can still enter a time.'; }
    finally { if (token===revision) box.removeAttribute('aria-busy'); }
  }
  form.addEventListener('input',validate);form.addEventListener('change',validate);
  form.querySelectorAll('[name=booking_mode],[name=patient_mode]').forEach(input=>input.addEventListener('change',syncModes));
  doctor.addEventListener('change',loadSlots);date.addEventListener('change',loadSlots);
  time.addEventListener('input',syncSelection);time.addEventListener('change',syncSelection);
  firstFree.addEventListener('click',()=>{
    const first=box.querySelector('.md-slot:not(:disabled)');
    if (first) { window.selectSlot(first);time.focus(); }
    else window.mdNotify?.('Enter a time above, or wait for the suggestions to load.','info');
  });
  syncModes();
});
