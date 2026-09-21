const $ = (id) => document.getElementById(id);

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

els.cropLayer.addEventListener("pointerdown", (ev) => {
  if (ev.button !== 0) return;
  const box = displayedImageBox();
  if (!box) return;
  ev.preventDefault();
  const origin = els.cropLayer.getBoundingClientRect();
  const pt = layerPoint(ev, origin);
  const handle = ev.target.closest(".crop-handle");
  const start = {
    left: crop.left * box.w,
    top: crop.top * box.h,
    right: (1 - crop.right) * box.w,
    bottom: (1 - crop.bottom) * box.h,
  };
  let mode = "draw";
  let dir = "";
  if (handle) {
    mode = "resize";
    dir = handle.dataset.dir || "";
  } else if (!ev.shiftKey && ev.target.closest(".crop-box")) {
    mode = "move";
  }
  cropDrag = { mode, dir, origin, start, x0: pt.x, y0: pt.y };
  els.cropLayer.setPointerCapture(ev.pointerId);
});

els.cropLayer.addEventListener("pointermove", (ev) => {
  if (!cropDrag) return;
  const box = displayedImageBox();
  if (!box) return;
  const pt = layerPoint(ev, cropDrag.origin);
  const { start, mode, dir } = cropDrag;
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
  cropDrag = null;
  persist();
  if (ev && els.cropLayer.hasPointerCapture(ev.pointerId)) {
    els.cropLayer.releasePointerCapture(ev.pointerId);
  }
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

function renderStatus(st) {
  const live = st.kvm?.connected && st.kvm?.has_frame;
  els.livePill.textContent = st.kvm?.connected ? (live ? "HDMI live" : "ждём кадр") : "нет сигнала";
  els.livePill.className = "pill " + (live ? "on" : "off");
  if (st.auto_running) {
    els.livePill.textContent = "автоскан";
    els.livePill.className = "pill scan";
    els.viewfinder.classList.add("scanning");
  } else {
    els.viewfinder.classList.remove("scanning");
  }
  if (live) els.viewfinder.classList.add("live");
  else els.viewfinder.classList.remove("live");
  if (!cropDrag) paintCrop();

  if (st.name) {
    els.sessionPill.textContent = `${st.name} · ${st.page_count} стр.`;
    els.sessionPill.className = "pill dim";
  }
  els.pageCount.textContent = String(st.page_count || 0);
  els.log.textContent = (st.log || []).join("\n");
  if (st.dir) {
    els.folderHint.textContent = `Папка: ${st.dir}. В новой сессии Cursor откройте её и приложите OCR_PROMPT.md.`;
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

poll();
setInterval(poll, 700);
