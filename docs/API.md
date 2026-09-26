# Контракты MVP

Все ответы — JSON. Каждая защищённая ручка требует `Authorization: Bearer <token>`. Токены задаются отдельно для ролей `source`, `viewer`, `controller`, `admin`. Транспорт — локальный HTTP для демо, не промышленный контракт.

| Метод и путь | Роль | Смысл |
|---|---|---|
| `GET /health` | не требуется | Доступность процесса, не проверка зависимостей |
| `GET /api/me` | viewer/controller/admin | Роль текущего токена для интерфейса |
| `POST /api/events` | source/admin | Принять одно событие |
| `POST /api/events/batch` | source/admin | Атомарно принять массив 1–100 событий (не более 1 МиБ); ответ `202` содержит упорядоченный массив `results` со статусом каждого события. Ошибка идентичности откатывает весь пакет. Карантин и конфликт фиксируются как отдельные результаты, как и при одиночном приёме. |
| `GET /api/items` | viewer/controller/admin | Изделия с числом сообщений и случаев |
| `GET /api/line` | viewer/controller/admin | Состояние каждого изделия без автоматического допуска |
| `GET /api/items/{item_id}` | viewer/controller/admin | Хронология в порядке `occurred_at`, случаи и решения |
| `GET /api/checkpoints` | viewer/controller/admin | Ожидаемые точки, наблюдения и журнал оповещений |
| `POST /api/checkpoints/scan` | admin | Явно проверить сроки; тело `{}` или `{"as_of":"..."}` |
| `GET /api/cases` | viewer/controller/admin | Случаи и история решений |
| `POST /api/cases/{case_id}/decisions` | controller/admin | Добавить решение; `expected_version` и `idempotency_key` обязательны |
| `GET /api/metrics` | viewer/controller/admin | Основные счётчики, версия правил |
| `GET /api/outbox` | viewer/controller/admin | Состояние исходящих результатов |
| `GET /api/integrity` | admin | Расшифровать и проверить имеющиеся записи |

Минимальный конверт входа:

```json
{
  "schema_version": 1,
  "source_id": "demo-camera",
  "event_id": "camera-42",
  "item_id": "I-003",
  "event_type": "InspectionReported",
  "occurred_at": "2026-09-25T10:11:00+03:00",
  "station_id": "A",
  "payload": {
    "inspection_result": "signs_detected",
    "observation_quality": "good",
    "defects": [{"type": "weld_anomaly", "area": "seam-A"}]
  }
}
```

`inspection_result`: `signs_detected`, `no_signs_detected`, `unable_to_assess`; `observation_quality`: `good`, `poor`, `unknown`. Отсутствующее качество трактуется как `unknown`, поэтому отрицательный результат без `good` не увеличивает число проверенных изделий. Отсутствие сигнала не означает годность. `occurred_at` обязан содержать смещение часового пояса.

Только наблюдение с `observation_quality=good` может создать случай для решения контролёра. Сигнал при `poor`/`unknown` остаётся в исходном журнале и истории, но не становится подтверждаемым случаем. `WorkOrderReceived` может содержать массив `checkpoints` с уникальными `id`, `station_id`, типом `IncomingInspectionCompleted` или `InspectionReported` и `due_at` с часовым поясом. Проверка сроков выполняется явно при демозапуске или через `POST /api/checkpoints/scan`; фоновый планировщик включается положительным `QC_CHECKPOINT_SCAN_INTERVAL`. Позднее пригодное наблюдение переводит текущий статус в `observed`, а переходы `missing → resolved` сохраняются в истории оповещений.

События операции требуют `payload.operation_run_id`; `ReworkStarted` дополнительно требует `previous_run_id`. История возвращает `operation_runs` с полным и активным интервалами в секундах. Если начало, конец или корректная пара пауза/возобновление отсутствуют, длительность остаётся `null` и возвращается `incomplete_reason`.

Идентичный повтор `(source_id,event_id)` возвращает `state=duplicate`; изменённый повтор — HTTP 409 и `state=conflict`. Неизвестная версия/тип или неправильный состав `payload` сохраняется зашифрованным с `state=quarantined`, причиной и без применения бизнес-правил. Без пары идентификаторов источник/сообщение журналировать сообщение пока невозможно: запрос отклоняется до сохранения.

Команда решения:

```json
{
  "action": "confirmed",
  "actor": "controller-1",
  "reason": "Проверено по первичному снимку и измерению",
  "expected_version": 1,
  "idempotency_key": "decision-I-003-1"
}
```

Решение добавляет отдельную запись и исходящее сообщение в одной транзакции. Повтор с тем же ключом возвращает прежний результат; та же карточка со старой версией даёт 409. Действия `confirmed`, `rejected`, `needs_more_inspection` не означают автоматически установленную причину дефекта.

Эмулятор: `GET /work-orders`, `POST /results`, `GET /receipts`, `POST /control/fail-next`, с отдельным интеграционным токеном. Внешние задания содержат `external_order_id`, `version`, `item_id`, `item_type`, `route`; результат — `message_id`, `schema_version`, `item_id`, `case_id`, `decision_id`, `status`, `at`, а при импорте задания — его внешний ID/версию. Повтор `message_id` не создаёт вторую квитанцию.
