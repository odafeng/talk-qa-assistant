const TARGET_RATE = 16000;
const TAB = 0, MIC = 1;  // first byte of each audio frame; the server maps them to 聽眾 / 我
const $ = id => document.getElementById(id);
const answers = new Map();  // id -> {raw, started, visible, done, citations, li}
const captures = [];
let ws, shownId = null, latestId = null, pinned = false, listening = false, thinking = false;

function setStatus(text, kind = "") {
  $("status").dataset.text = text;
  $("status").className = "status " + kind;
  paintStatus();
}
function paintStatus() {
  const base = $("status").dataset.text || "";
  $("status").textContent = thinking && listening ? base + "，正在判斷" : base;
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
  ws.onopen = () => setStatus(listening ? "聆聽中" : "已連線，還沒開始聆聽");
  ws.onclose = () => { setStatus("和本機伺服器斷線，3 秒後重連", "err"); setTimeout(connect, 3000); };
  ws.onmessage = e => handle(JSON.parse(e.data));
}

function handle(ev) {
  switch (ev.type) {
    case "hello": {
      document.title = $("title").textContent = ev.title + " Q&A 助手";
      const problems = [];
      if (!ev.anthropic_key) problems.push("沒有 Claude API key：在 talk-qa-assistant/.env 填好後重開，現在只會有逐字稿");
      if (!ev.gemini_key) problems.push("沒有 Gemini API key：在 talk-qa-assistant/.env 填好後重開");
      if (ev.warm_error) problems.push("Claude 預熱失敗：" + ev.warm_error);
      warn(problems.join("。"));
      showContext(ev.context);
      $("dlTranscript").href = "/download/" + ev.transcript;
      $("dlQa").href = "/download/" + ev.qa;
      break;
    }
    case "context": showContext(ev); setStatus("參考資料已更新", listening ? "on" : ""); break;
    case "status": if (listening) setStatus("聆聽中", "on"); break;
    case "error": warn(ev.text); break;
    case "thinking": thinking = ev.on; paintStatus(); break;
    case "interim": { const el = $("interim-" + ev.speaker); if (el) el.textContent = ev.speaker + "：" + ev.text; break; }
    case "final": addLine(ev); break;
    case "answer_start": answers.set(ev.id, {id: ev.id, raw: "", started: performance.now()}); break;
    case "answer_delta": { const a = answers.get(ev.id); a.raw += ev.text; update(a); break; }
    case "answer_done": finish(ev); break;
    case "answer_error": { const a = answers.get(ev.id); a.raw = "【注意】" + ev.text; a.done = true; update(a, true); break; }
  }
}

function showContext(c) {
  const parts = [`${c.papers} 篇論文`, `${c.notes} 份筆記`, `${c.images} 張圖`];
  if (c.slides) parts.push("投影片頁碼");
  $("ctxInfo").textContent = "參考資料：" + parts.join("、");
  $("ctxInfo").title = c.folder;
}

function addLine(ev) {
  const m = ev.line.match(/^\[(\d+:\d+)\] ([^：]+)：([\s\S]*)$/);
  const [time, who, text] = m ? m.slice(1) : ["", ev.speaker, ev.line];
  const box = $("lines");
  const atBottom = box.parentElement.scrollHeight - box.parentElement.scrollTop - box.parentElement.clientHeight < 40;
  const p = document.createElement("p");
  p.className = "line" + (ev.speaker === "聽眾" ? "" : " me");
  const tag = document.createElement("span");
  tag.className = "who";
  tag.textContent = `${who}　${time}`;
  p.append(tag, document.createTextNode(text));
  box.append(p);
  const interim = $("interim-" + ev.speaker);  // typed questions have no interim line
  if (interim) interim.textContent = "";
  $("dlTranscript").hidden = false;
  if (atBottom) p.scrollIntoView({block: "end"});
}

// 【標籤】 sections of a reply; the model's text is only ever inserted with textContent.
function sections(raw) {
  const out = {};
  for (const part of raw.trim().split(/(?=【[^】]{1,4}】)/)) {
    const m = part.match(/^【([^】]{1,4})】([\s\S]*)$/);
    if (m) out[m[1]] = m[2].trim();
  }
  return out;
}

