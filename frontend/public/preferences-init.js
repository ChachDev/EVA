// Apply appearance before React/CSS render, without requiring persistent storage.
(() => {
  let language, theme;
  try { language = localStorage.getItem('eva.ui.language'); theme = localStorage.getItem('eva.ui.theme'); } catch {}
  if (!['ru', 'kk', 'en'].includes(language)) {
    language = navigator.languages.map(tag => tag.toLowerCase().split('-')[0]).find(tag => ['ru', 'kk', 'en'].includes(tag)) || 'ru';
  }
  if (theme !== 'light' && theme !== 'dark') theme = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.lang = language;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
})();
