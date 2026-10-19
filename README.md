# Квитто: платёжный REST API

Тестовое задание: Python 3.11+, FastAPI, Pydantic v2, PostgreSQL 16, синхронный
SQLAlchemy 2.0, psycopg 3, pytest и httpx. Дополнительно реализованы HMAC вебхуков,
GitHub Actions с Ruff и pytest, а также правила для ассистентов в AGENTS.md.

## Запуск в Windows PowerShell

Команды выполняются из корня репозитория. Нужны Python 3.11+ и работающий Docker
Desktop. Активация виртуального окружения не требуется, поэтому менять PowerShell
ExecutionPolicy не нужно.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test]'
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# Настройте WEBHOOK_SECRET в .env: пример содержит только демонстрационный ключ.
docker compose up -d --wait db
docker compose ps
.\.venv\Scripts\python.exe -m uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8000
```

Если `.venv` уже существует и создан на Python 3.11+, первый шаг можно пропустить.
Ожидаемый статус БД в `docker compose ps`: `healthy`. Приложение доступно по
`http://127.0.0.1:8000`, Swagger UI: `http://127.0.0.1:8000/docs`.
Остановить сервер можно сочетанием Ctrl+C.

Существующий `compose.yaml` запускает только PostgreSQL. API запускается на хосте,
а не внутри Docker. Подключение в `.env.example` соответствует этому Compose:
`127.0.0.1:5433`, пользователь `kvitto`, основная БД `kvitto`.
Локальный пароль из Compose предназначен для разработки. `.env` игнорируется Git.
Переменные окружения PowerShell имеют приоритет над значениями `.env`.

`WEBHOOK_SECRET` обязателен: при отсутствующем, пустом или состоящем только из
пробелов значении приложение завершает startup с явной ошибкой до подключения
к БД. Проверка подписи никогда не отключается автоматически. Значение
`demo-only-change-before-use` в `.env.example` предназначено только для локального
примера. Для реальных запросов задайте свой случайный секрет в игнорируемом `.env`
или окружении процесса; после изменения перезапустите приложение. Например,
сгенерировать значение в своей консоли можно командой:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

Не сохраняйте сгенерированный ключ в README, AI_LOG или других отслеживаемых файлах.

На старте создаются отсутствующие таблицы и добавляются тарифы. Повторный запуск
обновляет канонические цены по уникальному названию без создания дубликатов.
`create_all` не изменяет уже существующую структуру таблиц; миграции в обязательную
часть не входят. Для чистой БД используется один процесс инициализации приложения.

## Отдельная база и запуск тестов

Создать `kvitto_test` один раз, не меняя Compose и основную БД:

```powershell
$databaseExists = docker compose exec -T db psql -U kvitto -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = 'kvitto_test'"
if ($LASTEXITCODE -ne 0) { throw 'Не удалось проверить наличие тестовой БД' }
if (($databaseExists -join '').Trim() -ne '1') {
    docker compose exec -T db createdb -U kvitto -O kvitto kvitto_test
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать kvitto_test' }
}
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pip check
```

Тесты читают `TEST_DATABASE_URL` из окружения или `.env`. Значение по умолчанию:
`postgresql+psycopg://kvitto:kvitto_local@127.0.0.1:5433/kvitto_test`.
`DATABASE_URL` приложения не используется для тестового подключения.

Фикстура проверяет и имя в URL, и результат `SELECT current_database()` до создания
таблиц или очистки. Разрешена только БД `kvitto_test`, без query-параметров URL.
Перед каждым тестом и после него очищается таблица `payments` **только в тестовой
БД**. Не храните в ней нужные данные. Параллельный запуск нескольких pytest-процессов
с общей тестовой БД не поддерживается. Конкурентные HTTP-запросы внутри тестов
проверяются через независимые SQLAlchemy-сессии и соединения.

Покрыты деньги и промокоды, полный ответ платежа, все сроки рассрочки,
валидация, повторные и конкурентные запросы с ключом, все 16 пар статусов,
конкурирующие вебхуки, 404 и ограничения реальной PostgreSQL.
Прежние сценарии вебхуков теперь отправляются с действительной подписью. Тесты
создают временный случайный `WEBHOOK_SECRET` в памяти и не используют реальный
ключ разработчика. Отдельно проверяются корректные/некорректные подписи,
неизменность платежа при 401, исходные байты тела и отказ запуска без секрета.

