const tg = window.Telegram?.WebApp;
const launchParams = new URLSearchParams(window.location.search);
const hasSignedLaunch = Boolean(launchParams.get("uid") && launchParams.get("sig"));
const isDevMode = launchParams.get("dev") === "1";
const THEME_STORAGE_KEY = "mealbot:theme";
const THEMES = {
  mealbot: "MealBot",
  authkit: "AuthKit",
  peak: "Peak",
  stripe: "Stripe",
  seed: "Seed",
};
if (tg) {
  tg.ready();
  tg.expand();
  tg.setHeaderColor("#ffffff");
  tg.setBackgroundColor("#ffffff");
}

function triggerGarminSyncFromMiniApp() {
  const platform = String(tg?.platform || navigator.userAgent || "").toLowerCase();
  if (!platform.includes("android")) return;
  if (sessionStorage.getItem("mealbot:garmin-sync-opened") === "1") return;

  sessionStorage.setItem("mealbot:garmin-sync-opened", "1");
  const iframe = document.createElement("iframe");
  iframe.hidden = true;
  iframe.style.display = "none";
  iframe.src = "intent://sync#Intent;scheme=garum-health-bridge;package=tech.garum.healthconnectbridge;end";
  document.documentElement.appendChild(iframe);
  window.setTimeout(() => iframe.remove(), 1500);
  window.setTimeout(() => loadMeals({ force: true, live: true }).catch(() => {}), 9000);
}

const state = {
  currentMonth: new Date(),
  selectedDay: toISODate(new Date()),
  meals: [],
  days: [],
  activeTab: "today",
  theme: localStorage.getItem(THEME_STORAGE_KEY) || "mealbot",
  chartMode: "kcal",
  devUserId: localStorage.getItem("mealbot:user_id") || "",
  devToken: localStorage.getItem("mealbot:token") || "",
  monthCache: new Map(),
  prefetching: new Set(),
  cheatdaySaving: false,
  lastCheatdayTapAt: 0,
};

const NUTRITION_PROFILE = {
  bodyWeightKg: 80,
  proteinMinPerKg: 1.2,
  proteinMaxPerKg: 1.6,
  fatMinEnergyPct: 20,
  fatMaxEnergyPct: 35,
  carbsMinEnergyPct: 45,
  carbsMaxEnergyPct: 65,
  proteinMinEnergyPct: 10,
  proteinMaxEnergyPct: 35,
};

NUTRITION_PROFILE.proteinMinG = Math.round(NUTRITION_PROFILE.bodyWeightKg * NUTRITION_PROFILE.proteinMinPerKg);
NUTRITION_PROFILE.proteinMaxG = Math.round(NUTRITION_PROFILE.bodyWeightKg * NUTRITION_PROFILE.proteinMaxPerKg);

const MACRO_COLORS = {
  protein: "#a3e635",
  fat: "#fbbf25",
  carbs: "#f5d1fe",
};

const els = {
  devAuth: document.getElementById("devAuth"),
  devUserId: document.getElementById("devUserId"),
  devToken: document.getElementById("devToken"),
  saveDevAuth: document.getElementById("saveDevAuth"),
  mainTabs: document.querySelectorAll("[data-main-tab]"),
  tabPages: document.querySelectorAll("[data-tab-page]"),
  themeBadge: document.getElementById("themeBadge"),
  themeOptions: document.querySelectorAll("[data-theme-option]"),
  totalKcal: document.getElementById("totalKcal"),
  totalProtein: document.getElementById("totalProtein"),
  totalFat: document.getElementById("totalFat"),
  totalCarbs: document.getElementById("totalCarbs"),
  intakeCaption: document.getElementById("intakeCaption"),
  expenseCaption: document.getElementById("expenseCaption"),
  balanceCaption: document.getElementById("balanceCaption"),
  macroCaption: document.getElementById("macroCaption"),
  nutritionBasis: document.getElementById("nutritionBasis"),
  nutritionTargets: document.getElementById("nutritionTargets"),
  monthLabel: document.getElementById("monthLabel"),
  calendar: document.getElementById("calendar"),
  mealList: document.getElementById("mealList"),
  selectedDayTitle: document.getElementById("selectedDayTitle"),
  dayCount: document.getElementById("dayCount"),
  expenditureButton: document.getElementById("expenditureButton"),
  cheatdayToggle: document.getElementById("cheatdayToggle"),
  chartCanvas: document.getElementById("chartCanvas"),
  toast: document.getElementById("toast"),
  loadingOverlay: document.getElementById("loadingOverlay"),
  dialog: document.getElementById("editDialog"),
  editForm: document.getElementById("editForm"),
  expenditureDialog: document.getElementById("expenditureDialog"),
  expenditureForm: document.getElementById("expenditureForm"),
  manualExpenditure: document.getElementById("manualExpenditure"),
};

