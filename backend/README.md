# Chach · Backend

Работаем в `main`; основная зона ответственности — `backend/`. Небольшие необходимые правки общих или соседних файлов допустимы по корневому AGENTS.md. Существенные межкомандные изменения согласуем, Git/GitHub по умолчанию обслуживают люди.

Backend хранит разговоры и версии чек-листов, проверяет ML-ответ, самостоятельно рассчитывает баллы и сохраняет неизменяемые входы каждой новой оценки. Основная БД — локальный PostgreSQL через SQLAlchemy и psycopg. SQLite доступен явно для изолированных тестов и прежних локальных файлов; автоматического fallback нет.

## Запуск

Команды выполняются из `backend/`. Нужны Python 3.11+ и отдельный PostgreSQL. Проверенное Python-окружение — CPython 3.13 / Windows x64; полный снимок зависимостей находится в `requirements.lock`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
```

Для новой локальной БД подготовлен `compose.yaml` с PostgreSQL 18 и портом `127.0.0.1:55432`. Данные находятся в игнорируемой `backend/data/postgres/`. Команда ниже предназначена для явного запуска разработчиком; приложение само Docker не запускает.

```powershell
docker compose up -d postgres
$env:DATABASE_URL = 'postgresql+psycopg://eva:eva-local-dev@127.0.0.1:55432/eva'
.\.venv\Scripts\python.exe -m app.cli db-upgrade
.\.venv\Scripts\python.exe -m app.cli seed-checklist
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`eva-local-dev` — только пароль локальной разработки. При его изменении согласуйте `EVA_POSTGRES_PASSWORD` для Compose и `DATABASE_URL` для backend. `.env.example` — справочник: API и CLI не загружают его автоматически. Compose использует собственные стандартные правила окружения.

API находится на `http://127.0.0.1:8000`, документация — `/docs`; Swagger/OpenAPI генерируется из канонических схем с локальным разрешением `$ref`. ML коллеги запускается отдельно на `127.0.0.1:8001`. Проверки интеграции выполнены на PostgreSQL 15.8; образ PostgreSQL 18 из Compose в этой сессии не запускался. Сеть приложения остаётся локальной; авторизация ещё не реализована.

## Импорт разговоров

Импорт выполняется отдельной явной командой. При запуске API наборы и аудио не скачиваются. Первоначальный чек-лист загружается через `seed-checklist`; это конфигурация критериев, без деморазговора и фиктивного ML-ответа.

Из локального файла внутри игнорируемой `backend/data/`:

```powershell
.\.venv\Scripts\python.exe -m app.cli import-dataset --file data/dev.json --group DEV
```

Из API организаторов — только после явного выбора источника разработчиком. Подставьте полученную от команды подписанную ссылку в переменную процесса; не записывайте её в код, README или историю общих команд.

```powershell
$env:EVA_DATASET_URL = '<подписанная HTTPS-ссылка организаторов>'
.\.venv\Scripts\python.exe -m app.cli import-dataset --url-env EVA_DATASET_URL --group DEV
Remove-Item Env:EVA_DATASET_URL
```

Разрешён только HTTPS-домен Power Platform организаторов. Proxy из окружения и redirects отключены, тело ограничено 10 MiB, общий сетевой бюджет — 30 секунд. URL и сырые диалоги не печатаются. Ссылки аудио сохраняются как данные и не открываются.

Адаптер принимает полный envelope `error/isSuccess/data`, точные поля записей и реплик и `total_records == len(records)`. `error` при успехе допускает `null` или пустую строку. Pagination и новые поля требуют явной адаптации; неизвестный формат отклоняется. Каналы нормализуются, исходные timestamps сохраняются, ID реплик создаются по исходному порядку. Для звонка без audio origin `offset_seconds=null`.

Весь набор проверяется до записи. Сохранение атомарное: ошибка или конфликт откатывает весь импорт. Повтор того же набора оставляет данные неизменными. Изменённое содержимое либо другая группа под прежним conversation ID дают конфликт; ID не переименовываются и данные не перезаписываются. Raw record, group и hash хранятся внутренне.

Для синтетического локального сценария есть отдельная команда:

```powershell
.\.venv\Scripts\python.exe -m app.cli seed-demo
```

Она явно добавляет синтетический разговор и чек-лист. `SEED_DEMO=true` делает то же при старте; значение по умолчанию — `false`. Без импорта списки разговоров пусты. Недоступность ML остаётся ошибкой, stub остаётся `needs_review`; фиктивной успешной оценки backend не создаёт.

