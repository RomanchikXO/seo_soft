const CARD_SETTINGS_DEFAULTS = {
  city: "",
  street: "",
  house: "",
  organization: "",
  coordinates: "",
  search_transitions: 0,
  maps_transitions: 0,
  competitor_open_chance_percent: 0,
  max_open_competitor_cards: 0,
  min_sleep_competitor_card_sec: 0,
  max_sleep_competitor_card_sec: 0,
  min_sleep_target_overview_sec: 0,
  max_sleep_target_overview_sec: 0,
  min_sleep_target_tab_sec: 0,
  max_sleep_target_tab_sec: 0,
  allow_target_events: false,
  click_show_phone: 0,
  click_website: 0,
  click_route: 0,
  click_messengers: 0,
  click_book_story: 0,
  map_zoom_clicks: 0,
};

const NUMERIC_FIELD_CONFIG = [
  { key: "search_transitions", label: "Переходы в поиске", inputKey: "searchTransitionsInput" },
  { key: "maps_transitions", label: "Переходы в карты", inputKey: "mapsTransitionsInput" },
  {
    key: "competitor_open_chance_percent",
    label: "Шанс открытия карточки конкурента (%)",
    inputKey: "competitorOpenChancePercentInput",
  },
  {
    key: "max_open_competitor_cards",
    label: "MAX открытых карточек конкурентов (шт.)",
    inputKey: "maxOpenCompetitorCardsInput",
  },
  {
    key: "min_sleep_competitor_card_sec",
    label: "MIN сон на карточке конкурента (сек.)",
    inputKey: "minSleepCompetitorCardSecInput",
  },
  {
    key: "max_sleep_competitor_card_sec",
    label: "MAX сон на карточке конкурента (сек.)",
    inputKey: "maxSleepCompetitorCardSecInput",
  },
  {
    key: "min_sleep_target_overview_sec",
    label: "MIN сон на обзоре целевой карточки (сек.)",
    inputKey: "minSleepTargetOverviewSecInput",
  },
  {
    key: "max_sleep_target_overview_sec",
    label: "MAX сон на обзоре целевой карточки (сек.)",
    inputKey: "maxSleepTargetOverviewSecInput",
  },
  {
    key: "min_sleep_target_tab_sec",
    label: "MIN сон на целевом действии (сек.)",
    inputKey: "minSleepTargetTabSecInput",
  },
  {
    key: "max_sleep_target_tab_sec",
    label: "MAX сон на целевом действии (сек.)",
    inputKey: "maxSleepTargetTabSecInput",
  },
  { key: "click_show_phone", label: "Клики \"Показать номер\"", inputKey: "clickShowPhoneInput" },
  { key: "click_website", label: "Клики \"Сайт\"", inputKey: "clickWebsiteInput" },
  { key: "click_route", label: "Клики \"Маршрут\"", inputKey: "clickRouteInput" },
  { key: "click_messengers", label: "Клики в мессенджеры", inputKey: "clickMessengersInput" },
  { key: "click_book_story", label: "Клики \"Записаться\" (сторис)", inputKey: "clickBookStoryInput" },
  {
    key: "map_zoom_clicks",
    label: "Клики на изменение масштаба карты",
    inputKey: "mapZoomClicksInput",
  },
];

const TELEGRAM_PROXY_PATTERN = /^https?:\/\/(?:[^\s:@/]+(?::[^\s:@/]*)?@)?[^\s:@/]+:\d{1,5}\/?$/;

const state = {
  cards: [],
  selectedCardId: null,
  selectedCardName: "",
  selectedKeyId: null,
  keys: [],
  runDates: [],
  shutdownSent: false,
  settingsMode: "edit",
  activeTab: "keys",
  optimizationThreads: 1,
  optimizationSelectedCardIds: new Set(),
  savedScheduleAt: null,
  scheduleAt: null,
  scheduleSnapshot: null,
  scheduleTimer: null,
  scheduleCountdownTimer: null,
};

