// static/app.js (mobile-first)
const wsUrl = (location.protocol === "https:" ? "wss:" : "ws:") + "//" + location.host + "/ws";
let ws;
function connect() {
  ws = new WebSocket(wsUrl);
  ws.onopen = () => console.log("WS open");
  ws.onmessage = (e) => {
    try { const data = JSON.parse(e.data); render(data); }
    catch(err){ console.log(err) }
  };
  ws.onclose = ()=> { console.log("WS closed"); setTimeout(connect,2000); };
  ws.onerror = (err) => console.log("WS err", err);
}
function render(payload) {
  const pending = payload.pending;
  document.getElementById("lastupdate").innerText = new Date().toLocaleTimeString();
  if (pending) {
    document.getElementById("serial").innerText = "Serial: " + (pending.serial || "-");
    document.getElementById("num").innerText = pending.number;
    const sizeEl = document.getElementById("size");
    sizeEl.innerText = pending.size || "-";
    sizeEl.className = "badge " + (pending.size === "Big" ? "big" : "small");
    document.getElementById("numconf").innerText = "NumConf " + (pending.number_conf||0).toFixed(3);
    document.getElementById("sizeconf").innerText = "SizeConf " + (pending.size_conf||0).toFixed(3);
    document.getElementById("predtime").innerText = "Pred: " + (pending.time||"");
  } else {
    document.getElementById("serial").innerText = "No strong prediction";
    document.getElementById("num").innerText = "-";
    document.getElementById("size").innerText = "-";
    document.getElementById("numconf").innerText = "";
    document.getElementById("sizeconf").innerText = "";
    document.getElementById("predtime").innerText = "";
  }
  const stats = payload.stats || {};
  document.getElementById("total").innerText = stats.total || 0;
  document.getElementById("correct_num").innerText = stats.correct_number || 0;
  document.getElementById("correct_size").innerText = stats.correct_size || 0;
  const last = payload.last_history || [];
  document.getElementById("history").innerText = last.map(x=>x[1]).slice(-40).join(" ");
}
connect();
      
