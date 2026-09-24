const $ = (id) => document.getElementById(id);

const I18N = {
  ru: {
    language: "Язык",
    tagline: "NanoKVM Pro · MJPEG → страницы → OCR-сессия",
    noSignal: "нет сигнала",
    waitingFrame: "ждём кадр",
    hdmiLive: "HDMI live",
    autoscan: "автоскан",
    noSession: "сессия не создана",
    device: "Устройство",
    kvmUrl: "Адрес NanoKVM",
    login: "Логин",
    password: "Пароль",
    connect: "Подключить",
    disconnect: "Отключить",
    exit: "Выход",
    exited: "приложение остановлено",
    session: "Сессия",
    folderName: "Имя папки",
    saveTo: "Куда сохранять",
    createSession: "Создать сессию",
    paging: "Перелистывание",
    key: "Клавиша",
    space: "Пробел",
    wheelDown: "Колесо мыши вниз",
    pauseMs: "Пауза, мс",
    maxPages: "Макс. страниц",
    stopDup: "Стоп, если кадр почти как предыдущий",
    crop: "Обрезка кадра",
    reset: "Сбросить",
    cropHint: "Клик по превью — клик на удалённом ПК, колесо — прокрутка. Рамку двигайте за края; Shift + протянуть — новая область.",
    top: "Сверху",
    bottom: "Снизу",
    left: "Слева",
    right: "Справа",
    previewAlt: "Превью HDMI",
    previewEmpty: "Подключите NanoKVM — здесь появится MJPEG с рабочего стола.",
    focus: "Фокус",
    focusTitle: "Один клик в статью, чтобы Page Down листал её, а не браузер",
    capture: "Снять",
    captureNext: "Снять и далее",
    autoscanBtn: "Автоскан",
    stop: "Стоп",
    pageOnly: "Только листануть",
    pages: "Страницы",
    folderHint: "После скана откройте папку сессии и приложите OCR_PROMPT.md.",
    folderReady: "Папка: {dir}",
    sessionPill: "{name} · {count} стр.",
    log_session: "сессия {name} -> {folder}",
    log_duplicate_frame: "этот кадр уже есть ({sim})",
    log_captured: "снято {file} ({bytes} байт)",
    log_auto_started: "автоскан запущен",
    log_auto_focus: "фокус в области съёма",
    log_page_limit: "достигнут лимит страниц",
    log_duplicate_stop: "повтор кадра — автоскан остановлен",
    log_end_of_document: "после листания кадр не изменился ({sim}) — конец документа",
    log_auto_error: "автоскан ошибка: {error}",
    log_auto_stopped: "автоскан остановлен",
    log_connected: "подключено к {url}",
    log_key: "клавиша {key}",
    log_focused: "клик в статью, курсор убран из области съёма",
    log_click: "клик {x}, {y}",
    log_limit_raised: "лимит увеличен до {max_pages}, продолжаю",
    log_error: "{error}",
    log_shutdown: "выход",
  },
  en: {
    language: "Language",
    tagline: "NanoKVM Pro · MJPEG → pages → OCR session",
    noSignal: "no signal",
    waitingFrame: "waiting for frame",
    hdmiLive: "HDMI live",
    autoscan: "autoscan",
    noSession: "no session",
    device: "Device",
    kvmUrl: "NanoKVM address",
    login: "Username",
    password: "Password",
    connect: "Connect",
    disconnect: "Disconnect",
    exit: "Exit",
    exited: "application stopped",
    session: "Session",
    folderName: "Folder name",
    saveTo: "Save to",
    createSession: "Create session",
    paging: "Paging",
    key: "Key",
    space: "Space",
    wheelDown: "Mouse wheel down",
    pauseMs: "Pause, ms",
    maxPages: "Max pages",
    stopDup: "Stop if the frame matches the previous one",
    crop: "Crop",
    reset: "Reset",
    cropHint: "Click the preview to click the remote PC, wheel to scroll. Drag the frame edges to crop; Shift+drag draws a new region.",
    top: "Top",
    bottom: "Bottom",
    left: "Left",
    right: "Right",
    previewAlt: "HDMI preview",
    previewEmpty: "Connect NanoKVM — the desktop MJPEG stream will appear here.",
    focus: "Focus",
    focusTitle: "Click into the article so Page Down scrolls it, not the browser chrome",
    capture: "Capture",
    captureNext: "Capture + next",
    autoscanBtn: "Autoscan",
    stop: "Stop",
    pageOnly: "Page only",
    pages: "Pages",
    folderHint: "After the scan, open the session folder and use OCR_PROMPT.md.",
    folderReady: "Folder: {dir}",
    sessionPill: "{name} · {count} pages",
    log_session: "session {name} -> {folder}",
    log_duplicate_frame: "this frame is already saved ({sim})",
    log_captured: "saved {file} ({bytes} bytes)",
    log_auto_started: "autoscan started",
    log_auto_focus: "focus in the capture area",
    log_page_limit: "page limit reached",
    log_duplicate_stop: "duplicate frame — autoscan stopped",
    log_end_of_document: "frame unchanged after paging ({sim}) — end of document",
    log_auto_error: "autoscan error: {error}",
    log_auto_stopped: "autoscan stopped",
    log_connected: "connected to {url}",
    log_key: "key {key}",
    log_focused: "clicked the article, cursor moved out of the capture area",
    log_click: "click {x}, {y}",
    log_limit_raised: "limit raised to {max_pages}, continuing",
    log_error: "{error}",
    log_shutdown: "exit",
  },
};

