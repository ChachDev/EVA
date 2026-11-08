import assert from 'node:assert/strict';
import test from 'node:test';
import { auditNotices } from '../src/audit-notices.ts';

test('saved model revisions are hidden while operational warnings survive', () => {
  const hash = 'a'.repeat(64);
  const failure = 'resolution-v2: перепроверка недоступна, сохранён исходный ответ.';
  const scope = 'Оценка основана на транскрипте; аудио не обрабатывалось.';
  assert.deepEqual(auditNotices([
    'Эксперимент consultation-v1.1, SHA-256 ' + hash + '.', 'resolution-v3-scope:' + hash,
    'Перепроверка потребности и ведения диалога interaction-review-v1, SHA-256 ' + hash + '.',
    'Адресная локальная проверка v7-evidence, SHA-256 ' + hash + '.', failure, scope, failure,
  ]), [failure, scope]);
});
