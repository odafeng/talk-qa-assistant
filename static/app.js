const TARGET_RATE = 16000;
const TAB = 0, MIC = 1;  // first byte of each audio frame; the server maps them to 聽眾 / 我
const $ = id => document.getElementById(id);
const cards = new Map();
const captures = [];
let ws;

function setStatus(text, kind = "") {
  $("status").textContent = text;
  $("status").className = "pill " + kind;
}
function warn(text) { $("warn").hidden = !text; $("warn").textContent = text || ""; }

// Same resampling and PCM16 packing as lecture_live_notes/static/app.js.
function downsample(buffer, inRate, outRate) {
  if (inRate === outRate) return buffer;
  const ratio = inRate / outRate, out = new Float32Array(Math.round(buffer.length / ratio));
  let o = 0, i = 0;
  while (o < out.length) {
    const next = Math.round((o + 1) * ratio);
    let sum = 0, n = 0;
    for (let k = i; k < next && k < buffer.length; k++) { sum += buffer[k]; n++; }
    out[o++] = n ? sum / n : 0;
    i = next;
  }
  return out;
}
function frame(channel, arr) {
  const view = new DataView(new ArrayBuffer(1 + arr.length * 2));
  view.setUint8(0, channel);
  for (let i = 0; i < arr.length; i++) {
    const s = Math.max(-1, Math.min(1, arr[i]));
    view.setInt16(1 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return view.buffer;
}

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onopen = () => setStatus("已連線，尚未聆聽");
  ws.onclose = () => { setStatus("與伺服器斷線，3 秒後重連", "err"); setTimeout(connect, 3000); };
  ws.onmessage = e => handle(JSON.parse(e.data));
}

function handle(ev) {
  switch (ev.type) {
    case "hello": {
      document.title = $("title").textContent = ev.title + " Q&A 助手";
      const problems = [];
      if (!ev.anthropic_key) problems.push("沒有 Claude API key（填 talk-qa-assistant/.env 後重開），只會有逐字稿");
      if (!ev.gemini_key) problems.push("沒有 Gemini API key");
      if (ev.warm_error) problems.push("Claude 預熱失敗：" + ev.warm_error);
      warn(problems.join("；"));
      $("dlTranscript").href = "/download/" + ev.transcript;
      $("dlQa").href = "/download/" + ev.qa;
      break;
    }
    case "status": setStatus(ev.text, "on"); break;
    case "error": warn(ev.text); break;
    case "thinking": $("thinking").hidden = !ev.on; break;
    case "interim": $("interim-" + ev.speaker).textContent = ev.speaker + "：" + ev.text; break;
    case "final": addLine(ev); break;
    case "answer_start": cards.set(ev.id, {raw: "", el: null, started: performance.now()}); break;
    case "answer_delta": { const c = cards.get(ev.id); c.raw += ev.text; render(c); break; }
    case "answer_done": finish(ev); break;
    case "answer_error": { const c = cards.get(ev.id); c.raw = "【注意】" + ev.text; render(c, true); break; }
  }
}

function addLine(ev) {
  const div = document.createElement("div");
  div.className = "line " + (ev.speaker === "聽眾" ? "aud" : "me");
  div.textContent = ev.line;
  $("lines").append(div);
  const interim = $("interim-" + ev.speaker);
  if (interim) interim.textContent = "";
  $("dlTranscript").hidden = false;
  div.scrollIntoView({block: "end"});
}

// SKIP / WAIT replies never get a card; an answer gets one as soon as it starts streaming.
function render(card, force = false) {
  const head = card.raw.trimStart();
  if (!force && !head.startsWith("【")) return;
  if (!card.el) {
    document.querySelectorAll(".card.newest").forEach(el => el.classList.remove("newest"));
    $("answers").querySelector(".empty")?.remove();
    card.el = document.createElement("div");
    card.el.className = "card newest";
    for (const cls of ["meta", "body", "cites"]) {
      const d = document.createElement("div");
      d.className = cls;
      card.el.append(d);
    }
    $("answers").prepend(card.el);
  }
  const body = card.el.querySelector(".body");
  body.textContent = "";
  for (const part of head.split(/(?=【[^】]{1,4}】)/)) {
    const m = part.match(/^【([^】]{1,4})】([\s\S]*)$/);
    if (!m) continue;
    const sec = document.createElement("div");
    sec.className = "sec" + (m[1] === "口語稿" ? " speech" : m[1] === "問題" ? " question" : "");
    const label = document.createElement("span");
    label.className = "label";
    label.textContent = m[1];
    sec.append(label, document.createTextNode(m[2].trim()));
    body.append(sec);
  }
}

function finish(ev) {
  const c = cards.get(ev.id);
  if (ev.status === "skip" || ev.status === "wait") return;
  if (ev.status === "refused") { c.raw = "【注意】模型拒答，請用自己的判斷回答。"; render(c, true); }
  render(c, true);
  if (!c.el) return;
  const secs = ((performance.now() - c.started) / 1000).toFixed(1);
  c.el.querySelector(".meta").textContent = `${new Date().toLocaleTimeString()} · ${secs}s`;
  const cites = c.el.querySelector(".cites");
  for (const cite of ev.citations) {
    const d = document.createElement("div");
    d.textContent = `原文 p.${cite.page}：「${cite.text}」`;
    cites.append(d);
  }
  $("dlQa").hidden = false;
}

async function capture(stream, channel, meterId) {
  const ctx = new AudioContext();
  await ctx.resume();
  const source = ctx.createMediaStreamSource(new MediaStream(stream.getAudioTracks()));
  const processor = ctx.createScriptProcessor(4096, 1, 1);
  // The processor only runs when connected to the destination; a muted gain keeps the
  // captured Meet audio from playing a second time.
  const sink = ctx.createGain();
  sink.gain.value = 0;
  processor.onaudioprocess = e => {
    const input = e.inputBuffer.getChannelData(0);
    let energy = 0;
    for (let i = 0; i < input.length; i++) energy += input[i] * input[i];
    $(meterId).value = Math.min(100, Math.sqrt(energy / input.length) * 400);
    if (ws.readyState === WebSocket.OPEN) ws.send(frame(channel, downsample(input, ctx.sampleRate, TARGET_RATE)));
  };
  source.connect(processor);
  processor.connect(sink);
  sink.connect(ctx.destination);
  stream.getAudioTracks()[0].onended = stop;
  captures.push({stream, ctx, processor, meterId});
  ws.send(JSON.stringify({type: "start", channel}));
}

async function start() {
  let tab;
  try {
    tab = await navigator.mediaDevices.getDisplayMedia({
      video: {frameRate: 1},  // only the audio is used; keep the video stream cheap
      audio: {echoCancellation: false, noiseSuppression: false, autoGainControl: false},
      selfBrowserSurface: "exclude",
    });
  } catch (e) { warn("沒有選到 Meet 分頁：" + e.message); return; }
  if (!tab.getAudioTracks().length) {
    tab.getTracks().forEach(t => t.stop());
    warn("沒有收到聲音：請重按「開始聆聽」，選 Meet 的分頁，並打開「同時分享分頁音訊」。");
    return;
  }
  warn("");
  await capture(tab, TAB, "levelTab");
  if ($("withMic").checked) {
    try {
      const mic = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true}});
      await capture(mic, MIC, "levelMic");
    } catch (e) { warn("麥克風無法使用，只收 Meet 分頁：" + e.message); }
  }
  setStatus($("withMic").checked ? "聆聽中：Meet 分頁＋我的麥克風" : "聆聽中：Meet 分頁", "on");
  $("btnStart").disabled = true;
  $("btnStop").disabled = false;
}

async function stop() {
  if (!captures.length) return;
  ws.send(JSON.stringify({type: "stop"}));
  for (const c of captures.splice(0)) {
    c.stream.getTracks().forEach(t => t.stop());
    c.processor.disconnect();
    await c.ctx.close();
    $(c.meterId).value = 0;
  }
  $("btnStart").disabled = false;
  $("btnStop").disabled = true;
  setStatus("已停止聆聽");
}

$("btnStart").onclick = start;
$("btnStop").onclick = stop;
$("btnNow").onclick = () => ws.send(JSON.stringify({type: "answer_now"}));
$("auto").onchange = e => ws.send(JSON.stringify({type: "auto", on: e.target.checked}));
$("askForm").onsubmit = e => {
  e.preventDefault();
  const text = $("askInput").value.trim();
  if (!text) return;
  ws.send(JSON.stringify({type: "ask", text}));
  $("askInput").value = "";
};
connect();
