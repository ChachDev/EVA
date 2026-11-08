(() => {
  function theme() {
    try { const value = localStorage.getItem('eva.ui.theme'); if (value === 'dark' || value === 'light') return value; } catch {}
    return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  document.documentElement.dataset.theme = theme();
  document.getElementById('theme-switch')?.addEventListener('click', () => {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('eva.ui.theme', next); } catch {}
  });
  document.querySelector('[data-workspace-language]')?.addEventListener('click', event => {
    try { localStorage.setItem('eva.ui.language', event.currentTarget.dataset.workspaceLanguage); } catch {}
  });
})();
