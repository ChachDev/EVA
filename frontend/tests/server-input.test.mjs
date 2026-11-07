import assert from 'node:assert/strict';
import test from 'node:test';
import { parseTranscript } from '../src/server-input.ts';

const message = fields => ({ timestamp: '00:00', speaker: 'client', text: 'Синтетическая реплика.', ...fields });
const parseJson = value => parseTranscript(JSON.stringify(value));

test('JSON message array preserves original order, text and supplied timestamps', () => {
  const rows = [
    message({ timestamp: '00:07', text: 'Қазақша синтетикалық мәтін.' }),
    message({ timestamp: '105:59', speaker: 'operator', text: 'Synthetic answer: no real dialogue.' }),
  ];
  assert.deepEqual(parseJson(rows), rows);
});

for (const field of ['messages', 'transcript']) {
  test('JSON ' + field + ' object accepts canonical messages', () => {
    const rows = [message(), message({ timestamp: '00:05', speaker: 'operator' })];
    assert.deepEqual(parseJson({ [field]: rows }), rows);
  });
}

test('JSON time/role aliases normalize explicit roles without guessing speakers', () => {
  const rows = parseJson({ transcript: [
    { time: '00:00', role: ' КЛИЕНТ ', text: 'Синтетический вопрос.' },
    { time: '00:04', role: 'ОПЕРАТОР', text: 'Синтетический ответ.' },
    { time: '00:07', role: 'BoT', text: 'Синтетический бот.' },
    { time: '00:09', role: 'система', text: 'Синтетическая система.' },
  ] });
  assert.deepEqual(rows.map(row => row.speaker), ['client', 'operator', 'bot', 'system']);
  assert.deepEqual(rows.map(row => row.timestamp), ['00:00', '00:04', '00:07', '00:09']);
});

for (const nested of [false, true]) {
  test((nested ? 'organizer data.records' : 'records') + ' accepts exactly one conversation', () => {
    const records = [{ transcript: [message(), message({ timestamp: '00:03', speaker: 'operator' })] }];
    const envelope = nested
      ? { error: '', isSuccess: true, data: { dataset_group_id: 'synthetic', total_records: 1, records } }
      : { records };
    assert.deepEqual(parseJson(envelope), records[0].transcript);
  });

  for (const records of [[], [{ transcript: [message()] }, { transcript: [message()] }], {}]) {
    test((nested ? 'data.records' : 'records') + ' rejects anything other than one conversation: ' + JSON.stringify(records), () => {
      assert.throws(() => parseJson(nested ? { data: { records } } : { records }));
    });
  }
}

test('TXT is recognized despite starting with a bracket, preserves times and explicit speakers', () => {
  const rows = parseTranscript(
    '[00:00] клиент: Синтетический вопрос.\r\n'
    + '\r\n'
    + '[00:04] оператор: Синтетический ответ: с двоеточием.\r\n'
    + '[00:05] бот: Synthetic bot.\r\n'
    + '[00:06] система: Synthetic system.',
  );
  assert.deepEqual(rows, [
    message({ text: 'Синтетический вопрос.' }),
    message({ timestamp: '00:04', speaker: 'operator', text: 'Синтетический ответ: с двоеточием.' }),
    message({ timestamp: '00:05', speaker: 'bot', text: 'Synthetic bot.' }),
    message({ timestamp: '00:06', speaker: 'system', text: 'Synthetic system.' }),
  ]);
});

test('TXT keeps a supplied timezone-aware ISO timestamp unchanged', () => {
  const timestamp = '2024-02-29T12:34:56.125+05:00';
  assert.deepEqual(parseTranscript('[' + timestamp + '] operator: Synthetic.'), [
    message({ timestamp, speaker: 'operator', text: 'Synthetic.' }),
  ]);
});

test('JSON keeps timezone and fractional seconds rather than replacing timestamps', () => {
  const rows = [message({ timestamp: '2024-02-29T12:34:56.125+05:00' })];
  assert.deepEqual(parseJson(rows), rows);
});

for (const input of [
  '', ' \r\n\t ', '[', '{"messages":', '{"messages":[}', '{}', 'null', '[]',
  '{"messages":{}}', '{"transcript":null}', 'Просто текст без роли и времени.',
  'клиент: Нет времени.', '[00:00] Нет явной роли.', '[00:00] клиент:', '[00:00]: Нет роли.',
]) {
  test('malformed or incomplete input is rejected: ' + JSON.stringify(input), () => {
    assert.throws(() => parseTranscript(input));
  });
}

for (const role of ['agent', '', ' ', '__proto__', 'constructor', 'toString', 1, true, null]) {
  test('unknown role cannot become a canonical speaker: ' + JSON.stringify(role), () => {
    assert.throws(() => parseJson([message({ speaker: role })]));
  });
}

for (const [name, row] of [
  ['speaker', { timestamp: '00:00', text: 'Synthetic.' }],
  ['timestamp', { speaker: 'client', text: 'Synthetic.' }],
  ['text', { timestamp: '00:00', speaker: 'client' }],
]) {
  test('missing required ' + name + ' is rejected rather than invented', () => {
    assert.throws(() => parseJson([row]));
  });
}

for (const row of [null, 1, false, 'Synthetic.', []]) {
  test('a message must be an object with canonical fields: ' + JSON.stringify(row), () => {
    assert.throws(() => parseJson([row]));
  });
}

for (const timestamp of ['', ' ', 1, true, '0'.repeat(129)]) {
  test('missing, wrongly typed or oversized timestamp is rejected: ' + JSON.stringify(timestamp), () => {
    assert.throws(() => parseJson([message({ timestamp })]));
  });
}

for (const timestamp of [
  'tomorrow', '00:60', '9:00', '00:-1', ' 00:00 ', '2026-01-01T12:00:00',
  '2026-02-29T12:00:00Z', '2024-02-30T12:00:00Z', '2026-13-01T12:00:00Z',
]) {
  test('invalid actual timestamp is rejected before submission: ' + timestamp, () => {
    assert.throws(() => parseJson([message({ timestamp })]));
  });
}

for (const text of ['', ' \t ', null, 1, true, 'x'.repeat(100_001)]) {
  test('empty, wrongly typed or oversized message text is rejected (' + typeof text + ', ' + String(text).length + ')', () => {
    assert.throws(() => parseJson([message({ text })]));
  });
}

test('maximum 100000-character message remains intact', () => {
  const text = 'я'.repeat(100_000);
  assert.equal(parseJson([message({ text })])[0].text, text);
});

test('exactly 10000 messages remain intact and no extra speaker alternation is invented', () => {
  const rows = Array.from({ length: 10_000 }, () => message({ speaker: 'operator' }));
  const parsed = parseJson(rows);
  assert.equal(parsed.length, 10_000);
  assert.ok(parsed.every(row => row.speaker === 'operator' && row.timestamp === '00:00'));
});

test('10001 messages are rejected rather than truncated', () => {
  assert.throws(() => parseJson(Array.from({ length: 10_001 }, () => message())));
});

test('keeps an unknown speaker, mixed language and explicitly absent time', () => {
  const row=message({timestamp:null,speaker:'unknown',speaker_id:'B',language:'mixed'});
  assert.deepEqual(parseJson([row]),[row]);
});

test('an invalid later message rejects the whole transcript', () => {
  assert.throws(() => parseJson([message(), message({ speaker: 'unmapped' }), message()]));
});
