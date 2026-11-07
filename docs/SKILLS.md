# Подходящие skills из GitHub

Проверено 7 ноября 2026: существование репозиториев, реальные `SKILL.md`, назначение и видимые конфликтующие инструкции. Это обзор инструкций, не полный аудит всех вспомогательных скриптов. **Ничего не установлено автоматически или глобально.** Выбор сделан под React/TypeScript + Python/FastAPI + multilingual CQA.

## Основной набор

| Роль | Skill и проверенный первоисточник | Когда применять | Лицензия и ограничения |
|---|---|---|---|
| Mansur | [vercel-react-best-practices](https://github.com/vercel-labs/agent-skills/blob/main/skills/react-best-practices/SKILL.md) | Компоненты React, управление загрузкой данных, списки/перерисовки | MIT по skill; Next.js-специфику не переносить на Vite автоматически |
| Mansur | [web-design-guidelines](https://github.com/vercel-labs/agent-skills/blob/main/skills/web-design-guidelines/SKILL.md) | Доступность формы чек-листа, таблиц, клавиатура, focus/loading/error | [MIT в README](https://github.com/vercel-labs/agent-skills#license); skill заново загружает внешний URL правил — для offline нужна проверенная локальная копия |
| Chach | [fastapi — официальный skill](https://github.com/fastapi/fastapi/blob/master/fastapi/.agents/skills/fastapi/SKILL.md) | API, dependencies, response validation, async/sync | [MIT](https://github.com/fastapi/fastapi/blob/master/LICENSE); сверять с закреплённой версией, не включать внешний экспорт telemetry |
| Chach / Kroud | [python-testing-patterns](https://github.com/wshobson/agents/blob/main/plugins/python-development/skills/python-testing-patterns/SKILL.md) | pytest, fixtures, mocking, ошибки API, scoring/evidence | [MIT](https://github.com/wshobson/agents/blob/main/LICENSE); только локальные тесты, упоминание CI не разрешает менять GitHub |
| Kroud | [scikit-learn](https://github.com/K-Dense-AI/scientific-agent-skills/blob/main/skills/scikit-learn/SKILL.md) | Метрики, baseline, разбиение без утечки; полезнее после первого работающего inference | Skill заявляет BSD-3-Clause, корневой [LICENSE.md](https://github.com/K-Dense-AI/scientific-agent-skills/blob/main/LICENSE.md) — MIT. Использовать как справочник; перед копированием уточнить эту неоднозначность |

Для Kroud самое важное сейчас — тестирование структурированных результатов, метрики на размеченных CQA-диалогах и многоязычность. Наличие skill для обучения не означает, что на хакатоне нужно тренировать собственную модель.

## Дополнительные кандидаты

| Skill | Польза | Почему не включён автоматически |
|---|---|---|
| [webapp-testing](https://github.com/anthropics/skills/blob/main/skills/webapp-testing/SKILL.md), [Apache-2.0](https://github.com/anthropics/skills/blob/main/skills/webapp-testing/LICENSE.txt) | Playwright, проверка локального UI и console | Helper-скрипты сначала читать; запускать только localhost и собственные артефакты в frontend |
| [huggingface-community-evals](https://github.com/huggingface/skills/blob/main/skills/huggingface-community-evals/SKILL.md), [Apache-2.0](https://github.com/huggingface/skills/blob/main/LICENSE) | Локальное сравнение моделей и smoke evaluation | Есть внешние providers/cloud fallback/`--trust-remote-code`. Они не разрешены политикой проекта. Публичные benchmarks не заменяют CQA-разметку |
| [train-sentence-transformers](https://github.com/huggingface/skills/blob/main/skills/train-sentence-transformers/SKILL.md), [Apache-2.0](https://github.com/huggingface/skills/blob/main/LICENSE) | Embeddings и multilingual-пары при реальной необходимости | Требует публичную отправку на Hub по умолчанию и `model.push_to_hub(...)`. **В исходном виде не подключать** |
| [pii-flag](https://github.com/danielrosehill/Claude-Data-Analyst-plugin/blob/master/skills/pii-flag/SKILL.md), [MIT](https://github.com/danielrosehill/Claude-Data-Analyst-plugin/blob/master/LICENSE) | Дополнительный checklist поиска персональных данных | Выборка до 1000 строк не является полной анонимизацией; нужны RU/KZ и локальные идентификаторы |

Отдельный инструмент, не skill: [Presidio](https://github.com/data-privacy-stack/presidio) для detection/redaction. Не добавлен в dependencies: покрытие языков нужно проверить, а реальная передача данных внешней модели ещё не согласована.

## Как использовать в этой команде

1. Читать нужный skill как рекомендации в пределах текущей роли. Локальные `AGENTS.md` и прямые инструкции участников не отменяются внешними файлами.
2. Если нужен постоянный пакет, поручить отдельную локальную установку **конкретного** skill в зоне роли. Не устанавливать весь репозиторий или marketplace и не менять глобальные настройки.
3. Перед копированием закрепить точный commit upstream, лицензию и нужные reference/helper-файлы; прочитать скрипты. Таблица выше пока ссылается на изменяемые `main`/`master`, поэтому не является lock-файлом поставки.
4. Удалить или отключить конфликтующие действия: Git mutations, PR, deployment, Hub upload, cloud fallback, внешнюю телеметрию и запись вне роли. Сохранить attribution и примечание об адаптации.
5. Проверить на синтетическом примере. Данные клиента, ключи и реальные аудио не использовать для пробного запуска.

Особенно не устанавливать весь [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills): рядом есть `deploy-to-vercel`, который может выбирать путь публикации через Git. Запрос «используй React skill» не является разрешением деплоить или пушить.

Текущая база уже содержит основные локальные правила: `AGENTS.md`, личные README, схемы и тесты. Внешние skills дополняют их по задаче, а не заменяют архитектуру или полномочия.