## HTTP-контракт

| Метод | Поведение |
|---|---|
| `GET /health` | Диагностика процесса, без обещания доступности или качества ML |
| `GET /api/v1/conversations` | Сохранённые нормализованные разговоры |
| `POST /api/v1/conversations` | `conversation-create` → `conversation`, 201; ручной ввод переписки или готового транскрипта |
| `GET /api/v1/conversations/{id}` | Один сохранённый разговор |
| `GET /api/v1/checklists` | Все сохранённые версии чек-листов |
| `POST /api/v1/checklists/{id}/versions` | `checklist-publish` → `checklist`, 201; новая неизменяемая версия |
| `POST /api/v1/evaluations` | Выбор сохранённых входов, ML, проверка, расчёт, атомарное сохранение; 201 |
| `GET /api/v1/evaluations` | `evaluation-list`: `items`, `next_cursor`; фильтр `conversation_id`, `limit` 1–100, opaque `cursor` |
| `GET /api/v1/evaluations/{id}` | Исходный сохранённый record |
| `GET /api/v1/evaluations/{id}/snapshot` | Исходный `evaluation-request`; 409 для старой записи без снимка |
| `GET /api/v1/review-cases/{evaluation_id}` | `review-case`: revision, original, final, appeals, decisions |
| `POST /api/v1/evaluations/{id}/appeals` | `appeal-create` → `review-case`, 201 |
| `POST /api/v1/appeals/{id}/decisions` | `appeal-decision` → `review-case`, 201 |
| `POST /api/v1/audio-assets` | Raw WAV/MP3/FLAC/Ogg + заголовок `Idempotency-Key: <UUID>` → `audio-asset`, 201 |
| `GET /api/v1/audio-assets/{id}` | Метаданные локальной записи |
| `GET /api/v1/audio-assets/{id}/content` | Воспроизведение локальной записи, Range 206/416 |
| `POST /api/v1/transcriptions` | `transcription-create` → `transcription-job`, 202; требует настроенного ASR |
| `GET /api/v1/transcriptions/{id}` | Состояние сохранённого задания |
| `POST /api/v1/transcriptions/{id}/review` | `transcription-review` → `transcription-job`; подтверждение ролей сегментов |

Тело создания не изменилось:

```json
{"conversation_id":"demo-ru-001","checklist_id":"freedom-cqa","checklist_version":1}
```

Неизвестный ID — 404; неизвестная версия существующего чек-листа — 409; невалидный ввод — 422; недоступность/общий timeout ML — 503; невалидный результат ML — 502. Ошибки возвращаются без SQL, credentials, входного JSON и содержимого диалогов. Некорректный JSON, UTF-8, повторённые ключи и NaN/Infinity отклоняются с 400; превышение тела — 413, неподдерживаемое кодирование тела — 415. Сервер создаёт собственный `X-Request-ID`, в том числе для ошибок, не доверяя присланному ID.

Превышение контекста модели возвращает 422 с фиксированным `code=ml_context_limit_exceeded` и `detail="Conversation and checklist exceed the local model context. No evaluation was saved."`. Оно распознаётся только для ML HTTP 422 с точным JSON `{"error":"context_limit_exceeded"}`: максимум 4096 байт (либо меньший настроенный предел), JSON Content-Type, без сжатия, невалидного UTF-8, повторённых ключей или неограниченных чисел. Другие upstream 422 и дополнительные поля остаются общим 502, без пересылки их содержимого. Оценка не сохраняется; автоматического обрезания разговора и повтора нет.

Новые 11 схем и синтетические примеры перечислены в `contracts/README.md`. Старые успешные payload v1 сохранены. `conversation-create` содержит UUID `operation_id`, channel, language и сообщения `{timestamp,speaker,text}`; ID разговора получается детерминированно из operation_id, ID реплик — из порядка. Повтор с тем же содержимым возвращает тот же разговор; другое содержимое под прежним operation_id — 409. HTTP-загрузки полного organizer envelope пока нет: для него остаётся CLI.

`checklist-publish` содержит `base_version`, `name`, `criteria`. Для нового ID `base_version=0` создаёт v1; для существующего ID нужен номер последней версии. Устаревшая база — 409. Новый состав критериев читается из БД без рестарта. Оценка выбирает точные ID и версию; опубликованная версия не изменяется.

