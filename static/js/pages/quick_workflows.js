document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-generate-password]').forEach(button => button.addEventListener('click', () => {
    const input=document.getElementById(button.dataset.generatePassword);
    const bytes=crypto.getRandomValues(new Uint8Array(12));
    input.value=Array.from(bytes,byte=>byte.toString(16).padStart(2,'0')).join('');
    input.type='text';
    const toggle=document.querySelector(`[data-pass-toggle="${input.id}"]`);
    if(toggle){toggle.setAttribute('aria-pressed','true');toggle.setAttribute('aria-label','Hide password');}
    input.dispatchEvent(new Event('input',{bubbles:true}));input.focus();input.select();
    window.mdNotify?.('Password generated. Copy it and share it securely.','info');
  }));
  const username=document.getElementById('new_doctor_username');
  const password=document.getElementById('new_doctor_password');
  if(username&&password){
    const sync=()=>{const filled=!!(username.value||password.value);username.required=filled;password.required=filled;};
    username.addEventListener('input',sync);password.addEventListener('input',sync);sync();
  }
});
