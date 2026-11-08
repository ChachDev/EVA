# Распознавание звонков EVA

Локальный сервис принимает сохранённое аудио по контракту Backend и распознаёт RU, KZ и EN через faster-whisper small на CPU. Qwen получает текст для оценки оператора. Whisper возвращает реальные временные границы и роль `unknown`. Одноканальная расшифровка сама по себе не устанавливает, кто говорил. В разделе «Аудиозаписи» проверяющий сопоставляет реплики с записью, назначает роли и открывает голосовой диалог для оценки. Если уже есть подписанный транскрипт этой записи, им можно пользоваться при проверке.

Команды первоначальной установки из корня проекта в PowerShell:

```powershell
python -m venv ml/asr/.venv
ml/asr/.venv/Scripts/python.exe -m pip install -r ml/asr/requirements.txt
python ml/asr/download_model.py
```

Загрузчик проверяет четыре файла фиксированной ревизии открытой модели. При работе с аудио Whisper использует только локальные файлы. Поддерживаются WAV, MP3, FLAC и Ogg до 25 МиБ; сегменты ограничены часовым диапазоном существующего контракта. Для текущего стенда из каталога `ml`:

```powershell
./asr/.venv/Scripts/python.exe -m uvicorn eva_asr.app:app --host 127.0.0.1 --port 18602 --no-access-log
```

ML и Backend можно поднять из корня в отдельных терминалах. На уже запущенном стенде Qwen работает на 59174. При наличии этого worker добавьте `--engine-port 59174` к первой команде. Без внешнего worker ML сам загружает Qwen при запуске (переменная `EVA_LLAMA_WORKER_PORT` должна быть пустой).

```powershell
$env:EVA_AUDIT_PROFILE='baseline-plus'
ml/.venv/Scripts/python.exe ml/integration/audit_stack.py ml --port 18601 --run-dir ml/data/audit-components/final-integration
ml/.venv/Scripts/python.exe ml/integration/audit_stack.py backend --port 18600 --ml-port 18601 --asr-port 18602 --mode shadow --run-dir ml/data/audit-components/final-integration
```

Одинаковый каталог запуска сохраняет привязку к существующей локальной базе. Для общего запуска Backend с PostgreSQL задайте `ASR_URL=http://127.0.0.1:18602` и `ASR_TIMEOUT_SECONDS=600` перед стартом. После загрузки файла запрос распознавания создаёт задание в очереди; после проверки ролей запись доступна в истории диалогов с исходным аудио. Смена порта Qwen требует перезапуска ML с новым адресом.

Тесты внутри отдельного окружения из каталога `ml`: `./asr/.venv/Scripts/python.exe -m unittest discover -s asr/tests -v`. Библиотека и веса имеют собственные условия использования. [faster-whisper (MIT)](https://github.com/SYSTRAN/faster-whisper) · [Карточка Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small).
