/* ==========================================================================
 *  Pfad:    /opt/ai/oroma/ui/static/scripts.js
 *  Projekt: ORÓMA
 *  Version: v3.6 (stable, +audio-badge, forgetting-fix, +SciCalc Patch1)
 *  Stand:   2025-09-28
 *
 *  Zweck
 *  ─────
 *  Zentrales Frontend-Script für das ORÓMA-Dashboard. Stellt Fetch/POST-Utilities
 *  inkl. Token-Header bereit, initialisiert optionale UI-Module nur dann, wenn
 *  die zugehörigen DOM-Elemente existieren, und aktualisiert Navbar-Badges über
 *  REST-APIs in festen Intervallen.
 *
 *  Erweiterungen v3.6 Patch1
 *  ─────────────────────────
 *  • Scientific Calculator (SciCalc):
 *      - Erweiterung um Evaluate/Roots/Taylor/Limit + Line/Bar/Pie (Chart.js)
 *      - APIs: /scicalc/api/{plot,roots,taylor,limit,bar,pie}
 *      - Init: safeInitSciCalc() (DOM-basiert, optional)
 *
 *  WICHTIG – Begriff „Health“ (zwei Bedeutungen, beide werden gebraucht!)
 *  ──────────────────────────────────────────────────────────────────────────
 *  1) System-Health (/health/api/health) → Seite /health + Navbar-Badge
 *  2) Core/Legacy-Health (/api/health)   → Checkmatrix auf Index/Status-Seite
 *  → Nicht verwechseln. Beide bleiben bestehen.
 *
 *  Sicherheit / Token
 *  ──────────────────
 *  - Token aus window.OROMA_UI_TOKEN oder localStorage("OROMA_UI_TOKEN")
 *  - Alle API-Calls senden X-OROMA-TOKEN (falls vorhanden)
 *
 *  Enthaltene Module (Auswahl)
 *  ───────────────────────────
 *    • System-Health (Seite /health + Badge)
 *    • Core/Legacy-Health (Index/Status-Matrix)
 *    • Audio-Badge (mit API-Fallback)
 *    • Forgetting-Badge (Fix für comp-Berechnung)
 *    • Research/Hypothesen (v3.6)
 *    • LLM-Chat
 *    • Learning-Curve (Chart.js)
 *    • Modelle (Vision, Audio)
 *    • Export/Import
 *    • Controls (Day/Dream/Night)
 *    • Vision/Audio Status
 *    • Spiele (TicTacToe, Connect4, Snake, Pong)
 *    • Curriculum-Calculator (mit API-Kompatibilitätslayer)
 *    • Scientific Calculator (Plot/Roots/Taylor/Limit/Bar/Pie)
 *    • Token-Dialog
 *    • Cleanup (Timer-Abmeldung beim Unload)
 * ========================================================================== */


/* ------------------------------
 *  Globale Config / Helpers
 * ------------------------------ */

const OROMA = {
  baseUrl: "",
  token: null,
  charts: {},
  timers: {},
  state: {},
};

// Token-Header
function getHeaders(isJson = true) {
  const h = {};
  if (isJson) h["Content-Type"] = "application/json";
  if (OROMA.token) h["X-OROMA-TOKEN"] = OROMA.token;
  return h;
}