const ui = {
  initScreen: document.getElementById("initScreen"),
  initMessage: document.getElementById("initMessage"),
  initProgress: document.getElementById("initProgress"),

  cardsList: document.getElementById("cardsList"),
  addCardBtn: document.getElementById("addCardBtn"),

  workspaceEmpty: document.getElementById("workspaceEmpty"),
  workspaceContent: document.getElementById("workspaceContent"),
  selectedCardTitle: document.getElementById("selectedCardTitle"),

  tabKeysBtn: document.getElementById("tabKeysBtn"),
  tabOptimizationBtn: document.getElementById("tabOptimizationBtn"),
  keysSection: document.getElementById("keysSection"),
  optimizationSection: document.getElementById("optimizationSection"),
  optimizationThreadsRange: document.getElementById("optimizationThreadsRange"),
  optimizationThreadsValue: document.getElementById("optimizationThreadsValue"),
  optimizationSelectAllBtn: document.getElementById("optimizationSelectAllBtn"),
  optimizationPlayBtn: document.getElementById("optimizationPlayBtn"),
  optimizationStatus: document.getElementById("optimizationStatus"),

  keysHead: document.getElementById("keysHead"),
  keysBody: document.getElementById("keysBody"),
  addKeyBtn: document.getElementById("addKeyBtn"),
  deleteKeyBtn: document.getElementById("deleteKeyBtn"),
  runSearchBtn: document.getElementById("runSearchBtn"),
  runStatus: document.getElementById("runStatus"),
  importFileInput: document.getElementById("importFileInput"),

  openScheduleBtn: document.getElementById("openScheduleBtn"),
  scheduleModal: document.getElementById("scheduleModal"),
  scheduleDateTimeInput: document.getElementById("scheduleDateTimeInput"),
  resetScheduleBtn: document.getElementById("resetScheduleBtn"),
  scheduleSummary: document.getElementById("scheduleSummary"),
  optimizationScheduleInfo: document.getElementById("optimizationScheduleInfo"),
  cancelScheduleBtn: document.getElementById("cancelScheduleBtn"),
  closeScheduleBtn: document.getElementById("closeScheduleBtn"),
  confirmScheduleBtn: document.getElementById("confirmScheduleBtn"),

  openSettingsBtn: document.getElementById("openSettingsBtn"),
  openGlobalSettingsBtn: document.getElementById("openGlobalSettingsBtn"),
  globalSettingsModal: document.getElementById("globalSettingsModal"),
  captchaServiceManual: document.getElementById("captchaServiceManual"),
  captchaServiceCapsola: document.getElementById("captchaServiceCapsola"),
  capsolaSettingsArea: document.getElementById("capsolaSettingsArea"),
  capsolaTokenInput: document.getElementById("capsolaTokenInput"),
  telegramTokenInput: document.getElementById("telegramTokenInput"),
  telegramChatIdInput: document.getElementById("telegramChatIdInput"),
  telegramProxyInput: document.getElementById("telegramProxyInput"),
  closeGlobalSettingsBtn: document.getElementById("closeGlobalSettingsBtn"),
  saveGlobalSettingsBtn: document.getElementById("saveGlobalSettingsBtn"),
  settingsModal: document.getElementById("settingsModal"),
  settingsModalTitle: document.getElementById("settingsModalTitle"),
  cardNameInput: document.getElementById("cardNameInput"),
  cityInput: document.getElementById("cityInput"),
  streetInput: document.getElementById("streetInput"),
  houseInput: document.getElementById("houseInput"),
  organizationInput: document.getElementById("organizationInput"),
  coordinatesInput: document.getElementById("coordinatesInput"),
  searchTransitionsInput: document.getElementById("searchTransitionsInput"),
  mapsTransitionsInput: document.getElementById("mapsTransitionsInput"),
  competitorOpenChancePercentInput: document.getElementById("competitorOpenChancePercentInput"),
  maxOpenCompetitorCardsInput: document.getElementById("maxOpenCompetitorCardsInput"),
  minSleepCompetitorCardSecInput: document.getElementById("minSleepCompetitorCardSecInput"),
  maxSleepCompetitorCardSecInput: document.getElementById("maxSleepCompetitorCardSecInput"),
  minSleepTargetOverviewSecInput: document.getElementById("minSleepTargetOverviewSecInput"),
  maxSleepTargetOverviewSecInput: document.getElementById("maxSleepTargetOverviewSecInput"),
  minSleepTargetTabSecInput: document.getElementById("minSleepTargetTabSecInput"),
  maxSleepTargetTabSecInput: document.getElementById("maxSleepTargetTabSecInput"),
  allowTargetEventsInput: document.getElementById("allowTargetEventsInput"),
  clickShowPhoneInput: document.getElementById("clickShowPhoneInput"),
  clickWebsiteInput: document.getElementById("clickWebsiteInput"),
  clickRouteInput: document.getElementById("clickRouteInput"),
  clickMessengersInput: document.getElementById("clickMessengersInput"),
  clickBookStoryInput: document.getElementById("clickBookStoryInput"),
  mapZoomClicksInput: document.getElementById("mapZoomClicksInput"),
  deleteCardBtn: document.getElementById("deleteCardBtn"),
  closeSettingsBtn: document.getElementById("closeSettingsBtn"),
  saveSettingsBtn: document.getElementById("saveSettingsBtn"),
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `HTTP ${response.status}`);
  }
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return null;
}

