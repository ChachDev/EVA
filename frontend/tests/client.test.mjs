import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import {
  apiMode, ApiError, AUDIO_MAX_BYTES, createHttpApi, DEFAULT_API_URL, JSON_MAX_BYTES,
} from '../src/api/client.ts';

const fixture = async name => JSON.parse(await readFile(new URL('../../contracts/examples/' + name + '.json', import.meta.url), 'utf8'));
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const operationId = 'cdd3295c-936b-490c-a573-0547d7628665';

function harness(handler, options = {}) {
  const calls = [];
  const api = createHttpApi({
    ...options,
    fetch: async (url, init) => {
      calls.push({ url, init });
      return handler(url, init, calls.length);
    },
  });
  return { api, calls };
}

test('HTTP is default and GET has no forced JSON header or credentials', async () => {
  assert.equal(apiMode, 'http');
  const { api, calls } = harness(() => json({ status: 'ok' }));
  assert.deepEqual(await api.health(), { status: 'ok' });
  assert.equal(calls[0].url, DEFAULT_API_URL + '/health');
  assert.equal(new Headers(calls[0].init.headers).has('Content-Type'), false);
  assert.equal(calls[0].init.credentials, 'omit');
  assert.equal(calls[0].init.redirect, 'error');
});

test('read routes encode IDs and use the configured Backend origin', async () => {
  const { api, calls } = harness(() => json({}), { baseUrl: 'http://127.0.0.1:8123///' });
  await api.getConversations();
  await api.getConversation('dialog /?#');
  await api.getChecklists();
  await api.getEvaluation('eval /?#');
  await api.getEvaluationSnapshot('eval /?#');
  await api.getReviewCase('eval /?#');
  await api.getAudioAsset('asset /?#');
  await api.getTranscription('job /?#');
  assert.deepEqual(calls.map(call => call.url), [
    '/api/v1/conversations', '/api/v1/conversations/dialog%20%2F%3F%23',
    '/api/v1/checklists', '/api/v1/evaluations/eval%20%2F%3F%23',
    '/api/v1/evaluations/eval%20%2F%3F%23/snapshot', '/api/v1/review-cases/eval%20%2F%3F%23',
    '/api/v1/audio-assets/asset%20%2F%3F%23', '/api/v1/transcriptions/job%20%2F%3F%23',
  ].map(path => 'http://127.0.0.1:8123' + path));
  assert.equal(api.audioContentUrl('asset /?#'), 'http://127.0.0.1:8123/api/v1/audio-assets/asset%20%2F%3F%23/content');
});

test('history reads every cursor page at limit 100 and keeps the conversation filter', async () => {
  const evaluation = await fixture('evaluation-record');
  const { api, calls } = harness((_url, _init, number) => json(number === 1
    ? { items: [{ ...evaluation, id: 'first' }], next_cursor: 'cursor+/=?' }
    : { items: [{ ...evaluation, id: 'second' }], next_cursor: null }));
  assert.deepEqual((await api.getEvaluations('dialog & one')).map(item => item.id), ['first', 'second']);
  assert.equal(calls.length, 2);
  for (const call of calls) {
    assert.equal(new URL(call.url).searchParams.get('limit'), '100');
    assert.equal(new URL(call.url).searchParams.get('conversation_id'), 'dialog & one');
  }
  assert.equal(new URL(calls[1].url).searchParams.get('cursor'), 'cursor+/=?');
});

test('history pagination rejects a repeated cursor instead of looping forever', async () => {
  const { api, calls } = harness(() => json({ items: [], next_cursor: 'same' }));
  await assert.rejects(api.getEvaluations(), error => error instanceof ApiError && error.code === 'invalid_response');
  assert.equal(calls.length, 2);
});

test('history does not return a partial list if a later page fails', async () => {
  const { api, calls } = harness((_url, _init, number) => number === 1
    ? json({ items: [], next_cursor: 'next' }) : json({ detail: 'private' }, 503));
  await assert.rejects(api.getEvaluations(), error => error instanceof ApiError && error.status === 503);
  assert.equal(calls.length, 2);
});