## Примеры запросов в PowerShell

Используется `curl.exe`, чтобы избежать alias `curl` в Windows PowerShell 5.1.
JSON передаётся через stdin: это работает без особенностей экранирования кавычек
в аргументах нативных программ разных версий PowerShell.

```powershell
curl.exe -i http://127.0.0.1:8000/tariffs

$tariffs = Invoke-RestMethod http://127.0.0.1:8000/tariffs
$tariffId = ($tariffs | Where-Object { $_.title -eq 'standard' }).id
$body = @{
    tariff_id = $tariffId
    email = 'student@example.com'
    method = 'installment'
    installment_months = 3
    promo_code = 'kvitto10'
} | ConvertTo-Json -Compress

# Первый запрос: 201. Для каждого нового примера выбирайте новый ключ.
$body | curl.exe -sS -i -X POST http://127.0.0.1:8000/payments -H 'Content-Type: application/json' -H 'Idempotency-Key: powershell-demo-1' --data-binary '@-'

# Повтор: 200, тот же платёж. Его ID нужен для следующих запросов.
$payment = ($body | curl.exe -sS -X POST http://127.0.0.1:8000/payments -H 'Content-Type: application/json' -H 'Idempotency-Key: powershell-demo-1' --data-binary '@-') | ConvertFrom-Json
curl.exe -i "http://127.0.0.1:8000/payments/$($payment.id)"
```

## Подписанный банковский вебхук в PowerShell

`X-Signature` — 64 шестнадцатеричных символа HMAC-SHA256, без префикса `sha256=`.
Секрет кодируется в UTF-8. Проверяется исходное тело HTTP-запроса: изменение
пробела, порядка полей или завершающего перевода строки меняет подпись. Отсутствие,
повреждение, повтор заголовка или неверная подпись дают 401 до валидации JSON
и до доступа к платежу. При действительной подписи сохраняются 200/404/409/422
и исходные правила переходов; повтор уже установленного статуса возвращает 409.

Продолжите пример выше с существующим `$payment`. В этом PowerShell-процессе
задайте тот же `WEBHOOK_SECRET`, с которым запущен сервер. Значение ниже работает
только для неизменённого локального демонстрационного `.env.example`.
Для собственного ключа установите соответствующую переменную окружения локально.

Пример использует .NET HttpClient, чтобы **один и тот же byte[]** передавался
в HMAC и в HTTP-тело без перекодирования и добавления перевода строки конвейером.
Используются API .NET, доступные в Windows PowerShell 5.1 и PowerShell 7;
фактически пример проверен в доступном PowerShell 7.6.5.

```powershell
$env:WEBHOOK_SECRET = 'demo-only-change-before-use' # Только локальная демонстрация.
Add-Type -AssemblyName System.Net.Http

function Send-SignedWebhook([string]$JsonBody) {
    $utf8 = [System.Text.UTF8Encoding]::new($false)
    [byte[]]$bodyBytes = $utf8.GetBytes($JsonBody)
    $hmac = [System.Security.Cryptography.HMACSHA256]::new()
    $http = [System.Net.Http.HttpClient]::new()
    $content = [System.Net.Http.ByteArrayContent]::new($bodyBytes)
    $response = $null
    try {
        $hmac.Key = $utf8.GetBytes($env:WEBHOOK_SECRET)
        $signature = [System.BitConverter]::ToString($hmac.ComputeHash($bodyBytes)).Replace('-', '').ToLowerInvariant()
        $http.DefaultRequestHeaders.Add('X-Signature', $signature)
        $content.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::new('application/json')
        $response = $http.PostAsync('http://127.0.0.1:8000/webhooks/bank', $content).GetAwaiter().GetResult()
        '{0} {1}' -f [int]$response.StatusCode, $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
    }
    finally {
        if ($null -ne $response) { $response.Dispose() }
        $content.Dispose()
        $http.Dispose()
        $hmac.Dispose()
    }
}

$webhook = @{ payment_id = $payment.id; status = 'succeeded' } | ConvertTo-Json -Compress
Send-SignedWebhook $webhook # 200 {"result":"ok"}

# succeeded -> failed запрещён: 409 {"error":"invalid_transition"}.
$invalidWebhook = @{ payment_id = $payment.id; status = 'failed' } | ConvertTo-Json -Compress
Send-SignedWebhook $invalidWebhook
```