function toISODate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function monthBounds(date) {
  const start = new Date(date.getFullYear(), date.getMonth(), 1);
  const end = new Date(date.getFullYear(), date.getMonth() + 1, 0);
  return { start: toISODate(start), end: toISODate(end) };
}

function monthKey(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function apiHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (tg?.initData) {
    headers["X-Telegram-Init-Data"] = tg.initData;
  } else if (state.devToken) {
    headers["X-Meal-Token"] = state.devToken;
  }
  return headers;
}

function authQuery() {
  if (tg?.initData) return "";
  const params = new URLSearchParams();
  if (hasSignedLaunch) {
    params.set("uid", launchParams.get("uid"));
    params.set("sig", launchParams.get("sig"));
  } else {
    if (state.devUserId) params.set("user_id", state.devUserId);
    if (state.devToken) params.set("token", state.devToken);
  }
  const query = params.toString();
  return query ? `&${query}` : "";
}

function authSearch() {
  const query = authQuery().replace(/^&/, "");
  return query ? `?${query}` : "";
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { ...apiHeaders(), ...(options.headers || {}) },
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function applyMealsData(data) {
  state.meals = data.meals || [];
  state.days = data.days || [];
  renderSummary();
  renderCalendar();
  renderMeals();
  drawChart();
}

function setPageLoading(isLoading) {
  document.body.classList.toggle("page-loading", isLoading);
  els.loadingOverlay.hidden = !isLoading;
}

function switchMainTab(tabName) {
  state.activeTab = tabName;
  els.mainTabs.forEach((button) => {
    const isActive = button.dataset.mainTab === tabName;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-selected", String(isActive));
  });
  els.tabPages.forEach((page) => {
    page.hidden = page.dataset.tabPage !== tabName;
    page.classList.toggle("active", page.dataset.tabPage === tabName);
  });
  if (tabName === "week") {
    requestAnimationFrame(drawChart);
  }
}

function syncTelegramTheme() {
  const background = getComputedStyle(document.body).getPropertyValue("--color-canvas-white").trim() || "#ffffff";
  if (!tg) return;
  tg.setHeaderColor(background);
  tg.setBackgroundColor(background);
}

function syncThemeColors() {
  const styles = getComputedStyle(document.body);
  MACRO_COLORS.protein = styles.getPropertyValue("--macro-protein").trim() || MACRO_COLORS.protein;
  MACRO_COLORS.fat = styles.getPropertyValue("--macro-fat").trim() || MACRO_COLORS.fat;
  MACRO_COLORS.carbs = styles.getPropertyValue("--macro-carbs").trim() || MACRO_COLORS.carbs;
}

function applyTheme(themeName) {
  const nextTheme = THEMES[themeName] ? themeName : "mealbot";
  state.theme = nextTheme;
  document.body.dataset.theme = nextTheme;
  syncThemeColors();
  els.themeBadge.textContent = THEMES[nextTheme];
  els.themeOptions.forEach((button) => {
    const isActive = button.dataset.themeOption === nextTheme;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
  });
  syncTelegramTheme();
  drawChart();
}

function setTheme(themeName) {
  localStorage.setItem(THEME_STORAGE_KEY, themeName);
  applyTheme(themeName);
  showToast(`Тема: ${THEMES[state.theme]}`);
}

async function fetchMonth(date, { live = false } = {}) {
  const { start, end } = monthBounds(date);
  const liveQuery = live ? "&live=1" : "";
  return requestJson(`/api/meals?from=${start}&to=${end}${liveQuery}${authQuery()}`);
}

async function prefetchAdjacentMonths() {
  const months = [
    new Date(state.currentMonth.getFullYear(), state.currentMonth.getMonth() - 1, 1),
    new Date(state.currentMonth.getFullYear(), state.currentMonth.getMonth() + 1, 1),
  ];

  await Promise.all(months.map(async (date) => {
    const key = monthKey(date);
    if (state.monthCache.has(key) || state.prefetching.has(key)) return;
    state.prefetching.add(key);
    try {
      state.monthCache.set(key, await fetchMonth(date));
    } catch (error) {
      console.warn("Month prefetch failed", key, error);
    } finally {
      state.prefetching.delete(key);
    }
  }));
}

async function loadMeals({ force = false, live = false } = {}) {
  const refreshButton = document.getElementById("refreshButton");
  const key = monthKey(state.currentMonth);

  if (!force && state.monthCache.has(key)) {
    applyMealsData(state.monthCache.get(key));
    prefetchAdjacentMonths();
    return;
  }

  refreshButton.classList.add("loading");
  refreshButton.setAttribute("aria-busy", "true");
  try {
    const data = await fetchMonth(state.currentMonth, { live });
    state.monthCache.set(key, data);
    applyMealsData(data);
    prefetchAdjacentMonths();
  } finally {
    setPageLoading(false);
    refreshButton.classList.remove("loading");
    refreshButton.removeAttribute("aria-busy");
  }
}

function selectedMeals() {
  return state.meals.filter((meal) => meal.food_day === state.selectedDay);
}

function selectedDayData() {
  return state.days.find((day) => day.date === state.selectedDay) || {
    date: state.selectedDay,
    count: 0,
    totals: {},
    balance: {},
  };
}

function dominantMacro(meal) {
  const macros = [
    ["protein", meal.protein_g || 0],
    ["fat", meal.fat_g || 0],
    ["carbs", meal.carbs_g || 0],
  ];
  const top = macros.reduce((best, current) => (current[1] > best[1] ? current : best));
  return top[1] > 0 ? top[0] : "empty";
}

function renderSummary() {
  const day = selectedDayData();
  const totals = day.totals || {};
  const balance = day.balance || {};
  const isCheatmeal = Boolean(day.is_cheatmeal);
  const intake = Math.round(balance.intake_kcal ?? totals.kcal ?? 0);
  const expense = balance.expenditure_kcal == null ? null : Math.round(balance.expenditure_kcal);
  const diff = balance.difference_kcal == null ? null : Math.round(balance.difference_kcal);
  const expenseNote = balance.note || "";
  const protein = Math.round(totals.protein_g || 0);
  const fat = Math.round(totals.fat_g || 0);
  const carbs = Math.round(totals.carbs_g || 0);

  els.totalKcal.textContent = intake;
  els.totalProtein.textContent = expense == null ? "—" : expense;
  els.totalFat.textContent = diff == null ? "—" : `${diff > 0 ? "+" : ""}${diff}`;
  els.totalCarbs.textContent = isCheatmeal ? "0/0/0" : `${protein}/${fat}/${carbs}`;

  els.intakeCaption.textContent = isCheatmeal ? "читмил: приход не считается" : "ккал за выбранный день";
  els.expenseCaption.textContent = expense == null ? expenseNote || "нет данных расхода" : "ккал сожжено";
  els.balanceCaption.textContent = diff == null ? "ждём расход" : diff > 0 ? "профицит, ккал" : "дефицит, ккал";
  if (isCheatmeal) {
    els.macroCaption.textContent = "БЖУ не учитываем";
  } else if (protein <= 0) {
    els.macroCaption.textContent = `цель белка ${NUTRITION_PROFILE.proteinMinG}-${NUTRITION_PROFILE.proteinMaxG} г`;
  } else if (protein < NUTRITION_PROFILE.proteinMinG) {
    els.macroCaption.textContent = `белок: ещё ${NUTRITION_PROFILE.proteinMinG - protein} г до цели`;
  } else if (protein > NUTRITION_PROFILE.proteinMaxG) {
    els.macroCaption.textContent = `белок выше цели ${NUTRITION_PROFILE.proteinMinG}-${NUTRITION_PROFILE.proteinMaxG} г`;
  } else {
    els.macroCaption.textContent = `белок в цели ${NUTRITION_PROFILE.proteinMinG}-${NUTRITION_PROFILE.proteinMaxG} г`;
  }
  els.cheatdayToggle.classList.toggle("active", isCheatmeal);
  els.cheatdayToggle.setAttribute("aria-pressed", String(isCheatmeal));
  renderNutritionTargets({
    intake,
    protein: isCheatmeal ? 0 : protein,
    fat: isCheatmeal ? 0 : fat,
    carbs: isCheatmeal ? 0 : carbs,
    isCheatmeal,
  });
}

function renderNutritionTargets({ intake, protein, fat, carbs, isCheatmeal = false }) {
  const proteinKcal = protein * 4;
  const fatKcal = fat * 9;
  const carbsKcal = carbs * 4;
  const macroKcal = proteinKcal + fatKcal + carbsKcal;
  const proteinPct = macroKcal > 0 ? (proteinKcal / macroKcal) * 100 : 0;
  const fatPct = macroKcal > 0 ? (fatKcal / macroKcal) * 100 : 0;
  const carbsPct = macroKcal > 0 ? (carbsKcal / macroKcal) * 100 : 0;
  els.nutritionBasis.textContent = isCheatmeal ? "читмил" : "цели";

  if (isCheatmeal) {
    els.nutritionTargets.innerHTML = `
      <article class="target-row cheatday-note">
        <strong>Приход за день выключен</strong>
        <span>Расход остаётся в статистике и графике баланса.</span>
      </article>
    `;
    return;
  }

  const proteinRow = targetRowHtml({
    label: "Белок",
    value: protein,
    min: NUTRITION_PROFILE.proteinMinG,
    max: NUTRITION_PROFILE.proteinMaxG,
    note: `${NUTRITION_PROFILE.proteinMinPerKg}-${NUTRITION_PROFILE.proteinMaxPerKg} г/кг`,
    color: MACRO_COLORS.protein,
  });

  els.nutritionTargets.innerHTML = `
    ${proteinRow}
    <article class="macro-balance">
      <header class="target-meta">
        <strong>Баланс калорий БЖУ</strong>
        <span>${macroKcal > 0 ? "100%" : "нет данных"}</span>
      </header>
      <div class="macro-strip" aria-label="Доля калорий из белков, жиров и углеводов">
        ${macroKcal > 0 ? `
          <span class="macro-segment" style="width:${proteinPct}%;background:${MACRO_COLORS.protein}"></span>
          <span class="macro-segment" style="width:${fatPct}%;background:${MACRO_COLORS.fat}"></span>
          <span class="macro-segment" style="width:${carbsPct}%;background:${MACRO_COLORS.carbs}"></span>
        ` : '<span class="macro-segment empty"></span>'}
      </div>
      <div class="macro-details">
        ${macroDetailHtml("Белки", proteinPct, `${NUTRITION_PROFILE.proteinMinEnergyPct}-${NUTRITION_PROFILE.proteinMaxEnergyPct}%`, macroPctStatus(proteinPct, NUTRITION_PROFILE.proteinMinEnergyPct, NUTRITION_PROFILE.proteinMaxEnergyPct), MACRO_COLORS.protein)}
        ${macroDetailHtml("Жиры", fatPct, `${NUTRITION_PROFILE.fatMinEnergyPct}-${NUTRITION_PROFILE.fatMaxEnergyPct}%`, macroPctStatus(fatPct, NUTRITION_PROFILE.fatMinEnergyPct, NUTRITION_PROFILE.fatMaxEnergyPct), MACRO_COLORS.fat)}
        ${macroDetailHtml("Углеводы", carbsPct, `${NUTRITION_PROFILE.carbsMinEnergyPct}-${NUTRITION_PROFILE.carbsMaxEnergyPct}%`, macroPctStatus(carbsPct, NUTRITION_PROFILE.carbsMinEnergyPct, NUTRITION_PROFILE.carbsMaxEnergyPct), MACRO_COLORS.carbs)}
      </div>
    </article>
  `;
}

function targetRowHtml(row) {
  const hasRange = row.min != null && row.max != null;
  const scaleMax = hasRange ? Math.max(row.max * 1.25, row.value, 1) : Math.max(row.value, 1);
  const rangeLeft = hasRange ? clamp((row.min / scaleMax) * 100, 0, 100) : 0;
  const rangeWidth = hasRange ? clamp(((row.max - row.min) / scaleMax) * 100, 0, 100 - rangeLeft) : 0;
  const rangeRight = clamp(rangeLeft + rangeWidth, 0, 100);
  const valueWidth = clamp((row.value / scaleMax) * 100, 0, 100);
  const valueStyle = row.value > 0 ? `width:${valueWidth}%;background:${row.color}` : "width:0";
  const rangeText = hasRange ? `${row.min}-${row.max} г` : row.note;
  const status = hasRange ? nutritionStatus(row.value, row.min, row.max) : "";
  const scaleMaxLabel = Math.round(scaleMax);
  return `
    <article class="target-row">
      <header>
        <strong>${row.label}</strong>
        <span>${Math.round(row.value)} г / ${rangeText}</span>
      </header>
      <div class="target-track" aria-hidden="true">
        ${hasRange ? `<span class="target-range" style="left:${rangeLeft}%;width:${rangeWidth}%;color:${row.color}"></span>` : ""}
        <span class="target-value" style="${valueStyle}"></span>
        ${hasRange ? `<span class="target-marker edge" style="left:0">0</span><span class="target-marker" style="left:${rangeLeft}%">${row.min}</span><span class="target-marker" style="left:${rangeRight}%">${row.max}</span><span class="target-marker edge" style="left:100%">${scaleMaxLabel}г</span>` : ""}
      </div>
      <div class="target-meta">
        <span>${row.note}</span>
        <span>${status}</span>
      </div>
    </article>
  `;
}

function macroDetailHtml(label, value, range, status, color) {
  return `
    <div class="macro-detail">
      <span class="macro-dot" style="background:${color}"></span>
      <span><strong>${label}</strong> ${Math.round(value)}% / ${range}</span>
      <span>${status}</span>
    </div>
  `;
}

function macroPctStatus(value, min, max) {
  if (value <= 0) return "нет данных";
  if (value < min) return "ниже";
  if (value > max) return "выше";
  return "в норме";
}

function nutritionStatus(value, min, max) {
  if (value <= 0) return "нет данных";
  if (value < min) return `ещё ${Math.round(min - value)} г`;
  if (value > max) return `выше на ${Math.round(value - max)} г`;
  return "в норме";
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function renderCalendar() {
  const date = state.currentMonth;
  const formatter = new Intl.DateTimeFormat("ru-RU", { month: "long", year: "numeric" });
  els.monthLabel.textContent = formatter.format(date);
  els.calendar.innerHTML = "";

  const first = new Date(date.getFullYear(), date.getMonth(), 1);
  const daysInMonth = new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
  const offset = (first.getDay() + 6) % 7;
  const byDate = new Map(state.days.map((day) => [day.date, day]));

  for (let i = 0; i < offset; i += 1) {
    const empty = document.createElement("span");
    empty.className = "day empty";
    els.calendar.appendChild(empty);
  }

  for (let day = 1; day <= daysInMonth; day += 1) {
    const current = new Date(date.getFullYear(), date.getMonth(), day);
    const key = toISODate(current);
    const data = byDate.get(key);
    const intake = data ? Math.round(data.balance?.intake_kcal ?? data.totals.kcal ?? 0) : 0;
    const expenditure = data?.balance?.expenditure_kcal == null ? null : Math.round(data.balance.expenditure_kcal);
    const difference = data?.balance?.difference_kcal;
    const isCheatmeal = Boolean(data?.is_cheatmeal);
    const balanceClass = !isCheatmeal && typeof difference === "number"
      ? difference > 0
        ? "profit-day"
        : difference < 0
          ? "deficit-day"
          : "balanced-day"
      : "";
    const button = document.createElement("button");
    button.type = "button";
    button.className = `day ${data ? "has-data" : ""} ${balanceClass} ${isCheatmeal ? "cheatday" : ""} ${key === state.selectedDay ? "selected" : ""}`;
    button.innerHTML = `
      <span class="num">${day}</span>
      <span class="kcal">${data ? (isCheatmeal ? "Читмил" : `Е ${intake}`) : ""}</span>
      <span class="burn">${expenditure == null ? "" : `Р ${expenditure}`}</span>
    `;
    button.addEventListener("click", () => {
      state.selectedDay = key;
      renderSummary();
      renderCalendar();
      renderMeals();
    });
    els.calendar.appendChild(button);
  }
}

function renderMeals() {
  const meals = selectedMeals();
  els.selectedDayTitle.textContent = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
  }).format(new Date(`${state.selectedDay}T12:00:00`));
  els.dayCount.textContent = `${meals.length} записей`;
  els.mealList.innerHTML = "";

  if (!meals.length) {
    els.mealList.innerHTML = '<p class="empty-state">За этот день записей нет.</p>';
    return;
  }

  meals.sort((a, b) => a.timestamp.localeCompare(b.timestamp)).forEach((meal) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = `meal-row macro-${dominantMacro(meal)}`;
    row.innerHTML = `
      <span>
        <strong>${escapeHtml(meal.name || "Без названия")}</strong>
        <span>${meal.timestamp} · Б ${meal.protein_g} / Ж ${meal.fat_g} / У ${meal.carbs_g}</span>
      </span>
      <span class="meal-kcal">${Math.round(meal.kcal)} ккал</span>
    `;
    row.addEventListener("click", () => openEditor(meal));
    els.mealList.appendChild(row);
  });
}