const els = {
  url: $("kvm-url"),
  user: $("kvm-user"),
  pass: $("kvm-pass"),
  sessionName: $("session-name"),
  outputDir: $("output-dir"),
  pageKey: $("page-key"),
  delayMs: $("delay-ms"),
  maxPages: $("max-pages"),
  stopDup: $("stop-dup"),
  cropTop: $("crop-top"),
  cropBottom: $("crop-bottom"),
  cropLeft: $("crop-left"),
  cropRight: $("crop-right"),
  cropLayer: $("crop-layer"),
  cropBox: $("crop-box"),
  cropReset: $("btn-crop-reset"),
  preview: $("preview"),
  viewfinder: $("viewfinder"),
  livePill: $("live-pill"),
  sessionPill: $("session-pill"),
  log: $("log"),
  thumbs: $("thumbs"),
  pageCount: $("page-count"),
  folderHint: $("folder-hint"),
};

const DEFAULT_CROP = { top: 0.1, right: 0, bottom: 0.05, left: 0.32 };
const MIN_KEEP = 0.04;
const LANGS = ["en", "ru"];

const stored = JSON.parse(
  localStorage.getItem("nanokvm-scanner") || localStorage.getItem("wiki-scanner") || "{}"
);
if (stored.url) els.url.value = stored.url;
if (stored.user) els.user.value = stored.user;
if (stored.outputDir) els.outputDir.value = stored.outputDir;
if (!els.sessionName.value) {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  els.sessionName.value = `scan-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`;
}

let lang = LANGS.includes(stored.lang)
  ? stored.lang
  : (navigator.language || "").toLowerCase().startsWith("ru")
    ? "ru"
    : "en";
let lastStatus = null;

function t(key, vars) {
  const table = I18N[lang] || I18N.en;
  let s = table[key] ?? I18N.en[key] ?? key;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      s = s.replaceAll(`{${k}}`, String(v ?? ""));
    }
  }
  return s;
}

function applyI18n() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    el.title = t(el.dataset.i18nTitle);
  });
  document.querySelectorAll("[data-i18n-alt]").forEach((el) => {
    el.alt = t(el.dataset.i18nAlt);
  });
  document.querySelectorAll("[data-i18n-aria]").forEach((el) => {
    el.setAttribute("aria-label", t(el.dataset.i18nAria));
  });
  document.querySelectorAll(".lang-switch button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.lang === lang);
  });
  if (!lastStatus || !lastStatus.name) {
    els.sessionPill.textContent = t("noSession");
  }
  if (!lastStatus || !lastStatus.kvm?.connected) {
    els.livePill.textContent = t("noSignal");
  }
  if (lastStatus) renderStatus(lastStatus);
}

