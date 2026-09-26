const state = { token: "", role: null, selected: null, items: [] };
const $ = id => document.getElementById(id);
const notice = message => { $("notice").textContent = message; };

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Authorization": `Bearer ${state.token}`, "Content-Type": "application/json", ...(options.headers || {}) }
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || data.error || `HTTP ${response.status}`);
  return data;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function labelFor(status) {
  return ({ awaiting_review: "Ожидает решения", confirmed: "Подтверждено", rejected: "Отклонено", needs_more_inspection: "Дополнительная проверка" })[status] || status;
}

const lineLabels = {
  missing_control: "Нет пригодного контроля в срок",
  confirmed_nonconformance: "Несоответствие подтверждено",
  review_required: "Требуется решение или проверка",
  insufficient_observation: "Недостаточно данных контроля",
  no_confirmed_nonconformance: "Нет подтверждённого несоответствия"
};

const qualityLabels = { good: "хорошо", poor: "плохо", unknown: "неизвестно" };
const deliveryLabels = { acknowledged: "подтверждено", pending: "ожидает отправки", error: "ошибка отправки" };

const scenarioDescriptions = {
  "I-001": "Штатные операции и пригодный итоговый контроль. Это не автоматический допуск изделия.",
  "I-002": "Признак обнаружен на входе, до последующих операций. Денежный эффект не рассчитывался.",
  "I-003": "Предупреждение станка и действие оператора рядом по времени с признаком. Причина не установлена автоматически.",
  "I-004": "Непригодный результат не закрывает контрольную точку и не считается подтверждением годности.",
  "I-005": "Первое решение сохранено, повторная операция связана с исходной, итоговый допуск не назначается автоматически.",
  "I-006": "Входной факт поступил после решения. История перестроена по времени источника; решение сохранено и требуется пересмотр."
};

function renderLine(items) {
  const board = $("line"); board.replaceChildren();
  if (!items.length) { board.append(element("p", "empty", "Изделий пока нет.")); return; }
  for (const item of items) {
    const button = element("button", `line-row ${item.state}`); button.type = "button";
    button.setAttribute("aria-label", `${item.item_id}: ${lineLabels[item.state] || item.state}`);
    button.append(element("strong", "", item.item_id),
                  element("span", "line-station", item.station_id || "Участок не указан"),
                  element("span", "line-state", lineLabels[item.state] || item.state));
    button.addEventListener("click", () => selectItem(item.item_id));
    board.append(button);
  }
}

function renderItems(items) {
  const list = $("items"); list.replaceChildren();
  $("item-count").textContent = String(items.length);
  if (!items.length) { list.append(element("p", "empty", "Событий пока нет. Загрузите демонстрационный набор.")); return; }
  for (const item of items) {
    const button = element("button", "item-button"); button.type = "button";
    button.setAttribute("aria-current", String(state.selected === item.item_id));
    const left = element("span"); left.append(element("strong", "", item.item_id), element("small", "Событий: " + item.event_count));
    button.append(left, element("span", "count", item.case_count + " случаев"));
    button.addEventListener("click", () => selectItem(item.item_id));
    list.append(button);
  }
}