async function toggleCheatday() {
  const now = Date.now();
  if (now - state.lastCheatdayTapAt < 450) return;
  state.lastCheatdayTapAt = now;
  if (state.cheatdaySaving) return;
  const day = selectedDayData();
  const nextValue = !Boolean(day.is_cheatmeal);
  const previousValue = Boolean(day.is_cheatmeal);
  state.cheatdaySaving = true;
  els.cheatdayToggle.disabled = true;
  els.cheatdayToggle.classList.add("saving");

  day.is_cheatmeal = nextValue;
  if (day.balance) {
    const expenditure = day.balance.expenditure_kcal;
    day.balance.intake_kcal = nextValue ? 0 : day.totals?.kcal || 0;
    day.balance.difference_kcal = expenditure == null ? null : Math.round((day.balance.intake_kcal - expenditure) * 10) / 10;
  }
  renderSummary();
  renderCalendar();
  drawChart();
  showToast(nextValue ? "Включаю читмил..." : "Выключаю читмил...");

  try {
    await requestJson(`/api/day-flags/${state.selectedDay}`, {
      method: "PATCH",
      body: JSON.stringify({
        user_id: state.devUserId,
        uid: launchParams.get("uid") || "",
        sig: launchParams.get("sig") || "",
        init_data: tg?.initData || "",
        is_cheatmeal: nextValue,
      }),
    });
    state.monthCache.clear();
    showToast(nextValue ? "День отмечен как читмил" : "Читмил выключен");
    await loadMeals({ force: true });
  } catch (error) {
    day.is_cheatmeal = previousValue;
    renderSummary();
    renderCalendar();
    drawChart();
    showToast(error.message || "Не удалось сохранить читмил");
  } finally {
    state.cheatdaySaving = false;
    els.cheatdayToggle.disabled = false;
    els.cheatdayToggle.classList.remove("saving");
  }
}