function setInitProgress(step, message) {
  ui.initMessage.textContent = message;
  ui.initProgress.style.width = `${step}%`;
}

async function startup() {
  try {
    setInitProgress(10, "Проверка сервиса...");
    await api("/api/health");
    setInitProgress(65, "Загрузка карточек...");
    await loadCards();
    setInitProgress(100, "Готово");
    setTimeout(() => ui.initScreen.classList.add("hidden"), 250);
  } catch (error) {
    alert(`Ошибка инициализации: ${error.message}`);
  }
}

async function loadCards() {
  state.cards = await api("/api/cards");
  const availableIds = new Set(state.cards.map((card) => card.id));
  state.optimizationSelectedCardIds = new Set(
    [...state.optimizationSelectedCardIds].filter((cardId) => availableIds.has(cardId))
  );
  renderCards();
  refreshOptimizationStatus();
}

function renderCards() {
  ui.cardsList.innerHTML = "";
  for (const card of state.cards) {
    const item = document.createElement(state.activeTab === "optimization" ? "div" : "button");
    item.className = `card-item ${state.selectedCardId === card.id ? "active" : ""}`;
    if (state.activeTab === "optimization") {
      const row = document.createElement("div");
      row.className = "card-item-row";

      const toggleLabel = document.createElement("label");
      toggleLabel.className = "card-opt-toggle";
      toggleLabel.title = "Выбрать карточку для оптимизации";
      toggleLabel.onclick = (event) => event.stopPropagation();

      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = state.optimizationSelectedCardIds.has(card.id);
      checkbox.setAttribute("aria-label", `Выбрать карточку ${card.name}`);
      checkbox.onclick = (event) => event.stopPropagation();
      checkbox.onchange = (event) => {
        event.stopPropagation();
        if (checkbox.checked) {
          state.optimizationSelectedCardIds.add(card.id);
        } else {
          state.optimizationSelectedCardIds.delete(card.id);
        }
        refreshOptimizationStatus();
      };

      const checkboxView = document.createElement("span");
      checkboxView.className = "card-opt-box";

      const name = document.createElement("span");
      name.className = "card-item-name";
      name.textContent = card.name;

      toggleLabel.appendChild(checkbox);
      toggleLabel.appendChild(checkboxView);
      row.appendChild(toggleLabel);
      row.appendChild(name);
      item.appendChild(row);
    } else {
      item.textContent = card.name;
    }
    item.onclick = () => selectCard(card.id, card.name);
    ui.cardsList.appendChild(item);
  }
}

async function selectCard(cardId, cardName) {
  state.selectedCardId = cardId;
  state.selectedCardName = cardName;
  state.selectedKeyId = null;
  ui.workspaceEmpty.classList.add("hidden");
  ui.workspaceContent.classList.remove("hidden");
  ui.selectedCardTitle.textContent = `Карточка: ${cardName}`;
  renderCards();
  await loadKeys();
}

async function loadKeys() {
  if (!state.selectedCardId) return;
  const data = await api(`/api/cards/${state.selectedCardId}/keys`);
  state.keys = data.keys;
  state.runDates = data.run_dates;
  renderKeysTable();
}

function buildTargetCheckbox(key, field) {
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = !!key[field];
  checkbox.onclick = (event) => event.stopPropagation();
  checkbox.onchange = async () => {
    const checked = checkbox.checked;
    try {
      await api(`/api/keys/${key.id}/targets`, {
        method: "PATCH",
        body: JSON.stringify({ [field]: checked }),
      });
      key[field] = checked;
    } catch (error) {
      checkbox.checked = !checked;
      alert(`Не удалось обновить флаг: ${error.message}`);
    }
  };
  return checkbox;
}

