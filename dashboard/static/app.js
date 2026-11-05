document.querySelectorAll('[data-copy]').forEach(button => {
  button.addEventListener('click', async () => {
    const input = document.getElementById(button.dataset.copy);
    try { await navigator.clipboard.writeText(input.value); button.textContent = 'Copied'; }
    catch { input.focus(); input.select(); }
  });
});
document.querySelectorAll('form[data-confirm]').forEach(form => {
  form.addEventListener('submit', event => {
    if (!window.confirm(form.dataset.confirm)) event.preventDefault();
  });
});
if (document.body.dataset.refresh === 'true') {
  let dirty = false;
  document.addEventListener('input', () => { dirty = true; });
  setInterval(() => {
    if (!dirty && !document.hidden && !document.activeElement?.matches('input,textarea,select')) window.location.reload();
  }, 15000);
}
