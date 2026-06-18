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

const DEFAULT_FIELD_KEYS = [
  "competitor_open_chance_percent",
  "max_open_competitor_cards",
  "min_sleep_competitor_card_sec",
  "max_sleep_competitor_card_sec",
  "min_sleep_target_overview_sec",
  "max_sleep_target_overview_sec",
  "min_sleep_target_tab_sec",
  "max_sleep_target_tab_sec",
  "click_show_phone",
  "click_website",
  "click_route",
  "click_messengers",
  "click_book_story",
  "map_zoom_clicks",
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
  cardDeleteMode: false,
  cardDeleteSelectedIds: new Set(),
  scheduleMode: "auto",
  autoWeekdays: new Set(),
  tasks: [],
  taskSeq: 0,
  tasksCountdownTimer: null,
  taskStatsPollTimer: null,
  cardDefaults: null,
};

const WEEKDAYS = [
  { day: 1, label: "Пн" },
  { day: 2, label: "Вт" },
  { day: 3, label: "Ср" },
  { day: 4, label: "Чт" },
  { day: 5, label: "Пт" },
  { day: 6, label: "Сб" },
  { day: 0, label: "Вс" },
];
const WEEKDAY_SHORT = { 0: "Вс", 1: "Пн", 2: "Вт", 3: "Ср", 4: "Чт", 5: "Пт", 6: "Сб" };
const MAX_TIMEOUT_MS = 2 ** 31 - 1;

const ui = {
  initScreen: document.getElementById("initScreen"),
  initMessage: document.getElementById("initMessage"),
  initProgress: document.getElementById("initProgress"),

  cardsList: document.getElementById("cardsList"),
  toggleDeleteModeBtn: document.getElementById("toggleDeleteModeBtn"),
  addCardBtn: document.getElementById("addCardBtn"),
  deleteSelectedCardsBtn: document.getElementById("deleteSelectedCardsBtn"),

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
  optimizationTasksList: document.getElementById("optimizationTasksList"),

  modeRealtime: document.getElementById("modeRealtime"),
  modeAuto: document.getElementById("modeAuto"),
  modeDeferred: document.getElementById("modeDeferred"),
  autoWeekdays: document.getElementById("autoWeekdays"),
  autoTimeInput: document.getElementById("autoTimeInput"),
  deferredDateTimeInput: document.getElementById("deferredDateTimeInput"),

  keysHead: document.getElementById("keysHead"),
  keysBody: document.getElementById("keysBody"),
  addKeyBtn: document.getElementById("addKeyBtn"),
  deleteKeyBtn: document.getElementById("deleteKeyBtn"),
  runSearchBtn: document.getElementById("runSearchBtn"),
  runStatus: document.getElementById("runStatus"),
  importFileInput: document.getElementById("importFileInput"),

  openSettingsBtn: document.getElementById("openSettingsBtn"),
  openGlobalSettingsBtn: document.getElementById("openGlobalSettingsBtn"),
  globalSettingsModal: document.getElementById("globalSettingsModal"),
  captchaServiceManual: document.getElementById("captchaServiceManual"),
  captchaServiceCapsola: document.getElementById("captchaServiceCapsola"),
  captchaServiceBotlab: document.getElementById("captchaServiceBotlab"),
  capsolaSettingsArea: document.getElementById("capsolaSettingsArea"),
  capsolaTokenInput: document.getElementById("capsolaTokenInput"),
  botlabSettingsArea: document.getElementById("botlabSettingsArea"),
  botlabTokenInput: document.getElementById("botlabTokenInput"),
  telegramTokenInput: document.getElementById("telegramTokenInput"),
  telegramChatIdInput: document.getElementById("telegramChatIdInput"),
  telegramProxyInput: document.getElementById("telegramProxyInput"),
  closeGlobalSettingsBtn: document.getElementById("closeGlobalSettingsBtn"),
  saveGlobalSettingsBtn: document.getElementById("saveGlobalSettingsBtn"),
  settingsModal: document.getElementById("settingsModal"),
  settingsModalTitle: document.getElementById("settingsModalTitle"),
  applyDefaultsBtn: document.getElementById("applyDefaultsBtn"),
  openDefaultsConfigBtn: document.getElementById("openDefaultsConfigBtn"),
  defaultsConfigModal: document.getElementById("defaultsConfigModal"),
  closeDefaultsConfigBtn: document.getElementById("closeDefaultsConfigBtn"),
  saveDefaultsConfigBtn: document.getElementById("saveDefaultsConfigBtn"),
  cardNameInput: document.getElementById("cardNameInput"),
  yandexOrgUrlInput: document.getElementById("yandexOrgUrlInput"),
  autofillYandexOrgBtn: document.getElementById("autofillYandexOrgBtn"),
  autofillYandexOrgStatus: document.getElementById("autofillYandexOrgStatus"),
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
    await ensureCardDefaultsLoaded();
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
  state.cardDeleteSelectedIds = new Set(
    [...state.cardDeleteSelectedIds].filter((cardId) => availableIds.has(cardId))
  );
  renderCards();
  refreshOptimizationStatus();
  refreshCardDeleteUi();
}