function renderCase(item) {
  const card = element("article", "case");
  const head = element("div", "case-head");
  head.append(element("h3", "", `${item.defect_type} · ${item.area}`), element("span", `badge ${item.status}`, labelFor(item.status)));
  card.append(head, element("p", "", `Случай ${item.case_id}. Причина: ${item.cause_status === "unknown" ? "не установлена" : item.cause_status}.`));
  if (item.context?.component_id || item.context?.operation_run_id)
    card.append(element("p", "history-note", `Связь наблюдения: компонент ${item.context.component_id || "не указан"}; выполнение операции ${item.context.operation_run_id || "не указано"}.`));
  if (item.context) {
    const descriptions = { incoming_signal: "Признак обнаружен на входном контроле.",
      new_after_last_good_observation: "После последнего пригодного контроля без признаков обнаружен новый сигнал; момент возникновения не доказан.",
      time_of_occurrence_unknown: "Время возникновения признака неизвестно: нет пригодного предшествующего контроля." };
    card.append(element("p", "history-note", descriptions[item.context.classification] || item.context.classification));
    if (item.context.machine_ingestion_ids.length || item.context.operator_ingestion_ids.length)
      card.append(element("p", "history-note", `Рядом по времени: событий станка — ${item.context.machine_ingestion_ids.length}, действий оператора — ${item.context.operator_ingestion_ids.length}. Временная связь не доказывает причину.`));
  }
  if (item.review_required) card.append(element("p", "history-note", "После решения пришло более раннее событие: требуется пересмотр оснований."));
  for (const decision of item.decisions) card.append(element("p", "history-note", `${decision.at}: ${labelFor(decision.action)} · ${decision.actor} · ${decision.reason}`));
  if (state.role !== "controller" && state.role !== "admin") {
    card.append(element("p", "history-note", "Для нового решения нужен токен контролёра."));
    return card;
  }
  const form = element("form", "decision-form");
  const select = element("select"); select.setAttribute("aria-label", "Решение контролёра");
  for (const [value, title] of [["confirmed", "Подтвердить"], ["rejected", "Отклонить"], ["needs_more_inspection", "Назначить проверку"]]) {
    const option = element("option", "", title); option.value = value; select.append(option);
  }
  const actor = element("input"); actor.placeholder = "Идентификатор контролёра"; actor.required = true; actor.setAttribute("aria-label", "Идентификатор контролёра");
  const reason = element("textarea"); reason.placeholder = "Основание решения"; reason.required = true; reason.setAttribute("aria-label", "Основание решения");
  const submit = element("button", "", "Сохранить решение"); submit.type = "submit";
  form.append(select, actor, reason, submit);
  form.addEventListener("submit", async ev => {
    ev.preventDefault(); submit.disabled = true; notice("Сохраняем решение…");
    try {
      await api(`/api/cases/${encodeURIComponent(item.case_id)}/decisions`, { method: "POST", body: JSON.stringify({
        action: select.value, actor: actor.value.trim(), reason: reason.value.trim(),
        expected_version: item.version, idempotency_key: crypto.randomUUID()
      }) });
      notice("Решение сохранено. Исходный сигнал не изменён.");
      await refresh();
    } catch (error) { notice(error.message); } finally { submit.disabled = false; }
  });
  card.append(form);
  return card;
}

function renderInvestigation(data) {
  const section = element("section", "investigation");
  section.append(element("h3", "section-label", "Материалы расследования"));
  if (!data.cases.length) {
    section.append(element("p", "empty", "Случаев дефекта нет. Доступна хронология контроля ниже."));
    return section;
  }
  const records = new Map(data.events.map(record => [record.ingestion_id, record]));
  for (const defectCase of data.cases) {
    const card = element("article", "investigation-case");
    card.append(element("strong", "", `${defectCase.defect_type} · ${defectCase.area}`));
    const context = defectCase.context;
    const evidence = [
      [context.last_good_ingestion_id, "Последний пригодный контроль без признаков"],
      [context.operation_start_ingestion_id, "Начало операции"],
      ...context.machine_ingestion_ids.map(id => [id, "Сигнал оборудования"]),
      ...context.operator_ingestion_ids.map(id => [id, "Действие оператора"]),
      [context.detection_ingestion_id, "Обнаружение признака"]
    ].filter(([id]) => records.has(id));
    const timeline = element("ol", "investigation-timeline");
    evidence.sort((a, b) => records.get(a[0]).event.occurred_at.localeCompare(records.get(b[0]).event.occurred_at));
    for (const [id, label] of evidence) {
      const record = records.get(id);
      const entry = element("li", "");
      entry.append(element("strong", "", label), element("span", "", `${record.event.occurred_at} · ${record.event.event_type}`));
      const payload = record.event.payload;
      if (payload.code || payload.equipment_id || payload.action)
        entry.append(element("small", "", [payload.equipment_id, payload.code, payload.action].filter(Boolean).join(" · ")));
      if (payload.evidence_image) entry.append(element("small", "", "Фото приложено к событию ниже"));
      timeline.append(entry);
    }
    for (const decision of defectCase.decisions) {
      const entry = element("li", "investigation-decision");
      entry.append(element("strong", "", `Решение контролёра: ${labelFor(decision.action)}`),
                   element("span", "", `${decision.at} · ${decision.actor}`));
      timeline.append(entry);
    }
    card.append(timeline);
    card.append(element("p", "investigation-caution", `Связано с компонентом: ${context.component_id || "не указан"}; с выполнением операции: ${context.operation_run_id || "не указано"}. Возможные альтернативы: входное несоответствие, оборудование, перемещение или недостаток наблюдений; ни одна причина не подтверждена автоматически.`));
    card.append(element("p", "investigation-caution", defectCase.review_required
      ? "После решения поступило более раннее событие. Основания требуют пересмотра; прежнее решение сохранено."
      : "События сопоставлены по времени. Причина дефекта и ответственность человека автоматически не установлены."));
    section.append(card);
  }
  return section;
}