for (const page of [{ items: [], next_cursor: '' }, { items: null, next_cursor: null }, { items: [] }]) {
  test('history rejects malformed page ' + JSON.stringify(page), async () => {
    const { api } = harness(() => json(page));
    await assert.rejects(api.getEvaluations(), error => error instanceof ApiError && error.code === 'invalid_response');
  });
}

test('mutation routes preserve canonical payloads, operation IDs and expected revisions', async () => {
  const conversation = await fixture('conversation-create');
  const checklist = await fixture('checklist-publish');
  const appeal = await fixture('appeal-create');
  const decision = await fixture('appeal-decision');
  const transcription = await fixture('transcription-create');
  const review = await fixture('transcription-review');
  const { api, calls } = harness(() => json({}));
  await api.createConversation(conversation);
  await api.publishChecklist('check / one', checklist);
  await api.createAppeal('eval / one', appeal);
  await api.decideAppeal('appeal / one', decision);
  await api.createTranscription(transcription);
  await api.reviewTranscription('job / one', review);
  const expected = [
    ['/api/v1/conversations', conversation],
    ['/api/v1/checklists/check%20%2F%20one/versions', checklist],
    ['/api/v1/evaluations/eval%20%2F%20one/appeals', appeal],
    ['/api/v1/appeals/appeal%20%2F%20one/decisions', decision],
    ['/api/v1/transcriptions', transcription],
    ['/api/v1/transcriptions/job%20%2F%20one/review', review],
  ];
  expected.forEach(([path, payload], index) => {
    assert.equal(calls[index].url, DEFAULT_API_URL + path);
    assert.equal(calls[index].init.method, 'POST');
    assert.equal(new Headers(calls[index].init.headers).get('Content-Type'), 'application/json');
    assert.deepEqual(JSON.parse(calls[index].init.body), payload);
  });
});

test('manual retry sends exactly the caller operation ID and payload again', async () => {
  const payload = { ...(await fixture('appeal-create')), operation_id: operationId };
  const before = structuredClone(payload);
  const { api, calls } = harness(() => json({}));
  await api.createAppeal('eval', payload);
  await api.createAppeal('eval', payload);
  assert.equal(calls[0].init.body, calls[1].init.body);
  assert.deepEqual(payload, before);
});

test('review-case keeps the canonical final result separate from the original record', async () => {
  const reviewCase = await fixture('review-case');
  const { api } = harness(() => json(reviewCase));
  const received = await api.getReviewCase(reviewCase.evaluation_id);
  assert.deepEqual(received, reviewCase);
  assert.deepEqual(received.final.criteria, reviewCase.final.criteria);
  assert.equal('original' in received.final, false);
});

test('immutable snapshot is returned without replacing it with current inputs', async () => {
  const snapshot = await fixture('evaluation-request');
  const { api } = harness(() => json(snapshot));
  assert.deepEqual(await api.getEvaluationSnapshot('eval'), snapshot);
});

test('audio upload sends raw bytes, canonical MIME and caller Idempotency-Key', async () => {
  const asset = await fixture('audio-asset');
  const bytes = new Blob([new Uint8Array([82, 73, 70, 70])], { type: 'audio/x-wav' });
  const { api, calls } = harness(() => json(asset));
  assert.deepEqual(await api.uploadAudio(bytes, operationId), asset);
  const { init } = calls[0];
  assert.equal(calls[0].url, DEFAULT_API_URL + '/api/v1/audio-assets');
  assert.equal(init.body, bytes);
  assert.equal(new Headers(init.headers).get('Content-Type'), 'audio/x-wav');
  assert.equal(new Headers(init.headers).get('Idempotency-Key'), operationId);
});

test('audio MIME can be supplied for files whose browser type is empty', async () => {
  const { api, calls } = harness(() => json({}));
  await api.uploadAudio(new Blob(['synthetic']), operationId, 'audio/flac');
  assert.equal(new Headers(calls[0].init.headers).get('Content-Type'), 'audio/flac');
});

