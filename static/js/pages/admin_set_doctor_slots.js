document.addEventListener('DOMContentLoaded', () => {
  const form=document.getElementById('weeklyHoursForm');
  function sync(card) {
    const off=card.querySelector('[data-off-toggle]').checked;
    card.classList.toggle('day-off',off);
    card.querySelectorAll('[data-time-fields] input').forEach(input=>{input.disabled=off;input.required=!off && ['start_time','end_time'].includes(input.dataset.timeKey);});
    const badge=card.querySelector('.md-badge');badge.textContent=off?'Off':'Open';badge.classList.toggle('md-badge--neutral',off);badge.classList.toggle('md-badge--sched',!off);
    const breaks=card.querySelector('.md-hours-break');
    if(breaks && [...breaks.querySelectorAll('input')].some(input=>input.value)) breaks.open=true;
  }
  form.querySelectorAll('[data-day-card]').forEach(card=>{sync(card);card.querySelector('[data-off-toggle]').addEventListener('change',()=>sync(card));});
  document.getElementById('copyMonday').addEventListener('click',()=>{
    const monday=form.querySelector('[data-day-card=Monday]');
    if (monday.querySelector('[data-off-toggle]').checked) {window.mdNotify?.('Set Monday hours first.','info');return;}
    const values=[...monday.querySelectorAll('[data-time-fields] input')].map(input=>input.value);
    form.querySelectorAll('[data-day-card]').forEach(card=>{
      if(card===monday)return;
      card.querySelector('[data-off-toggle]').checked=false;
      card.querySelectorAll('[data-time-fields] input').forEach((input,i)=>input.value=values[i]);sync(card);
    });
    form.dispatchEvent(new Event('change',{bubbles:true}));
    window.mdNotify?.('Monday copied. Adjust any day, then save the week once.','info');
  });
});