const crop = { ...DEFAULT_CROP };
let cropDrag = null;

function clamp(n, lo, hi) {
  return Math.min(hi, Math.max(lo, n));
}

function pct(v) {
  return String(Math.round(v * 1000) / 10);
}

function fitCropAxis(lo, hi) {
  lo = clamp(lo, 0, 0.95);
  hi = clamp(hi, 0, 0.95);
  const keep = 1 - lo - hi;
  if (keep >= MIN_KEEP) return [lo, hi];
  const extra = MIN_KEEP - keep;
  const span = lo + hi;
  if (span <= 0) return [0, 0];
  return [Math.max(0, lo - extra * (lo / span)), Math.max(0, hi - extra * (hi / span))];
}

function normalizeCrop() {
  [crop.left, crop.right] = fitCropAxis(crop.left, crop.right);
  [crop.top, crop.bottom] = fitCropAxis(crop.top, crop.bottom);
}

function loadCrop(src) {
  if (!src || typeof src !== "object") return;
  for (const key of ["top", "right", "bottom", "left"]) {
    const n = Number(src[key]);
    if (Number.isFinite(n)) crop[key] = n > 1 ? n / 100 : n;
  }
  normalizeCrop();
}

function readCropInputs() {
  const read = (el, fallback) => {
    const n = Number(el.value);
    return Number.isFinite(n) ? n / 100 : fallback;
  };
  crop.top = read(els.cropTop, crop.top);
  crop.bottom = read(els.cropBottom, crop.bottom);
  crop.left = read(els.cropLeft, crop.left);
  crop.right = read(els.cropRight, crop.right);
  normalizeCrop();
}

function syncCropInputs() {
  els.cropTop.value = pct(crop.top);
  els.cropBottom.value = pct(crop.bottom);
  els.cropLeft.value = pct(crop.left);
  els.cropRight.value = pct(crop.right);
}

loadCrop(stored.crop);
if (!stored.crop) readCropInputs();
syncCropInputs();

function persist() {
  localStorage.setItem(
    "nanokvm-scanner",
    JSON.stringify({
      url: els.url.value,
      user: els.user.value,
      outputDir: els.outputDir.value,
      crop: { ...crop },
      lang,
    })
  );
}

function displayedImageBox() {
  const img = els.preview;
  const nw = img.naturalWidth;
  const nh = img.naturalHeight;
  const cw = img.clientWidth;
  const ch = img.clientHeight;
  if (!nw || !nh || !cw || !ch) return null;
  const scale = Math.min(cw / nw, ch / nh);
  const w = nw * scale;
  const h = nh * scale;
  return { left: (cw - w) / 2, top: (ch - h) / 2, w, h };
}

function paintCrop() {
  const box = displayedImageBox();
  if (!box) {
    els.cropLayer.hidden = true;
    return;
  }
  els.cropLayer.hidden = false;
  els.cropLayer.style.left = `${box.left}px`;
  els.cropLayer.style.top = `${box.top}px`;
  els.cropLayer.style.width = `${box.w}px`;
  els.cropLayer.style.height = `${box.h}px`;
  els.cropBox.style.left = `${crop.left * 100}%`;
  els.cropBox.style.top = `${crop.top * 100}%`;
  els.cropBox.style.width = `${(1 - crop.left - crop.right) * 100}%`;
  els.cropBox.style.height = `${(1 - crop.top - crop.bottom) * 100}%`;
}

