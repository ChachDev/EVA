import conversationFixture from '../../../contracts/examples/conversation.json';
import checklistFixture from '../../../contracts/examples/checklist.json';
import evaluationFixture from '../../../contracts/examples/evaluation-response.json';
import scoreFixture from '../../../contracts/examples/score-summary.json';
import type { Checklist, Conversation, CqaApi, EvaluationResponse, ScoreSummary } from './types';

export const apiMode = import.meta.env.VITE_API_MODE === 'http' ? 'http' : 'demo';
const baseUrl = (import.meta.env.VITE_API_URL || 'http://localhost:8000').replace(/\/$/, '');

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 30_000);
  try {
    const response = await fetch(`${baseUrl}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    });
    if (!response.ok) throw new Error(`Сервер вернул ошибку ${response.status}.`);
    return await response.json() as T;
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new Error('Сервер не ответил за 30 секунд. Повторите запрос.');
    }
    if (error instanceof TypeError) throw new Error('Нет связи с API. Проверьте запуск backend и адрес в .env.');
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

const httpApi: CqaApi = {
  health: () => request('/health'),
  getChecklists: () => request('/api/v1/checklists'),
  getConversations: () => request('/api/v1/conversations'),
  evaluate: (payload) => request('/api/v1/evaluations', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
};

// The JSON examples are synthetic. This adapter never contacts a model or API.
const demoApi: CqaApi = {
  health: async () => ({ status: 'demo' }),
  getChecklists: async () => [structuredClone(checklistFixture) as Checklist],
  getConversations: async () => [structuredClone(conversationFixture) as Conversation],
  evaluate: async (payload) => {
    if (payload.conversation_id !== conversationFixture.id ||
        payload.checklist_id !== checklistFixture.id ||
        payload.checklist_version !== checklistFixture.version) {
      throw new Error('Для выбранного диалога и версии чек-листа нет демопримера.');
    }
    const scoring = structuredClone(scoreFixture) as ScoreSummary;
    return {
      id: 'demo-evaluation',
      status: scoring.status === 'complete' ? 'completed' : 'needs_review',
      created_at: new Date().toISOString(),
      original: structuredClone(evaluationFixture) as EvaluationResponse,
      scoring,
    };
  },
};

export const api: CqaApi = apiMode === 'http' ? httpApi : demoApi;