window.mealbotToggleCheatday = (event) => {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  toggleCheatday();
};

function openExpenditureEditor() {
  const day = selectedDayData();
  const expenditure = day.balance?.expenditure_kcal;
  els.manualExpenditure.value = expenditure == null ? "" : Math.round(expenditure);
  els.expenditureDialog.showModal();
}

async function saveExpenditure(event) {
  event.preventDefault();
  if (event.submitter?.value === "cancel") {
    els.expenditureDialog.close();
    return;
  }

  const expenditure = Number(String(els.manualExpenditure.value).replace(",", "."));
  if (!Number.isFinite(expenditure) || expenditure <= 0) {
    showToast("Введите расход в ккал");
    return;
  }

  showToast("Сохраняю расход...");
  try {
    await requestJson(`/api/day-expenditure/${state.selectedDay}`, {
      method: "PATCH",
      body: JSON.stringify({
        user_id: state.devUserId,
        uid: launchParams.get("uid") || "",
        sig: launchParams.get("sig") || "",
        init_data: tg?.initData || "",
        expenditure_kcal: expenditure,
      }),
    });
    els.expenditureDialog.close();
    state.monthCache.clear();
    showToast("Расход сохранён");
    await loadMeals({ force: true });
  } catch (error) {
    showToast(error.message || "Не удалось сохранить расход");
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  })[char]);
}