function applyCropRect(x0, y0, x1, y1, box) {
  const minW = box.w * MIN_KEEP;
  const minH = box.h * MIN_KEEP;
  let left = Math.min(x0, x1);
  let right = Math.max(x0, x1);
  let top = Math.min(y0, y1);
  let bottom = Math.max(y0, y1);
  if (right - left < minW) {
    if (x1 >= x0) right = left + minW;
    else left = right - minW;
  }
  if (bottom - top < minH) {
    if (y1 >= y0) bottom = top + minH;
    else top = bottom - minH;
  }
  left = clamp(left, 0, box.w - minW);
  top = clamp(top, 0, box.h - minH);
  right = clamp(right, left + minW, box.w);
  bottom = clamp(bottom, top + minH, box.h);
  crop.left = left / box.w;
  crop.top = top / box.h;
  crop.right = 1 - right / box.w;
  crop.bottom = 1 - bottom / box.h;
  normalizeCrop();
  syncCropInputs();
  paintCrop();
}

function layerPoint(ev, origin) {
  return { x: ev.clientX - origin.left, y: ev.clientY - origin.top };
}

const CLICK_PX = 6;

function sendRemoteClick(nx, ny, button) {
  if (!lastStatus?.kvm?.connected || lastStatus?.auto_running) return;
  withBusy(async () => {
    await api("/api/click", { x: nx, y: ny, button });
    poll();
  })();
}

function sendRemoteWheel(nx, ny, ticks) {
  if (!lastStatus?.kvm?.connected || lastStatus?.auto_running || !ticks) return;
  api("/api/wheel", { x: nx, y: ny, ticks }).catch(() => {});
}

els.viewfinder.addEventListener(
  "wheel",
  (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    if (cropDrag) return;
    if (Math.floor(ev.deltaY) === 0) return;
    const box = displayedImageBox();
    if (!box) return;
    const img = els.preview.getBoundingClientRect();
    const x = ev.clientX - img.left - box.left;
    const y = ev.clientY - img.top - box.top;
    if (x < 0 || y < 0 || x > box.w || y > box.h) return;
    // Как NanoKVM: scrollDirection по умолчанию -1, знак deltaY сводится к ±1.
    const ticks = (ev.deltaY > 0 ? 1 : -1) * -1;
    sendRemoteWheel(clamp(x / box.w, 0, 1), clamp(y / box.h, 0, 1), ticks);
  },
  { passive: false }
);

els.cropLayer.addEventListener("contextmenu", (ev) => ev.preventDefault());

els.cropLayer.addEventListener("pointerdown", (ev) => {
  if (ev.button !== 0 && ev.button !== 2) return;
  const box = displayedImageBox();
  if (!box) return;
  ev.preventDefault();
  const origin = els.cropLayer.getBoundingClientRect();
  const pt = layerPoint(ev, origin);
  const handle = ev.button === 0 ? ev.target.closest(".crop-handle") : null;
  const start = {
    left: crop.left * box.w,
    top: crop.top * box.h,
    right: (1 - crop.right) * box.w,
    bottom: (1 - crop.bottom) * box.h,
  };
  let mode = "click";
  let dir = "";
  if (handle) {
    mode = "resize";
    dir = handle.dataset.dir || "";
  } else if (ev.button === 0 && ev.shiftKey) {
    mode = "draw";
  } else if (ev.button === 0 && ev.target.closest(".crop-box")) {
    mode = "click-or-move";
  }
  cropDrag = { mode, dir, origin, start, x0: pt.x, y0: pt.y, button: ev.button, moved: false };
  els.cropLayer.setPointerCapture(ev.pointerId);
});

