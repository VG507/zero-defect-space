# Воспроизводимые сценарии S1–S12

Все данные синтетические. Проверка: `python -m unittest discover -s tests -v`. Сквозной маршрут импортирует задания по HTTP, применяет события, проверяет историю, контрольные точки, показатели и отправляет результаты в отдельный HTTP-эмулятор. Он не доказывает совместимость с промышленной системой.

| Сценарий из плана | Вход и проверяемый результат | Автоматизированное свидетельство |
|---|---|---|
| S1 Нормальное производство | I-001: входной контроль, две операции A/B, итоговый контроль; 240 и 300 с активного времени, случаев нет | `DemoTests.test_full_demo_route_with_separate_emulator` |
| S2 Входной дефект | I-002: признак до операции, решение confirmed, классификация `incoming_signal`, причина `unknown` | `DemoTests.test_full_demo_route_with_separate_emulator` |
| S3 Новый дефект | I-003: пригодное «без признаков» до операции, затем признак; классификация «новый после последнего контроля», но момент возникновения не доказан | `DemoTests.test_full_demo_route_with_separate_emulator`, `DemoTests.test_synthetic_scenario_expected_results` |
| S4 Неопределённость | I-004: `unable_to_assess`/`poor`, просроченная контрольная точка, отдельное состояние `missing_control`; плохой сигнал не создаёт подтверждаемого случая | `DemoTests.test_full_demo_route_with_separate_emulator`, `CoreTests.test_poor_quality_signal_is_not_a_confirmable_case`, `CoreTests.test_checkpoint_missing_then_resolved_by_late_usable_observation` |
| S5 Станок | Предупреждение W-01 и действие оператора связаны с R-003-A и видны рядом с признаком; `cause_status=unknown` | `DemoTests.test_full_demo_route_with_separate_emulator` |
| S6 Повторная доставка | Точное сообщение повторно: `duplicate`, одна запись; изменённый повтор: `conflict` и отдельный след | `CoreTests.test_dedup_conflict_and_quarantine`, `BridgeTests.test_bidirectional_failure_retry_and_idempotency` |
| S7 Позднее событие | I-006: ранний входной факт поступает после решения; история сортируется по времени события, случай сохраняет ID/решение, классификация пересчитывается, выставлен пересмотр | `DemoTests.test_full_demo_route_with_separate_emulator`, `CoreTests.test_late_incoming_evidence_reclassifies_without_losing_decision` |
| S8 Изменённое решение | Подтверждение → отклонение: две записи решения, версия увеличивается, число подтверждённых случаев падает | `CoreTests.test_multiple_defects_and_changed_decision`, `tests/browser_smoke.js` |
| S9 Несколько дефектов | Один сигнал с двумя типами: два случая; повторное наблюдение одного не добавляет третий | `CoreTests.test_multiple_defects_and_changed_decision` |
| S10 Повторная обработка | I-005: подтверждённый случай, затем два `operation_run`, второй со ссылкой `previous_run_id`; прежнее решение сохранено, после повторной операции назначена дополнительная проверка, допуска нет | `DemoTests.test_full_demo_route_with_separate_emulator`, `CoreTests.test_operation_intervals_rework_and_incomplete_boundary` |
| S11 Интеграционная ошибка | Отдельный HTTP-процесс отвечает 503, outbox хранит ошибку; retry того же результата получает ACK и одну квитанцию | `BridgeTests.test_bidirectional_failure_retry_and_idempotency` |
| S12 Вмешательство | Изменение существующего шифротекста приводит к ошибке проверки AES-GCM | `CoreTests.test_late_order_and_tamper_detection` |

Граница проверок: S1–S5, S7 и S10 показаны совместно в стартовом UI; остальные варианты запускаются как независимые тесты. Новый эталон демо — 5 проверенных / 3 с последним решением `confirmed` / 1 без пригодного контроля. У I-006 есть одновременно `confirmed` и флаг обязательного пересмотра — это не окончательный допуск. Проверка целостности включает 39 исходных событий; решения и ACK считаются отдельно. Станочное предупреждение и действие оператора — временные обстоятельства, а не доказанная причина. Для промышленной достоверности нужны реальные источники, утверждённые правила сопоставления и испытания с заказчиком.
