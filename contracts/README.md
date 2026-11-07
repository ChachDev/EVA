# Контракты EVA v1.0.0

Это общая граница между тремя владельцами. Связанные совместимые изменения допустимы в рамках задачи с обновлением примеров и проверкой потребителей; ломающие изменения согласуются заранее. Схемы — JSON Schema Draft 2020-12, `$id` вида `https://eva.local/schemas/<name>.schema.json`. Это идентификаторы, не сетевые адреса: разрешение ссылок выполняется через локальный registry.

## Согласованные маршруты

| Направление | HTTP / файл | Назначение |
|---|---|---|
| Frontend → Backend | `GET /api/v1/conversations` | Массив `conversation` |
| Frontend → Backend | `POST /api/v1/conversations` | `conversation-create` → `conversation`, 201 |
| Frontend → Backend | `GET /api/v1/conversations/{id}` | Один `conversation` |
| Frontend → Backend | `GET /api/v1/checklists` | Массив `checklist` |
| Frontend → Backend | `POST /api/v1/checklists/{id}/versions` | `checklist-publish` → `checklist`, 201 |
| Frontend → Backend | `POST /api/v1/evaluations` | `evaluation-create` → `evaluation-record` |
| Frontend → Backend | `GET /api/v1/evaluations` | `evaluation-list`; `conversation_id`, `limit` 1–100, `cursor` |
| Frontend → Backend | `GET /api/v1/evaluations/{id}` | Сохранённый `evaluation-record` |
| Frontend → Backend | `GET /api/v1/evaluations/{id}/snapshot` | Исходный `evaluation-request`; legacy без снимка — 409 |
| Frontend → Backend | `GET /api/v1/review-cases/{evaluation_id}` | `review-case` с original и final |
| Frontend → Backend | `POST /api/v1/evaluations/{id}/appeals` | `appeal-create` → `review-case`, 201 |
| Frontend → Backend | `POST /api/v1/appeals/{id}/decisions` | `appeal-decision` → `review-case`, 201 |
| Frontend → Backend | `POST /api/v1/audio-assets` | Raw audio + UUID `Idempotency-Key` → `audio-asset`, 201 |
| Frontend → Backend | `GET /api/v1/audio-assets/{id}` | `audio-asset` |
| Frontend → Backend | `GET /api/v1/audio-assets/{id}/content` | Байты локального файла, Range 206/416 |
| Frontend → Backend | `POST /api/v1/transcriptions` | `transcription-create` → `transcription-job`, 202 |
| Frontend → Backend | `GET /api/v1/transcriptions/{id}` | `transcription-job` |
| Frontend → Backend | `POST /api/v1/transcriptions/{id}/review` | `transcription-review` → `transcription-job` |
| Backend → ML | `POST /v1/evaluate` | `evaluation-request` → `evaluation-response` |
| Backend → локальный ASR | `POST /v1/transcribe` | Multipart audio → `transcription-result`; провайдер пока отсутствует |
| Локальные сервисы | `GET /health` | Диагностика процесса; не подтверждает качество модели |

Канонические форматы в `schemas/`, синтетические примеры в `examples/`. `evaluation-request.json` объединяет пример чек-листа и разговора. `evaluation-record.json` — пример сохранённой оценки. Демо размечено вручную, `provider=fixture`; ответы работающей заглушки имеют `provider=stub` и нуждаются в проверке.

Новый совместимый пакет содержит 11 отдельных схем и одноимённых синтетических примеров: `conversation-create`, `checklist-publish`, `evaluation-list`, `appeal-create`, `appeal-decision`, `review-case`, `audio-asset`, `transcription-create`, `transcription-job`, `transcription-result`, `transcription-review`. Старые v1-форматы успешных ответов не расширялись. Примеры с provider=fixture описывают форму; runtime ASR отклоняет фиктивного провайдера.

## Версии, повторы и пересмотр

`conversation-create` получает UUID `operation_id`, channel/language и массив `{timestamp,speaker,text}`. Backend назначает детерминированный ID разговора из operation_id и последовательные ID реплик. Тот же operation_id с тем же содержимым возвращает прежний разговор; изменённое содержимое — 409. Для полного organizer envelope остаётся явный CLI-импорт.

`checklist-publish` содержит `base_version`, `name`, `criteria`. Новый checklist ID и base_version=0 создают v1. Для существующего ID base_version должен совпадать с последней версией; конфликт — 409. Новая версия доступна без рестарта, старые версии и оценки неизменны.