function toggleCardDeleteSelection(cardId) {
  if (state.cardDeleteSelectedIds.has(cardId)) {
    state.cardDeleteSelectedIds.delete(cardId);
  } else {
    state.cardDeleteSelectedIds.add(cardId);
  }
  renderCards();
  refreshCardDeleteUi();
}

function toggleCardOptimizationSelection(cardId) {
  if (state.optimizationSelectedCardIds.has(cardId)) {
    state.optimizationSelectedCardIds.delete(cardId);
  } else {
    state.optimizationSelectedCardIds.add(cardId);
  }
  renderCards();
  refreshOptimizationStatus();
}

function handleCardClick(card) {
  if (state.cardDeleteMode) {
    toggleCardDeleteSelection(card.id);
    return;
  }
  if (state.activeTab === "optimization") {
    toggleCardOptimizationSelection(card.id);
    return;
  }
  selectCard(card.id, card.name);
}

function renderCards() {
  ui.cardsList.innerHTML = "";
  for (const card of state.cards) {
    const item = document.createElement("div");
    const classes = ["card-item"];
    const isViewMode = !state.cardDeleteMode && state.activeTab !== "optimization";
    if (isViewMode && state.selectedCardId === card.id) {
      classes.push("active");
    }
    if (state.cardDeleteMode && state.cardDeleteSelectedIds.has(card.id)) {
      classes.push("selected-delete");
    }
    if (!state.cardDeleteMode && state.optimizationSelectedCardIds.has(card.id)) {
      classes.push("selected-optimization");
    }
    item.className = classes.join(" ");

    const name = document.createElement("span");
    name.className = "card-item-name";
    name.textContent = card.name;
    item.appendChild(name);

    item.onclick = () => handleCardClick(card);
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
  ui.yandexOrgUrlInput.value = "";
  setAutofillStatus("");
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

function numericFieldConfigByKey(key) {
  return NUMERIC_FIELD_CONFIG.find((field) => field.key === key);
}

async function ensureCardDefaultsLoaded() {
  if (state.cardDefaults) return state.cardDefaults;
  try {
    state.cardDefaults = await api("/api/card-defaults");
  } catch (_error) {
    state.cardDefaults = null;
  }
  return state.cardDefaults;
}

async function applyCardDefaultsToSettingsForm() {
  const defaults = await ensureCardDefaultsLoaded();
  if (!defaults) {
    alert("Не удалось загрузить значения по умолчанию.");
    return;
  }
  for (const key of DEFAULT_FIELD_KEYS) {
    const config = numericFieldConfigByKey(key);
    if (config && ui[config.inputKey]) {
      ui[config.inputKey].value = String(defaults[key] ?? 0);
    }
  }
}

function fillDefaultsConfigForm(defaults) {
  for (const key of DEFAULT_FIELD_KEYS) {
    const input = document.getElementById(`default_${key}`);
    if (!input) continue;
    input.value = String(defaults[key] ?? 0);
  }
}

async function openDefaultsConfigModal() {
  const defaults = (await ensureCardDefaultsLoaded()) || {};
  fillDefaultsConfigForm(defaults);
  ui.defaultsConfigModal.classList.remove("hidden");
}

function collectDefaultsPayload() {
  const payload = {};
  for (const key of DEFAULT_FIELD_KEYS) {
    const input = document.getElementById(`default_${key}`);
    const config = numericFieldConfigByKey(key);
    payload[key] = parseNonNegativeNumber(input ? input.value : 0, config ? config.label : key);
  }
  return payload;
}

async function saveDefaultsConfig() {
  let payload;
  try {
    payload = collectDefaultsPayload();
  } catch (error) {
    alert(error.message);
    return;
  }
  try {
    await api("/api/card-defaults", { method: "POST", body: JSON.stringify(payload) });
    state.cardDefaults = payload;
    ui.defaultsConfigModal.classList.add("hidden");
  } catch (error) {
    alert(`Не удалось сохранить значения по умолчанию: ${error.message}`);
  }
}

function setAutofillStatus(message, type = "") {
  if (!ui.autofillYandexOrgStatus) return;
  ui.autofillYandexOrgStatus.textContent = message || "";
  ui.autofillYandexOrgStatus.classList.remove("error", "success");
  if (type) {
    ui.autofillYandexOrgStatus.classList.add(type);
  }
}

async function autofillByYandexOrgUrl() {
  const url = (ui.yandexOrgUrlInput.value || "").trim();
  if (!url) {
    setAutofillStatus("Введите ссылку на организацию.", "error");
    return;
  }

  ui.autofillYandexOrgBtn.disabled = true;
  const initialText = ui.autofillYandexOrgBtn.textContent;
  ui.autofillYandexOrgBtn.textContent = "Заполняю...";
  setAutofillStatus("Получаю данные из Яндекс.Карт...");

  try {
    const data = await api("/api/yandex-org/autofill", {
      method: "POST",
      body: JSON.stringify({ url }),
    });

    if (data.organization) {
      ui.organizationInput.value = data.organization;
    }
    if (data.city) {
      ui.cityInput.value = data.city;
    }
    if (data.street) {
      ui.streetInput.value = data.street;
    }
    if (data.house) {
      ui.houseInput.value = data.house;
    }
    if (data.coordinates) {
      ui.coordinatesInput.value = data.coordinates;
    }

    setAutofillStatus("Данные организации заполнены.", "success");
  } catch (error) {
    setAutofillStatus(`Не удалось заполнить: ${error.message}`, "error");
  } finally {
    ui.autofillYandexOrgBtn.disabled = false;
    ui.autofillYandexOrgBtn.textContent = initialText;
  }
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
  ui.optimizationSelectAllBtn.classList.toggle(
    "hidden",
    state.activeTab !== "optimization" || state.cardDeleteMode
  );

  const allSelected =
    state.cards.length > 0 && state.cards.every((card) => state.optimizationSelectedCardIds.has(card.id));
  ui.optimizationSelectAllBtn.textContent = allSelected ? "Отменить все" : "Выбрать все";
  ui.optimizationSelectAllBtn.disabled = state.cards.length === 0;

  if (state.activeTab !== "optimization") {
    ui.optimizationStatus.textContent = "";
    return;
  }
  const selectedCount = state.optimizationSelectedCardIds.size;
  ui.optimizationStatus.textContent = `Выбрано карточек: ${selectedCount}. Потоки: ${state.optimizationThreads}.`;
}

function refreshCardDeleteUi() {
  ui.toggleDeleteModeBtn.classList.toggle("active", state.cardDeleteMode);
  ui.deleteSelectedCardsBtn.classList.toggle("hidden", !state.cardDeleteMode);
  ui.deleteSelectedCardsBtn.disabled = state.cardDeleteSelectedIds.size === 0;
  ui.addCardBtn.disabled = state.cardDeleteMode;
  document.body.classList.toggle("card-delete-mode", state.cardDeleteMode);
}

function toggleCardDeleteMode() {
  state.cardDeleteMode = !state.cardDeleteMode;
  if (!state.cardDeleteMode) {
    state.cardDeleteSelectedIds.clear();
  }
  renderCards();
  refreshCardDeleteUi();
  refreshOptimizationStatus();
}

async function deleteSelectedCards() {
  const cardIds = [...state.cardDeleteSelectedIds];
  if (cardIds.length === 0) {
    alert("Выберите карточки для удаления.");
    return;
  }
  const confirmed = confirm(
    `Удалить ${cardIds.length} карточек?\nЭто удалит ключевые фразы и всю статистику без возможности восстановления.`
  );
  if (!confirmed) return;

  try {
    for (const cardId of cardIds) {
      await api(`/api/cards/${cardId}`, { method: "DELETE" });
      state.optimizationSelectedCardIds.delete(cardId);
      if (state.selectedCardId === cardId) {
        clearSelectedCardView();
      }
    }
    state.cardDeleteSelectedIds.clear();
    await loadCards();
  } catch (error) {
    alert(`Не удалось удалить карточки: ${error.message}`);
  }
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
  if (state.cardDeleteMode) return;
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

ui.toggleDeleteModeBtn.onclick = toggleCardDeleteMode;
ui.deleteSelectedCardsBtn.onclick = deleteSelectedCards;
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
async function executeOptimization(cardIds, threads, onProgress) {
  const { run_id: runId } = await api("/api/optimization/run", {
    method: "POST",
    body: JSON.stringify({ card_ids: cardIds, threads }),
  });

  while (true) {
    const status = await api(`/api/optimization/status/${runId}`);
    if (onProgress) {
      onProgress(status, runId);
    }
    if (status.status === "done") {
      return { summary: status.summary || status, runId };
    }
    if (status.status === "error") {
      throw new Error(status.error || "Ошибка оптимизации");
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

function formatDurationHHMMSS(totalSeconds) {
  const safeSeconds = Math.max(0, Math.round(Number(totalSeconds) || 0));
  const hours = Math.floor(safeSeconds / 3600);
  const minutes = Math.floor((safeSeconds % 3600) / 60);
  const seconds = safeSeconds % 60;
  return [hours, minutes, seconds].map((part) => String(part).padStart(2, "0")).join(":");
}

function formatTaskStartedAt(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("ru-RU");
}

function formatClickCounts(clicks) {
  const source = clicks || {};
  const values = ["tel", "site", "route", "msg", "story"].map((key) => Number(source[key] || 0));
  return values.join("/");
}

function renderTaskStatsBlock(stats, task) {
  const block = document.createElement("div");
  block.className = "task-stats";

  if (stats.is_scheduled_preview) {
    const note = document.createElement("div");
    note.className = "task-stats-note";
    note.textContent = stats.scheduled_note || "Статистика выполнения появится после запуска задачи.";
    block.appendChild(note);
  } else if (task?.lastStats && !task.stats && task.status === "scheduled") {
    const note = document.createElement("div");
    note.className = "task-stats-note";
    note.textContent = "Статистика последнего запуска. Следующий запуск запланирован.";
    block.appendChild(note);
  }

  const rows = [
    ["Дата/время старта", formatTaskStartedAt(stats.started_at)],
    ["Кол-во потоков", String(stats.threads ?? "—")],
    ["Всего ключевых фраз", String(stats.total_key_phrases ?? 0)],
    ["Запланировано переходов", String(stats.total_work_units ?? "—")],
    [
      "Успешно выполнено переходов",
      `${stats.total_successful ?? 0} (Поиск: ${stats.search_performed ?? 0}, Карты: ${stats.maps_performed ?? 0})`,
    ],
    [
      "Неудачных попыток",
      stats.is_scheduled_preview ? "—" : String(stats.total_failed_attempts ?? 0),
    ],
    [
      "Среднее время на 1 успешный переход",
      formatDurationHHMMSS(stats.avg_seconds_per_work_unit ?? stats.avg_seconds_per_phrase ?? 0),
    ],
    [
      "Оставшееся время (примерно)",
      stats.is_scheduled_preview ? "—" : formatDurationHHMMSS(stats.estimated_remaining_seconds ?? 0),
    ],
    ["Всего время в работе", stats.is_scheduled_preview ? "—" : formatDurationHHMMSS(stats.elapsed_seconds ?? 0)],
  ];

  for (const [label, value] of rows) {
    const row = document.createElement("div");
    row.className = "task-stats-row";
    row.innerHTML = `<span class="task-stats-label">${label}:</span> <span class="task-stats-value">${value}</span>`;
    block.appendChild(row);
  }

  const divider = document.createElement("div");
  divider.className = "task-stats-divider";
  block.appendChild(divider);

  const detailsTitle = document.createElement("div");
  detailsTitle.className = "task-stats-details-title";
  detailsTitle.textContent = "Детально по карточкам:";
  block.appendChild(detailsTitle);

  const cards = Array.isArray(stats.cards) ? stats.cards : [];
  if (cards.length === 0) {
    const empty = document.createElement("div");
    empty.className = "task-stats-card-line";
    empty.textContent = "Нет данных по карточкам.";
    block.appendChild(empty);
  } else {
    for (const card of cards) {
      const line = document.createElement("div");
      line.className = "task-stats-card-line";
      line.textContent =
        `${card.card_name}: успешно ${card.performed ?? 0}, неудач ${card.failures ?? 0}, в работе ${card.in_flight ?? 0} ` +
        `(Поиск: ${card.search_performed ?? 0}, Карты: ${card.maps_performed ?? 0}) ` +
        `[Клики tel/site/route/msg/story: ${formatClickCounts(card.clicks)}]`;
      block.appendChild(line);
    }
  }

  return block;
}

function getTaskCardLines(task) {
  return task.cardIds.map((cardId) => {
    const card = state.cards.find((item) => item.id === cardId);
    const cardName = card?.name || `Карточка #${cardId}`;
    return {
      card_id: cardId,
      card_name: cardName,
      performed: 0,
      failures: 0,
      in_flight: 0,
      search_performed: 0,
      maps_performed: 0,
      clicks: { tel: 0, site: 0, route: 0, msg: 0, story: 0 },
    };
  });
}

function buildScheduledTaskPreview(task) {
  const when = task.nextAt ? new Date(task.nextAt).toLocaleString("ru-RU") : "—";
  return {
    is_scheduled_preview: true,
    scheduled_note: `Задача ожидает запуска. Следующий старт: ${when}.`,
    started_at: null,
    threads: task.threads,
    total_key_phrases: "—",
    total_work_units: "—",
    total_failed_attempts: 0,
    search_performed: 0,
    maps_performed: 0,
    total_successful: 0,
    avg_seconds_per_phrase: 0,
    estimated_remaining_seconds: 0,
    elapsed_seconds: 0,
    cards: getTaskCardLines(task),
  };
}

function getTaskDisplayStats(task) {
  if (task.stats) return task.stats;
  if (task.lastStats) return task.lastStats;
  if (task.status === "scheduled") return buildScheduledTaskPreview(task);
  return null;
}

function toggleTaskStats(taskId) {
  const task = state.tasks.find((item) => item.id === taskId);
  if (!task) return;
  task.statsExpanded = !task.statsExpanded;
  renderTasks();
}

function ensureTaskStatsPollTimer() {
  const hasRunningWithStats = state.tasks.some((task) => task.status === "running" && task.statsExpanded && task.runId);
  if (hasRunningWithStats && !state.taskStatsPollTimer) {
    state.taskStatsPollTimer = setInterval(async () => {
      const tasksToRefresh = state.tasks.filter(
        (task) => task.status === "running" && task.statsExpanded && task.runId
      );
      if (tasksToRefresh.length === 0) {
        clearInterval(state.taskStatsPollTimer);
        state.taskStatsPollTimer = null;
        return;
      }
      for (const task of tasksToRefresh) {
        try {
          task.stats = await api(`/api/optimization/status/${task.runId}`);
        } catch (_error) {
          // ignore transient polling errors
        }
      }
      renderTasks();
    }, 1000);
  } else if (!hasRunningWithStats && state.taskStatsPollTimer) {
    clearInterval(state.taskStatsPollTimer);
    state.taskStatsPollTimer = null;
  }
}

ui.optimizationPlayBtn.onclick = scheduleTaskFromPlay;
async function openGlobalSettingsModal() {
  ui.globalSettingsModal.classList.remove("hidden");
  try {
    const settings = await api("/api/settings");
    const service = settings.captcha_service || "manual";
    if (service === "capsola") {
      ui.captchaServiceCapsola.checked = true;
    } else if (service === "botlab") {
      ui.captchaServiceBotlab.checked = true;
    } else {
      ui.captchaServiceManual.checked = true;
    }
    ui.capsolaTokenInput.value = settings.capsola_token || "";
    ui.botlabTokenInput.value = settings.botlab_token || "";
    handleCaptchaServiceChange();
    ui.telegramTokenInput.value = settings.telegram_token || "";
    ui.telegramChatIdInput.value = settings.telegram_chat_id || "";
    ui.telegramProxyInput.value = settings.telegram_proxy || "";
  } catch (error) {
    alert(`Не удалось загрузить глобальные настройки: ${error.message}`);
  }
}

async function saveGlobalSettings() {
  let service = "manual";
  if (ui.captchaServiceCapsola.checked) {
    service = "capsola";
  } else if (ui.captchaServiceBotlab.checked) {
    service = "botlab";
  }
  const token = ui.capsolaTokenInput.value.trim();
  const botlabToken = ui.botlabTokenInput.value.trim();
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
        botlab_token: botlabToken,
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
  ui.capsolaSettingsArea.classList.toggle("hidden", !ui.captchaServiceCapsola.checked);
  ui.botlabSettingsArea.classList.toggle("hidden", !ui.captchaServiceBotlab.checked);
}

ui.captchaServiceManual.onchange = handleCaptchaServiceChange;
ui.captchaServiceCapsola.onchange = handleCaptchaServiceChange;
ui.captchaServiceBotlab.onchange = handleCaptchaServiceChange;
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
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const head = days > 0 ? `${days}д ` : "";
  return `${head}${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
}

function setScheduleMode(mode) {
  state.scheduleMode = mode;
  const blocks = {
    realtime: ui.modeRealtime,
    auto: ui.modeAuto,
    deferred: ui.modeDeferred,
  };
  for (const [blockMode, element] of Object.entries(blocks)) {
    element.classList.toggle("active", mode === blockMode);
    element.classList.toggle("dimmed", mode !== blockMode);
  }
}

function renderWeekdays() {
  ui.autoWeekdays.innerHTML = "";
  for (const { day, label } of WEEKDAYS) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `weekday-chip ${state.autoWeekdays.has(day) ? "on" : ""}`;
    chip.textContent = label;
    chip.onclick = () => {
      setScheduleMode("auto");
      if (state.autoWeekdays.has(day)) {
        state.autoWeekdays.delete(day);
      } else {
        state.autoWeekdays.add(day);
      }
      renderWeekdays();
    };
    ui.autoWeekdays.appendChild(chip);
  }
}

function computeNextAutoRun(weekdays, timeStr, fromTs) {
  const [hours, minutes] = timeStr.split(":").map((part) => Number(part));
  if (!Number.isFinite(hours) || !Number.isFinite(minutes)) return null;
  for (let offset = 0; offset <= 7; offset += 1) {
    const candidate = new Date(fromTs);
    candidate.setDate(candidate.getDate() + offset);
    candidate.setHours(hours, minutes, 0, 0);
    if (weekdays.includes(candidate.getDay()) && candidate.getTime() > fromTs) {
      return candidate.getTime();
    }
  }
  return null;
}

function describeTask(task) {
  if (task.type === "realtime") {
    return "Realtime · моментальный запуск";
  }
  if (task.type === "auto") {
    const days = task.weekdays.map((day) => WEEKDAY_SHORT[day]).join(", ");
    return `Автозапуск · ${days} в ${task.time}`;
  }
  return `Отложенный запуск · ${new Date(task.nextAt).toLocaleString("ru-RU")}`;
}

function armTask(task) {
  if (task.paused || task.stopRequested) return;
  const delay = task.nextAt - Date.now();
  if (delay > MAX_TIMEOUT_MS) {
    task.timer = setTimeout(() => armTask(task), MAX_TIMEOUT_MS);
    return;
  }
  task.timer = setTimeout(() => runTask(task), Math.max(0, delay));
}

function scheduleTaskFromPlay() {
  const cardIds = [...state.optimizationSelectedCardIds];
  if (cardIds.length === 0) {
    alert("Выберите хотя бы одну карточку для оптимизации.");
    return;
  }
  const threads = state.optimizationThreads;
  if (threads <= 0) {
    alert("Укажите количество потоков больше 0.");
    return;
  }

  const task = {
    id: ++state.taskSeq,
    type: state.scheduleMode,
    cardIds,
    threads,
    status: "scheduled",
    resultText: "",
    stats: null,
    lastStats: null,
    statsExpanded: false,
    runId: null,
    timer: null,
    paused: false,
    pausedRemainingMs: null,
    pauseOnRunId: false,
    stopRequested: false,
    weekdays: [],
    time: "",
    nextAt: null,
  };

  if (state.scheduleMode === "realtime") {
    task.nextAt = Date.now();
  } else if (state.scheduleMode === "auto") {
    const weekdays = [...state.autoWeekdays].sort((a, b) => a - b);
    if (weekdays.length === 0) {
      alert("Выберите хотя бы один день недели для автозапуска.");
      return;
    }
    const time = (ui.autoTimeInput.value || "").trim();
    if (!time) {
      alert("Укажите время автозапуска.");
      return;
    }
    const nextAt = computeNextAutoRun(weekdays, time, Date.now());
    if (!nextAt) {
      alert("Не удалось вычислить время запуска. Проверьте дни недели и время.");
      return;
    }
    task.weekdays = weekdays;
    task.time = time;
    task.nextAt = nextAt;
  } else {
    const value = ui.deferredDateTimeInput.value;
    if (!value) {
      alert("Укажите дату и время отложенного запуска.");
      return;
    }
    const timestamp = new Date(value).getTime();
    if (!Number.isFinite(timestamp) || timestamp <= Date.now()) {
      alert("Дата и время запуска должны быть в будущем.");
      return;
    }
    task.nextAt = timestamp;
  }

  state.tasks.push(task);
  state.optimizationSelectedCardIds.clear();
  renderCards();
  refreshOptimizationStatus();
  armTask(task);
  renderTasks();
  ensureCountdownTimer();
}

async function runTask(task) {
  if (task.paused || task.stopRequested) return;
  task.timer = null;
  task.status = "running";
  task.stats = null;
  task.statsExpanded = false;
  task.runId = null;
  renderTasks();
  try {
    const { summary: result } = await executeOptimization(task.cardIds, task.threads, (progress, runId) => {
      task.runId = runId;
      task.stats = progress;
      if (progress.dispatch_control === "paused") {
        task.paused = true;
      } else if (progress.dispatch_control === "active") {
        task.paused = false;
      }
      if (task.stopRequested && runId) {
        api(`/api/optimization/stop/${runId}`, { method: "POST" }).catch(() => {});
      }
      if (task.pauseOnRunId && runId) {
        task.pauseOnRunId = false;
        api(`/api/optimization/pause/${runId}`, { method: "POST" })
          .then(() => {
            task.paused = true;
            renderTasks();
          })
          .catch(() => {});
      }
      if (task.statsExpanded) {
        renderTasks();
      }
    });
    const finishedAt = new Date().toLocaleTimeString("ru-RU");
    const stoppedByUser = Boolean(result.stopped_by_user || task.stopRequested);
    task.status = "done";
    task.stats = task.stats && task.stats.status === "done" ? task.stats : {
      status: "done",
      started_at: result.started_at,
      threads: task.threads,
      total_key_phrases: task.stats?.total_key_phrases ?? 0,
      total_work_units: task.stats?.total_work_units ?? ((result.total_search_target || 0) + (result.total_maps_target || 0)),
      search_performed: result.total_search_performed || 0,
      maps_performed: result.total_maps_performed || 0,
      total_successful: (result.total_search_performed || 0) + (result.total_maps_performed || 0),
      total_failed_attempts: 0,
      elapsed_seconds: result.duration_seconds || 0,
      avg_seconds_per_phrase: result.duration_seconds && ((result.total_search_performed || 0) + (result.total_maps_performed || 0))
        ? result.duration_seconds / ((result.total_search_performed || 0) + (result.total_maps_performed || 0))
        : 0,
      avg_seconds_per_work_unit: result.duration_seconds && ((result.total_search_performed || 0) + (result.total_maps_performed || 0))
        ? result.duration_seconds / ((result.total_search_performed || 0) + (result.total_maps_performed || 0))
        : 0,
      estimated_remaining_seconds: 0,
      cards: Array.isArray(result.cards)
        ? result.cards.map((card) => ({
            card_id: card.card_id,
            card_name: card.card_name || card.organization || "Без названия",
            performed: (card.search_performed || 0) + (card.maps_performed || 0),
            failures:
              Math.max(0, (card.search_target || 0) - (card.search_performed || 0)) +
              Math.max(0, (card.maps_target || 0) - (card.maps_performed || 0)),
            in_flight: 0,
            search_performed: card.search_performed || 0,
            maps_performed: card.maps_performed || 0,
            clicks: {
              tel: card.maps_action_counts?.["Показать телефон"] || 0,
              site: card.maps_action_counts?.["Сайт"] || 0,
              route: card.maps_action_counts?.["Маршрут"] || 0,
              msg: card.maps_action_counts?.["мессенджер"] || 0,
              story: card.maps_action_counts?.["Записаться"] || 0,
            },
          }))
        : [],
    };
    task.resultText = stoppedByUser
      ? `Остановлено в ${finishedAt}. Карточек: ${result.processed_cards}, ` +
        `Поиск: ${result.total_search_performed}/${result.total_search_target}, ` +
        `Карты: ${result.total_maps_performed}/${result.total_maps_target}.`
      : `Готово в ${finishedAt}. Карточек: ${result.processed_cards}, ` +
        `Поиск: ${result.total_search_performed}/${result.total_search_target}, ` +
        `Карты: ${result.total_maps_performed}/${result.total_maps_target}.`;
    task.paused = false;
    task.stopRequested = false;
    task.pausedRemainingMs = null;
  } catch (error) {
    task.status = "error";
    task.resultText = `Ошибка запуска: ${error.message}`;
    if (task.stats) {
      task.stats.status = "error";
      task.stats.error = error.message;
    }
  }

  if (task.type === "auto" && !task.stopRequested) {
    const nextAt = computeNextAutoRun(task.weekdays, task.time, Date.now());
    if (nextAt) {
      if (task.stats) {
        task.lastStats = task.stats;
      }
      task.nextAt = nextAt;
      task.status = "scheduled";
      task.stats = null;
      task.statsExpanded = false;
      task.runId = null;
      task.resultText = "";
      armTask(task);
    }
  }

  renderTasks();
  ensureCountdownTimer();
  ensureTaskStatsPollTimer();
}

function pauseTask(taskId) {
  const task = state.tasks.find((item) => item.id === taskId);
  if (!task || task.paused || task.stopRequested) return;
  if (task.status === "scheduled") {
    if (task.timer) clearTimeout(task.timer);
    task.timer = null;
    task.pausedRemainingMs = Math.max(0, task.nextAt - Date.now());
    task.paused = true;
    renderTasks();
    ensureCountdownTimer();
    return;
  }
  if (task.status === "running") {
    if (task.runId) {
      api(`/api/optimization/pause/${task.runId}`, { method: "POST" })
        .then(() => {
          task.paused = true;
          renderTasks();
        })
        .catch((error) => alert(`Не удалось поставить задачу на паузу: ${error.message}`));
      return;
    }
    task.pauseOnRunId = true;
    task.paused = true;
    renderTasks();
  }
}

function startTask(taskId) {
  const task = state.tasks.find((item) => item.id === taskId);
  if (!task || !task.paused || task.stopRequested) return;
  if (task.status === "scheduled") {
    task.nextAt = Date.now() + (task.pausedRemainingMs || 0);
    task.pausedRemainingMs = null;
    task.paused = false;
    armTask(task);
    renderTasks();
    ensureCountdownTimer();
    return;
  }
  if (task.status === "running" && task.runId) {
    api(`/api/optimization/resume/${task.runId}`, { method: "POST" })
      .then(() => {
        task.paused = false;
        renderTasks();
      })
      .catch((error) => alert(`Не удалось возобновить задачу: ${error.message}`));
  }
}

function stopTask(taskId) {
  const task = state.tasks.find((item) => item.id === taskId);
  if (!task || task.stopRequested) return;
  task.stopRequested = true;
  task.paused = false;
  task.pausedRemainingMs = null;
  if (task.status === "scheduled") {
    if (task.timer) clearTimeout(task.timer);
    task.timer = null;
    cancelTask(taskId);
    return;
  }
  if (task.status === "running" && task.runId) {
    api(`/api/optimization/stop/${task.runId}`, { method: "POST" })
      .then(() => renderTasks())
      .catch((error) => alert(`Не удалось остановить задачу: ${error.message}`));
    return;
  }
  renderTasks();
}

function cancelTask(taskId) {
  const index = state.tasks.findIndex((item) => item.id === taskId);
  if (index === -1) return;
  const [task] = state.tasks.splice(index, 1);
  if (task.timer) clearTimeout(task.timer);
  renderTasks();
  ensureCountdownTimer();
}

function ensureCountdownTimer() {
  const hasPending = state.tasks.some((task) => task.status === "scheduled" && task.nextAt);
  if (hasPending && !state.tasksCountdownTimer) {
    state.tasksCountdownTimer = setInterval(tickCountdowns, 1000);
  } else if (!hasPending && state.tasksCountdownTimer) {
    clearInterval(state.tasksCountdownTimer);
    state.tasksCountdownTimer = null;
  }
}

function getTaskCountdownMs(task) {
  if (task.status !== "scheduled" || !task.nextAt) return 0;
  if (task.paused && task.pausedRemainingMs != null) {
    return task.pausedRemainingMs;
  }
  return task.nextAt - Date.now();
}

function tickCountdowns() {
  for (const task of state.tasks) {
    if (task.status !== "scheduled" || !task.nextAt || task.paused) continue;
    const span = ui.optimizationTasksList.querySelector(`.task-countdown[data-task="${task.id}"]`);
    if (span) span.textContent = formatCountdown(task.nextAt - Date.now());
  }
}

function renderTaskControlButtons(task) {
  const group = document.createElement("div");
  group.className = "task-controls";

  const showControls = task.status === "scheduled" || task.status === "running";
  if (!showControls) {
    return group;
  }

  const startBtn = document.createElement("button");
  startBtn.type = "button";
  startBtn.className = "btn task-control";
  startBtn.textContent = "Старт";
  startBtn.disabled = !task.paused || task.stopRequested;
  startBtn.onclick = (event) => {
    event.stopPropagation();
    startTask(task.id);
  };

  const stopBtn = document.createElement("button");
  stopBtn.type = "button";
  stopBtn.className = "btn task-control";
  stopBtn.textContent = "Стоп";
  stopBtn.disabled = task.stopRequested;
  stopBtn.onclick = (event) => {
    event.stopPropagation();
    stopTask(task.id);
  };

  const pauseBtn = document.createElement("button");
  pauseBtn.type = "button";
  pauseBtn.className = "btn task-control";
  pauseBtn.textContent = "Пауза";
  pauseBtn.disabled = task.paused || task.stopRequested;
  pauseBtn.onclick = (event) => {
    event.stopPropagation();
    pauseTask(task.id);
  };

  group.appendChild(startBtn);
  group.appendChild(stopBtn);
  group.appendChild(pauseBtn);
  return group;
}

function renderTasks() {
  ui.optimizationTasksList.innerHTML = "";
  for (const task of state.tasks) {
    const displayStats = getTaskDisplayStats(task);
    const card = document.createElement("div");
    card.className = `task-card ${task.status}${task.paused ? " paused" : ""} task-card-clickable`;
    card.onclick = () => toggleTaskStats(task.id);
    card.title = task.statsExpanded ? "Скрыть статистику" : "Показать статистику";

    const row = document.createElement("div");
    row.className = "task-row";

    const title = document.createElement("div");
    title.className = "task-title";
    const dot = document.createElement("span");
    dot.className = "task-dot";
    title.appendChild(dot);
    title.appendChild(document.createTextNode(describeTask(task)));
    row.appendChild(title);

    row.appendChild(renderTaskControlButtons(task));

    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "btn task-cancel";
    cancelBtn.textContent = task.status === "done" || task.status === "error" ? "Убрать" : "Отменить";
    cancelBtn.onclick = (event) => {
      event.stopPropagation();
      cancelTask(task.id);
    };
    row.appendChild(cancelBtn);

    card.appendChild(row);

    const meta = document.createElement("div");
    meta.className = "task-meta";
    if (task.status === "scheduled" && task.nextAt) {
      const when = new Date(task.nextAt).toLocaleString("ru-RU");
      const pauseNote = task.paused ? " На паузе." : "";
      meta.innerHTML =
        `Карточек: ${task.cardIds.length}, потоки: ${task.threads}. ` +
        `Следующий запуск: <b>${when}</b> · ` +
        `через <span class="task-countdown" data-task="${task.id}">${formatCountdown(getTaskCountdownMs(task))}</span>` +
        pauseNote;
      meta.innerHTML += task.statsExpanded
        ? " Статистика развёрнута."
        : " Нажмите, чтобы открыть статистику.";
    } else if (task.status === "running") {
      const pauseNote = task.paused ? " На паузе — открытые окна дорабатывают." : "";
      const stopNote = task.stopRequested ? " Останавливается — новые окна не открываются." : "";
      meta.textContent =
        `Выполняется… Карточек: ${task.cardIds.length}, потоки: ${task.threads}.` +
        pauseNote +
        stopNote;
      meta.textContent += task.statsExpanded
        ? " Статистика развёрнута."
        : " Нажмите, чтобы открыть статистику.";
    } else {
      meta.textContent = `Карточек: ${task.cardIds.length}, потоки: ${task.threads}.`;
      meta.textContent += task.statsExpanded
        ? " Статистика развёрнута."
        : " Нажмите, чтобы открыть статистику.";
    }
    card.appendChild(meta);

    if (task.resultText && !task.statsExpanded) {
      const result = document.createElement("div");
      result.className = "task-result";
      result.textContent = task.resultText;
      card.appendChild(result);
    }

    if (task.statsExpanded && displayStats) {
      card.appendChild(renderTaskStatsBlock(displayStats, task));
    }

    ui.optimizationTasksList.appendChild(card);
  }
  ensureTaskStatsPollTimer();
}

ui.modeRealtime.addEventListener("mousedown", () => setScheduleMode("realtime"));
ui.modeRealtime.addEventListener("focusin", () => setScheduleMode("realtime"));
ui.modeAuto.addEventListener("mousedown", () => setScheduleMode("auto"));
ui.modeAuto.addEventListener("focusin", () => setScheduleMode("auto"));
ui.modeDeferred.addEventListener("mousedown", () => setScheduleMode("deferred"));
ui.modeDeferred.addEventListener("focusin", () => setScheduleMode("deferred"));

function initScheduleModePanel() {
  renderWeekdays();
  const soon = new Date(Date.now() + 60 * 60 * 1000);
  ui.deferredDateTimeInput.value = toLocalInputValue(soon);
  const pad = (n) => String(n).padStart(2, "0");
  ui.autoTimeInput.value = `${pad(soon.getHours())}:${pad(soon.getMinutes())}`;
  setScheduleMode("auto");
}

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
  if (event.target === ui.settingsModal && state.settingsMode !== "create") {
    ui.settingsModal.classList.add("hidden");
  }
};
ui.saveSettingsBtn.onclick = saveSettings;
ui.autofillYandexOrgBtn.onclick = autofillByYandexOrgUrl;
ui.applyDefaultsBtn.onclick = applyCardDefaultsToSettingsForm;
ui.openDefaultsConfigBtn.onclick = openDefaultsConfigModal;
ui.closeDefaultsConfigBtn.onclick = () => ui.defaultsConfigModal.classList.add("hidden");
ui.saveDefaultsConfigBtn.onclick = saveDefaultsConfig;
ui.defaultsConfigModal.onclick = (event) => {
  if (event.target === ui.defaultsConfigModal) {
    ui.defaultsConfigModal.classList.add("hidden");
  }
};
window.addEventListener("pagehide", requestShutdownOnClose);

syncOptimizationThreadsUi();
initScheduleModePanel();
startup();