function renderKeysTable() {
  ui.keysHead.innerHTML = "";
  ui.keysBody.innerHTML = "";

  const headRow = document.createElement("tr");
  for (const column of ["Ключевая фраза", "Поиск", "Карты", ...state.runDates, ""]) {
    const th = document.createElement("th");
    th.textContent = column;
    headRow.appendChild(th);
  }
  ui.keysHead.appendChild(headRow);

  for (const key of state.keys) {
    const row = document.createElement("tr");
    if (state.selectedKeyId === key.id) row.classList.add("selected");
    row.onclick = () => {
      state.selectedKeyId = key.id;
      renderKeysTable();
    };

    const phraseCell = document.createElement("td");
    phraseCell.textContent = key.phrase;
    phraseCell.className = "phrase-cell";
    phraseCell.title = "Нажмите, чтобы редактировать";
    phraseCell.onclick = async (event) => {
      event.stopPropagation();
      await editKeyById(key.id);
    };
    row.appendChild(phraseCell);

    const searchCell = document.createElement("td");
    searchCell.appendChild(buildTargetCheckbox(key, "search_enabled"));
    row.appendChild(searchCell);

    const mapsCell = document.createElement("td");
    mapsCell.appendChild(buildTargetCheckbox(key, "maps_enabled"));
    row.appendChild(mapsCell);

    for (const runDate of state.runDates) {
      const td = document.createElement("td");
      td.textContent = key.positions?.[runDate] || "";
      row.appendChild(td);
    }

    const actionsCell = document.createElement("td");
    const delBtn = document.createElement("button");
    delBtn.textContent = "🗑";
    delBtn.className = "icon-btn";
    delBtn.style.width = "28px";
    delBtn.style.height = "28px";
    delBtn.style.fontSize = "14px";
    delBtn.style.display = "flex";
    delBtn.style.alignItems = "center";
    delBtn.style.justifyContent = "center";
    delBtn.style.borderColor = "#8c2a2a";
    delBtn.style.color = "#ff6b6b";
    delBtn.title = "Удалить ключ";
    delBtn.onclick = async (event) => {
      event.stopPropagation();
      if (!confirm(`Удалить ключ "${key.phrase}"?`)) return;
      await api(`/api/keys/${key.id}`, { method: "DELETE" });
      if (state.selectedKeyId === key.id) state.selectedKeyId = null;
      await loadKeys();
    };
    actionsCell.appendChild(delBtn);
    row.appendChild(actionsCell);

    ui.keysBody.appendChild(row);
  }
}

function parseNonNegativeNumber(rawValue, fieldName) {
  const textValue = String(rawValue || "").trim().replace(",", ".");
  if (!textValue) return 0;
  const numericValue = Number(textValue);
  if (!Number.isFinite(numericValue) || numericValue < 0) {
    throw new Error(`Поле "${fieldName}" должно быть неотрицательным числом.`);
  }
  return numericValue;
}

function fillSettingsForm(cardName, settings) {
  const merged = { ...CARD_SETTINGS_DEFAULTS, ...(settings || {}) };
  ui.cardNameInput.value = cardName || "";
  ui.cityInput.value = merged.city || "";
  ui.streetInput.value = merged.street || "";
  ui.houseInput.value = merged.house || "";
  ui.organizationInput.value = merged.organization || "";
  ui.coordinatesInput.value = merged.coordinates || "";
  ui.allowTargetEventsInput.checked = !!merged.allow_target_events;
  for (const field of NUMERIC_FIELD_CONFIG) {
    ui[field.inputKey].value = String(merged[field.key] ?? 0);
  }
}

function collectSettingsPayload() {
  const payload = {
    city: ui.cityInput.value || "",
    street: ui.streetInput.value || "",
    house: ui.houseInput.value || "",
    organization: ui.organizationInput.value || "",
    coordinates: ui.coordinatesInput.value || "",
    allow_target_events: ui.allowTargetEventsInput.checked,
  };
  for (const field of NUMERIC_FIELD_CONFIG) {
    payload[field.key] = parseNonNegativeNumber(ui[field.inputKey].value, field.label);
  }
  return payload;
}

function switchTab(tab) {
  state.activeTab = tab;
  const isKeys = tab === "keys";
  ui.tabKeysBtn.classList.toggle("active", isKeys);
  ui.tabOptimizationBtn.classList.toggle("active", !isKeys);
  ui.keysSection.classList.toggle("hidden", !isKeys);
  ui.optimizationSection.classList.toggle("hidden", isKeys);
  renderCards();
  refreshOptimizationStatus();
}

function refreshOptimizationStatus() {
  ui.optimizationSelectAllBtn.classList.toggle("hidden", state.activeTab !== "optimization");

  const allSelected =
    state.cards.length > 0 && state.cards.every((card) => state.optimizationSelectedCardIds.has(card.id));
  ui.optimizationSelectAllBtn.textContent = allSelected ? "Снять все" : "Выбрать все";
  ui.optimizationSelectAllBtn.disabled = state.cards.length === 0;

  if (state.activeTab !== "optimization") {
    ui.optimizationStatus.textContent = "";
    return;
  }
  const selectedCount = state.optimizationSelectedCardIds.size;
  ui.optimizationStatus.textContent = `Выбрано карточек: ${selectedCount}. Потоки: ${state.optimizationThreads}.`;
}

function syncOptimizationThreadsUi() {
  ui.optimizationThreadsRange.value = String(state.optimizationThreads);
  ui.optimizationThreadsValue.textContent = String(state.optimizationThreads);
}

