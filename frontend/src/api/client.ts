import conversationFixture from '../../../contracts/examples/conversation.json' with { type: 'json' };
import checklistFixture from '../../../contracts/examples/checklist.json' with { type: 'json' };
import evaluationFixture from '../../../contracts/examples/evaluation-response.json' with { type: 'json' };
import scoreFixture from '../../../contracts/examples/score-summary.json' with { type: 'json' };
import type {
  Checklist, Conversation, CqaApi, Evaluation, EvaluationList,
  EvaluationListQuery, EvaluationResponse, ScoreSummary,
} from './types';

const environment = import.meta.env ?? {};
export const apiMode = environment.VITE_API_MODE === 'demo' ? 'demo' : 'http';
export const DEFAULT_API_URL = 'http://127.0.0.1:8000';
export const JSON_MAX_BYTES = 1024 * 1024;
export const AUDIO_MAX_BYTES = 25 * 1024 * 1024;

export class ApiError extends Error {
  readonly status: number | null;
  readonly code: 'http' | 'network' | 'timeout' | 'invalid_response' | 'invalid_input' | 'demo';

  constructor(message: string, status: number | null = null, code: ApiError['code'] = 'http') {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

interface HttpApiOptions {
  baseUrl?: string;
  fetch?: typeof globalThis.fetch;
  timeoutMs?: number;
}

const errorMessages: Record<number, string> = {
  400: 'Сервер отклонил формат запроса. Проверьте введённые данные.',
  404: 'Запись не найдена. Обновите список данных.',
  409: 'Данные или версия изменились либо идентификатор операции уже использован. Обновите данные и проверьте результат операции.',
  413: 'Размер данных превышает допустимый лимит.',
  415: 'Этот формат файла или запроса не поддерживается.',
  422: 'Данные не прошли проверку. Проверьте поля, роли, время реплик и версию чек-листа.',
  502: 'Сервис оценки или распознавания вернул некорректный ответ. Результат не подтверждён.',
  503: 'Сервис временно недоступен или занят. Проверьте доступность Backend, ML и настройку локального ASR.',
  504: 'Время ожидания сервиса истекло. Сначала проверьте историю оценок или состояние задания: операция могла завершиться.',
};

const timeoutMessage = 'Время ожидания ответа истекло. Сначала проверьте историю оценок или состояние задания: операция могла завершиться. Не запускайте оценку повторно автоматически.';
const invalidResponse = () => new ApiError('Сервер вернул ответ неподдерживаемого формата.', null, 'invalid_response');
const pathId = (id: string) => encodeURIComponent(id);

function jsonBody(payload: unknown): string {
  let body: string | undefined;
  try { body = JSON.stringify(payload); }
  catch { throw new ApiError('Не удалось подготовить данные запроса.', null, 'invalid_input'); }
  if (body === undefined) throw new ApiError('Данные запроса отсутствуют.', null, 'invalid_input');
  if (new TextEncoder().encode(body).byteLength > JSON_MAX_BYTES) {
    throw new ApiError('Размер JSON превышает допустимый лимит 1 МиБ. Запрос не отправлен.', 413, 'invalid_input');
  }
  return body;
}

export function createHttpApi(options: HttpApiOptions = {}): CqaApi {
  const baseUrl = (options.baseUrl ?? (environment.VITE_API_URL || (environment.PROD ? '' : DEFAULT_API_URL))).replace(/\/+$/, '');
  const fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
  const timeoutMs = options.timeoutMs ?? Number(environment.VITE_API_TIMEOUT_MS || 30_000);
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error('API timeout must be positive.');

  async function request<T>(path: string, init?: RequestInit): Promise<T> {
    const controller = new AbortController();
    const timer = globalThis.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetcher(baseUrl + path, {
        ...init, signal: controller.signal, redirect: 'error', credentials: baseUrl === '' ? 'same-origin' : 'omit',
      });
      if (!response.ok) {
        let message = errorMessages[response.status] ?? 'Не удалось выполнить запрос. Проверьте состояние сервиса.';
        if (response.status === 503 && (path === '/api/v1/evaluations' || path === '/api/v1/transcriptions')) {
          try {
            const body = await response.json();
            if (path === '/api/v1/transcriptions' && body?.detail === 'Local speech recognition is not configured. The audio asset remains saved.') {
              message = 'Аудиозапись сохранена. На сервере не подключено распознавание речи.';
            } else if (path === '/api/v1/evaluations' && body?.detail === 'ML service is unavailable. No evaluation was saved.') {
              message = 'Сервис оценки не ответил. Транскрипт сохранён, оценку можно повторить.';
            }
          } catch { /* Keep the fixed 503 message for other upstream responses. */ }
        }
        if (response.status===409 && path.startsWith('/api/v1/batches/') && path.endsWith('/results')) {
          const failures:Record<string,string>={
            'Not an organizer DEV or PROD group.':'Для сдачи нужен набор организаторов DEV или PROD.',
            'Batch still has unfinished items.':'Дождитесь завершения всех заданий.',
            'Dataset manifest or original sources are incomplete.':'Есть лишние, пропущенные или повторные ID, либо нет исходного транскрипта.',
            'Unexpected number of organizer chats or calls.':'Ожидается DEV: 60 чатов и 5 звонков; PROD: 10 чатов.',
            'Not the verified Freedom checklist.':'Версия чек-листа отличается от подтверждённых правил Freedom.',
            'Result belongs to a different source or checklist.':'Снимок оценки не соответствует источнику или чек-листу.',
            'Only recorded real local model results may be exported.':'Обнаружен демонстрационный или неподтверждённый результат.',
            'Source turn order or text changed.':'Порядок или текст реплик не совпадает с оригиналом.',
            'Stored score differs from recalculation.':'Итоговый балл не прошёл пересчёт.',
            'Missing rationale or evidence.':'У оценки отсутствует основание или ссылка на реплику.',
            'No actual model identity is available for this run.':'Нет подтверждённого имени фактически использованной модели.',
          };
          try {const body=await response.json();message=Object.hasOwn(failures,body?.detail)?failures[body.detail]:'Выгрузка не прошла проверку.'} catch { /* fixed fallback */ }
        }
        if (response.status === 422) {
          // Only this fixed public code affects the UI; upstream body text is never shown.
          try {
            const body: unknown = await response.json();
            if (body !== null && typeof body === 'object'
                && 'code' in body && body.code === 'ml_context_limit_exceeded') {
              message = 'Диалог и чек-лист превышают контекст локальной модели. Оценка не сохранена.';
            }
          } catch { /* Keep the fixed validation message. */ }
        }
        throw new ApiError(message, response.status);
      }
      if (response.status === 204) return undefined as T;
      try { return await response.json() as T; }
      catch (error) {
        if (controller.signal.aborted) throw error;
        throw invalidResponse();
      }
    } catch (error) {
      if (controller.signal.aborted) throw new ApiError(timeoutMessage, null, 'timeout');
      if (error instanceof ApiError) throw error;
      throw new ApiError(
        'Нет связи с Backend. Проверьте его запуск и адрес. Если отправляли данные, сначала проверьте историю или состояние задания.',
        null, 'network',
      );
    } finally {
      globalThis.clearTimeout(timer);
    }
  }

  const post = <T>(path: string, payload: unknown) => request<T>(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: jsonBody(payload),
  });