function renderCostComparison(itemId) {
  if (itemId !== "I-002") return null;
  const section = element("section", "cost-comparison");
  section.append(element("h3", "section-label", "Цена позднего обнаружения — учебный расчёт"),
                 element("p", "", "Сценарий: входной дефект найден до обработки. Суммы ниже — редактируемые допущения, не данные предприятия и не доказанная экономия."));
  const inputs = [];
  for (const [label, value] of [["Входной контроль, ₽", 2000], ["Мехобработка, ₽", 12000], ["Сборка, ₽", 8000], ["Финальный контроль, ₽", 5000]]) {
    const field = element("label", "cost-field", label);
    const input = element("input"); input.type = "number"; input.min = "0"; input.step = "100"; input.value = String(value);
    field.append(input); section.append(field); inputs.push(input);
  }
  const result = element("div", "cost-result"); section.append(result);
  const format = amount => new Intl.NumberFormat("ru-RU").format(amount) + " ₽";
  const update = () => {
    const values = inputs.map(input => Number(input.value));
    if (inputs.some(input => input.value === "") || values.some(value => !Number.isFinite(value) || value < 0)) {
      result.textContent = "Введите неотрицательные суммы для всех этапов."; return;
    }
    const early = values[0]; const late = values.reduce((sum, value) => sum + value, 0);
    result.replaceChildren(element("p", "", `При входном выявлении: ${format(early)}`),
                           element("p", "", `При выявлении после сборки: ${format(late)}`),
                           element("strong", "", `Разница в этой модели: ${format(late - early)}`));
  };
  inputs.forEach(input => input.addEventListener("input", update)); update();
  return section;
}