// API GET
async function apiGet(path, asText = false, timeoutMs = 8000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(OROMA.baseUrl + path, {
      method: "GET",
      headers: getHeaders(false),
      signal: ctrl.signal,
      credentials: "same-origin",
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return asText ? await res.text() : await res.json();
  } finally { clearTimeout(t); }
}

// API POST (JSON)
async function apiPost(path, payload, timeoutMs = 12000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(OROMA.baseUrl + path, {
      method: "POST",
      headers: getHeaders(true),
      body: JSON.stringify(payload || {}),
      signal: ctrl.signal,
      credentials: "same-origin",
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    return await res.json();
  } finally { clearTimeout(t); }
}

// Datei-Upload (FormData)
async function apiUpload(path, fileFieldName, fileObj, extraFields) {
  const fd = new FormData();
  fd.append(fileFieldName, fileObj);
  if (extraFields && typeof extraFields === "object") {
    Object.entries(extraFields).forEach(([k, v]) => fd.append(k, v));
  }
  const headers = {};
  if (OROMA.token) headers["X-OROMA-TOKEN"] = OROMA.token;
  const res = await fetch(OROMA.baseUrl + path, {
    method: "POST",
    headers,
    body: fd,
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error("HTTP " + res.status);
  return await res.json();
}

// Toast
function toast(msg, type = "info", ms = 2500) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.style.position = "fixed";
    el.style.right = "12px";
    el.style.bottom = "12px";
    el.style.padding = "10px 14px";
    el.style.borderRadius = "6px";
    el.style.color = "#fff";
    el.style.zIndex = "9999";
    el.style.fontFamily = "system-ui, Arial, sans-serif";
    document.body.appendChild(el);
  }
  el.style.background = type === "error" ? "#b00020" : (type === "ok" ? "#2e7d32" : "#333");
  el.textContent = msg;
  el.style.opacity = "0.97";
  setTimeout(() => { el.style.opacity = "0"; }, ms);
}

// Busy-Overlay
function setBusy(on) {
  let ov = document.getElementById("busyOverlay");
  if (!ov) {
    ov = document.createElement("div");
    ov.id = "busyOverlay";
    ov.style.position = "fixed";
    ov.style.inset = "0";
    ov.style.background = "rgba(0,0,0,.15)";
    ov.style.backdropFilter = "blur(1px)";
    ov.style.display = "none";
    ov.style.zIndex = "9998";
    ov.innerHTML = '<div style="position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);background:#fff;padding:12px 16px;border-radius:8px;font-family:system-ui,Arial;">Bitte warten…</div>';
    document.body.appendChild(ov);
  }
  ov.style.display = on ? "block" : "none";
}


/* ------------------------------
 *  Health (Index/Status-Tab)
 * ------------------------------ */

function safeInitHealth() {
  const healthEl = document.getElementById("healthBox");
  if (!healthEl) return;

  async function refresh() {
    try {
      const js = await apiGet("/api/health");
      const keys = ["core", "db", "wrappers", "onnxruntime", "llm", "export_delay", "vectordb"];
      const rows = keys.map(k => {
        const v = js[k] || {};
        const ok = v.ok ? "OK" : "FEHLER";
        const c = v.ok ? "hcok" : "hcerr";
        return `<tr><td>${k}</td><td class="${c}">${ok}</td><td>${v.msg || ""}</td><td>${v.error || ""}</td></tr>`;
      }).join("");
      healthEl.innerHTML = `
        <table class="hc">
          <thead><tr><th>Check</th><th>Status</th><th>Info</th><th>Error</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    } catch (e) {
      healthEl.innerHTML = `<div class="hcerr">Health-Check fehlgeschlagen: ${e}</div>`;
    }
  }

  refresh();
  OROMA.timers.health = setInterval(refresh, 10000);
}


/* ------------------------------
 *  Learning Curve (Chart.js)
 * ------------------------------ */

function safeInitLearningChart() {
  const canvas = document.getElementById("learningChart");
  if (!canvas || typeof Chart === "undefined") return;

  const ctx = canvas.getContext("2d");
  OROMA.charts.learning = new Chart(ctx, {
    type: "line",
    data: {
      labels: [],
      datasets: [
        { label: "Qualität (Ø)", data: [], borderWidth: 2, tension: 0.25, pointRadius: 0 },
        { label: "SnapChains (#)", data: [], borderWidth: 2, tension: 0.25, pointRadius: 0, yAxisID: 'y1' }
      ]
    },
    options: {
      responsive: true,
      scales: {
        y:  { beginAtZero: true, title: { display: true, text: "Qualität" } },
        y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: "Anzahl" } }
      },
      plugins: { legend: { position: "bottom" } }
    }
  });

  async function refresh() {
    try {
      const q = await apiGet("/api/learning/curve");
      const labels = (q.ts || []).map(t => new Date(t * 1000).toLocaleString());
      OROMA.charts.learning.data.labels = labels;
      OROMA.charts.learning.data.datasets[0].data = q.quality || [];
      OROMA.charts.learning.data.datasets[1].data = q.count || [];
      OROMA.charts.learning.update();
      const win = document.getElementById("learningWindow");
      if (win) win.textContent = q.window || "n/a";
    } catch (e) {
      console.warn("Learning chart error:", e);
    }
  }

  refresh();
  OROMA.timers.learning = setInterval(refresh, 15000);
}


/* ------------------------------
 *  Chat (LLM)
 * ------------------------------ */

function safeInitChat() {
  const box = document.getElementById("chatBox");
  const inMsg = document.getElementById("chatInput");
  const btnSend = document.getElementById("chatSend");
  const selModelPath = document.getElementById("llmModelPath"); // optional

  if (!box) return;

  function appendChat(role, text) {
    const row = document.createElement("div");
    row.className = "chatrow " + role;
    row.textContent = (role === "you" ? "Du: " : "ORÓMA: ") + text;
    box.appendChild(row);
    box.scrollTop = box.scrollHeight;
  }

  async function loadChatModel() {
    if (!selModelPath || !selModelPath.value) return;
    setBusy(true);
    try {
      const js = await apiPost("/api/chat/load_model", { model_path: selModelPath.value });
      if (js.ok) toast("LLM geladen: " + (js.model || ""), "ok");
      else toast("LLM-Load Fehler: " + (js.error || "unbekannt"), "error");
    } catch (e) {
      toast("LLM-Load API-Fehler: " + e, "error");
    } finally { setBusy(false); }
  }

  async function sendChat() {
    const msg = inMsg ? inMsg.value.trim() : "";
    if (!msg) return;
    appendChat("you", msg);
    if (inMsg) inMsg.value = "";
    try {
      const js = await apiPost("/api/chat/send", { prompt: msg });
      appendChat("ai", (js.response || js.reply || "(keine Antwort)"));
    } catch (e) {
      appendChat("ai", "Fehler: " + e);
    }
  }

  if (btnSend) btnSend.addEventListener("click", sendChat);
  if (inMsg) inMsg.addEventListener("keydown", (ev) => { if (ev.key === "Enter") sendChat(); });

  const btnLoadChatModel = document.getElementById("chatLoadModel");
  if (btnLoadChatModel && selModelPath) {
    btnLoadChatModel.addEventListener("click", loadChatModel);
  }
}


/* ------------------------------
 *  Modelle (Vision/Audio) – optional
 * ------------------------------ */

function safeInitModels() {
  const vSel = document.getElementById("visionModelSelect");
  const vBtn = document.getElementById("visionLoadBtn");
  const vBack = document.getElementById("visionBackend");

  const aSel = document.getElementById("audioModelSelect");
  const aBtn = document.getElementById("audioLoadBtn");
  const aKind = document.getElementById("audioKind");

  async function loadVision() {
    if (!vSel) return;
    const name = vSel.value;
    const backend = vBack ? vBack.value : "onnx";
    if (!name) { toast("Kein Vision-Modell gewählt", "error"); return; }
    setBusy(true);
    try {
      const js = await apiPost("/api/models/vision/load", { name, backend });
      js.ok ? toast("Vision-Modell geladen", "ok") : toast("Laden fehlgeschlagen", "error");
    } catch (e) { toast("Vision-Load Fehler: " + e, "error"); }
    finally { setBusy(false); }
  }

  async function loadAudio() {
    if (!aSel) return;
    const name = aSel.value;
    const kind = aKind ? aKind.value : "whisper";
    if (!name) { toast("Kein Audio-Modell gewählt", "error"); return; }
    setBusy(true);
    try {
      const js = await apiPost("/api/models/audio/load", { name, kind });
      js.ok ? toast("Audio-Modell geladen", "ok") : toast("Laden fehlgeschlagen", "error");
    } catch (e) { toast("Audio-Load Fehler: " + e, "error"); }
    finally { setBusy(false); }
  }

  if (vBtn) vBtn.addEventListener("click", loadVision);
  if (aBtn) aBtn.addEventListener("click", loadAudio);
}


/* ------------------------------
 *  Export / Import – optional
 * ------------------------------ */

function safeInitExportImport() {
  const btnMark = document.getElementById("exportMarkBtn");
  const inDays = document.getElementById("exportDays");
  const inQual = document.getElementById("exportQual");
  const btnCreate = document.getElementById("exportCreateBtn");
  const inImport = document.getElementById("importZip");

  async function markRecent() {
    const days = parseInt((inDays && inDays.value) ? inDays.value : "7", 10);
    const min_quality = parseFloat((inQual && inQual.value) ? inQual.value : "0.6");
    setBusy(true);
    try {
      const js = await apiPost("/api/export/mark", { days, min_quality });
      toast(`Markiert: ${js.marked || 0}`, "ok");
    } catch (e) { toast("Markierung fehlgeschlagen: " + e, "error"); }
    finally { setBusy(false); }
  }

  async function createZip() {
    setBusy(true);
    try {
      const js = await apiPost("/api/export/create", {});
      if (js.ok) {
        toast("Export erstellt", "ok");
        if (js.zip_path) {
          const link = document.getElementById("exportZipLink");
          if (link) {
            link.href = js.zip_path;
            link.style.display = "inline-block";
            link.textContent = "Export herunterladen";
          }
        }
      } else { toast("Export verweigert", "error"); }
    } catch (e) { toast("Export Fehler: " + e, "error"); }
    finally { setBusy(false); }
  }

  async function importZip() {
    const f = (inImport && inImport.files && inImport.files[0]) ? inImport.files[0] : null;
    if (!f) { toast("Bitte ZIP wählen", "error"); return; }
    setBusy(true);
    try {
      const js = await apiUpload("/api/import", "package", f, {});
      js.ok ? toast("Import erfolgreich", "ok") : toast("Import fehlgeschlagen", "error");
    } catch (e) { toast("Import Fehler: " + e, "error"); }
    finally { setBusy(false); }
  }

  if (btnMark)   btnMark.addEventListener("click", markRecent);
  if (btnCreate) btnCreate.addEventListener("click", createZip);
  if (inImport)  inImport.addEventListener("change", importZip);
}


/* ------------------------------
 *  Controls (Day/Dream/Night)
 * ------------------------------ */

function safeInitControls() {
  const dreamChk = document.getElementById("dreamToggle");
  const thInp = document.getElementById("lightThreshold");
  const dlInp = document.getElementById("nightDelay");

  async function setDream() {
    const enable = dreamChk && dreamChk.checked;
    try {
      await apiPost("/api/control/dream", { enable: !!enable });
      toast(`Traummodus ${enable ? "aktiviert" : "deaktiviert"}`, "ok");
    } catch (e) { toast("Traummodus Fehler: " + e, "error"); }
  }

  async function setNight() {
    const lt = thInp ? parseInt(thInp.value || "25", 10) : 25;
    const dm = dlInp ? parseInt(dlInp.value || "30", 10) : 30;
    try {
      await apiPost("/api/control/night", { light_threshold: lt, delay_minutes: dm });
      toast(`Nachtmodus gesetzt (Schwelle=${lt}, Verzögerung=${dm}m)`, "ok");
    } catch (e) {
      toast("Nachtmodus Fehler: " + e, "error");
    }
  }

  if (dreamChk) dreamChk.addEventListener("change", setDream);
  if (thInp)    thInp.addEventListener("change", setNight);
  if (dlInp)    dlInp.addEventListener("change", setNight);
}


/* ------------------------------
 *  Vision/Audio Status – optional
 * ------------------------------ */

function safeInitVisionAudioStatus() {
  const vBox = document.getElementById("visionStatus");
  const aBox = document.getElementById("audioStatus");
  if (!vBox && !aBox) return;

  async function refresh() {
    if (vBox) {
      try {
        const v = await apiGet("/api/vision/status");
        vBox.textContent = `Vision: ${v.model || "-"} [${v.backend || "-"}], FPS=${v.fps != null ? v.fps : "-"}`;
      } catch (_) {
        vBox.textContent = "Vision: n/a";
      }
    }
    if (aBox) {
      try {
        const a = await apiGet("/api/audio/status");
        aBox.textContent = `Audio: ${a.model || "-"} [${a.kind || "-"}], Level=${a.level != null ? a.level : "-"}`;
      } catch (_) {
        aBox.textContent = "Audio: n/a";
      }
    }
  }
  refresh();
  OROMA.timers.av = setInterval(refresh, 3000);
}


/* ------------------------------
 *  Spiele – Helpers & Init
 * ------------------------------ */

function createGamePoller({ statePath, drawFn, intervalMs = 500 }) {
  let dead = false;
  async function tick() {
    if (dead) return;
    try {
      const st = await apiGet(statePath);
      if (!dead) drawFn(st);
    } catch (e) {
      console.warn("poller", statePath, e);
    }
  }
  const id = setInterval(tick, intervalMs);
  tick();
  return {
    stop() { dead = true; try { clearInterval(id); } catch(_){} },
    id
  };
}

function safeInitGames() {
  initTicTacToe();
  initConnect4();
  initSnake();
  initPong();
}

/* TicTacToe */
function initTicTacToe() {
  const elBoard = document.getElementById("tttBoard");
  if (!elBoard) return;
  const ctx = elBoard.getContext("2d");
  const S = 300;
  elBoard.width = S; elBoard.height = S;

  async function draw() {
    const st = await apiGet("/api/games/tictactoe/state");
    ctx.clearRect(0,0,S,S);
    ctx.strokeStyle = "#444"; ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(S/3, 0); ctx.lineTo(S/3, S);
    ctx.moveTo(2*S/3, 0); ctx.lineTo(2*S/3, S);
    ctx.moveTo(0, S/3); ctx.lineTo(S, S/3);
    ctx.moveTo(0, 2*S/3); ctx.lineTo(S, 2*S/3);
    ctx.stroke();

    const b = st.board || Array(9).fill(null);
    ctx.lineWidth = 3;
    for (let i=0;i<9;i++){
      const r = Math.floor(i/3), c = i%3;
      const x = c*(S/3)+S/6, y=r*(S/3)+S/6;
      const v = b[i];
      if (v === "X") {
        ctx.strokeStyle="#1e88e5";
        ctx.beginPath();
        ctx.moveTo(x-30,y-30); ctx.lineTo(x+30,y+30);
        ctx.moveTo(x+30,y-30); ctx.lineTo(x-30,y+30);
        ctx.stroke();
      } else if (v === "O") {
        ctx.strokeStyle="#e53935";
        ctx.beginPath();
        ctx.arc(x,y,34,0,Math.PI*2);
        ctx.stroke();
      }
    }
    const info = document.getElementById("tttInfo");
    if (info) {
      if (st.winner === "draw") info.textContent = "Unentschieden.";
      else if (st.winner) info.textContent = `Gewinner: ${st.winner}`;
      else info.textContent = `Am Zug: ${st.turn}`;
    }
  }

  async function click(ev) {
    const rect = elBoard.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const y = ev.clientY - rect.top;
    const c = Math.floor(x/(S/3));
    const r = Math.floor(y/(S/3));
    const idx = r*3 + c;
    try {
      await apiPost("/api/games/tictactoe/move", { idx });
      await draw();
    } catch (e) {
      toast("Zug abgelehnt: " + e, "error");
    }
  }

  elBoard.addEventListener("click", click);
  draw();
  OROMA.timers.ttt = setInterval(draw, 2000);
}

/* Connect4 */
function initConnect4() {
  const canvas = document.getElementById("c4Board");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  const W = 350, H = 300, COLS = 7, ROWS = 6;
  canvas.width = W; canvas.height = H;

  async function draw() {
    const st = await apiGet("/api/games/connect4/state");
    ctx.clearRect(0,0,W,H);
    ctx.fillStyle = "#0d47a1";
    ctx.fillRect(0,0,W,H);
    const grid = st.grid || Array.from({length:ROWS},()=>Array(COLS).fill(0));
    const cw = W/COLS, ch = H/ROWS;
    for (let r=0;r<ROWS;r++){
      for (let c=0;c<COLS;c++){
        const cx = c*cw + cw/2;
        const cy = r*ch + ch/2;
        ctx.beginPath();
        ctx.arc(cx, cy, Math.min(cw,ch)/2-6, 0, Math.PI*2);
        const v = grid[r][c];
        if (v===1) ctx.fillStyle="#ffeb3b";
        else if (v===2) ctx.fillStyle="#e53935";
        else ctx.fillStyle="#eeeeee";
        ctx.fill();
      }
    }
    const info = document.getElementById("c4Info");
    if (info) {
      if (st.winner === "draw") info.textContent="Unentschieden.";
      else if (st.winner) info.textContent = `Gewinner: Spieler ${st.winner}`;
      else info.textContent = `Am Zug: Spieler ${st.turn || "-"}`;
    }
  }

  async function click(ev) {
    const rect = canvas.getBoundingClientRect();
    const x = ev.clientX - rect.left;
    const c = Math.floor(x/(canvas.width/7));
    try {
      await apiPost("/api/games/connect4/move", { col: c });
      await draw();
    } catch (e) { toast("Zug abgelehnt: " + e, "error"); }
  }

  canvas.addEventListener("click", click);
  draw();
  OROMA.timers.c4 = setInterval(draw, 2000);
}

/* Snake */
function initSnake() {
  const canvas = document.getElementById("snakeCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  canvas.width = 320; canvas.height = 240;

  function drawState(st) {
    ctx.clearRect(0,0,canvas.width,canvas.height);
    const cw = Math.floor(canvas.width / (st.width || 16));
    const ch = Math.floor(canvas.height / (st.height || 12));
    (st.snake || []).forEach(seg => {
      const x = (seg.x != null ? seg.x : seg[0]);
      const y = (seg.y != null ? seg.y : seg[1]);
      ctx.fillStyle = "#43a047";
      ctx.fillRect(x*cw, y*ch, cw-1, ch-1);
    });
    if (st.food) {
      const fx = (st.food.x != null ? st.food.x : st.food[0]);
      const fy = (st.food.y != null ? st.food.y : st.food[1]);
      ctx.fillStyle = "#e53935";
      ctx.fillRect(fx*cw, fy*ch, cw-1, ch-1);
    }
    const info = document.getElementById("snakeInfo");
    if (info) info.textContent = st.running ? "Läuft" : "Pausiert";
  }

  const poller = createGamePoller({
    statePath: "/games/snake/state",
    drawFn: drawState,
    intervalMs: 300
  });
  OROMA.timers.snake = poller.id;

  async function sendCmd(cmd, dx, dy) {
    const payload = { cmd };
    if (dx != null && dy != null) { payload.dx = dx; payload.dy = dy; }
    try { await apiPost("/games/snake/cmd", payload); }
    catch (e) { toast("Snake Fehler: " + e, "error"); }
  }

  const map = { ArrowUp:[0,-1], ArrowDown:[0,1], ArrowLeft:[-1,0], ArrowRight:[1,0] };
  window.addEventListener("keydown", (ev) => {
    if (!document.getElementById("snakeCanvas")) return;
    if (map[ev.key]) {
      const [dx,dy] = map[ev.key];
      sendCmd("dir", dx, dy);
    }
  });

  const btnStart = document.getElementById("snakeStart");
  const btnPause = document.getElementById("snakePause");
  const btnReset = document.getElementById("snakeReset");
  if (btnStart) btnStart.addEventListener("click", () => sendCmd("start"));
  if (btnPause) btnPause.addEventListener("click", () => sendCmd("pause"));
  if (btnReset) btnReset.addEventListener("click", () => sendCmd("reset"));
}

/* Pong */
function initPong() {
  const canvas = document.getElementById("pongCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  canvas.width = 320; canvas.height = 200;

  function drawState(st) {
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0,0,W,H);
    ctx.strokeStyle = "#aaa"; ctx.beginPath();
    ctx.moveTo(W/2,0); ctx.lineTo(W/2,H); ctx.stroke();
    ctx.fillStyle = "#1e88e5";
    ctx.beginPath();
    ctx.arc(st.bx || 10, st.by || 10, 5, 0, Math.PI*2);
    ctx.fill();
    ctx.fillStyle="#333";
    ctx.fillRect(10, st.lp || 80, 6, 36);
    ctx.fillRect(W-16, st.rp || 80, 6, 36);
    ctx.fillStyle="#000";
    ctx.font="12px Arial";
    ctx.fillText(`${st.scoreL||0}`, W/4, 14);
    ctx.fillText(`${st.scoreR||0}`, 3*W/4, 14);
    const info = document.getElementById("pongInfo");
    if (info) info.textContent = st.running ? "Läuft" : "Pausiert";
  }

  const poller = createGamePoller({
    statePath: "/games/pong/state",
    drawFn: drawState,
    intervalMs: 200
  });
  OROMA.timers.pong = poller.id;

  async function cmd(c) {
    try { await apiPost("/games/pong/cmd", {cmd:c}); }
    catch (e) { toast("Pong Fehler: " + e, "error"); }
  }

  const btnStart = document.getElementById("pongStart");
  const btnPause = document.getElementById("pongPause");
  const btnReset = document.getElementById("pongReset");
  const btnLU = document.getElementById("pongLU");
  const btnLD = document.getElementById("pongLD");
  const btnRU = document.getElementById("pongRU");
  const btnRD = document.getElementById("pongRD");
  if (btnStart) btnStart.addEventListener("click", () => cmd("start"));
  if (btnPause) btnPause.addEventListener("click", () => cmd("pause"));
  if (btnReset) btnReset.addEventListener("click", () => cmd("reset"));
  if (btnLU) btnLU.addEventListener("click", () => cmd("lp_up"));
  if (btnLD) btnLD.addEventListener("click", () => cmd("lp_down"));
  if (btnRU) btnRU.addEventListener("click", () => cmd("rp_up"));
  if (btnRD) btnRD.addEventListener("click", () => cmd("rp_down"));
}

// in scripts.js – irgendwo bei den Games:
function initFlappy() {
  // nur triggern, wenn das Canvas existiert – die eigentliche Logik steckt im Template
  const el = document.getElementById("flappyCanvas");
  if (!el) return;
  // bewusst leer: das Template übernimmt alles (Zeichnen/Loop)
}

// ... in safeInitGames():
function safeInitGames() {
  initTicTacToe();
  initConnect4();
  initSnake();
  initPong();
  initFlappy(); // ← optionaler Hook
}


/* ------------------------------
 *  Token-Dialog (optional)
 * ------------------------------ */

function initTokenDialog(){
  const btn = document.getElementById("tokenSaveBtn");
  const inp = document.getElementById("tokenInput");
  if (!btn || !inp) return;
  inp.value = localStorage.getItem("OROMA_UI_TOKEN") || "";
  btn.addEventListener("click", () => {
    const val = (inp.value || "").trim();
    localStorage.setItem("OROMA_UI_TOKEN", val);
    OROMA.token = val || null;
    toast("Token gespeichert", "ok");
  });
}


/* ------------------------------
 *  Calculator (Curriculum, Patch1) – mit API-Kompatibilität
 * ------------------------------ */

// Kompat-Layer: unterstützt alte (/api/new_task, /api/solve) und neue (/api/new_task?level, /api/answer) Endpunkte
async function calcApiNewTask(level) {
  // Versuch: GET /calculator/api/new_task?level=...
  try {
    const r = await apiGet(`/calculator/api/new_task?level=${encodeURIComponent(level)}`);
    // mögliche Formen: {ok, task_id, level, expr} oder {task:{id,expr,level}} oder {id,expr,level}
    const t = r.task || r;
    const task_id = r.task_id ?? t.id;
    const expr = r.expr ?? t.expr;
    const lev  = r.level ?? t.level ?? level;
    if (task_id != null && expr != null) return { ok: true, task_id, expr, level: lev };
  } catch(_) {}
  // Fallback: POST /calculator/api/new_task {level}
  const r2 = await apiPost("/calculator/api/new_task", { level });
  if (r2.ok && r2.task) return { ok: true, task_id: r2.task.id, expr: r2.task.expr, level: r2.task.level ?? level };
  throw new Error((r2 && r2.error) ? r2.error : "new_task failed");
}

async function calcApiAnswer(task_id, answer) {
  // Versuch: POST /calculator/api/answer
  try {
    const r = await apiPost("/calculator/api/answer", { task_id, answer });
    if (r && (r.correct != null || r.truth != null || r.reward != null)) return r;
  } catch(_) {}
  // Fallback: POST /calculator/api/solve  (altes Schema nutzt 'got')
  const r2 = await apiPost("/calculator/api/solve", { task_id, got: answer });
  return { ok: r2.ok, correct: r2.correct, truth: r2.truth, reward: r2.reward ?? null, result_id: r2.result_id };
}

async function calcApiRecent() {
  // Versuch: neues /calculator/api/recent
  try { const r = await apiGet("/calculator/api/recent"); if (r.ok) return r.rows || []; } catch(_) {}
  // Fallback: altes /calculator/api/results
  try {
    const r2 = await apiGet("/calculator/api/results");
    if (r2 && Array.isArray(r2.results)) {
      return r2.results.map(x => ({
        ts: x.created_at, level: x.level ?? "-", expr: x.expr, got: x.got, correct: !!x.correct, reward: x.reward
      }));
    }
  } catch(_){}
  return [];
}

async function calcApiStats() {
  // Versuch: neues /calculator/api/stats
  try { const r = await apiGet("/calculator/api/stats"); if (r.ok !== false) return r; } catch(_){}
  // Fallback: aus results berechnen
  const rows = await calcApiRecent();
  if (!rows.length) return { ok: true, acc: null, mean_reward: null };
  const acc = rows.filter(r => r.correct).length / rows.length;
  const rr = rows.map(r => Number(r.reward)).filter(v => Number.isFinite(v));
  const mean_reward = rr.length ? (rr.reduce((a,b)=>a+b,0)/rr.length) : null;
  return { ok: true, acc, mean_reward };
}

function safeInitCalculator() {
  const exprEl = document.getElementById("calcExpr");
  const ansEl  = document.getElementById("calcAnswer");
  const subBtn = document.getElementById("calcSubmit");
  const fbEl   = document.getElementById("calcFeedback");
  const l1Btn  = document.getElementById("calcNewL1");
  const l2Btn  = document.getElementById("calcNewL2");
  const l3Btn  = document.getElementById("calcNewL3");
  const tbody  = document.getElementById("calcRecentTbody");
  const chartCanvas = document.getElementById("calcChart");

  if (!exprEl && !ansEl && !subBtn && !l1Btn && !l2Btn && !l3Btn && !tbody && !chartCanvas) {
    return;
  }

  OROMA.state.calc = { currentTaskId: null, currentLevel: 1 };

  async function newTask(level) {
    try {
      const js = await calcApiNewTask(level);
      OROMA.state.calc.currentTaskId = js.task_id;
      OROMA.state.calc.currentLevel = js.level || level;
      if (exprEl) exprEl.textContent = js.expr || "(?)";
      if (ansEl) { ansEl.value = ""; ansEl.focus(); }
      if (fbEl) fbEl.textContent = "";
    } catch (e) {
      toast("Calculator: neue Aufgabe fehlgeschlagen: " + e, "error");
    }
  }

  async function submitAnswer() {
    const tid = OROMA.state.calc.currentTaskId;
    if (!tid) { toast("Keine aktive Aufgabe – bitte neue Aufgabe starten.", "error"); return; }
    const val = ansEl ? ansEl.value.trim() : "";
    if (val === "") { toast("Bitte Antwort eingeben", "error"); return; }
    let num = Number(val);
    if (!Number.isFinite(num)) num = Number(val.replace(",", "."));
    if (!Number.isFinite(num)) { toast("Antwort muss eine Zahl sein", "error"); return; }

    try {
      const js = await calcApiAnswer(tid, num);
      if (fbEl) {
        const txt = js.correct ? "✅ Korrekt" : "❌ Falsch";
        const rw  = (js.reward != null && Number.isFinite(Number(js.reward))) ? ` | Reward: ${Number(js.reward).toFixed(3)}` : "";
        fbEl.textContent = `${txt}${rw}`;
      }
      const truthEl = document.getElementById("calcTruth");
      if (truthEl && (js.truth != null)) truthEl.textContent = String(js.truth);
      await refreshRecent();
      await refreshStatsChart();
    } catch (e) {
      toast("Calculator: Submit fehlgeschlagen: " + e, "error");
    }
  }

  async function refreshRecent() {
    if (!tbody) return;
    try {
      const rows = await calcApiRecent();
      tbody.innerHTML = rows.map(r => {
        const d = new Date((r.ts || 0) * 1000).toLocaleString();
        const cor = r.correct ? "✅" : "❌";
        const rw = (r.reward != null && Number.isFinite(Number(r.reward))) ? (Number(r.reward).toFixed(3)) : "-";
        return `<tr>
          <td>${d}</td>
          <td>L${r.level || "-"}</td>
          <td>${r.expr || ""}</td>
          <td>${(r.got != null) ? r.got : "-"}</td>
          <td>${cor}</td>
          <td>${rw}</td>
        </tr>`;
      }).join("");
    } catch (e) {
      if (tbody) tbody.innerHTML = `<tr><td colspan="6">Fehler beim Laden: ${String(e)}</td></tr>`;
    }
  }

  async function refreshStatsChart() {
    if (!chartCanvas || typeof Chart === "undefined") return;
    try {
      const js = await calcApiStats();
      const acc = (js.acc != null) ? Number(js.acc) : null;
      const meanRw = (js.mean_reward != null) ? Number(js.mean_reward) : null;

      if (!OROMA.charts.calc) {
        const ctx = chartCanvas.getContext("2d");
        OROMA.charts.calc = new Chart(ctx, {
          type: "line",
          data: {
            labels: [],
            datasets: [
              { label: "Accuracy", data: [], borderWidth: 2, tension: 0.25, pointRadius: 0 },
              { label: "Mean Reward", data: [], borderWidth: 2, tension: 0.25, pointRadius: 0, yAxisID: 'y1' },
            ]
          },
          options: {
            responsive: true,
            scales: {
              y:  { beginAtZero: true, max: 1, title: { display: true, text: "Accuracy" } },
              y1: { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false }, title: { display: true, text: "Reward" } }
            },
            plugins: { legend: { position: "bottom" } }
          }
        });
      }
      const lab = new Date().toLocaleTimeString();
      const ch = OROMA.charts.calc;
      ch.data.labels.push(lab);
      ch.data.datasets[0].data.push((acc != null) ? acc : null);
      ch.data.datasets[1].data.push((meanRw != null) ? meanRw : null);

      const MAX = 120;
      if (ch.data.labels.length > MAX) {
        ch.data.labels.shift();
        ch.data.datasets.forEach(ds => ds.data.shift());
      }
      ch.update();
    } catch (e) {
      // non-fatal
    }
  }

  if (l1Btn) l1Btn.addEventListener("click", () => newTask(1));
  if (l2Btn) l2Btn.addEventListener("click", () => newTask(2));
  if (l3Btn) l3Btn.addEventListener("click", () => newTask(3));
  if (subBtn) subBtn.addEventListener("click", submitAnswer);
  if (ansEl)  ansEl.addEventListener("keydown", (ev) => { if (ev.key === "Enter") submitAnswer(); });

  refreshRecent();
  refreshStatsChart();
}


/* ------------------------------
 *  Scientific Calculator (Patch 1 for v3.6)
 * ------------------------------ */

function safeInitSciCalc() {
  const expr = document.getElementById("expr");
  const xmin = document.getElementById("xmin");
  const xmax = document.getElementById("xmax");
  const x0   = document.getElementById("x0"); // optional
  const out  = document.getElementById("scicalcResult");

  const btnPlot   = document.getElementById("btnPlot");
  const btnRoots  = document.getElementById("btnRoots");
  const btnTaylor = document.getElementById("btnTaylor");
  const btnLimit  = document.getElementById("btnLimit");
  const btnBar    = document.getElementById("btnBar");
  const btnPie    = document.getElementById("btnPie");

  const chartLine = document.getElementById("chartLine");
  const chartBar  = document.getElementById("chartBar");
  const chartPie  = document.getElementById("chartPie");

  if (!expr || !xmin || !xmax || !out) return;  // Seite nicht aktiv

  function show(txt, type="info") {
    if (out) out.textContent = txt;
    // dezent: kein Toast bei jedem Info-Text
    if (type === "error") toast(txt, type);
  }

  function needChartJs() {
    if (typeof Chart === "undefined") {
      show("Chart.js ist nicht geladen – bitte im Template einbinden.", "error");
      return false;
    }
    return true;
  }

  async function doPlot() {
    try {
      const js = await apiPost("/scicalc/api/plot", {
        expr: expr.value, xmin: parseFloat(xmin.value), xmax: parseFloat(xmax.value)
      });
      if (!js.ok) throw new Error(js.error || "Fehler");
      if (!chartLine || !needChartJs()) return;
      const ctx = chartLine.getContext("2d");
      if (OROMA.charts.sciLine) OROMA.charts.sciLine.destroy();
      OROMA.charts.sciLine = new Chart(ctx, {
        type: "line",
        data: { labels: js.x, datasets: [{ label: expr.value, data: js.y, borderWidth: 2, pointRadius: 0 }] },
        options: { responsive: true }
      });
      show(`Plot f(x) für Bereich [${xmin.value}, ${xmax.value}]`);
    } catch(e) { show("Plot-Fehler: " + e, "error"); }
  }

  async function doRoots() {
    try {
      const js = await apiPost("/scicalc/api/roots", {
        expr: expr.value, xmin: parseFloat(xmin.value), xmax: parseFloat(xmax.value)
      });
      if (!js.ok) throw new Error(js.error);
      show("Nullstellen: " + ((js.roots || []).join(", ") || "keine im Bereich"));
    } catch(e) { show("Root-Fehler: " + e, "error"); }
  }

  async function doTaylor() {
    try {
      const x0v = x0 ? parseFloat(x0.value) : 0;
      const js = await apiPost("/scicalc/api/taylor", {
        expr: expr.value, x0: Number.isFinite(x0v) ? x0v : 0, n: 5
      });
      if (!js.ok) throw new Error(js.error);
      show("Taylor: " + js.series);
    } catch(e) { show("Taylor-Fehler: " + e, "error"); }
  }

  async function doLimit() {
    try {
      const x0v = x0 ? parseFloat(x0.value) : 0;
      const js = await apiPost("/scicalc/api/limit", {
        expr: expr.value, x0: Number.isFinite(x0v) ? x0v : 0
      });
      if (!js.ok) throw new Error(js.error);
      show("Limit = " + js.limit);
    } catch(e) { show("Limit-Fehler: " + e, "error"); }
  }

  async function doBar() {
    try {
      const js = await apiPost("/scicalc/api/bar", { data: {A:3, B:7, C:5} });
      if (!js.ok) throw new Error(js.error);
      if (!chartBar || !needChartJs()) return;
      const ctx = chartBar.getContext("2d");
      if (OROMA.charts.sciBar) OROMA.charts.sciBar.destroy();
      OROMA.charts.sciBar = new Chart(ctx, {
        type: "bar",
        data: { labels: js.labels, datasets: [{ label: "Balkendiagramm", data: js.values }] },
        options: { responsive: true }
      });
      show("Balkendiagramm aktualisiert.");
    } catch(e) { show("Bar-Fehler: " + e, "error"); }
  }

  async function doPie() {
    try {
      const js = await apiPost("/scicalc/api/pie", { data: {A:40, B:25, C:35} });
      if (!js.ok) throw new Error(js.error);
      if (!chartPie || !needChartJs()) return;
      const ctx = chartPie.getContext("2d");
      if (OROMA.charts.sciPie) OROMA.charts.sciPie.destroy();
      OROMA.charts.sciPie = new Chart(ctx, {
        type: "pie",
        data: { labels: js.labels, datasets: [{ label: "Tortendiagramm", data: js.values }] },
        options: { responsive: true }
      });
      show("Tortendiagramm aktualisiert.");
    } catch(e) { show("Pie-Fehler: " + e, "error"); }
  }

  if (btnPlot)   btnPlot.onclick = doPlot;
  if (btnRoots)  btnRoots.onclick = doRoots;
  if (btnTaylor) btnTaylor.onclick = doTaylor;
  if (btnLimit)  btnLimit.onclick = doLimit;
  if (btnBar)    btnBar.onclick = doBar;
  if (btnPie)    btnPie.onclick = doPie;
}

/* ------------------------------
 *  SetCalc (Mengenlehre, Patch 2 for v3.6 – Hybrid-UI)
 * ------------------------------ */

function safeInitSetCalc() {
  const A = document.getElementById("setA");
  const B = document.getElementById("setB");
  const U = document.getElementById("setU");
  const out = document.getElementById("setcalcResult");
  const vennCanvas = document.getElementById("setVennChart");

  const btnUnion = document.getElementById("btnUnion");
  const btnInter = document.getElementById("btnInter");
  const btnDiff  = document.getElementById("btnDiff");
  const btnCompl = document.getElementById("btnCompl");
  const btnPow   = document.getElementById("btnPow");
  const btnCart  = document.getElementById("btnCart");
  const btnVenn  = document.getElementById("btnVenn");

  if (!A || !out) return; // Seite nicht aktiv

  function showCompact(label, res) {
    if (Array.isArray(res)) {
      out.textContent = `${label} = {${res.join(", ")}}`;
      if (res.length > 20) addDetailsButton(res);
    } else if (typeof res === "object") {
      out.textContent = `${label}: ${JSON.stringify(res)}`;
      addDetailsButton(res);
    } else {
      out.textContent = `${label} = ${res}`;
    }
  }

  function addDetailsButton(data) {
    const btn = document.createElement("button");
    btn.textContent = "Details anzeigen";
    btn.style.marginLeft = "8px";
    btn.onclick = () => {
      alert(JSON.stringify(data, null, 2));
    };
    out.appendChild(btn);
  }

  async function call(api, body) {
    try {
      const js = await apiPost(`/setcalc/api/${api}`, body);
      if (!js.ok) throw new Error(js.error || "Fehler");
      return js;
    } catch (e) {
      out.textContent = "Fehler: " + e;
      toast("SetCalc Fehler: " + e, "error");
      throw e;
    }
  }

  if (btnUnion) btnUnion.onclick = async () => {
    const js = await call("union", {A: A.value, B: B.value});
    showCompact("A ∪ B", js.result);
  };

  if (btnInter) btnInter.onclick = async () => {
    const js = await call("intersection", {A: A.value, B: B.value});
    showCompact("A ∩ B", js.result);
  };

  if (btnDiff) btnDiff.onclick = async () => {
    const js = await call("difference", {A: A.value, B: B.value});
    showCompact("A \\ B", js.result);
  };

  if (btnCompl) btnCompl.onclick = async () => {
    const js = await call("complement", {A: A.value, U: U.value});
    showCompact("Komplement", js.result);
  };

  if (btnPow) btnPow.onclick = async () => {
    const js = await call("powerset", {A: A.value});
    showCompact("|P(A)|", js.count);
    addDetailsButton(js.result);
  };

  if (btnCart) btnCart.onclick = async () => {
    const js = await call("cartesian", {A: A.value, B: B.value});
    showCompact("|A×B|", js.count);
    addDetailsButton(js.pairs);
  };

  if (btnVenn) btnVenn.onclick = async () => {
    const js = await call("venn_counts", {A: A.value, B: B.value});
    showCompact("Venn", js);

    if (vennCanvas && typeof Chart !== "undefined") {
      const ctx = vennCanvas.getContext("2d");
      vennCanvas.style.display = "block";
      if (OROMA.charts.venn) OROMA.charts.venn.destroy();
      OROMA.charts.venn = new Chart(ctx, {
        type: "bar",
        data: {
          labels: ["|A|", "|B|", "|A∩B|"],
          datasets: [{
            label: "Mächtigkeit",
            data: [js.sizeA, js.sizeB, js.sizeAB]
          }]
        },
        options: {
          responsive: true,
          plugins: { legend: { display: false } }
        }
      });
    }
  };
}

/* ------------------------------
 *  Badges – Forgetting (Patch 2.2)  (FIX enthalten)
 * ------------------------------ */

function safeInitForgettingBadge() {
  const el = document.getElementById("forgetting-badge");
  if (!el) return;

  async function refresh() {
    try {
      const j = await apiGet("/forgetting/api/state");
      if (!j.ok) {
        el.textContent = "ERR";
        el.className = "forgetting-badge forgetting-fail";
        return;
      }
      const w = j.avg_weight ?? 0.0;
      const comp = (j.n_active != null && j.n_compressed != null)
        ? ((j.n_active + j.n_compressed) > 0 ? (j.n_compressed / (j.n_active + j.n_compressed)) : 0.0)
        : (j.compression_rate ?? 0.0);

      if (w >= 0.7 && comp < 0.25) {
        el.textContent = "GOOD";
        el.className = "forgetting-badge forgetting-ok";
      } else if (w >= 0.4 && comp < 0.5) {
        el.textContent = "MID";
        el.className = "forgetting-badge forgetting-warn";
      } else {
        el.textContent = "LOW";
        el.className = "forgetting-badge forgetting-fail";
      }
    } catch (e) {
      el.textContent = "ERR";
      el.className = "forgetting-badge forgetting-fail";
    }
  }

  refresh();
  OROMA.timers.forgetting = setInterval(refresh, 15000);
}


/* ------------------------------
 *  Badges – Core (Gap / Phase / ASR / Health)
 * ------------------------------ */

function safeInitGapBadge() {
  const el = document.getElementById("gap-badge");
  if (!el) return;
  async function refresh() {
    try {
      const j = await apiGet("/gaps/api/summary");
      if (!j.ok || !j.summary) { el.textContent = "ERR"; el.className = "gap-badge gap-high"; return; }
      const conf = j.summary.confidence ?? 0.0;
      if (conf >= 0.75) { el.textContent = "LOW";  el.className = "gap-badge gap-low"; }
      else if (conf >= 0.4) { el.textContent = "MED"; el.className = "gap-badge gap-med"; }
      else { el.textContent = "HIGH"; el.className = "gap-badge gap-high"; }
    } catch { el.textContent = "ERR"; el.className = "gap-badge gap-high"; }
  }
  refresh();
  OROMA.timers.gap = setInterval(refresh, 10000);
}

function safeInitPhaseBadge() {
  const el = document.getElementById("phase-badge");
  if (!el) return;
  async function refresh() {
    try {
      const j = await apiGet("/control/api/status");
      if (!j || j.ok === false) {
        el.textContent = "ERR";
        el.className = "phase-badge phase-unknown";
        return;
      }
      // Robust: erst top-level phase, dann circadian.phase; Case-normalisieren
      let p = (j.phase || (j.circadian && j.circadian.phase) || "unknown");
      p = String(p).trim().toUpperCase();

      if (p === "DAY") {
        el.textContent = "DAY";
        el.className = "phase-badge phase-day";
      } else if (p === "DREAM") {
        el.textContent = "DREAM";
        el.className = "phase-badge phase-dream";
      } else {
        el.textContent = "UNKNOWN";
        el.className = "phase-badge phase-unknown";
      }
    } catch {
      el.textContent = "ERR";
      el.className = "phase-badge phase-unknown";
    }
  }
  refresh();
  OROMA.timers.phase = setInterval(refresh, 10000);
}

function safeInitAsrBadge() {
  const el = document.getElementById("asr-badge");
  if (!el) return;
  async function refresh() {
    try {
      const j = await apiGet("/asr2/api/status");
      if (j.ok && j.active) { el.textContent = "OK"; el.className = "asr-badge asr-ok"; }
      else { el.textContent = "OFF"; el.className = "asr-badge asr-off"; }
    } catch { el.textContent = "OFF"; el.className = "asr-badge asr-off"; }
  }
  refresh();
  OROMA.timers.asr = setInterval(refresh, 20000);
}

function safeInitHealthBadge() {
  const el = document.getElementById("health-badge");
  if (!el) return;
  async function refresh() {
    try {
      const j = await apiGet("/health/api/health");
      if (!j.ok) { el.textContent = "ERR"; el.className = "health-badge npu-fail"; return; }
      const gpu_warn = !!(j.gpu_status && (j.gpu_status.includes("error") || j.gpu_status.includes("unavailable")));
      if (j.npu_status && j.npu_status.includes("stream")) {
        el.textContent = gpu_warn ? "NPU OK / GPU WARN" : "NPU OK";
        el.className = gpu_warn ? "health-badge npu-warn" : "health-badge npu-ok";
      } else if (j.npu_status && j.npu_status.includes("no-streams")) {
        el.textContent = "NPU …";
        el.className = "health-badge npu-warn";
      } else {
        el.textContent = "NPU OFF";
        el.className = "health-badge npu-off";
      }
    } catch { el.textContent = "ERR"; el.className = "health-badge npu-fail"; }
  }
  refresh();
  OROMA.timers.hbadge = setInterval(refresh, 15000);
}


/* ------------------------------
 *  Badge – Audio (Mic/Headset)
 * ------------------------------ */

function safeInitAudioBadge() {
  const el = document.getElementById("audio-badge");
  if (!el) return;

  async function getAudioStatus() {
    try { return await apiGet("/audio/api/status"); }
    catch (_) {
      try { return await apiGet("/api/audio/status"); }
      catch (e2) { return null; }
    }
  }

  function setBadge(txt, cls) {
    el.textContent = txt;
    el.className = "audio-badge " + cls;
  }

  async function refresh() {
    try {
      const j = await getAudioStatus();
      if (!j) { setBadge("OFF", "audio-off"); return; }

      const ok     = !!(j.ok ?? j.active ?? (j.input && j.input.active));
      const muted  = !!(j.muted ?? (j.input && j.input.muted));
      const device = (j.device || (j.input && j.input.device) || "").toString();

      if (!ok)            { setBadge("OFF", "audio-off"); return; }
      if (muted)          { setBadge("MUTED", "audio-muted"); return; }
      if (device) {
        const short = device.includes("Jabra") ? "Jabra"
                    : device.length > 12 ? device.slice(0,12) + "…" : device;
        setBadge(short, "audio-ok");
      } else {
        setBadge("OK", "audio-ok");
      }
    } catch {
      setBadge("ERR", "audio-fail");
    }
  }

  refresh();
  OROMA.timers.abadge = setInterval(refresh, 15000);
}


/* ------------------------------
 *  Research / Hypothesen (v3.6)
 * ------------------------------ */

function safeInitResearch() {
  const listBox = document.getElementById("hypoList");
  const txtBox  = document.getElementById("hypoText");
  const btnNew  = document.getElementById("hypoSubmit");

  if (!listBox || !txtBox || !btnNew) return;

  async function loadHypotheses() {
    try {
      const j = await apiGet("/research/api/list");
      if (!j.ok) { listBox.textContent = "Fehler beim Laden."; return; }
      if (!j.items || !j.items.length) {
        listBox.textContent = "Keine Hypothesen gespeichert.";
        return;
      }
      listBox.innerHTML = "<ul>" + j.items.map(h =>
        `<li><b>${h.id}</b>: ${h.text} <small>[${h.created}]</small></li>`
      ).join("") + "</ul>";
    } catch (e) {
      listBox.textContent = "Fehler: " + e;
    }
  }

  async function submitHypothesis() {
    const txt = (txtBox.value || "").trim();
    if (!txt) { toast("Bitte Text eingeben", "error"); return; }
    try {
      const j = await apiPost("/research/api/new", { text: txt });
      if (j.ok) {
        toast("Hypothese gespeichert (ID=" + j.id + ")", "ok");
        txtBox.value = "";
        await loadHypotheses();
      } else {
        toast("Fehler: " + (j.error || "unbekannt"), "error");
      }
    } catch (e) {
      toast("API Fehler: " + e, "error");
    }
  }

  btnNew.addEventListener("click", submitHypothesis);
  loadHypotheses();
}


/* ------------------------------
 *  Curriculum / Missions Badges (v3.6)
 * ------------------------------ */

function safeInitCurriculumBadge() {
  const el = document.getElementById("curriculum-badge");
  if (!el) return;

  async function refresh() {
    try {
      const j = await apiGet("/curriculum/api/state");
      if (!j.ok) {
        el.textContent = "ERR";
        el.className = "curriculum-badge badge-fail";
        return;
      }
      el.textContent = `Stage ${j.state.stage}: ${j.stage_name}`;
      el.className = "curriculum-badge badge-ok";
    } catch (e) {
      el.textContent = "ERR";
      el.className = "curriculum-badge badge-fail";
    }
  }

  refresh();
  OROMA.timers.curriculum = setInterval(refresh, 20000);
}

function safeInitMissionsBadge() {
  const el = document.getElementById("missions-badge");
  if (!el) return;

  async function refresh() {
    try {
      const j = await apiGet("/missions/api/list");
      if (!j.ok) {
        el.textContent = "ERR";
        el.className = "missions-badge badge-fail";
        return;
      }
      const active = (j.items || []).filter(m => !m.done).length;
      if (active > 0) {
        el.textContent = `${active} offen`;
        el.className = "missions-badge badge-warn";
      } else {
        el.textContent = "0 offen";
        el.className = "missions-badge badge-ok";
      }
    } catch (e) {
      el.textContent = "ERR";
      el.className = "missions-badge badge-fail";
    }
  }

  refresh();
  OROMA.timers.missions = setInterval(refresh, 20000);
}


/* ------------------------------
 *  Init (zentral)
 * ------------------------------ */

document.addEventListener("DOMContentLoaded", () => {
  OROMA.token = window.OROMA_UI_TOKEN || localStorage.getItem("OROMA_UI_TOKEN") || null;

  // Basisseiten & Module
  safeInitHealth();
  safeInitLearningChart();
  safeInitChat();
  safeInitModels();
  safeInitExportImport();
  safeInitControls();
  safeInitVisionAudioStatus();
  safeInitGames();
  safeInitCalculator();
  safeInitSciCalc();     // ← SciCalc hier einmalig einbinden
  safeInitSetCalc();     // ← SetCalc (Patch 2)
  initTokenDialog();

  // Badges
  safeInitGapBadge();
  safeInitPhaseBadge();
  safeInitAsrBadge();
  safeInitHealthBadge();
  safeInitForgettingBadge();
  safeInitCurriculumBadge();
  safeInitMissionsBadge();
  safeInitAudioBadge();
});


/* ------------------------------
 *  Cleanup beim Unload
 * ------------------------------ */
window.addEventListener("beforeunload", () => {
  Object.values(OROMA.timers).forEach(id => { try { clearInterval(id); } catch(_){} });
});