Для standard с `KVITTO10` итог: `amount = 1791000`, `discount = 199000`,
`schedule = [597000, 597000, 597000]`.
Без промокода: `amount = 1990000`, `discount = 0`,
`schedule = [663334, 663333, 663333]`.

## Контракт и принятые решения

| Маршрут | Результат |
| --- | --- |
| `GET /tariffs` | 200, список `{id, title, price}` |
| `POST /payments` | 201 для нового; 200 при повторе с ключом |
| `GET /payments/{id}` | 200 или 404 |
| `POST /webhooks/bank` | 401 при неверной подписи; с подписью — 200 `{"result":"ok"}`, 404, 409 `{"error":"invalid_transition"}` или 422 |

Платёж содержит `id`, `status`, `tariff_id`, `amount`, `discount`, `method`,
`installment_months`, `schedule`, `email`, `created_at`. Новый статус: `pending`.
Деньги в API и PostgreSQL — целые копейки (`BIGINT` в БД). `discount` — величина
скидки в копейках; `amount` — сумма после скидки. Промокод проверяется без учёта
регистра, но без удаления пробелов. Для указанных тарифов скидка 10% делится точно;
для гипотетической цены с остатком копейки скидка округляется вниз через `// 10`.

График вычисляется из сохранённых суммы и срока через `divmod`: первые `remainder`
платежей на копейку больше. Отдельной колонки графика нет, поэтому его невозможно
рассинхронизировать с суммой. Изменение цены тарифа не меняет старые платежи.

Тарифы имеют целочисленный ID, платежи — UUID. `created_at` хранится как
`TIMESTAMPTZ`, соединения используют UTC. Для `card`/`sbp` срок и график равны `null`;
переданный срок отклоняется с 422. Для рассрочки разрешены только целые 3, 6, 12.
Email проверяет `EmailStr` с явно установленным `email-validator`.
Неизвестный тариф при новом платеже — 404. Ошибки схем — 422 со стандартной
структурой FastAPI `{"detail": [...]}`. Обработчик только `RequestValidationError`
безопасно сериализует диагностические значения: нечисловые/бесконечные float
представляет строками, а Unicode-суррогаты экранирует. Правила Pydantic и обработка
настоящих серверных ошибок не изменяются.

Ключ идемпотентности опционален, регистрозависим, длина 1–255 символов. Без ключа
каждый POST создаёт новый платёж. Проверка существующего ключа происходит после
валидации схемы, но до поиска тарифа: валидный повтор с другим телом возвращает
первый платёж, даже если новый `tariff_id` не существует. Возвращается его текущее
состояние, включая статус. Если схема повтора невалидна, возвращается 422.

Предварительное чтение ключа ускоряет повтор, а защиту от гонки обеспечивает
`UNIQUE(idempotency_key)` и `INSERT ... ON CONFLICT DO NOTHING RETURNING`.
Проигравший конкурентный запрос читает победивший платёж отдельным SELECT при
`READ COMMITTED`. Ключ фиксируется вместе с платежом в одной транзакции.

Вебхук блокирует строку через `SELECT ... FOR UPDATE` до commit. Допустимы только
`pending -> succeeded`, `pending -> failed`, `succeeded -> refunded`.
Повтор того же статуса — запрещённый переход с 409 по буквальному условию ТЗ.
Запрещённый переход выполняет только чтение. Для проверки значений и денег
используются ограничения PostgreSQL, для переходов — проверка под блокировкой
в сервисе. Прямые SQL-изменения статуса в обход сервиса не проверяют переходы.