function clearSelectedCardView() {
  state.selectedCardId = null;
  state.selectedCardName = "";
  state.selectedKeyId = null;
  state.keys = [];
  state.runDates = [];
  ui.workspaceContent.classList.add("hidden");
  ui.workspaceEmpty.classList.remove("hidden");
  ui.selectedCardTitle.textContent = "Карточка";
  ui.keysHead.innerHTML = "";
  ui.keysBody.innerHTML = "";
}

function openCreateCardModal() {
  state.settingsMode = "create";
  ui.settingsModalTitle.textContent = "Добавить карточку";
  ui.saveSettingsBtn.textContent = "Создать карточку";
  ui.deleteCardBtn.classList.add("hidden");
  fillSettingsForm("", CARD_SETTINGS_DEFAULTS);
  ui.settingsModal.classList.remove("hidden");
}

async function openSelectedCardSettingsModal() {
  if (!state.selectedCardId) {
    alert("Выберите карточку.");
    return;
  }
  state.settingsMode = "edit";
  ui.settingsModalTitle.textContent = "Настройки карточки";
  ui.saveSettingsBtn.textContent = "Сохранить";
  ui.deleteCardBtn.classList.remove("hidden");
  const settings = await api(`/api/cards/${state.selectedCardId}/settings`);
  fillSettingsForm(state.selectedCardName, settings);
  ui.settingsModal.classList.remove("hidden");
}

async function saveSettings() {
  const name = ui.cardNameInput.value.trim();
  if (!name) {
    alert("Название карточки не может быть пустым.");
    return;
  }

  let settingsPayload;
  try {
    settingsPayload = collectSettingsPayload();
  } catch (error) {
    alert(error.message);
    return;
  }

  try {
    if (state.settingsMode === "create") {
      const created = await api("/api/cards", {
        method: "POST",
        body: JSON.stringify({ name, settings: settingsPayload }),
      });
      await loadCards();
      await selectCard(created.id, created.name);
    } else {
      if (!state.selectedCardId) {
        alert("Выберите карточку.");
        return;
      }
      if (name !== state.selectedCardName) {
        await api(`/api/cards/${state.selectedCardId}`, {
          method: "PUT",
          body: JSON.stringify({ name }),
        });
      }
      await api(`/api/cards/${state.selectedCardId}/settings`, {
        method: "POST",
        body: JSON.stringify(settingsPayload),
      });
      await loadCards();
      const current = state.cards.find((item) => item.id === state.selectedCardId);
      const updatedName = current ? current.name : name;
      state.selectedCardName = updatedName;
      ui.selectedCardTitle.textContent = `Карточка: ${updatedName}`;
      renderCards();
    }
    ui.settingsModal.classList.add("hidden");
  } catch (error) {
    alert(`Не удалось сохранить настройки: ${error.message}`);
  }
}

async function deleteSelectedCard() {
  if (state.settingsMode !== "edit" || !state.selectedCardId) {
    alert("Выберите карточку.");
    return;
  }
  const confirmed = confirm(
    `Удалить карточку "${state.selectedCardName}"?\nЭто удалит ключевые фразы и всю статистику без возможности восстановления.`
  );
  if (!confirmed) return;

  const deletingCardId = state.selectedCardId;
  try {
    await api(`/api/cards/${deletingCardId}`, { method: "DELETE" });
    state.optimizationSelectedCardIds.delete(deletingCardId);
    ui.settingsModal.classList.add("hidden");
    clearSelectedCardView();
    await loadCards();
  } catch (error) {
    alert(`Не удалось удалить карточку: ${error.message}`);
  }
}

async function addKey() {
  if (!state.selectedCardId) return;
  const phrase = prompt("Ключевая фраза:");
  if (!phrase) return;
  await api(`/api/cards/${state.selectedCardId}/keys`, {
    method: "POST",
    body: JSON.stringify({ phrase }),
  });
  await loadKeys();
}

async function editKeyById(keyId) {
  const target = state.keys.find((item) => item.id === keyId);
  if (!target) return;
  const phrase = prompt("Редактирование ключевой фразы:", target?.phrase || "");
  if (!phrase) return;
  await api(`/api/keys/${keyId}`, {
    method: "PUT",
    body: JSON.stringify({ phrase }),
  });
  await loadKeys();
}

async function deleteKey() {
  if (!state.selectedKeyId) {
    alert("Выберите ключ.");
    return;
  }
  if (!confirm("Удалить выбранный ключ?")) return;
  await api(`/api/keys/${state.selectedKeyId}`, { method: "DELETE" });
  state.selectedKeyId = null;
  await loadKeys();
}