Апелляция и решение содержат UUID `operation_id` и `expected_revision`, взятую из review-case. Каждая успешная мутация увеличивает revision; конкурентный запрос со старой revision получает 409. Повтор того же operation_id с тем же содержимым возвращает сохранённый ответ операции; другое содержимое — 409. Это может быть прежняя revision: актуальное состояние получают отдельным GET. `accepted` требует `override` — канонический scored-результат одного критерия с валидными evidence; `rejected` не допускает override. Backend пересчитывает итог по сохранённому снимку. `original` и snapshot неизменны, `final` хранится отдельно. `REVIEWER_ID` задаёт локальную метку автора решения, без аутентификации или проверки прав.

## Локальное аудио и ASR

Upload сохраняет байты под серверным UUID в `backend/data/audio/`, размер, MIME и SHA-256. Поддерживаются `audio/wav` (также `audio/x-wav`), `audio/mpeg`, `audio/flac`, `audio/ogg`. Для WAV проверяются PCM-структура, полнота и длительность до часа; для сжатых форматов проверяется сигнатура, duration может быть null. Это не замена декодеру ASR. Имя файла клиента не используется как путь. Повтор Idempotency-Key с другим содержимым или MIME даёт 409. Перед чтением проверяются путь, размер и хеш; внешние аудиоссылки автоматически не скачиваются.

При пустом `ASR_URL` файл сохраняется, а запрос распознавания возвращает 503. Для обработки записей запустите локальный Whisper, укажите его адрес в `ASR_URL` и включите фоновые задания Backend. [Установка и запуск распознавания](../ml/asr/README.md).

Для отдельного локального провайдера требуется plain HTTP loopback origin в `ASR_URL`. Backend вызывает `POST /v1/transcribe`, multipart: файл в поле `file` с каноническим MIME, строковые поля `language` (`RU|KZ|EN`) и `audio_sha256`. Ответ 200, `application/json` или `application/*+json`, схема `transcription-result`:

```json
{"schema_version":"1.0.0","audio_sha256":"<sha256 исходного файла>","language":"RU","provider":"<реальный provider>","model":"<реальная model>","segments":[{"speaker":"unknown","text":"<распознанная речь>","start_seconds":0.0,"end_seconds":0.9}]}
```

Это описание wire-формата, не результат распознавания. SHA и язык должны совпадать с заданием; provider/model — короткие идентификаторы без URL, stub/mock/fixture/demo/unconfigured отклоняются. От 1 до 10000 сегментов, текст непустой; конечные смещения `0 <= start < end <= 3600`, начала не убывают. Для WAV конец не выходит за известную длительность с допуском 0.05 с. Суммарный текст ограничен 1 млн символов, один сегмент — 65536. ASR-ответ ограничен 2 MiB; redirects, environment proxies, compressed response и cloud fallback отключены.

Один worker на backend process обрабатывает задания по DB lease. Общая очередь ограничена 100 queued/running заданиями; при переполнении 503 + Retry-After. `ASR_TIMEOUT_SECONDS=300` ограничивает вызов, автоматических повторов распознавания нет. Истёкшая lease переводится в failed/interrupted после сканирования; завершённый результат не перезаписывается. Для новой попытки нужен новый operation_id. Для текущего локального запуска используйте один uvicorn worker: несколько backend processes увеличивают число одновременных ML/ASR-вызовов, глобального inference-ограничителя нет.

Из валидного результата с client/operator создаётся `voice_call` conversation с точными offset_seconds. Любой unknown переводит задание в `needs_review`, conversation ещё нет. Проверяющий отправляет `{operation_id,roles:["client","operator",...]}` в исходном порядке всех сегментов; после подтверждения создаётся разговор, готовый к обычной оценке. Ошибка/таймаут не создаёт фиктивного текста или оценки.

## Хранение и миграции

Alembic хранит версию схемы. API применяет миграции при старте, CLI `db-upgrade` позволяет выполнить их отдельно. PostgreSQL advisory lock сериализует миграторы; ожидание блокировки ограничено пятью секундами. SQLite использует короткую write-транзакцию. `0001_storage` вводит базовое хранение, `0002_workflows` — внутренние lookup-поля истории, апелляции, решения, revisions и receipts, `0003_media` — аудиометаданные, задания и подтверждение ролей. Автоматический downgrade этих миграций запрещён ради сохранности истории.

