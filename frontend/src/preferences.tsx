import { useEffect, useState, type ReactNode } from 'react';
import { Globe2, Moon, Sun } from 'lucide-react';
import { useI18n } from './i18n';
import { detectLocale, isLocale, LANGUAGE_KEY, setLocale } from './i18n-core';
import { updateMetadata } from './seo';

type Theme = 'light' | 'dark';
const THEME_KEY = 'eva.ui.theme';
const isTheme = (value: unknown): value is Theme => value === 'light' || value === 'dark';
function readTheme(): Theme {
  try { const saved = localStorage.getItem(THEME_KEY); if (isTheme(saved)) return saved; } catch { /* Optional storage. */ }
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#101c17' : '#f5f7f5');
}
export function PreferencesProvider({ children }: { children: ReactNode }) {
  const { locale } = useI18n();
  useEffect(() => {
    document.documentElement.lang = locale;
    updateMetadata(locale);
  }, [locale]);
  useEffect(() => {
    const syncRoute = () => updateMetadata(document.documentElement.lang === 'kk' ? 'kk' : document.documentElement.lang === 'en' ? 'en' : 'ru');
    const syncStorage = (event: StorageEvent) => {
      if (event.key === LANGUAGE_KEY || event.key === null) setLocale(detectLocale(event.newValue, navigator.languages), false);
      if (event.key === THEME_KEY || event.key === null) { applyTheme(readTheme()); window.dispatchEvent(new Event('eva-theme-change')); }
    };
    const media = matchMedia('(prefers-color-scheme: dark)');
    const syncSystemTheme = () => {
      let explicit = false;
      try { explicit = isTheme(localStorage.getItem(THEME_KEY)); } catch { /* Use system preference. */ }
      if (!explicit) { applyTheme(media.matches ? 'dark' : 'light'); window.dispatchEvent(new Event('eva-theme-change')); }
    };
    applyTheme(readTheme());
    window.addEventListener('storage', syncStorage);
    window.addEventListener('hashchange', syncRoute);
    media.addEventListener('change', syncSystemTheme);
    return () => { window.removeEventListener('storage', syncStorage); window.removeEventListener('hashchange', syncRoute); media.removeEventListener('change', syncSystemTheme); };
  }, []);
  return children;
}
export function AppearanceControls() {
  const { locale, t } = useI18n();
  const [theme, updateTheme] = useState(readTheme);
  useEffect(() => {
    const sync = () => updateTheme(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');
    window.addEventListener('eva-theme-change', sync);
    return () => window.removeEventListener('eva-theme-change', sync);
  }, []);
  function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem(THEME_KEY, next); } catch { /* Session change still works. */ }
    applyTheme(next); updateTheme(next); window.dispatchEvent(new Event('eva-theme-change'));
  }
  return <div className="appearance-controls">
    <label className="language-control"><Globe2 aria-hidden="true" size={16}/><select aria-label={t('Язык интерфейса')} value={locale} onChange={event => { if (isLocale(event.target.value)) setLocale(event.target.value); }}>
      <option value="kk" lang="kk">KZ · Қазақша</option><option value="ru" lang="ru">RU · Русский</option><option value="en" lang="en">EN · English</option>
    </select></label>
    <button type="button" className="theme-toggle" onClick={toggleTheme} aria-label={t(theme === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему')} title={t(theme === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему')}>
      {theme === 'dark' ? <Sun size={17} aria-hidden="true"/> : <Moon size={17} aria-hidden="true"/>}<span className="theme-label">{t(theme === 'dark' ? 'Светлая тема' : 'Тёмная тема')}</span>
    </button>
  </div>;
}
