if (window.lucide) { lucide.createIcons({ attrs: { 'stroke-width': 1.75 } }); }
document.addEventListener('DOMContentLoaded',()=>{
    document.querySelectorAll('[data-copy-target]').forEach(button=>button.addEventListener('click',async()=>{
        const target=document.getElementById(button.dataset.copyTarget);if(!target)return;
        const text=target.href||target.value||target.textContent.trim();
        try {await navigator.clipboard.writeText(text);window.mdNotify?.('Address copied.','success');}
        catch (_) {const range=document.createRange();range.selectNodeContents(target);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);window.mdNotify?.('Select the address and copy it with your browser.','info');}
    }));
});