async function importKeys(file) {
  if (!state.selectedCardId || !file) return;
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`/api/cards/${state.selectedCardId}/keys/import`, {
    method: "POST",
    body: formData,
  });
  if (!response.ok) {
    alert(await response.text());
    return;
  }
  const result = await response.json();
  alert(`Добавлено ключей: ${result.inserted}`);
  await loadKeys();
}

async function runSearch() {
  if (!state.selectedCardId) {
    alert("Выберите карточку.");
    return;
  }
  const enabledKeys = state.keys.filter((item) => item.search_enabled);
  if (enabledKeys.length === 0) {
    alert("Нет ключей с включенным флагом 'Поиск'.");
    return;
  }

  ui.runSearchBtn.disabled = true;
  const initialText = ui.runSearchBtn.textContent;
  ui.runSearchBtn.textContent = "▶ Запуск...";
  ui.runStatus.textContent = `Выполняется запуск (${enabledKeys.length} ключей)...`;
  try {
    const result = await api(`/api/cards/${state.selectedCardId}/run-search`, {
      method: "POST",
    });
    ui.runStatus.textContent = `Готово. Запущено запросов: ${result.executed}`;
    alert(`Запущено запросов: ${result.executed}`);
  } catch (error) {
    ui.runStatus.textContent = "Ошибка запуска.";
    alert(`Не удалось выполнить запуск: ${error.message}`);
  } finally {
    ui.runSearchBtn.disabled = false;
    ui.runSearchBtn.textContent = initialText;
  }
}

function requestShutdownOnClose() {
  if (state.shutdownSent) return;
  state.shutdownSent = true;
  try {
    navigator.sendBeacon("/api/shutdown", new Blob(["close"], { type: "text/plain" }));
  } catch (_error) {
    // ignore best-effort shutdown signal
  }
}

ui.addCardBtn.onclick = openCreateCardModal;
ui.addKeyBtn.onclick = addKey;
ui.deleteKeyBtn.onclick = deleteKey;
ui.runSearchBtn.onclick = runSearch;
ui.importFileInput.onchange = async (event) => {
  const [file] = event.target.files || [];
  await importKeys(file);
  ui.importFileInput.value = "";
};

ui.tabKeysBtn.onclick = () => switchTab("keys");
ui.tabOptimizationBtn.onclick = () => switchTab("optimization");
ui.optimizationThreadsRange.oninput = () => {
  state.optimizationThreads = Number(ui.optimizationThreadsRange.value) || 0;
  syncOptimizationThreadsUi();
  refreshOptimizationStatus();
};
ui.optimizationSelectAllBtn.onclick = () => {
  const allSelected =
    state.cards.length > 0 && state.cards.every((card) => state.optimizationSelectedCardIds.has(card.id));
  if (allSelected) {
    state.optimizationSelectedCardIds.clear();
  } else {
    state.optimizationSelectedCardIds = new Set(state.cards.map((card) => card.id));
  }
  renderCards();
  refreshOptimizationStatus();
};
async function runOptimization(cardIds, threads) {
  const ids = cardIds ?? [...state.optimizationSelectedCardIds];
  const threadCount = threads ?? state.optimizationThreads;
  if (ids.length === 0) {
    alert("Выберите хотя бы одну карточку для оптимизации.");
    return;
  }
  if (threadCount <= 0) {
    alert("Укажите количество потоков больше 0.");
    return;
  }
  ui.optimizationPlayBtn.disabled = true;
  const initialText = ui.optimizationPlayBtn.textContent;
  ui.optimizationPlayBtn.textContent = "▶ Запуск...";
  ui.optimizationStatus.textContent = `Выполняется оптимизация: карточек ${ids.length}, потоков ${threadCount}.`;
  try {
    const result = await api("/api/optimization/run", {
      method: "POST",
      body: JSON.stringify({
        card_ids: ids,
        threads: threadCount,
      }),
    });
    ui.optimizationStatus.textContent =
      `Готово. Карточек: ${result.processed_cards}, Поиск: ${result.total_search_performed}/${result.total_search_target}, Карты: ${result.total_maps_performed}/${result.total_maps_target}.`;
    return result;
  } catch (error) {
    ui.optimizationStatus.textContent = "Ошибка оптимизации.";
    alert(`Не удалось выполнить оптимизацию: ${error.message}`);
  } finally {
    ui.optimizationPlayBtn.disabled = false;
    ui.optimizationPlayBtn.textContent = initialText;
  }
}

function armScheduledRun() {
  const cardIds = [...state.optimizationSelectedCardIds];
  if (cardIds.length === 0) {
    alert("Выберите хотя бы одну карточку для оптимизации.");
    return;
  }
  if (state.optimizationThreads <= 0) {
    alert("Укажите количество потоков больше 0.");
    return;
  }
  const delay = state.savedScheduleAt - Date.now();
  clearSchedule();
  state.scheduleAt = state.savedScheduleAt;
  state.scheduleSnapshot = { cardIds, threads: state.optimizationThreads };
  state.scheduleTimer = setTimeout(triggerScheduledRun, delay);
  state.scheduleCountdownTimer = setInterval(updateScheduleInfo, 1000);
  updateScheduleView();
}