  async function getEvaluationPage(query: EvaluationListQuery = {}): Promise<EvaluationList> {
    const params = new URLSearchParams();
    params.set('limit', String(query.limit ?? 100));
    if (query.conversation_id !== undefined) params.set('conversation_id', query.conversation_id);
    if (query.cursor !== undefined) params.set('cursor', query.cursor);
    const page = await request<EvaluationList>('/api/v1/evaluations?' + params.toString());
    if (!page || !Array.isArray(page.items) || page.items.length > 100
        || !(page.next_cursor === null || (typeof page.next_cursor === 'string' && page.next_cursor.length > 0))) {
      throw invalidResponse();
    }
    return page;
  }

  return {
    batches:{
      groups:()=>request('/api/v1/batch-groups'),list:()=>request('/api/v1/batches'),
      get:id=>request('/api/v1/batches/'+pathId(id)),
      start:(group,c)=>post('/api/v1/batches',{group_id:group,checklist_id:c.id,checklist_version:c.version}),
      retry:id=>post('/api/v1/batches/'+pathId(id)+'/retry',{}),results:id=>request('/api/v1/batches/'+pathId(id)+'/results'),
      quality:id=>request('/api/v1/batches/'+pathId(id)+'/quality'),
      blind:id=>request('/api/v1/blind/'+pathId(id)),label:(id,reviewer,answers)=>post('/api/v1/blind/'+pathId(id),{reviewer,answers}),
    },
    health: () => request('/health'),
    getChecklists: () => request('/api/v1/checklists'),
    publishChecklist: (id, payload) => post('/api/v1/checklists/' + pathId(id) + '/versions', payload),
    getConversations: () => request('/api/v1/conversations'),
    getConversation: id => request('/api/v1/conversations/' + pathId(id)),
    createConversation: payload => post('/api/v1/conversations', payload),
    evaluate: payload => post('/api/v1/evaluations', payload),
    auditExport: id => request('/api/v1/evaluations/'+pathId(id)+'/audit'),
    deriveVersion: (id,version) => post('/api/v1/evaluations/'+pathId(id)+'/versions/'+version,{}),
    getEvaluationPage,
    getEvaluations: async conversationId => {
      const items: Evaluation[] = [];
      const cursors = new Set<string>();
      let cursor: string | undefined;
      do {
        const page = await getEvaluationPage({ conversation_id: conversationId, limit: 100, cursor });
        items.push(...page.items);
        if (page.next_cursor === null) return items;
        if (cursors.has(page.next_cursor)) throw invalidResponse();
        cursors.add(page.next_cursor);
        cursor = page.next_cursor;
      } while (true);
    },
    getEvaluation: id => request('/api/v1/evaluations/' + pathId(id)),
    getEvaluationSnapshot: id => request('/api/v1/evaluations/' + pathId(id) + '/snapshot'),
    getReviewCase: id => request('/api/v1/review-cases/' + pathId(id)),
    createAppeal: (id, payload) => post('/api/v1/evaluations/' + pathId(id) + '/appeals', payload),
    decideAppeal: (id, payload) => post('/api/v1/appeals/' + pathId(id) + '/decisions', payload),
    uploadAudio: (blob, operationId, contentType) => {
      const mime = (contentType ?? blob.type).split(';', 1)[0].trim().toLowerCase();
      const supported: readonly string[] = ['audio/wav', 'audio/x-wav', 'audio/mpeg', 'audio/flac', 'audio/ogg'];
      if (!supported.includes(mime)) {
        throw new ApiError('Поддерживаются WAV, MP3, FLAC и Ogg. Укажите корректный тип аудио.', 415, 'invalid_input');
      }
      if (!blob.size || blob.size > AUDIO_MAX_BYTES) {
        throw new ApiError('Аудиофайл должен быть непустым и не превышать 25 МиБ.', 413, 'invalid_input');
      }
      return request('/api/v1/audio-assets', {
        method: 'POST', headers: { 'Content-Type': mime, 'Idempotency-Key': operationId,
          ...(typeof File !== 'undefined' && blob instanceof File ? { 'X-Audio-Filename': encodeURIComponent(blob.name) } : {}) }, body: blob,
      });
    },
    getAudioAsset: id => request('/api/v1/audio-assets/' + pathId(id)),
    audioContentUrl: id => baseUrl + '/api/v1/audio-assets/' + pathId(id) + '/content',
    createTranscription: payload => post('/api/v1/transcriptions', payload),
    getTranscription: id => request('/api/v1/transcriptions/' + pathId(id)),
    transcriptionHistory: offset => request('/api/v1/transcriptions?limit=20&offset=' + offset),
    exportTranscript: id => request('/api/v1/transcriptions/' + pathId(id) + '/export'),
    deleteConversation: id => request('/api/v1/conversations/' + pathId(id), {method:'DELETE'}),
    deleteEvaluation: id => request('/api/v1/evaluations/' + pathId(id), {method:'DELETE'}),
    deleteChecklist: (id,version) => request('/api/v1/checklists/' + pathId(id) + '/versions/' + version, {method:'DELETE'}),
    reviewTranscription: (id, payload) => post('/api/v1/transcriptions/' + pathId(id) + '/review', payload),
  };
}