async function selectItem(itemId) {
  const selection = state.selection = (state.selection || 0) + 1;
  state.selected = itemId;
  $("scenario-explanation").textContent = scenarioDescriptions[itemId] || "События из загруженного журнала.";
  $("print-report").disabled = true;
  renderItems(state.items);
  $("detail-title").textContent = itemId;
  const panel = $("detail"); panel.replaceChildren(element("p", "empty", "Загружаем историю…"));
  try {
    const data = await api(`/api/items/${encodeURIComponent(itemId)}`);
    if (selection !== state.selection) return;
    panel.replaceChildren();
    panel.append(element("h3", "section-label", "Состав изделия"));
    if (!data.components.length) panel.append(element("p", "empty", "Состав изделия источником не передан."));
    for (const component of data.components) {
      const card = element("article", "component-row");
      card.append(element("strong", "", `${component.component_id} · ${component.component_type}`),
                  element("span", "", component.installed ? "В составе изделия" : "Снят с изделия"),
                  element("small", "", component.transitions.map(change => `${change.action}: ${change.at} (${change.event_id})`).join("; ")));
      panel.append(card);
    }
    panel.append(renderInvestigation(data));
    const comparison = renderCostComparison(itemId);
    if (comparison) panel.append(comparison);
    panel.append(element("h3", "section-label", "Хронология событий"));
    if (!data.events.length) panel.append(element("p", "empty", "Событий нет."));
    for (const record of data.events) {
      const row = element("article", `event ${record.state}`);
      row.append(element("h3", "", record.event.event_type));
      row.append(element("small", "", `${record.event.occurred_at} · ${record.event.source_id} · ${record.state}`));
      const payload = record.event.payload;
      const linkage = [record.event.station_id && `участок ${record.event.station_id}`,
                       record.event.operator_alias && `оператор ${record.event.operator_alias}`,
                       payload.equipment_id && `оборудование ${payload.equipment_id}`,
                       payload.operation_run_id && `выполнение ${payload.operation_run_id}`,
                       payload.component_id && `компонент ${payload.component_id}`].filter(Boolean);
      if (linkage.length) row.append(element("p", "", linkage.join(" · ")));
      if (payload.inspection_result) row.append(element("p", "", `Контроль: ${payload.inspection_result}; качество: ${qualityLabels[payload.observation_quality || "unknown"] || "неизвестно"}.`));
      if (payload.defects?.length) row.append(element("p", "", `Признаки: ${payload.defects.map(d => `${d.type} (${d.area || "не указана"})`).join(", ")}`));
      const evidence = payload.evidence_image;
      if (evidence && ["image/jpeg", "image/png"].includes(evidence.mime_type) && /^[A-Za-z0-9+/]+={0,2}$/.test(evidence.data_base64)) {
        const figure = element("figure", "evidence");
        const image = element("img"); image.alt = `Фото, переданное источником для события ${record.event.event_id}`;
        image.src = `data:${evidence.mime_type};base64,${evidence.data_base64}`;
        figure.append(image, element("figcaption", "", "Фото от источника события; автоматический анализ изображения не проводился."));
        row.append(figure);
      }
      if (record.reason) row.append(element("p", "", `Причина карантина: ${record.reason}`));
      panel.append(row);
    }
    panel.append(element("h3", "section-label", "Ожидаемые контрольные точки"));
    if (!data.checkpoints.length) panel.append(element("p", "empty", "Маршрутные контрольные точки не заданы."));
    for (const point of data.checkpoints) {
      const pointLabels = { observed: "Наблюдение пригодно", missing: "Нет пригодного контроля в срок", awaiting: "Срок ещё не наступил" };
      const card = element("article", `checkpoint ${point.state}`);
      card.append(element("strong", "", `${point.id} · ${point.station_id}`),
                  element("span", "", pointLabels[point.state] || point.state),
                  element("small", "", `Срок: ${point.due_at}; тип: ${point.event_type}`));
      if (point.alert_history.length) card.append(element("small", "", `Аудит переходов: ${point.alert_history.map(alert => alert.state).join(" → ")}`));
      panel.append(card);
    }
    panel.append(element("h3", "section-label", "Исполнения операций"));
    if (!data.operation_runs.length) panel.append(element("p", "empty", "Операции не зарегистрированы."));
    for (const run of data.operation_runs) {
      const description = run.active_seconds === null
        ? `Интервал не рассчитан: ${run.incomplete_reason}.`
        : `Активно: ${run.active_seconds} с; на участке: ${run.elapsed_seconds} с.`;
      panel.append(element("p", "history-note", `${run.operation_run_id} · ${run.operation || "операция не указана"} · участок ${run.station_id || "не указан"} · оператор ${run.operator_alias || "не указан"} · оборудование ${run.equipment_id || "не указано"} · начало ${run.start || "неизвестно"} · конец ${run.finish || "неизвестно"} · паузы ${run.pauses.length} · ${description}${run.previous_run_id ? ` Повтор после ${run.previous_run_id}.` : ""}`));
    }
    panel.append(element("h3", "section-label", "Случаи и решения"));
    if (!data.cases.length) panel.append(element("p", "empty", "Признаков дефекта нет. Это не означает автоматического допуска изделия."));
    for (const item of data.cases) panel.append(renderCase(item));
    $("print-report").disabled = false;
  } catch (error) {
    if (selection !== state.selection) return;
    panel.replaceChildren(element("p", "empty", error.message)); notice(error.message);
  }
}