function handlePlayClick() {
  if (state.scheduleAt) {
    return;
  }
  if (state.savedScheduleAt && state.savedScheduleAt > Date.now()) {
    armScheduledRun();
    return;
  }
  state.savedScheduleAt = null;
  updateScheduleView();
  runOptimization();
}

ui.optimizationPlayBtn.onclick = handlePlayClick;
async function openGlobalSettingsModal() {
  ui.globalSettingsModal.classList.remove("hidden");
  try {
    const settings = await api("/api/settings");
    const service = settings.captcha_service || "manual";
    if (service === "capsola") {
      ui.captchaServiceCapsola.checked = true;
      ui.capsolaSettingsArea.classList.remove("hidden");
    } else {
      ui.captchaServiceManual.checked = true;
      ui.capsolaSettingsArea.classList.add("hidden");
    }
    ui.capsolaTokenInput.value = settings.capsola_token || "";
    ui.telegramTokenInput.value = settings.telegram_token || "";
    ui.telegramChatIdInput.value = settings.telegram_chat_id || "";
    ui.telegramProxyInput.value = settings.telegram_proxy || "";
  } catch (error) {
    alert(`Не удалось загрузить глобальные настройки: ${error.message}`);
  }
}

async function saveGlobalSettings() {
  const service = ui.captchaServiceCapsola.checked ? "capsola" : "manual";
  const token = ui.capsolaTokenInput.value.trim();
  const telegramProxy = ui.telegramProxyInput.value.trim();
  if (telegramProxy && !TELEGRAM_PROXY_PATTERN.test(telegramProxy)) {
    alert(
      "Неверный формат прокси.\nИспользуйте: http://логин:пароль@хост:порт\nНапример: http://123.45.67.89:8080"
    );
    return;
  }
  try {
    await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        captcha_service: service,
        capsola_token: token,
        telegram_token: ui.telegramTokenInput.value.trim(),
        telegram_chat_id: ui.telegramChatIdInput.value.trim(),
        telegram_proxy: telegramProxy,
      }),
    });
    ui.globalSettingsModal.classList.add("hidden");
  } catch (error) {
    alert(`Не удалось сохранить глобальные настройки: ${error.message}`);
  }
}

function handleCaptchaServiceChange() {
  if (ui.captchaServiceCapsola.checked) {
    ui.capsolaSettingsArea.classList.remove("hidden");
  } else {
    ui.capsolaSettingsArea.classList.add("hidden");
  }
}

ui.captchaServiceManual.onchange = handleCaptchaServiceChange;
ui.captchaServiceCapsola.onchange = handleCaptchaServiceChange;
ui.openGlobalSettingsBtn.onclick = openGlobalSettingsModal;
ui.closeGlobalSettingsBtn.onclick = () => ui.globalSettingsModal.classList.add("hidden");
ui.saveGlobalSettingsBtn.onclick = saveGlobalSettings;
ui.globalSettingsModal.onclick = (event) => {
  if (event.target === ui.globalSettingsModal) {
    ui.globalSettingsModal.classList.add("hidden");
  }
};