Внутренние таблицы: `conversations`, `checklist_versions`, `evaluations`, `evaluation_inputs`, `review_cases`, `appeals`, `review_decisions`, `operation_receipts`, `media_assets`, `transcription_jobs`, `transcription_reviews`. Приложение не перезаписывает существующую версию чек-листа, разговор или исходную оценку. Record и полный request snapshot новой оценки сохраняются одной транзакцией. Снимок содержит разговор и конкретный чек-лист; он не зависит от будущих версий. Во время вызова ML транзакция БД не удерживается. В публичном `evaluation-record` дополнительных полей нет. PostgreSQL row locks сериализуют пересмотр одной оценки; чтение review-case использует единый REPEATABLE READ snapshot.

Прежний `backend/data/eva.sqlite3` не удаляется. Для доступа к нему можно явно задать:

```powershell
$env:DATABASE_URL = 'sqlite:///C:/Users/Chach/Documents/GitHub/EVA/backend/data/eva.sqlite3'
```

Первая миграция принимает старую таблицу `evaluations` без переписывания payload. Старые записи не получают выдуманных снимков: у них внутренний snapshot отсутствует. Автоматического переноса старого SQLite-файла в PostgreSQL нет. Для новых оценок через API снимок сохраняется обязательно. SQLite-файлы конфигурации должны находиться внутри `backend/`.

## Расчёт и ML

Backend использует общие JSON Schema Draft 2020-12 и локальный registry без сетевой загрузки `$ref`. Дополнительно проверяются IDs, полнота критериев, типы, разрешённые ступени, N/A и evidence.

Для boolean `ratio=1/0`; для scale `ratio=value/max(scale)`. Итог: `100 × sum(weight × ratio) / sum(applicable weight)`. Decimal и ROUND_HALF_UP округляют опубликованные результаты до двух знаков, без суммирования заранее округлённых вкладов. 7/10 при весе 15 даёт 10.5 исходных взвешенных баллов.

N/A исключает разрешённый вес. Неопределённость не превращается в N/A: при pending итог и нормализованные вклады взвешенных критериев — null. Flags имеют вес 0 и не обнуляют итог. Секции нормируются отдельно; без применимого веса результат `not_calculable`.

`ML_TIMEOUT_SECONDS=20` задаёт общий бюджет ожидания ML-слота и вызова; HTTPX также ограничивает сетевые ожидания. Это не deadline всей HTTP-операции с чтением/сохранением БД. `ML_MAX_CONCURRENCY=1` и `EVALUATION_QUEUE_CAPACITY=4` допускают по умолчанию одну активную ML-оценку и ещё четыре принятых запроса; дальнейшие получают 503 + Retry-After. Ожидание синхронное, очередь не переживает restart. Дополнительные workers увеличивают общий предел. Автоматических повторов оценки и облачного fallback нет.

Тело JSON ограничено `REQUEST_MAX_BYTES=1048576` (1 MiB), в том числе без Content-Length; для raw upload отдельный `AUDIO_MAX_BYTES=26214400` (25 MiB). `ML_MAX_RESPONSE_BYTES=2097152` ограничивает ML-ответ 2 MiB. Ошибочный UTF-8, неограниченные числа, повторённые JSON-ключи и непарные Unicode surrogate отклоняются. Неожиданный ответ провайдера не сохраняется как оценка.

Frontend сейчас ожидает 30 секунд. Возможности выбранной модели, p95, отмена и достаточность общего 20-секундного ML-бюджета ещё требуют измерений; успешный health не подтверждает эти свойства.

## Диагностика данных и соединений

```powershell
.\.venv\Scripts\python.exe -m app.cli doctor
.\.venv\Scripts\python.exe -m app.cli audit-data
```

`doctor` выполняет DB SELECT 1 и ограниченный GET /health ML, не запускает inference и не применяет миграции. Печатает только безопасные статусы, проверенные provider/model и лимиты; код 0 — ready, 2 — degraded. Доступная БД не означает, что миграции или данные проверены. Последнее наблюдение в этой сессии: database=ready, ml=unavailable; реальная сквозная оценка этим не подтверждается.

`audit-data` валидирует сохранённые разговоры/чек-листы и печатает агрегаты без реплик. Проверенный локальный набор: 65 разговоров, 1268 сообщений, 60 agent_chat / 5 voice_call, RU 35 / KZ 15 / EN 15, 5 ссылок на аудио и 1 валидная версия чек-листа. Это проверка формы и отношений ID, без скачивания аудио или оценки качества модели.