test('empty, oversized and unsupported audio never contacts Backend', async () => {
  const { api, calls } = harness(() => json({}));
  for (const blob of [
    new Blob([], { type: 'audio/wav' }),
    new Blob([new Uint8Array(AUDIO_MAX_BYTES + 1)], { type: 'audio/wav' }),
    new Blob(['synthetic'], { type: 'text/plain' }),
  ]) {
    await assert.rejects(async () => api.uploadAudio(blob, operationId), error => error instanceof ApiError && error.code === 'invalid_input');
  }
  assert.equal(calls.length, 0);
});

test('audio at the 25 MiB boundary is permitted by the client', async () => {
  const { api, calls } = harness(() => json({}));
  await api.uploadAudio(new Blob([new Uint8Array(AUDIO_MAX_BYTES)], { type: 'audio/wav' }), operationId);
  assert.equal(calls.length, 1);
});

test('JSON limit counts UTF-8 bytes rather than JavaScript characters', async () => {
  const payload = {
    operation_id: operationId, channel: 'agent_chat', language: 'RU',
    messages: Array.from({ length: 6 }, () => ({ timestamp: '00:00', speaker: 'operator', text: 'я'.repeat(90_000) })),
  };
  assert.ok(JSON.stringify(payload).length < JSON_MAX_BYTES);
  const { api, calls } = harness(() => json({}));
  await assert.rejects(async () => api.createConversation(payload), error =>
    error instanceof ApiError && error.status === 413 && error.message.includes('1 МиБ')
    && !/сократ|обрез|удал/iu.test(error.message));
  assert.equal(calls.length, 0);
});

for (const status of [409, 422, 502, 503, 504]) {
  test('HTTP ' + status + ' is safe and never triggers an inference retry', async () => {
    const secret = 'synthetic-private-upstream-content';
    const { api, calls } = harness(() => json({ detail: secret }, status));
    await assert.rejects(api.evaluate({ conversation_id: 'c', checklist_id: 'q', checklist_version: 1 }), error => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.status, status);
      assert.ok(!error.message.includes(secret));
      assert.ok(/[А-Яа-я]/u.test(error.message));
      return true;
    });
    assert.equal(calls.length, 1);
  });
}

test('context-limit code has a fixed explanation without suggesting automatic truncation', async () => {
  const { api } = harness(() => json({ code: 'ml_context_limit_exceeded', detail: 'private' }, 422));
  await assert.rejects(api.evaluate({ conversation_id: 'c', checklist_id: 'q', checklist_version: 1 }), error =>
    error instanceof ApiError && error.message.includes('контекст локальной модели')
    && error.message.includes('Оценка не сохранена') && !error.message.includes('private'));
});

test('malformed validation error body uses the safe fixed message', async () => {
  const { api } = harness(() => new Response('private malformed body', { status: 422 }));
  await assert.rejects(api.health(), error => error instanceof ApiError && error.status === 422 && !error.message.includes('private'));
});

test('network exceptions never expose endpoints or exception text', async () => {
  const { api, calls } = harness(() => { throw new TypeError('private-url-and-secret'); });
  await assert.rejects(api.health(), error =>
    error instanceof ApiError && error.code === 'network' && !error.message.includes('private'));
  assert.equal(calls.length, 1);
});

test('timeout advises checking saved history and does not retry', async () => {
  const { api, calls } = harness((_url, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener('abort', () => reject(new DOMException('private', 'AbortError')), { once: true });
  }), { timeoutMs: 5 });
  await assert.rejects(api.evaluate({ conversation_id: 'c', checklist_id: 'q', checklist_version: 1 }), error =>
    error instanceof ApiError && error.code === 'timeout' && error.message.includes('историю')
    && !error.message.includes('private'));
  assert.equal(calls.length, 1);
});

test('successful non-JSON response is an explicit safe format error', async () => {
  const { api } = harness(() => new Response('<html>private</html>'));
  await assert.rejects(api.health(), error =>
    error instanceof ApiError && error.code === 'invalid_response' && !error.message.includes('private'));
});