function toLocalInputValue(date) {
  const pad = (n) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

function formatCountdown(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const pad = (n) => String(n).padStart(2, "0");
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
}

function clearSchedule() {
  if (state.scheduleTimer) {
    clearTimeout(state.scheduleTimer);
    state.scheduleTimer = null;
  }
  if (state.scheduleCountdownTimer) {
    clearInterval(state.scheduleCountdownTimer);
    state.scheduleCountdownTimer = null;
  }
  state.scheduleAt = null;
  state.scheduleSnapshot = null;
}

function updateScheduleView() {
  const count = state.optimizationSelectedCardIds.size;
  const hasSaved = !!state.savedScheduleAt;
  const isArmed = !!state.scheduleAt;

  ui.openScheduleBtn.classList.toggle("scheduled", hasSaved || isArmed);

  if (hasSaved) {
    const when = new Date(state.savedScheduleAt).toLocaleString("ru-RU");
    ui.scheduleSummary.textContent =
      `Сохранено время запуска: ${when}. Запуск начнётся после нажатия «Play».`;
    ui.cancelScheduleBtn.classList.remove("hidden");
  } else {
    ui.scheduleSummary.textContent =
      `Будет запущена оптимизация выбранных карточек. Сейчас выбрано: ${count}, потоки: ${state.optimizationThreads}.`;
    ui.cancelScheduleBtn.classList.add("hidden");
  }

  updateScheduleInfo();
}

function updateScheduleInfo() {
  if (state.scheduleAt) {
    const remaining = state.scheduleAt - Date.now();
    const when = new Date(state.scheduleAt).toLocaleString("ru-RU");
    ui.optimizationScheduleInfo.classList.remove("hidden");
    ui.optimizationScheduleInfo.innerHTML =
      `<span class="schedule-dot"></span>` +
      `<span>Запуск запланирован на <b>${when}</b> &nbsp; ` +
      `До запуска: <span class="schedule-countdown">${formatCountdown(remaining)}</span></span>` +
      `<button type="button" class="btn schedule-info-cancel">Отменить</button>`;
  } else if (state.savedScheduleAt) {
    const when = new Date(state.savedScheduleAt).toLocaleString("ru-RU");
    ui.optimizationScheduleInfo.classList.remove("hidden");
    ui.optimizationScheduleInfo.innerHTML =
      `<span class="schedule-dot"></span>` +
      `<span>Время запуска сохранено: <b>${when}</b>. Нажмите «Play», чтобы запустить по времени.</span>` +
      `<button type="button" class="btn schedule-info-cancel">Отменить</button>`;
  } else {
    ui.optimizationScheduleInfo.classList.add("hidden");
    ui.optimizationScheduleInfo.innerHTML = "";
    return;
  }
  const cancelBtn = ui.optimizationScheduleInfo.querySelector(".schedule-info-cancel");
  if (cancelBtn) cancelBtn.onclick = cancelSchedule;
}

async function triggerScheduledRun() {
  const snapshot = state.scheduleSnapshot;
  state.savedScheduleAt = null;
  clearSchedule();
  updateScheduleView();
  if (!snapshot) return;
  switchTab("optimization");
  await runOptimization(snapshot.cardIds, snapshot.threads);
}

function openScheduleModal() {
  if (state.savedScheduleAt) {
    ui.scheduleDateTimeInput.value = toLocalInputValue(new Date(state.savedScheduleAt));
  } else {
    const defaultDate = new Date(Date.now() + 60 * 60 * 1000);
    ui.scheduleDateTimeInput.value = toLocalInputValue(defaultDate);
  }
  updateScheduleView();
  ui.scheduleModal.classList.remove("hidden");
}

function confirmSchedule() {
  const value = ui.scheduleDateTimeInput.value;
  if (!value) {
    state.savedScheduleAt = null;
    clearSchedule();
    updateScheduleView();
    ui.scheduleModal.classList.add("hidden");
    return;
  }

  const target = new Date(value);
  const timestamp = target.getTime();
  if (!Number.isFinite(timestamp) || timestamp <= Date.now()) {
    alert("Дата и время запуска должны быть в будущем.");
    return;
  }

  clearSchedule();
  state.savedScheduleAt = timestamp;
  updateScheduleView();
  ui.scheduleModal.classList.add("hidden");
}

function resetSchedule() {
  ui.scheduleDateTimeInput.value = "";
  state.savedScheduleAt = null;
  clearSchedule();
  updateScheduleView();
  ui.scheduleDateTimeInput.focus();
}

function cancelSchedule() {
  state.savedScheduleAt = null;
  clearSchedule();
  updateScheduleView();
}

ui.openScheduleBtn.onclick = openScheduleModal;
ui.confirmScheduleBtn.onclick = confirmSchedule;
ui.resetScheduleBtn.onclick = resetSchedule;
ui.scheduleDateTimeInput.oninput = updateScheduleView;
ui.cancelScheduleBtn.onclick = cancelSchedule;
ui.closeScheduleBtn.onclick = () => ui.scheduleModal.classList.add("hidden");
ui.scheduleModal.onclick = (event) => {
  if (event.target === ui.scheduleModal) {
    ui.scheduleModal.classList.add("hidden");
  }
};

ui.openSettingsBtn.onclick = async () => {
  try {
    await openSelectedCardSettingsModal();
  } catch (error) {
    alert(`Не удалось загрузить настройки: ${error.message}`);
  }
};
ui.closeSettingsBtn.onclick = () => ui.settingsModal.classList.add("hidden");
ui.deleteCardBtn.onclick = deleteSelectedCard;
ui.settingsModal.onclick = (event) => {
  if (event.target === ui.settingsModal) {
    ui.settingsModal.classList.add("hidden");
  }
};
ui.saveSettingsBtn.onclick = saveSettings;
window.addEventListener("pagehide", requestShutdownOnClose);

syncOptimizationThreadsUi();
startup();