els.cropLayer.addEventListener("pointermove", (ev) => {
  if (!cropDrag) return;
  const box = displayedImageBox();
  if (!box) return;
  const pt = layerPoint(ev, cropDrag.origin);
  const dist = Math.hypot(pt.x - cropDrag.x0, pt.y - cropDrag.y0);
  if (!cropDrag.moved && dist < CLICK_PX && cropDrag.mode !== "resize" && cropDrag.mode !== "draw") {
    return;
  }
  if (!cropDrag.moved) {
    cropDrag.moved = true;
    if (cropDrag.mode === "click-or-move") cropDrag.mode = "move";
    if (cropDrag.mode === "click") return;
  }
  const { start, mode, dir } = cropDrag;
  if (mode === "click") return;
  if (mode === "draw") {
    applyCropRect(cropDrag.x0, cropDrag.y0, pt.x, pt.y, box);
    return;
  }
  if (mode === "move") {
    const w = start.right - start.left;
    const h = start.bottom - start.top;
    let left = start.left + (pt.x - cropDrag.x0);
    let top = start.top + (pt.y - cropDrag.y0);
    left = clamp(left, 0, box.w - w);
    top = clamp(top, 0, box.h - h);
    applyCropRect(left, top, left + w, top + h, box);
    return;
  }
  if (mode !== "resize") return;
  let x0 = start.left;
  let y0 = start.top;
  let x1 = start.right;
  let y1 = start.bottom;
  if (dir.includes("w")) x0 = pt.x;
  if (dir.includes("e")) x1 = pt.x;
  if (dir.includes("n")) y0 = pt.y;
  if (dir.includes("s")) y1 = pt.y;
  applyCropRect(x0, y0, x1, y1, box);
});

function endCropDrag(ev) {
  if (!cropDrag) return;
  const drag = cropDrag;
  cropDrag = null;
  if (ev && els.cropLayer.hasPointerCapture(ev.pointerId)) {
    els.cropLayer.releasePointerCapture(ev.pointerId);
  }
  if (!drag.moved && drag.mode !== "resize") {
    const box = displayedImageBox();
    if (box && box.w && box.h) {
      sendRemoteClick(clamp(drag.x0 / box.w, 0, 1), clamp(drag.y0 / box.h, 0, 1), drag.button);
    }
    return;
  }
  persist();
}

els.cropLayer.addEventListener("pointerup", endCropDrag);
els.cropLayer.addEventListener("pointercancel", endCropDrag);

["cropTop", "cropBottom", "cropLeft", "cropRight"].forEach((key) => {
  els[key].addEventListener("input", () => {
    readCropInputs();
    paintCrop();
  });
  els[key].addEventListener("change", () => {
    readCropInputs();
    syncCropInputs();
    persist();
  });
});

els.cropReset.onclick = () => {
  Object.assign(crop, DEFAULT_CROP);
  syncCropInputs();
  paintCrop();
  persist();
};

els.preview.addEventListener("load", paintCrop);
if (window.ResizeObserver) {
  new ResizeObserver(paintCrop).observe(els.viewfinder);
}

async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

function withBusy(fn) {
  return async () => {
    try {
      await fn();
    } catch (err) {
      els.log.textContent = String(err.message || err);
    }
  };
}

function settings() {
  return {
    name: els.sessionName.value.trim(),
    output_dir: els.outputDir.value.trim(),
    page_key: els.pageKey.value,
    delay_ms: Number(els.delayMs.value),
    max_pages: Number(els.maxPages.value),
    stop_on_duplicate: els.stopDup.checked,
    crop: { ...crop },
  };
}

function formatLogEntry(entry) {
  if (typeof entry === "string") return entry;
  if (!entry || typeof entry !== "object") return String(entry ?? "");
  const time = entry.t ? `${entry.t}  ` : "";
  if (entry.key) return time + t(`log_${entry.key}`, entry);
  return time + (entry.error || "");
}