Архитектура: два ORM-класса, схемы Pydantic, один модуль сервисных операций,
сессия на запрос. Подтверждение транзакции происходит до успешного ответа API.
Alembic, фильтры списка платежей и контейнеризация API не реализованы.

## GitHub Actions

`.github/workflows/ci.yml` запускается на push и pull_request. Ubuntu runner
использует Python 3.13 и отдельный PostgreSQL 16 с healthcheck. В сервисе создаётся
`kvitto_test`; DATABASE_URL и TEST_DATABASE_URL направлены исключительно в неё.
Сначала выполняется `ruff check .`, затем полный `pytest`, затем `pip check`.
Любая ошибка останавливает job. PostgreSQL и окружение отдельные для каждого job;
конкурентные запросы внутри pytest используют настоящий сервер БД. Предупреждения
не подавляются. Секрет вебхука для тестов генерируется фикстурой в памяти, поэтому
для CI не нужен реальный WEBHOOK_SECRET или GitHub Secret.

## Проверки обязательной части до бонусов

19.10.2026: Windows PowerShell, Python 3.14.4, PostgreSQL 16, FastAPI 0.146.0,
Pydantic 2.14.1, SQLAlchemy 2.0.55, psycopg 3.3.6, pytest 9.1.1, httpx 0.28.1,
Starlette 0.52.1. Итоговый `python -m pytest -q`: **104 passed, 1 warning in 3.71s**
(исходные 96 случаев и 8 новых). Добавлены сырые JSON-запросы с числовым
переполнением, NaN и непарными Unicode-суррогатами, откат после выполненных
INSERT/UPDATE и конкурентный INSERT после отката первой транзакции.
`pip check`: зависимости согласованы.

Предупреждение относится к alias `anyio.abc.BlockingPortal` внутри тестового клиента
Starlette. Оно не подавляется и не является ошибкой теста. Для тестов закреплена
ветка Starlette `<1`, сохраняющая TestClient с httpx, требуемым заданием.

Приложение также запущено через Uvicorn и проверено по HTTP. Полный пример
`curl.exe` с созданием, повтором, скидкой, рассрочкой и вебхуками выполнен на
`kvitto_test`. В основной БД проверены startup, тарифы, OpenAPI, Swagger и 404/422;
тестовые платежи в ней не создавались. При попытке направить pytest на основную БД
сработал защитный отказ до инициализации/очистки.
После финальных исправлений оба проблемных сырых HTTP-запроса (`1e309` и
`"\ud800"`) повторены через Uvicorn на `kvitto_test`: оба вернули 422 с `detail`,
платежи не созданы.

## Проверки бонусной ветки


### Проверки бонусной реализации

19.10.2026 — первоначальная реализация HMAC, GitHub Actions и AGENTS.md.

На Python 3.14.4 и PostgreSQL 16 успешно пройдены 125 тестов.
Проверки Ruff и pip check завершились успешно.

### Финальная проверка после независимого аудита

19.10.2026 — устранена обнаруженная при аудите Astra уязвимость обхода HMAC при использовании `root_path`.

Проверка подписи перенесена в собственный `APIRoute`, что обеспечивает её выполнение после маршрутизации, но до обработки JSON.

Результаты локальных проверок:
- **172 passed, 1 warning** (pytest).
- **Ruff:** All checks passed.
- **pip check:** No broken requirements found.
- Проверены `root_path=""`, `/api` и `/api/v1`.
- Неподписанные и неверно подписанные вебхуки возвращают HTTP 401.
- Разрешённые переходы статусов работают; запрещённые возвращают HTTP 409.
- Конкурентные сценарии и откаты транзакций проходят успешно.

### GitHub Actions

Бонусная ветка `feat/bonus-hmac-ci` объединена с `main` через Pull Request #1.

GitHub Actions успешно выполнен на Python 3.13 с PostgreSQL 16:
- Ruff — успешно.
- Интеграционные тесты — успешно.
- Проверка зависимостей — успешно.

Результат CI для итоговой ветки `main`:
https://github.com/Baladeg/kvitto-payments-api/actions/runs/39249131633

Сохраняется одно предупреждение Starlette/AnyIO, не влияющее на успешность тестов.