Апелляция и решение используют UUID `operation_id` и compare-and-swap через `expected_revision`. Review-case начинается с revision=0; каждая успешная мутация увеличивает revision. Повтор идентичной операции возвращает сохранённый ответ, включая его прежнюю revision; для актуального состояния нужен GET. Другой payload под тем же operation_id либо устаревшая revision — 409. Идентификаторы критерия и сообщений проверяются по исходному snapshot.

`original` — неизменный evaluation-record, `final` — отдельно сохранённые criteria и scoring. `accepted` требует override одного критерия со status=scored и допустимыми значением/evidence; `rejected` запрещает override. Backend пересчитывает final по исходному чек-листу. `review_status` отражает жизненный цикл обращений (`pending|appealed|resolved`), а не качество ML: при resolved итог по-прежнему может быть needs_review. Старый record без snapshot читается, но пересмотр и чтение снимка возвращают 409. `reviewer_id` задаётся локальной серверной конфигурацией; аутентификация ещё не реализована.

## Аудио и граница ASR

Upload принимает raw `audio/wav` (также `audio/x-wav`), `audio/mpeg`, `audio/flac`, `audio/ogg`, отдельный лимит по умолчанию 25 MiB. Остальные JSON-тела ограничены 1 MiB. Имена клиента не используются как пути, повтор UUID `Idempotency-Key` с другим MIME/содержимым даёт 409. `duration_seconds` может быть null для сжатых форматов; проверка сигнатуры не гарантирует успешное декодирование. Внешние ссылки аудио не скачиваются автоматически.

ASR выключен, пока не задан `ASR_URL` с plain HTTP loopback origin. Без ASR_URL `POST /api/v1/transcriptions` возвращает 503, сохраняя ранее загруженный asset. Текущий ML не реализует `/v1/transcribe`; транспорт и очередь Backend не подтверждают наличие распознавания.

Wire-контракт будущего локального ASR: `POST /v1/transcribe`, multipart file-поле `file` с каноническим MIME и полями `language` (`RU|KZ|EN`), `audio_sha256`. Ответ 200 с JSON `transcription-result`, связанный с теми же SHA-256 и языком. Provider/model — короткие идентификаторы без URL, runtime отклоняет stub/mock/fixture/demo/unconfigured. Сегменты содержат speaker (`client|operator|unknown`), текст и конечные смещения `0 <= start_seconds < end_seconds <= 3600`; начала не убывают, при известной длительности конец не выходит за неё с допуском 0.05 с. До 10000 сегментов, 65536 символов в одном и 1 млн в сумме. Ответ ограничен 2 MiB; redirects, environment proxies и compressed response отключены. Таймаут по умолчанию 300 секунд.

Job проходит queued → running → completed/needs_review/failed. Один worker на backend process получает DB lease; глобальная backlog ограничена 100 queued/running. Истёкшая lease становится failed/interrupted при сканировании, автоматического retry нет. При любом unknown conversation ещё не создаётся. Проверяющий отправляет `transcription-review` с UUID operation_id и ролями client/operator для **всех** сегментов в исходном порядке; после подтверждения появляется voice_call conversation с offset_seconds. Ошибка провайдера не создаёт фиктивный транскрипт или оценку.

## Семантика

- `schema_version=1.0.0` обозначает формат обмена. `checklist.version` — отдельная целочисленная версия содержания чек-листа. Оценку нельзя пересчитывать с тихо изменённой версией.
- Нормализованный канал: `agent_chat` или `voice_call`; языки `RU`, `KZ`, `EN`. Внешний `conversation_id` становится `conversation.id`. `transcript` становится `messages`.
- В исходном API каналы имеют пробелы: `agent chat` / `voice call`. Преобразование выполняется только адаптером Backend. Не менять исходные `timestamp`; `offset_seconds` — отдельное поле для навигации.
- Идентификаторы реплик создаёт Backend детерминированно в порядке исходного массива (`m0001`, `m0002`, ...), не по тексту. Они уникальны в разговоре и сохраняются при оценке и апелляции. Связь доказательства всегда пара `(conversation_id, message_id)`.
- Для ISO-времени звонка нужен подтверждённый origin аудиозаписи. Не считать автоматически начало транскрипта началом файла. Если привязка неизвестна, `offset_seconds=null`, UI открывает запись без обещания точного seek.
- ML оценивает только оператора. Реплики client/bot/system служат контекстом, могут быть доказательством, но действия бота не засчитываются оператору.
- `status=scored,value=null` означает N/A и допустимо только по `allow_na` + `na_condition`; все доказательства имеют kind `not_applicable`, объяснение указывает условие.
- `status=needs_review,value=null` означает неопределённость/неподключённую модель. Это не N/A; конечный итог не вычисляется. Нельзя повысить балл, исключая неизвестные критерии.
- Каждое решение содержит непустое объяснение и доказательство с существующими `message_ids`. `missing_action` указывает участок, где действие ожидалось; `uncertain` — проверенный участок при недостатке оснований. UI восстанавливает цитату из сохранённого транскрипта: свободные вымышленные цитаты не нужны.
- При `needs_review` все evidence имеют kind `uncertain`; при `scored` kind `uncertain` запрещён, в том числе рядом с N/A. Неопределённое доказательство не может давать финальный балл.
- Нельзя дублировать критерии/сообщения, пропускать критерии из ответа, ссылаться на чужие ID, выдавать `true` за числовую ступень. JSON Schema проверяет форму, сервисы дополнительно проверяют эти отношения.
- Набор критериев динамический: новые `id` не требуют ветвлений в исходном коде. `scale` хранит разрешённые ступени; значение обязано быть одной из них. Максимум шкалы должен быть положительным.

