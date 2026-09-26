# Профили интеграционных адаптеров целевых систем

Документ формализует маппинг данных, протоколы обмена, правила сопоставления идентификаторов (ID Mapping) и обработку ошибок для взаимодействия с корпоративными системами заказчика (РКК «Энергия»).

---

## 1. 1С:ERP Управление предприятием

### Назначение
Импорт производственных заданий и маршрутных листов; экспорт актов брака и решений контролёра ОТК.

### Протокол и транспорт
* **Транспорт:** HTTPS REST API (JSON / OData).
* **Аутентификация:** Mutual TLS + Basic/OAuth 2.0.

### Таблица сопоставления сущностей (Mapping)

| Поле 1С:ERP | Поле Zero Defect Space | Тип данных | Описание |
|---|---|---|---|
| `ЗаказНаПроизводство.Номер` | `payload.external_order_id` | String | Номер заказа |
| `ЗаказНаПроизводство.Версия` | `payload.version` | Integer | Версия технологической карты |
| `ШтрихкодНоменклатуры` / `СерийныйНомер` | `event.item_id` | String | Уникальный код детали/изделия |
| `ТехнологическаяОперация.Код` | `payload.operation` | String | Код операции (токарная, фрезерная и т.д.) |
| `РабочийЦентр.Код` | `event.station_id` | String | Идентификатор рабочего центра / станка |
| `СтатусКонтроля` | `outbox.payload.status` | Enum | `accepted` / `defect_confirmed` / `needs_inspection` |

### Пример трансляции входящего заказа (JSON из 1C)
```json
{
  "doc_type": "1C_ProductionOrder",
  "doc_number": "1C-PRD-2026-904",
  "item_code": "DET-TURBINE-042",
  "route_points": ["ST-INCOMING", "ST-MILL", "ST-QC-FINAL"],
  "checkpoints": [
    {"checkpoint_id": "CP-1", "station": "ST-INCOMING", "type": "IncomingInspectionCompleted", "due": "2026-09-25T11:00:00+03:00"}
  ]
}
```

---

## 2. «Галактика:ERP»

### Назначение
Синхронизация партий деталей, сопроводительных паспортов изделий и статусов дефектовки.

### Таблица сопоставления сущностей

| Таблица / Поле Галактика | Поле Zero Defect Space | Назначение |
|---|---|---|
| `KATMC.BARKOD` | `item_id` | Уникальный идентификатор ТМЦ |
| `SPORDER.NMATER` | `payload.batch_number` | Номер плавки / партии металла |
| `KATWORK.NAME` | `payload.operation` | Название техпроцесса |
| `AKT_DEFECT.NUM` | `outbox.payload.case_id` | Акт несоответствия при браке |

---

## 3. MES-система цехового уровня

### Назначение
Оперативный мониторинг исполнения операций, фиксация пауз и простоев оборудования.

### Поток событий
1. `MES.OperationStart` $\to$ `OperationStarted` (`operation_run_id`, `station_id`, `operator_alias`).
2. `MES.OperationPause` $\to$ `OperationPaused` (фиксация начала простоя).
3. `MES.OperationResume` $\to$ `OperationResumed` (возобновление).
4. `MES.OperationFinish` $\to$ `OperationFinished` (расчет чистого времени цикла).

---

## 4. КОМПАС-3D (САПР / PDM)

### Назначение
Импорт графа электронной структуры изделия (BOM / состав сборочной единицы) для сквозной прослеживаемости компонентов.

### Формат графа сборки
```json
{
  "assembly_id": "ASM-ROTOR-01",
  "assembly_version": "2.1",
  "components": [
    {"component_id": "COMP-BLADE-01", "name": "Лопатка турбины №1", "quantity": 1},
    {"component_id": "COMP-BLADE-02", "name": "Лопатка турбины №2", "quantity": 1},
    {"component_id": "COMP-DISK-MAIN", "name": "Диск ротора опорный", "quantity": 1}
  ],
  "geometry_attached": false
}
```
*Примечание:* Отсутствие 3D-геометрии фиксируется флагом `geometry_attached: false`, что предотвращает ложные ожидания от визуализатора САПР.