function demoUnavailable(): never {
  throw new ApiError('Это действие доступно при подключении к Backend. Деморежим использует только синтетический пример.', null, 'demo');
}

// Compatibility for the old demo screen. Unsupported mutations never fall through to HTTP.
const demoApi: CqaApi = {
  health: async () => ({ status: 'demo' }),
  getChecklists: async () => [structuredClone(checklistFixture) as Checklist],
  getConversations: async () => [structuredClone(conversationFixture) as Conversation],
  getConversation: async id => {
    if (id !== conversationFixture.id) return demoUnavailable();
    return structuredClone(conversationFixture) as Conversation;
  },
  evaluate: async payload => {
    if (payload.conversation_id !== conversationFixture.id
        || payload.checklist_id !== checklistFixture.id
        || payload.checklist_version !== checklistFixture.version) return demoUnavailable();
    const scoring = structuredClone(scoreFixture) as ScoreSummary;
    return {
      id: 'demo-evaluation', status: scoring.status === 'complete' ? 'completed' : 'needs_review',
      created_at: new Date().toISOString(), original: structuredClone(evaluationFixture) as EvaluationResponse, scoring,
    };
  },
  getEvaluations: async () => [],
  getEvaluationPage: async () => ({ items: [], next_cursor: null }),
  publishChecklist: async () => demoUnavailable(),
  createConversation: async () => demoUnavailable(),
  getEvaluation: async () => demoUnavailable(),
  getEvaluationSnapshot: async () => demoUnavailable(),
  getReviewCase: async () => demoUnavailable(),
  createAppeal: async () => demoUnavailable(),
  decideAppeal: async () => demoUnavailable(),
  uploadAudio: async () => demoUnavailable(),
  getAudioAsset: async () => demoUnavailable(),
  audioContentUrl: () => demoUnavailable(),
  createTranscription: async () => demoUnavailable(),
  getTranscription: async () => demoUnavailable(),
  transcriptionHistory: async () => demoUnavailable(),
  exportTranscript: async () => demoUnavailable(),
  deleteConversation: async () => demoUnavailable(),
  deleteEvaluation: async () => demoUnavailable(),
  deleteChecklist: async () => demoUnavailable(),
  reviewTranscription: async () => demoUnavailable(),
};

export const api: CqaApi = apiMode === 'demo' ? demoApi : createHttpApi();
