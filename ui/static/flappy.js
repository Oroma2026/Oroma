// /opt/ai/oroma/v2.11/ui/static/flappy.js
(function(){
  const $ = (s)=>document.querySelector(s);
  const sleep = (ms)=>new Promise(r=>setTimeout(r,ms));

  const elAscii = $("#fb-ascii");
  const elState = $("#fb-state");
  const elReset = $("#fb-reset");
  const elFlap  = $("#fb-flap");
  const elAuto  = $("#fb-autopilot");
  const elStatus= $("#fb-status");
  const elSeed  = $("#fb-seed");
  const elApplySeed = $("#fb-apply-seed");
  const elCfg   = $("#fb-config");

  async function api(path, opts={}){
    const res = await fetch(path, Object.assign({
      headers: {"Content-Type":"application/json"}
    }, opts));
    if (path.endsWith("/ascii")) return await res.text();
    return await res.json();
  }

  function renderState(st){
    elState.textContent = JSON.stringify(st, null, 2);
    elStatus.textContent = `score=${st.score}  y=${st.y.toFixed(2)} vy=${st.vy.toFixed(2)} dx=${st.dx.toFixed(2)} alive=${st.alive}`;
  }

  function renderCfg(cfg){
    elCfg.innerHTML = `
      <table class="table" style="width:100%; font-family:monospace;">
        <tbody>
          ${Object.entries(cfg).map(([k,v]) => `
            <tr><td style="width:220px">${k}</td>
            <td><input data-k="${k}" value="${v}" style="width:100%"></td></tr>
          `).join("")}
        </tbody>
      </table>
      <button id="fb-save-cfg" class="btn">Save Config</button>
    `;
    $("#fb-save-cfg").onclick = async ()=>{
      const inputs = elCfg.querySelectorAll("input[data-k]");
      const patch = {};
      inputs.forEach(inp => patch[inp.dataset.k] = inp.value);
      await api("/api/flappy/config", {method:"POST", body: JSON.stringify(patch)});
      await refreshAll();
    };
  }

  async function refreshAll(){
    const [state, ascii, cfg] = await Promise.all([
      api("/api/flappy/state"),
      api("/api/flappy/ascii"),
      api("/api/flappy/config")
    ]);
    renderState(state);
    elAscii.textContent = ascii;
    renderCfg(cfg);
  }

  elReset.onclick = async ()=>{
    await api("/api/flappy/reset", {method:"POST", body:"{}"});
    await refreshAll();
  };

  elFlap.onclick = ()=> step(1);
  document.addEventListener("keydown", (e)=>{
    if (e.code === "Space"){
      e.preventDefault();
      step(1);
    }
  });

  async function step(a){
    const r = await api("/api/flappy/step", {method:"POST", body: JSON.stringify({action:a})});
    if (r && r.state) renderState(r.state);
  }

  elAuto.onchange = async ()=>{
    await api("/api/flappy/autopilot", {method:"POST", body: JSON.stringify({enabled: elAuto.checked})});
  };

  elApplySeed.onclick = async ()=>{
    const seed = parseInt(elSeed.value || "0", 10);
    await api("/api/flappy/config", {method:"POST", body: JSON.stringify({seed: isNaN(seed)? null : seed})});
    await api("/api/flappy/reset", {method:"POST", body:"{}"});
    await refreshAll();
  };

  (async function loop(){
    while(true){
      try{
        const [state, ascii] = await Promise.all([
          api("/api/flappy/state"),
          api("/api/flappy/ascii"),
        ]);
        renderState(state);
        elAscii.textContent = ascii;
      }catch(e){}
      await sleep(200);
    }
  })();

  refreshAll();
})();