import { translate as t, formatDate, formatNumber as localNumber } from "./i18n-core.ts";
import type { ConversationCreate } from './api/types';

const roles: Record<string, ConversationCreate['messages'][number]['speaker']> = {
  client: 'client', клиент: 'client', operator: 'operator', оператор: 'operator',
  bot: 'bot', бот: 'bot', system: 'system', система: 'system', клиенті: 'client', жүйе: 'system',
};

/** Preserve supplied roles and time; never invent alternating speakers or timestamps. */
export function parseTranscript(raw: string, channel?: ConversationCreate['channel']): ConversationCreate['messages'] {
  const input = raw.trim();
  if (!input) throw new Error(t('Добавьте переписку.'));
  let rows: unknown[];
  const timestampedText = /^\[[^\]]+\]\s*[^:]+:/.test(input);
  if (input.startsWith('{') || (input.startsWith('[') && !timestampedText)) {
    let parsed;
    try { parsed = JSON.parse(input); } catch { throw new Error(t('Некорректный JSON. Проверьте файл.')); }
    const records = parsed?.data?.records ?? parsed?.records;
    if (records && (!Array.isArray(records) || records.length !== 1)) {
      throw new Error(t('Загрузите один разговор за раз.'));
    }
    const source = records ? records[0] : parsed;
    rows = Array.isArray(source) ? source : source?.messages ?? source?.transcript;
    if (!Array.isArray(rows)) throw new Error(t('В JSON нужен массив messages или transcript.'));
  } else {
    rows = input.split(/\r?\n/).filter(line => line.trim()).map((line, i) => {
      const match = line.match(/^\[([^\]]+)\]\s*([^:]+):\s*(.+)$/);
      if (!match) throw new Error(t('Строка {index}: нужен формат [00:00] клиент: текст. Время и роль обязательны.', {index: i + 1}));
      return { timestamp: match[1], speaker: match[2].trim(), text: match[3] };
    });
  }
  if (!rows.length || rows.length > 10000) throw new Error(t('Допустимо от 1 до 10 000 реплик.'));
  return rows.map((row, i) => {
    if (!row || typeof row !== 'object') throw new Error(t('Реплика {index}: нужен объект.', {index: i + 1}));
    const m = row as Record<string, unknown>;
    const role = m.speaker ?? m.role;
    const key = typeof role === 'string' ? role.toLowerCase().trim() : '';
    const speaker = Object.hasOwn(roles, key) ? roles[key] : undefined;
    const timestamp = m.timestamp ?? m.time;
    if (!speaker) throw new Error(t('Реплика {index}: укажите client, operator, bot или system.', {index: i + 1}));
    if (typeof timestamp !== 'string' || !timestamp.trim() || timestamp.length > 128) {
      throw new Error(t('Реплика {index}: нужна исходная метка времени.', {index: i + 1}));
    }
    const chatTime = timestamp === null || /^[0-9]{2,}:[0-5][0-9]$/.test(timestamp);
    const iso = timestamp?.match(/^([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:[.,][0-9]+)?)?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):?[0-5][0-9])$/);
    const year = Number(iso?.[1]), month = Number(iso?.[2]), day = Number(iso?.[3]);
    const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    const voiceTime = !!iso && year >= 1 && month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1] && Number(iso[4]) < 24 && Number(iso[5]) < 60 && Number(iso[6] ?? 0) < 60;
    if (channel === 'agent_chat' ? !chatTime : channel === 'voice_call' ? !voiceTime : !chatTime && !voiceTime) {
      throw new Error(t('Реплика {index}: для чата нужно MM:SS, для звонка — корректное ISO-время с часовым поясом.', {index: i + 1}));
    }
    if (typeof m.text !== 'string' || !m.text.trim() || m.text.length > 100000) {
      throw new Error(t('Реплика {index}: текст пуст или превышает 100 000 символов.', {index: i + 1}));
    }
    const extra = Object.fromEntries(['speaker_id','event','translation','language','quality','offset_seconds','end_seconds'].filter(k => k in m).map(k => [k,m[k]]));
    return { timestamp, speaker, text: m.text, ...extra };
  });
}

export function displayTime(value: string) {
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? value : formatDate(time);
}

export function formatNumber(value: number | null | undefined) {
  return value == null ? '—' : localNumber(value);
}