async function refresh() {
  if (!state.token) { notice("Введите токен роли."); return; }
  try {
    const [me, metrics, items, line, outbox] = await Promise.all([api("/api/me"), api("/api/metrics"), api("/api/items"), api("/api/line"), api("/api/outbox")]);
    state.role = me.role;
    $("verify-integrity").hidden = me.role !== "admin";
    $("checked").textContent = metrics.checked_items;
    $("confirmed").textContent = metrics.items_with_confirmed_defect;
    $("pending").textContent = metrics.pending_cases;
    $("unknown").textContent = metrics.items_without_usable_inspection;
    const analytics = $("analytics"); analytics.replaceChildren();
    for (const [label, values] of [
      ["Типы дефектов", metrics.confirmed_defects_by_type],
      ["Линии", metrics.confirmed_defects_by_line],
      ["Посты", metrics.confirmed_defects_by_station],
      ["Установленная/неизвестная причина", metrics.confirmed_cases_by_cause],
      ["Подтверждённые ошибки по сопоставимым работам", metrics.confirmed_errors_by_comparable_work]
    ]) {
      const entries = Object.entries(values || {});
      analytics.append(element("p", "analytics-row", `${label}: ${entries.length ? entries.map(([name, count]) => `${name} — ${count}`).join("; ") : "нет подтверждённых данных"}`));
    }
    const durations = metrics.operation_durations || [];
    analytics.append(element("p", "analytics-row", `Завершённые операции: ${durations.length}; активное время по изделию, посту, оператору и смене:`));
    for (const run of durations) analytics.append(element("p", "analytics-row", `${run.item_id} / ${run.station_id || "пост не указан"} / ${run.operator_alias || "оператор не указан"} / ${run.shift_id || "смена не указана"}: ${run.active_seconds} с`));
    analytics.append(element("small", "", "Причина и ошибка оператора учитываются только после явного решения контролёра. Отсутствующая смена не выводится из времени автоматически."));
    state.items = items; renderItems(items);
    renderLine(line);
    const exchange = $("outbox"); exchange.replaceChildren();
    if (!outbox.length) exchange.append(element("p", "empty", "Пока нет исходящих результатов."));
    for (const message of outbox) {
      const row = element("div", "message");
      row.append(element("code", "", message.payload.item_id), element("code", "", message.message_id),
                 element("span", `state-${message.state}`, deliveryLabels[message.state] || message.state),
                 element("span", "", `Попыток: ${message.attempts}`));
      if (message.last_error) row.append(element("small", "", message.last_error));
      exchange.append(row);
    }
    const system = $("system-status"); system.replaceChildren();
    const facts = [
      `Сервер ответил; роль: ${me.role}.`,
      `Реестр: ${items.length} изделий; обзор линии: ${line.length} записей.`,
      `Исходящий журнал: ${outbox.length} сообщений; подтверждено эмулятором: ${outbox.filter(m => m.state === "acknowledged").length}.`,
      "Интеграции с реальными системами предприятия и промышленный CV здесь не проверены."
    ];
    for (const fact of facts) system.append(element("p", "", fact));
    if (state.selected) await selectItem(state.selected);
    else if (items.length) await selectItem(items[0].item_id);
    notice("");
  } catch (error) { notice(error.message); }
}

$("access-form").addEventListener("submit", ev => { ev.preventDefault(); state.token = $("token").value.trim(); refresh(); });
$("refresh").addEventListener("click", refresh);
$("demo-actions").addEventListener("click", event => {
  const itemId = event.target.closest("button[data-item]")?.dataset.item;
  if (!itemId) return;
  if (!state.token) { notice("Сначала откройте данные токеном роли."); return; }
  if (!state.items.some(item => item.item_id === itemId)) { notice("Сценарий отсутствует в текущем наборе."); return; }
  selectItem(itemId);
  $("detail-title").scrollIntoView({ behavior: "smooth", block: "start" });
});
$("print-report").addEventListener("click", () => window.print());
$("verify-integrity").addEventListener("click", async () => {
  const panel = $("integrity-result");
  panel.replaceChildren(element("p", "", "Проверяем события, решения, доставки и аудиторский якорь…"));
  try {
    const result = await api("/api/integrity");
    if (!result.valid || !result.event_digests_verified || !result.hash_chain_verified || !result.anchor_verified || !result.decisions_and_outbox_verified)
      throw new Error("Не все этапы проверки подтверждены сервером.");
    panel.replaceChildren(element("strong", "integrity-count", `Проверено исходных событий: ${result.checked_events}`));
    for (const label of ["Содержимое каждого события сверено с контрольным хэшем",
                         "Связность хэш-цепочки подтверждена",
                         "Вершина цепочки сверена с локальным HMAC-якорем",
                         "Решения контролёра и состояние доставки сверены с HMAC-якорем"]) {
      panel.append(element("p", "integrity-step", `✓ ${label}`));
    }
    panel.append(element("small", "integrity-scope", "Решения и ACK не входят в хэш-цепочку событий, но сверяются с локальным якорем. Старые записи зафиксированы при миграции без доказательства их прежней целостности. Это не внешний доверенный аудит."));
  } catch (error) {
    panel.replaceChildren(element("strong", "integrity-failed", "Целостность не подтверждена"),
                          element("p", "", error.message));
  }
});