function openEditor(meal) {
  document.getElementById("rowId").value = meal.row_id;
  document.getElementById("timestamp").value = meal.timestamp;
  document.getElementById("name").value = meal.name;
  document.getElementById("weight").value = meal.weight_g;
  document.getElementById("kcal").value = meal.kcal;
  document.getElementById("protein").value = meal.protein_g;
  document.getElementById("fat").value = meal.fat_g;
  document.getElementById("carbs").value = meal.carbs_g;
  document.getElementById("confidence").value = meal.confidence;
  document.getElementById("note").value = meal.note;
  els.dialog.showModal();
}

async function saveMeal(event) {
  event.preventDefault();
  const rowId = document.getElementById("rowId").value;
  const payload = {
    user_id: state.devUserId,
    uid: launchParams.get("uid") || "",
    sig: launchParams.get("sig") || "",
    init_data: tg?.initData || "",
    timestamp: document.getElementById("timestamp").value,
    name: document.getElementById("name").value,
    weight_g: document.getElementById("weight").value,
    kcal: document.getElementById("kcal").value,
    protein_g: document.getElementById("protein").value,
    fat_g: document.getElementById("fat").value,
    carbs_g: document.getElementById("carbs").value,
    confidence: document.getElementById("confidence").value,
    note: document.getElementById("note").value,
  };
  await requestJson(`/api/meals/${rowId}`, { method: "PATCH", body: JSON.stringify(payload) });
  state.monthCache.clear();
  els.dialog.close();
  showToast("Запись сохранена");
  await loadMeals({ force: true });
}