// SKIP and WAIT never surface. An answer surfaces as soon as it starts streaming, takes the stage
// unless the speaker pinned an older one, and an （更新） reply strikes out the entry it replaces.
function update(a, force = false) {
  if (!a.visible && (force || a.raw.trimStart().startsWith("【"))) {
    a.visible = true;
    a.replaces = latestId;
    latestId = a.id;
    a.li = document.createElement("li");
    const btn = document.createElement("button");
    btn.append(document.createElement("time"), document.createElement("span"));
    btn.firstChild.textContent = new Date().toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
    btn.onclick = () => show(a, a.id !== latestId);
    a.li.append(btn);
    $("history").prepend(a.li);
    if (pinned) $("jumpLatest").hidden = false; else show(a);
  }
  if (!a.visible) return;
  const sec = sections(a.raw);
  if (a.replaces && !a.updateChecked && sec["問題"] !== undefined && a.raw.length > 12) {
    a.updateChecked = true;
    if (sec["問題"].startsWith("（更新）")) answers.get(a.replaces)?.li?.classList.add("replaced");
  }
  a.li.querySelector("span").textContent = (sec["問題"] || "回答").replace(/^（更新）/, "更新：");
  if (shownId === a.id) paint(a);
}

function show(a, pin = false) {
  shownId = a.id;
  pinned = pin;
  document.querySelectorAll(".history li").forEach(li => li.classList.toggle("shown", li === a.li));
  $("jumpLatest").hidden = !pin || a.id === latestId;
  paint(a);
}

function paint(a) {
  const sec = sections(a.raw);
  const cue = $("cue");
  cue.classList.remove("empty");
  cue.classList.toggle("streaming", !a.done);
  cue.querySelector(".cue-question").textContent = (sec["問題"] || "").replace(/^（更新）/, "更新：");
  cue.querySelector(".cue-speech").textContent = sec["口語稿"] ?? "";
  const meta = cue.querySelector(".cue-meta");
  meta.textContent = "";
  const add = (tag, cls, text) => {
    if (!text) return;
    const el = document.createElement(tag);
    el.className = cls;
    el.textContent = text;
    meta.append(el);
  };
  add("p", "slide", sec["翻到"] && "翻到 " + sec["翻到"]);
  add("p", "basis", sec["依據"]);
  add("p", "caution", sec["注意"]);
  if (a.done && a.citations?.length) {
    const box = document.createElement("details");
    box.className = "quotes";
    const sum = document.createElement("summary");
    sum.textContent = `原文引句（${a.citations.length}）`;
    box.append(sum);
    for (const c of a.citations) {
      const q = document.createElement("p");
      q.textContent = `第 ${c.page} 頁：${c.text}`;
      box.append(q);
    }
    meta.append(box);
  }
  if (a.done && a.seconds) add("p", "timing", `${a.seconds} 秒產生`);
}

function finish(ev) {
  const a = answers.get(ev.id);
  if (ev.status === "skip" || ev.status === "wait") { answers.delete(ev.id); return; }
  if (ev.status === "refused") a.raw = "【注意】模型拒答這一題，請用自己的判斷回答。";
  a.done = true;
  a.citations = ev.citations;
  a.seconds = ((performance.now() - a.started) / 1000).toFixed(1);
  update(a, true);
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

async function startListening() {
  let tab;
  try {
    tab = await navigator.mediaDevices.getDisplayMedia({
      video: {frameRate: 1},  // only the audio is used; keep the video stream cheap
      audio: {echoCancellation: false, noiseSuppression: false, autoGainControl: false},
      selfBrowserSurface: "exclude",
    });
  } catch (e) { warn("沒有選到 Meet 分頁，再按一次「開始聆聽」。（" + e.message + "）"); return; }
  if (!tab.getAudioTracks().length) {
    tab.getTracks().forEach(t => t.stop());
    warn("收不到聲音：再按一次「開始聆聽」，選 Meet 的分頁，並打開「同時分享分頁音訊」。");
    return;
  }
  warn("");
  await capture(tab, TAB, "levelTab");
  if ($("withMic").checked) {
    try {
      const mic = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true}});
      await capture(mic, MIC, "levelMic");
    } catch (e) { warn("麥克風無法使用，只收 Meet 分頁。（" + e.message + "）"); }
  }
  listening = true;
  $("btnListen").textContent = "停止聆聽";
  $("btnListen").classList.add("live");
  setStatus("聆聽中", "on");
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
  listening = false;
  $("btnListen").textContent = "開始聆聽";
  $("btnListen").classList.remove("live");
  setStatus("已停止聆聽");
}

$("btnListen").onclick = () => (listening ? stop() : startListening());
$("jumpLatest").onclick = () => show(answers.get(latestId));
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
