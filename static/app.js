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
  preview: $("preview"),
  viewfinder: $("viewfinder"),
  livePill: $("live-pill"),
  sessionPill: $("session-pill"),
  log: $("log"),
  thumbs: $("thumbs"),
  pageCount: $("page-count"),
  folderHint: $("folder-hint"),
};

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

function persist() {
  localStorage.setItem(
    "nanokvm-scanner",
    JSON.stringify({
      url: els.url.value,
      user: els.user.value,
      outputDir: els.outputDir.value,
    })
  );
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
    crop: {
      top: Number(els.cropTop.value) / 100,
      bottom: Number(els.cropBottom.value) / 100,
      left: Number(els.cropLeft.value) / 100,
      right: Number(els.cropRight.value) / 100,
    },
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