function renderStatus(st) {
  lastStatus = st;
  const live = st.kvm?.connected && st.kvm?.has_frame;
  if (st.auto_running) {
    els.livePill.textContent = t("autoscan");
    els.livePill.className = "pill scan";
    els.viewfinder.classList.add("scanning");
  } else {
    els.livePill.textContent = st.kvm?.connected ? (live ? t("hdmiLive") : t("waitingFrame")) : t("noSignal");
    els.livePill.className = "pill " + (live ? "on" : "off");
    els.viewfinder.classList.remove("scanning");
  }
  if (live) els.viewfinder.classList.add("live");
  else els.viewfinder.classList.remove("live");
  if (!cropDrag) paintCrop();

  if (st.name) {
    els.sessionPill.textContent = t("sessionPill", { name: st.name, count: st.page_count || 0 });
    els.sessionPill.className = "pill dim";
  } else {
    els.sessionPill.textContent = t("noSession");
    els.sessionPill.className = "pill dim";
  }
  els.pageCount.textContent = String(st.page_count || 0);
  els.log.textContent = (st.log || []).map(formatLogEntry).join("\n");
  if (st.dir) {
    els.folderHint.textContent = t("folderReady", { dir: st.dir });
  } else {
    els.folderHint.textContent = t("folderHint");
  }
  if (!els.outputDir.value && st.default_scans) {
    els.outputDir.value = st.default_scans;
  }
  renderThumbs(st.pages || []);
}

let thumbSig = "";
function renderThumbs(pages) {
  const sig = pages.map((p) => p.file).join("|");
  if (sig === thumbSig) return;
  thumbSig = sig;
  els.thumbs.innerHTML = "";
  for (const page of [...pages].reverse()) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.innerHTML = `<img src="/api/page/${page.file}?v=${page.md5}" alt="${page.file}" /><span>${page.file}</span>`;
    btn.onclick = () => window.open(`/api/page/${page.file}`, "_blank");
    els.thumbs.appendChild(btn);
  }
}

let previewArmed = false;
function armPreview() {
  if (previewArmed) return;
  previewArmed = true;
  els.preview.src = "/api/preview.mjpeg";
}

async function poll() {
  try {
    const st = await api("/api/status");
    renderStatus(st);
    if (st.kvm?.connected) armPreview();
  } catch (err) {
    els.log.textContent = String(err.message || err);
  }
}

function setLang(next) {
  if (!LANGS.includes(next) || next === lang) return;
  lang = next;
  persist();
  applyI18n();
}

document.querySelectorAll(".lang-switch button").forEach((btn) => {
  btn.onclick = () => setLang(btn.dataset.lang);
});

$("btn-connect").onclick = withBusy(async () => {
  persist();
  await api("/api/connect", {
    url: els.url.value.trim(),
    username: els.user.value.trim(),
    password: els.pass.value,
  });
  previewArmed = false;
  armPreview();
  poll();
});

$("btn-disconnect").onclick = withBusy(async () => {
  await api("/api/disconnect", {});
  previewArmed = false;
  els.preview.removeAttribute("src");
  poll();
});

$("btn-exit").onclick = () => {
  persist();
  clearInterval(pollTimer);
  previewArmed = false;
  els.preview.removeAttribute("src");
  const shutdown = api("/api/shutdown", {}).catch(() => {});
  window.close();
  shutdown.then(() => {
    els.livePill.textContent = t("noSignal");
    els.livePill.className = "pill off";
    els.viewfinder.classList.remove("live", "scanning");
    els.log.textContent = t("exited");
    window.close();
  });
};

$("btn-session").onclick = withBusy(async () => {
  persist();
  await api("/api/session/start", settings());
  poll();
});

$("btn-capture").onclick = withBusy(() => api("/api/capture", settings()).then(poll));
$("btn-next").onclick = withBusy(() => api("/api/capture-next", settings()).then(poll));
$("btn-page-only").onclick = withBusy(() => api("/api/page", { key: els.pageKey.value }).then(poll));
$("btn-focus").onclick = withBusy(() => api("/api/focus", {}).then(poll));
$("btn-auto").onclick = withBusy(async () => {
  persist();
  await api("/api/auto/start", settings());
  poll();
});
$("btn-stop").onclick = withBusy(() => api("/api/auto/stop", {}).then(poll));

["url", "user", "outputDir"].forEach((key) => {
  els[key].addEventListener("change", persist);
});

applyI18n();
persist();
poll();
const pollTimer = setInterval(poll, 700);