## Расчёт, за который отвечает Backend

Для применимых критериев с весом: `ratio=1/0` для boolean, `ratio=value/max(scale)` для scale. `A` — сумма их весов. Итог: `round_half_up(100 × sum(weight × ratio) / A, 2)`. Округляется конечный результат, не промежуточные доли.

У шестого критерия максимум шкалы **10**, вес **15**: 7/10 даёт 10.5 балла до нормировки. N/A убирает вес из знаменателя. При `A=0` итог `null` и `not_calculable`. Грубые ошибки имеют тип `flag`, вес 0, отображаются отдельно и не обнуляют итог.

`criteria.points` — нормированный вклад в общий итог; `effective_weight` — вес после перераспределения до 100. У N/A оба `null`; у flag оба 0. Отображаемые округлённые вклады могут суммироваться в 99.99/100.01 — итог берётся из неокруглённых долей. При неопределённых решениях отображение вкладов не должно создавать впечатление финального итога.

`sections[].score` — локальная нормировка секции до 100, а не вклад секции в общий итог. Секция только с флагами/без применимого веса получает `null`. Это проектное решение: ТЗ требует секции, но не задаёт способ их агрегации.

`has_gross_error=true`, если есть подтверждённый flag=true; `null`, если такого нет, но флаг ещё не проверен; иначе false. Пока хотя бы один критерий не решён, запись остаётся `needs_review`.

## Дополнение аудита

`POST /v1/audit` возвращает `audit-bundle` с совместимым `evaluation-response`, результатами правил Sentinel, снимком версий, проверенными цитатами и фактическим временем. Штатный `POST /v1/evaluate` сохраняет прежний формат. Дополнительные поля сообщений при ручном импорте необязательны. Событие `identity_verified` принимается только для системной роли. Фактический источник сохраняет исходный текст и порядок реплик, а неизвестное время остаётся null.

`GET /api/v1/evaluations/{id}/audit` возвращает `audit-export` с исходной и действующей оценками, историей автора/времени/доказательств и отдельным результатом эксперимента. `POST /api/v1/evaluations/{id}/versions/{version}` принимает пустой JSON-объект и возвращает новую неизменяемую запись. Версия с одними изменёнными весами переиспользует решения. [Настройка режимов](../docs/EVA_AUDIT.md).

## Что ещё предстоит согласовать

Frontend ещё подключает новые сценарии. ML-владелец выбирает и реализует реальный локальный ASR, команда проверяет язык/роли/смещения и измеряет latency реальной оценки. Backend оставляет 20 секунд на ожидание ML-слота и inference, Frontend — 30 секунд на запрос; это текущие ограничения, не подтверждённая производительность модели. Долговечные задания оценки, аутентификация, права, удаление/retention и перенос данных между БД требуют отдельных решений.

Корневой README и часть `docs/ARCHITECTURE.md` пока отстают от этой поставки. Актуальные команды, миграции и ограничения приведены в `backend/README.md`.

Изменение общего контракта: оценить совместимость → для существенных изменений согласовать решение → обновить схему, примеры и затронутых потребителей → проверить результат. Небольшие совместимые уточнения можно выполнить в текущей задаче. Нерешённые вопросы фиксируются в CHANGE_REQUESTS.md; ломающие изменения требуют новой версии. Примеры проверяются tools/check_contracts.py.