async function deleteCurrentMeal() {
  const rowId = document.getElementById("rowId").value;
  await requestJson(`/api/meals/${rowId}${authSearch()}`, {
    method: "DELETE",
  });
  state.monthCache.clear();
  els.dialog.close();
  showToast("Запись удалена");
  await loadMeals({ force: true });
}

function drawChart() {
  if (state.activeTab !== "week" || els.chartCanvas.closest("[hidden]")) return;
  const canvas = els.chartCanvas;
  const ctx = canvas.getContext("2d");
  const rect = canvas.getBoundingClientRect();
  if (rect.width <= 0) return;
  const themeStyles = getComputedStyle(document.body);
  const chartIntake = themeStyles.getPropertyValue("--chart-intake").trim() || "#000000";
  const chartExpense = themeStyles.getPropertyValue("--chart-expense").trim() || "#a3e635";
  const chartDeficit = themeStyles.getPropertyValue("--chart-deficit").trim() || "#22c55e";
  const chartSurplus = themeStyles.getPropertyValue("--chart-surplus").trim() || "#f472b6";
  const chartGrid = themeStyles.getPropertyValue("--chart-grid").trim() || "rgba(23, 23, 23, 0.16)";
  const chartText = themeStyles.getPropertyValue("--color-midnight-ink").trim() || "#171717";
  const scale = window.devicePixelRatio || 1;
  canvas.width = rect.width * scale;
  canvas.height = 220 * scale;
  ctx.scale(scale, scale);
  ctx.clearRect(0, 0, rect.width, 220);

  const days = state.days;
  if (!days.length) {
    ctx.fillStyle = chartText;
    ctx.fillText("Нет данных за период", 12, 30);
    return;
  }

  const width = rect.width;
  const height = 220;
  const isMacroChart = state.chartMode === "macros";
  const isBalanceChart = state.chartMode === "balance";
  const padding = { top: isMacroChart || isBalanceChart ? 48 : 34, right: isMacroChart ? 28 : 12, bottom: 28, left: 32 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const xFor = (index) => padding.left + (days.length === 1 ? plotW / 2 : (plotW / (days.length - 1)) * index);

  ctx.strokeStyle = chartGrid;
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i += 1) {
    const y = padding.top + (plotH / 3) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(width - padding.right, y);
    ctx.stroke();
  }

  ctx.font = "12px Satoshi, system-ui, sans-serif";
  const drawLegend = (series) => {
    let legendX = padding.left;
    series.forEach((item) => {
      ctx.fillStyle = item.color;
      ctx.beginPath();
      ctx.arc(legendX + 4, 14, 4, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = chartText;
      ctx.fillText(item.label, legendX + 12, 18);
      legendX += ctx.measureText(item.label).width + 38;
    });
  };

  if (isMacroChart) {
    const macroSeries = [
      { key: "protein", label: "белки", color: MACRO_COLORS.protein },
      { key: "fat", label: "жиры", color: MACRO_COLORS.fat },
      { key: "carbs", label: "углеводы", color: MACRO_COLORS.carbs },
    ];
    drawLegend(macroSeries);

    const barWidth = Math.max(9, Math.min(22, plotW / days.length - 5));
    days.forEach((day, index) => {
      const proteinG = day.is_cheatmeal ? 0 : day.totals.protein_g || 0;
      const fatG = day.is_cheatmeal ? 0 : day.totals.fat_g || 0;
      const carbsG = day.is_cheatmeal ? 0 : day.totals.carbs_g || 0;
      const totalMacroG = proteinG + fatG + carbsG;
      if (totalMacroG <= 0) return;

      const x = xFor(index) - barWidth / 2;
      let y = padding.top + plotH;
      [
        { value: carbsG, color: macroSeries[2].color },
        { value: fatG, color: macroSeries[1].color },
        { value: proteinG, color: macroSeries[0].color },
      ].forEach((segment) => {
        const h = (segment.value / totalMacroG) * plotH;
        y -= h;
        ctx.fillStyle = segment.color;
        ctx.fillRect(x, y, barWidth, h);
      });
    });

    ctx.fillStyle = chartText;
    ctx.font = "11px Satoshi, system-ui, sans-serif";
    ctx.fillText("100%", 2, padding.top + 4);
    ctx.fillText("50%", 8, padding.top + plotH / 2 + 4);
    ctx.fillText("0%", 14, padding.top + plotH + 4);
    days.forEach((day, index) => {
      if (index % Math.ceil(days.length / 6) !== 0 && index !== days.length - 1) return;
      ctx.fillText(String(Number(day.date.slice(-2))), xFor(index) - 5, height - 8);
    });
    return;
  }

  if (isBalanceChart) {
    const balanceValues = days.map((day) => {
      const value = day.balance?.difference_kcal;
      return typeof value === "number" && Number.isFinite(value) ? value : null;
    });
    const visibleValues = balanceValues.filter((value) => value != null);
    drawLegend([
      { label: "дефицит", color: chartDeficit },
      { label: "профицит", color: chartSurplus },
    ]);

    if (!visibleValues.length) {
      ctx.fillStyle = chartText;
      ctx.fillText("Нет данных по расходу", padding.left, padding.top + 20);
      return;
    }

    const maxAbs = Math.max(...visibleValues.map((value) => Math.abs(value)), 1);
    const yZero = padding.top + plotH / 2;
    const yForBalance = (value) => yZero - (value / maxAbs) * (plotH / 2);
    const barWidth = Math.max(6, Math.min(18, plotW / days.length - 4));

    ctx.strokeStyle = chartGrid;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(padding.left, yZero);
    ctx.lineTo(width - padding.right, yZero);
    ctx.stroke();

    balanceValues.forEach((value, index) => {
      if (value == null) return;
      const x = xFor(index) - barWidth / 2;
      const y = yForBalance(value);
      ctx.fillStyle = value <= 0 ? chartDeficit : chartSurplus;
      ctx.fillRect(x, Math.min(y, yZero), barWidth, Math.max(2, Math.abs(yZero - y)));
    });

    ctx.fillStyle = chartText;
    ctx.font = "11px Satoshi, system-ui, sans-serif";
    ctx.fillText(`+${Math.round(maxAbs)}`, 2, padding.top + 4);
    ctx.fillText("0", 18, yZero + 4);
    ctx.fillText(`-${Math.round(maxAbs)}`, 2, padding.top + plotH + 4);
    days.forEach((day, index) => {
      if (index % Math.ceil(days.length / 6) !== 0 && index !== days.length - 1) return;
      ctx.fillText(String(Number(day.date.slice(-2))), xFor(index) - 5, height - 8);
    });
    return;
  }

  const series = [
    { label: "съедено", color: chartIntake, values: days.map((day) => day.balance?.intake_kcal ?? day.totals.kcal ?? 0) },
    { label: "расход", color: chartExpense, values: days.map((day) => day.balance?.expenditure_kcal ?? null) },
  ];
  const max = Math.max(...series.flatMap((item) => item.values.filter((value) => value != null)), 1);
  drawLegend(series);

  const yFor = (value) => padding.top + plotH - (value / max) * plotH;

  series.forEach((item) => {
    ctx.strokeStyle = item.color;
    ctx.fillStyle = item.color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    let started = false;
    item.values.forEach((value, index) => {
      if (value == null) {
        started = false;
        return;
      }
      const x = xFor(index);
      const y = yFor(value);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();

    item.values.forEach((value, index) => {
      if (value == null) return;
      ctx.beginPath();
      ctx.arc(xFor(index), yFor(value), 3, 0, Math.PI * 2);
      ctx.fill();
    });
  });

  ctx.fillStyle = chartText;
  ctx.font = "11px Satoshi, system-ui, sans-serif";
  days.forEach((day, index) => {
    if (index % Math.ceil(days.length / 6) !== 0 && index !== days.length - 1) return;
    ctx.fillText(String(Number(day.date.slice(-2))), xFor(index) - 5, height - 8);
  });
}

function showToast(text) {
  els.toast.textContent = text;
  els.toast.hidden = false;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => {
    els.toast.hidden = true;
  }, 1800);
}

document.getElementById("prevMonth").addEventListener("click", async () => {
  state.currentMonth = new Date(state.currentMonth.getFullYear(), state.currentMonth.getMonth() - 1, 1);
  state.selectedDay = toISODate(new Date(state.currentMonth.getFullYear(), state.currentMonth.getMonth(), 1));
  await loadMeals();
});

document.getElementById("nextMonth").addEventListener("click", async () => {
  state.currentMonth = new Date(state.currentMonth.getFullYear(), state.currentMonth.getMonth() + 1, 1);
  state.selectedDay = toISODate(new Date(state.currentMonth.getFullYear(), state.currentMonth.getMonth(), 1));
  await loadMeals();
});

document.getElementById("refreshButton").addEventListener("click", () => loadMeals({ force: true, live: true }));
els.mainTabs.forEach((button) => {
  button.addEventListener("click", () => switchMainTab(button.dataset.mainTab));
});
els.themeOptions.forEach((button) => {
  button.addEventListener("click", () => setTheme(button.dataset.themeOption));
});
els.cheatdayToggle.addEventListener("click", window.mealbotToggleCheatday);
els.cheatdayToggle.addEventListener("pointerup", window.mealbotToggleCheatday);
els.cheatdayToggle.addEventListener("touchend", window.mealbotToggleCheatday);
els.expenditureButton.addEventListener("click", openExpenditureEditor);
els.editForm.addEventListener("submit", saveMeal);
els.expenditureForm.addEventListener("submit", saveExpenditure);
document.getElementById("deleteMeal").addEventListener("click", deleteCurrentMeal);
document.querySelectorAll("[data-chart]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-chart]").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    state.chartMode = button.dataset.chart;
    drawChart();
  });
});

els.saveDevAuth.addEventListener("click", async () => {
  state.devUserId = els.devUserId.value.trim();
  state.devToken = els.devToken.value.trim();
  localStorage.setItem("mealbot:user_id", state.devUserId);
  localStorage.setItem("mealbot:token", state.devToken);
  state.monthCache.clear();
  await loadMeals({ force: true });
});

window.addEventListener("resize", drawChart);

if (isDevMode && !tg && !hasSignedLaunch) {
  document.body.classList.add("dev-mode");
  els.devAuth.hidden = false;
  els.devUserId.value = state.devUserId;
  els.devToken.value = state.devToken;
}

applyTheme(state.theme);

loadMeals()
  .then(triggerGarminSyncFromMiniApp)
  .catch((error) => showToast(error.message || "Ошибка загрузки"));
