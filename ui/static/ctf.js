// /opt/ai/oroma/v2.11/ui/static/ctf.js
(function(){
  const $ = (s)=>document.querySelector(s);
  const sleep = (ms)=>new Promise(r=>setTimeout(r,ms));

  const elAscii = $("#ctf-ascii");
  const elState = $("#ctf-state");
  const elReset = $("#ctf-reset");
  const elAutoA = $("#ctf-autoA");
  const elAutoB = $("#ctf-autoB");
  const elSpeed = $("#ctf-speed");
  const elApplyAuto = $("#ctf-apply-auto");
  const elStatus = $("#ctf-status");
  const elCfg = $("#ctf-config");
  const elSeed = $("#ctf-seed");
  const elApplySeed = $("#ctf-apply-seed");

  let running = true;

  async function api(path, opts={}){
    const res = await fetch(path, Object.assign({
      headers: {"Content-Type":"application/json"}
    }, opts));
    if (path.endsWith("/ascii")) { return await res.text(); }
    return await res.json();
  }

  function renderState(st){
    elState.textContent = JSON.stringify(st, null, 2);
    elStatus.textContent = `A=${st.A_score}  B=${st.B_score}  steps=${st.steps}  size=${st.width}x${st.height}  done=${st.done}`;
  }

  function renderCfg(cfg){
    elCfg.innerHTML = `
      <table class="table" style="width:100%; font-family:monospace;">
        <tbody>
          ${Object.entries(cfg).map(([k,v]) => `
            <tr><td style="width:220px">${k}</td>
            <td><input data-k="${k}" value="${v}" style="width:100%"></td></tr>`).join("")}
        </tbody>
      </table>
      <button id="ctf-save-cfg" class="btn">Save (wirkt teilweise nach Reset)</button>
    `;
    $("#ctf-save-cfg").onclick = async ()=>{
      const inputs = elCfg.querySelectorAll("input[data-k]");
      const patch = {};
      inputs.forEach(inp => patch[inp.dataset.k] = inp.value);
      await api("/api/ctf/config", {method:"POST", body: JSON.stringify(patch)});
      await refreshAll();
    };
  }

  async function refreshAll(){
    const [state, ascii, cfg] = await Promise.all([
      api("/api/ctf/state"),
      api("/api/ctf/ascii"),
      api("/api/ctf/config"),
    ]);
    renderState(state);
    elAscii.textContent = ascii;
    renderCfg(cfg);
  }

  elReset.onclick = async ()=>{
    const seed = parseInt(elSeed.value || "0", 10);
    await api("/api/ctf/reset", {method:"POST", body: JSON.stringify({seed: isNaN(seed)? null : seed})});
    await refreshAll();
  };

  elApplySeed.onclick = elReset.onclick;

  elApplyAuto.onclick = async ()=>{
    const A = elAutoA.value || "off";
    const B = elAutoB.value || "off";
    const speed = parseFloat(elSpeed.value || "0.15");
    await api("/api/ctf/autopilot", {method:"POST", body: JSON.stringify({A,B,speed})});
  };

  // Keyboard: A=WASD/Q ; B=IJKL/U
  const Amap = { "KeyQ":0, "KeyW":1, "KeyS":2, "KeyA":3, "KeyD":4 };
  const Bmap = { "KeyU":0, "KeyI":1, "KeyK":2, "KeyJ":3, "KeyL":4 };
  document.addEventListener("keydown", async (e)=>{
    if (Amap[e.code] !== undefined && (elAutoA.value||"off")==="off"){
      await api("/api/ctf/step", {method:"POST", body: JSON.stringify({A:Amap[e.code], B:0})});
    } else if (Bmap[e.code] !== undefined && (elAutoB.value||"off")==="off"){
      await api("/api/ctf/step", {method:"POST", body: JSON.stringify({A:0, B:Bmap[e.code]})});
    }
  });

  // Live-Refresh Loop
  (async function loop(){
    while(running){
      try{
        const [state, ascii] = await Promise.all([
          api("/api/ctf/state"),
          api("/api/ctf/ascii"),
        ]);
        renderState(state);
        elAscii.textContent = ascii;
      }catch(e){}
      await sleep(200); // ~5 FPS
    }
  })();

  refreshAll();
})();