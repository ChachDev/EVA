import { useEffect, useState } from 'react';
import { api } from './api/client';
import type { TranscriptionSummary } from './api/types';
import { useI18n } from './i18n';
import { displayTime } from './server-input';

const states = { queued: 'В очереди', running: 'Распознаётся', completed: 'Транскрипт готов', needs_review: 'Нужно определить роли', failed: 'Ошибка распознавания' };

export default function AudioHistory({open,revision}: {open: (id:string) => void; revision:number}) {
  const { t } = useI18n();
  const [offset,setOffset] = useState(0);
  const [page,setPage] = useState<{items:TranscriptionSummary[];has_more:boolean}>();
  const [error,setError] = useState('');
  useEffect(() => {
    let active = true;
    setPage(undefined); setError('');
    api.transcriptionHistory(offset).then(result => { if (active) setPage(result); })
      .catch(reason => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [offset, revision]);
  return <section className="panel live-panel">
    <div className="panel-heading"><h2>{t('История распознавания')}</h2></div>
    {error ? <p role="alert">{t(error)}</p> : !page ? <p role="status">{t('Загружаем задание…')}</p> :
      page.items.length ? <ul className="audio-history">{page.items.map(item =>
        <li key={item.id}><button className="button secondary" onClick={() => open(item.id)}>
          <strong>{item.display_name || item.id}</strong><span>{displayTime(item.created_at)} · {item.language} · {t(states[item.status])}</span>
        </button></li>)}</ul> : <p>{t('Записей пока нет')}</p>}
    <div className="live-controls">{offset > 0 && <button className="button secondary" onClick={() => setOffset(offset - 20)}>{t('Назад')}</button>}
      {page?.has_more && <button className="button secondary" onClick={() => setOffset(offset + 20)}>{t('Далее')}</button>}</div>
  </section>;
}
