const TARGET_RATE = 16000;
const $ = id => document.getElementById(id);
const cards = new Map();
let ws, ctx, stream, source, processor, sink;

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
function toPCM16(arr) {
  const view = new DataView(new ArrayBuffer(arr.length * 2));
  for (let i = 0; i < arr.length; i++) {
    const s = Math.max(-1, Math.min(1, arr[i]));
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
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
      const problems = [];
      if (!ev.anthropic_key) problems.push("沒有 Claude API key（填 tools/qa_assistant/.env 後重開）");
      if (!ev.gemini_key) problems.push("沒有 Gemini API key");
      if (ev.warm_error) problems.push("Claude 預熱失敗：" + ev.warm_error);
      warn(problems.join("；"));
      break;
    }
    case "status": setStatus(ev.text, "on"); break;
    case "error": setStatus("有錯誤", "err"); warn(ev.text); break;
    case "interim": $("interim").textContent = ev.text; break;
    case "final":
      $("pending").textContent = ($("pending").textContent + " " + ev.text).trim();
      $("interim").textContent = "";
      break;
    case "segment": addSegment(ev); break;
    case "answer_start": startCard(ev); break;
    case "answer_delta": { const c = cards.get(ev.id); c.raw += ev.text; render(c); break; }
    case "answer_done": finishCard(ev); break;
    case "answer_error": { const c = cards.get(ev.id); c.raw = "⚠️ " + ev.text; render(c); break; }
  }
}

function addSegment(ev) {
  $("pending").textContent = "";
  const div = document.createElement("div");
  div.className = "seg";
  div.textContent = ev.text;
  const btn = document.createElement("button");
  btn.textContent = "回答這段";
  btn.onclick = () => ws.send(JSON.stringify({type: "answer_segment", id: ev.id}));
  div.append(document.createElement("br"), btn);
  $("segments").append(div);
  div.scrollIntoView({block: "end"});
}

function startCard(ev) {
  document.querySelectorAll(".card.newest").forEach(el => el.classList.remove("newest"));
  const el = document.createElement("div");
  el.className = "card newest";
  el.innerHTML = '<span class="meta"></span><div class="q"></div><div class="body">思考中…</div><div class="cites"></div>';
  el.querySelector(".q").textContent = "逐字稿：" + ev.question;
  el.querySelector(".meta").textContent = ({auto: "自動", manual: "手動", segment: "指定"})[ev.source] || "";
  $("answers").prepend(el);
  cards.set(ev.id, {el, raw: "", started: performance.now()});
}

// Render 【標籤】 sections; the model's text is inserted with textContent only.
function render(card) {
  const body = card.el.querySelector(".body");
  body.textContent = "";
  const parts = card.raw.split(/(?=【[^】]{1,4}】)/);
  for (const part of parts) {
    const m = part.match(/^【([^】]{1,4})】([\s\S]*)$/);
    const span = document.createElement("span");
    span.className = "sec" + (m && m[1] === "回答" ? " answer" : "");
    if (m) {
      const label = document.createElement("span");
      label.className = "label";
      label.textContent = m[1];
      span.append(label, document.createTextNode(m[2].trim()));
    } else {
      span.textContent = part;
    }
    body.append(span);
  }
}

function finishCard(ev) {
  const c = cards.get(ev.id);
  const secs = ((performance.now() - c.started) / 1000).toFixed(1);
  c.el.querySelector(".meta").textContent += ` · ${secs}s`;
  if (ev.skipped) {
    c.el.classList.add("skipped");
    c.el.classList.remove("newest");
    c.el.querySelector(".q").textContent += "（判斷不是問題，略過）";
    return;
  }
  if (ev.refused) { c.raw += "\n⚠️ 模型拒答，請改用自己的判斷。"; }
  c.raw = ev.text || c.raw;
  render(c);
  const cites = c.el.querySelector(".cites");
  for (const cite of ev.citations) {
    const d = document.createElement("div");
    d.textContent = `原文 p.${cite.page}：「${cite.text}」`;
    cites.append(d);
  }
}

async function startAudio(mediaStream, label) {
  stream = mediaStream;
  ctx = new AudioContext();
  await ctx.resume();
  source = ctx.createMediaStreamSource(new MediaStream(stream.getAudioTracks()));
  processor = ctx.createScriptProcessor(4096, 1, 1);
  // Route through a muted gain node: the processor must reach the destination to run,
  // but the captured Meet audio must not play a second time.
  sink = ctx.createGain();
  sink.gain.value = 0;
  processor.onaudioprocess = e => {
    const input = e.inputBuffer.getChannelData(0);
    let energy = 0;
    for (let i = 0; i < input.length; i++) energy += input[i] * input[i];
    $("level").value = Math.min(100, Math.sqrt(energy / input.length) * 400);
    if (ws.readyState === WebSocket.OPEN) ws.send(toPCM16(downsample(input, ctx.sampleRate, TARGET_RATE)));
  };
  source.connect(processor);
  processor.connect(sink);
  sink.connect(ctx.destination);
  stream.getAudioTracks()[0].onended = stopAudio;
  ws.send(JSON.stringify({type: "start"}));
  setStatus(label, "on");
  $("btnStop").disabled = false;
  $("btnTab").disabled = $("btnMic").disabled = true;
}

async function stopAudio() {
  if (!stream) return;
  ws.send(JSON.stringify({type: "stop"}));
  stream.getTracks().forEach(t => t.stop());
  processor && processor.disconnect();
  ctx && await ctx.close();
  stream = ctx = processor = null;
  $("level").value = 0;
  $("btnStop").disabled = true;
  $("btnTab").disabled = $("btnMic").disabled = false;
  setStatus("已停止聆聽");
}

$("btnTab").onclick = async () => {
  try {
    const s = await navigator.mediaDevices.getDisplayMedia({
      video: {frameRate: 1},  // only the audio is used; keep the video stream cheap
      audio: {echoCancellation: false, noiseSuppression: false, autoGainControl: false},
      selfBrowserSurface: "exclude",
    });
    if (!s.getAudioTracks().length) {
      s.getTracks().forEach(t => t.stop());
      warn("沒有收到聲音：分享時請選 Meet 的分頁，並勾選「同時分享分頁音訊」。");
      return;
    }
    warn("");
    await startAudio(s, "聆聽 Meet 分頁中");
  } catch (e) { warn("無法擷取分頁：" + e.message); }
};
$("btnMic").onclick = async () => {
  try {
    const s = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true}});
    await startAudio(s, "麥克風收音中（你自己的聲音也會被聽到）");
  } catch (e) { warn("無法使用麥克風：" + e.message); }
};
$("btnStop").onclick = stopAudio;
$("auto").onchange = e => ws.send(JSON.stringify({type: "auto", on: e.target.checked}));
$("askForm").onsubmit = e => {
  e.preventDefault();
  const text = $("askInput").value.trim();
  if (!text) return;
  ws.send(JSON.stringify({type: "ask", text}));
  $("askInput").value = "";
};
connect();