## Проверки

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pip check
```

По умолчанию тесты используют synthetic fixtures, mock transport и изолированные SQLite-файлы внутри `backend/tests/.tmp/`. PostgreSQL-тесты пропускаются без явной переменной; это не подтверждение PostgreSQL-интеграции.

Для реальной проверки БД заранее создайте отдельную локальную базу с именем, заканчивающимся на `_test`:

```powershell
$env:EVA_TEST_DATABASE_URL = 'postgresql+psycopg://eva:eva-local-dev@127.0.0.1:55432/eva_backend_test'
.\.venv\Scripts\python.exe -m pytest -m postgres --basetemp=tests/.tmp-postgres
```

Эти проверки используют уникальные synthetic IDs, проверяют миграции, API/restart, snapshots, rollback и конкурирующие записи. Реальные DEV-данные не используются в fixtures. Не запускайте две pytest-сессии с одним `--basetemp`.

Результат финальной проверки 2026-11-07: **587 passed**, включая 10 opt-in PostgreSQL-тестов, без пропусков и падений. Единственное предупреждение — deprecation текущего TestClient/httpx; зависимости не менялись, `pip check` успешен. Проверены все синтетические contract examples, HTTP-лимиты, повторы, конкурирующие revisions, миграции, restart, аудио Range и ошибки ASR. После обновления локального сервера `/health`, `/docs` и история оценок возвращают 200; OpenAPI содержит 19 HTTP-операций и 18 канонических схем. Основная БД обновлена до `0003_media`; audit-data после миграции подтвердил прежние 65 разговоров. Doctor остаётся degraded: DB ready, ML unavailable. Реальные inference/ASR и нагрузочная производительность не подтверждены.

Ограничения хранения: новые timestamps имеют фиксированную точность до микросекунд; старые записи со смешанными форматами времени сохраняются как есть и могут требовать отдельной нормализации для строгой хронологической сортировки. Аварийное завершение между записью аудиофайла и commit БД может оставить файл без строки метаданных; API такой файл не публикует. Автоматическая очистка файлов и политика хранения пока не реализованы.

## Состав текущего пакета изменений

- **Код API и сценариев:** `app/main.py`, `app/config.py`, `app/http_boundary.py`, `app/openapi.py`, `app/workflow_api.py`, `app/workflows.py`, `app/media.py`, `app/media_api.py`, `app/transcription_client.py`, `app/transcription_worker.py`.
- **Хранение и диагностика:** `app/models.py`, `app/storage.py`, `app/ml_client.py`, `app/cli.py`, `app/diagnostics.py`; миграции `migrations/versions/0002_workflows.py`, `0003_media.py`.
- **Тесты:** `tests/test_api_reliability.py`, `test_checklist_runtime.py`, `test_diagnostics.py`, `test_http_boundary.py`, `test_openapi.py`, `test_workflow_api.py`, `test_workflows.py`, `test_media.py`, `test_media_api.py`, `test_transcription_client.py`, `test_ml_client.py`, обновлённые `test_storage.py`, `test_postgres.py`.
- **Контракты:** новые `.schema.json` в `contracts/schemas/` и одноимённые `.json` в `contracts/examples/`: `conversation-create`, `checklist-publish`, `evaluation-list`, `appeal-create`, `appeal-decision`, `review-case`, `audio-asset`, `transcription-create`, `transcription-job`, `transcription-result`, `transcription-review`.
- **Документация и настройки:** `backend/README.md`, `backend/.env.example`, `backend/CHANGE_REQUESTS.md`, `contracts/README.md`. Изменения общих инструкций и других документов коллег сохраняются отдельно от этого пакета.

## Следующие задачи

1. Frontend подключает новые маршруты, версии, историю, раздельные original/final, upload и подтверждение ролей.
2. ML-владелец предоставляет настоящий локальный ASR по описанному wire-контракту и исправляет сравнение числовых ступеней 20/20.0; см. `CHANGE_REQUESTS.md`.
3. Команда измеряет реальный evaluation runtime и согласует синхронный бюджет либо отдельные долговечные задания оценки. ML имеет ISSAI-адаптер; наличие исходников не подтверждает текущую доступность или качество сервиса.
4. Перед общим сетевым размещением отдельно нужны авторизация, права доступа, backup/restore и retention.

Общий корневой README и часть архитектурного описания ещё содержат старые сведения о SQLite/stub; актуальный backend запуск и границы готовности приведены здесь. Новый ASR transport и worker проверяются синтетически, настоящий speech provider пока отсутствует.
