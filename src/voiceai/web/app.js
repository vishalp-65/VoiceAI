// Browser client for the voice booking agent.
//
// Uses the Pipecat JS client over the SmallWebRTC transport to (a) stream mic
// audio to the FastAPI server and play the agent's reply, and (b) receive RTVI
// events that drive the live transcript, the mic meter, and the latency HUD.
//
// Latency shown = time from "user stopped speaking" to "bot started speaking",
// i.e. the perceived silence-to-first-audio gap, measured client-side.

import { PipecatClient } from "https://esm.sh/@pipecat-ai/client-js";
import { SmallWebRTCTransport } from "https://esm.sh/@pipecat-ai/small-webrtc-transport";

// --- DOM ------------------------------------------------------------------
const els = {
  status: document.getElementById("status"),
  transcript: document.getElementById("transcript"),
  jumpBtn: document.getElementById("jumpBtn"),
  callBtn: document.getElementById("callBtn"),
  callLabel: document.querySelector("#callBtn .label"),
  micMeter: document.getElementById("micMeter"),
  micLabel: document.getElementById("micLabel"),
  botAudio: document.getElementById("botAudio"),
  lat: document.getElementById("lat"),
  latAvg: document.getElementById("latAvg"),
  latBest: document.getElementById("latBest"),
  latN: document.getElementById("latN"),
  callState: document.getElementById("callState"),
  spark: document.getElementById("spark"),
};

// --- State ----------------------------------------------------------------
let client = null;
let connected = false;
let userStoppedAt = null;     // performance.now() when the caller stopped talking
let awaitingReply = false;    // true between user-stop and the next bot-start
let userBubble = null;        // live (interim) user transcript bubble (its text node)
let botBubble = null;         // live bot transcript bubble (its text node)
let thinkingEl = null;        // "Robin is thinking…" placeholder row
const latencies = [];

// --- Smart auto-scroll -----------------------------------------------------
// Follow the latest message only while the user is parked at the bottom. If they
// scroll up to read history we leave them there and surface a "jump" button.
// Scroll writes are batched into one rAF tick so a burst of streamed tokens
// can't thrash layout.
let pinned = true;
let scrollQueued = false;

function nearBottom(threshold = 90) {
  const el = els.transcript;
  return el.scrollHeight - el.scrollTop - el.clientHeight < threshold;
}
function followBottom(force = false) {
  if (!force && !pinned) return;
  if (force) pinned = true;
  if (scrollQueued) return;
  scrollQueued = true;
  requestAnimationFrame(() => {
    scrollQueued = false;
    els.transcript.scrollTop = els.transcript.scrollHeight;
  });
}
els.transcript.addEventListener("scroll", () => {
  pinned = nearBottom();
  els.jumpBtn.classList.toggle("show", !pinned);
}, { passive: true });
els.jumpBtn.addEventListener("click", () => {
  followBottom(true);
  els.jumpBtn.classList.remove("show");
});

// --- Transcript helpers ---------------------------------------------------
function clearEmpty() {
  const empty = els.transcript.querySelector(".empty");
  if (empty) empty.remove();
}
function resetTranscript() {
  els.transcript.innerHTML =
    '<div class="empty"><span class="ring" aria-hidden="true">' +
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/></svg>' +
    '</span><div>Press <b>Start call</b> and allow your microphone to begin talking with Robin.</div></div>';
  userBubble = botBubble = thinkingEl = null;
  pinned = true;
  els.jumpBtn.classList.remove("show");
}

function nowLabel() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// Build one chat row (avatar + bubble) and return its inner text node so callers
// can stream text into it cheaply via .textContent.
function makeBubble(kind, who) {
  clearEmpty();
  const row = document.createElement("div");
  row.className = `row ${kind}`;

  const avatar = document.createElement("div");
  avatar.className = "avatar";
  avatar.textContent = who[0];
  avatar.setAttribute("aria-hidden", "true");

  const col = document.createElement("div");
  col.className = "bubble-col";
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.innerHTML = `<span>${who}</span><span>${nowLabel()}</span>`;
  const bubble = document.createElement("div");
  bubble.className = `bubble ${kind}`;
  col.append(meta, bubble);

  row.append(avatar, col);
  els.transcript.appendChild(row);
  followBottom();
  return bubble;
}

function onUserText(text, isFinal) {
  hideThinking();
  if (!userBubble) {
    userBubble = makeBubble("user", "You");
    userBubble.classList.add("interim");
  }
  userBubble.textContent = text;
  followBottom();
  if (isFinal) {
    userBubble.classList.remove("interim");
    userBubble = null;
    // Caller finished a turn — the reply is next, so show the thinking dots.
    showThinking();
  }
}
function onBotText(chunk) {
  hideThinking();
  if (!botBubble) botBubble = makeBubble("bot", "Robin");
  botBubble.textContent += chunk;
  followBottom();
}

// --- "Robin is thinking…" indicator ---------------------------------------
function showThinking() {
  if (thinkingEl || botBubble) return;
  clearEmpty();
  thinkingEl = document.createElement("div");
  thinkingEl.className = "row bot thinking";
  thinkingEl.setAttribute("aria-label", "Robin is thinking");
  thinkingEl.innerHTML =
    '<div class="avatar" aria-hidden="true">R</div>' +
    '<div class="bubble-col"><div class="bubble"><span></span><span></span><span></span></div></div>';
  els.transcript.appendChild(thinkingEl);
  followBottom();
}
function hideThinking() {
  if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; }
}

// --- Latency HUD + sparkline ----------------------------------------------
function latClass(ms) {
  return ms < 1000 ? "latency-good" : ms < 1600 ? "latency-mid" : "latency-bad";
}
function recordLatency(ms) {
  ms = Math.round(ms);
  latencies.push(ms);
  const avg = Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length);
  const best = Math.min(...latencies);
  els.lat.textContent = ms;
  els.lat.className = `big ${latClass(ms)}`;
  els.latAvg.textContent = `${avg} ms`;
  els.latBest.textContent = `${best} ms`;
  els.latN.textContent = latencies.length;
  drawSpark();
}

// Lightweight bar sparkline of the most recent turns, colour-coded by threshold.
function drawSpark() {
  const c = els.spark;
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth, h = c.clientHeight;
  if (!w || !h) return;
  if (c.width !== w * dpr || c.height !== h * dpr) {
    c.width = w * dpr; c.height = h * dpr;
  }
  const ctx = c.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const data = latencies.slice(-32);
  if (!data.length) return;
  const max = Math.max(1600, ...data);
  const gap = 3;
  const bw = Math.max(2, (w - gap * (data.length - 1)) / data.length);
  const colors = { "latency-good": "#3fb950", "latency-mid": "#d29922", "latency-bad": "#f85149" };
  data.forEach((v, i) => {
    const bh = Math.max(2, (v / max) * (h - 2));
    const x = i * (bw + gap);
    ctx.fillStyle = colors[latClass(v)];
    ctx.globalAlpha = 0.35 + 0.65 * ((i + 1) / data.length); // fade older bars
    const r = Math.min(2, bw / 2);
    const y = h - bh;
    ctx.beginPath();
    ctx.roundRect(x, y, bw, bh, r);
    ctx.fill();
  });
  ctx.globalAlpha = 1;
}
window.addEventListener("resize", drawSpark, { passive: true });

// --- Status ---------------------------------------------------------------
function setStatus(label, kind) {
  els.status.textContent = label;
  els.status.className = `pill ${kind || ""}`.trim();
  els.callState.textContent = label.toLowerCase();
}
function setCallBtn({ label, mode, busy }) {
  if (label) els.callLabel.textContent = label;
  els.callBtn.classList.toggle("end", mode === "end");
  els.callBtn.disabled = !!busy;
}

// --- Mic meter (Web Audio) -------------------------------------------------
// Drives the footer equalizer from the caller's actual mic level. Falls back
// silently if the AudioContext or local track is unavailable.
let audioCtx = null, analyser = null, meterRAF = 0, meterData = null;
const METER_BARS = 9;

function buildMeterBars() {
  if (els.micMeter.childElementCount) return;
  for (let i = 0; i < METER_BARS; i++) {
    const b = document.createElement("span");
    b.className = "bar";
    els.micMeter.appendChild(b);
  }
}
function startMicMeter(track) {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx || !track) return;
    buildMeterBars();
    audioCtx = new Ctx();
    const src = audioCtx.createMediaStreamSource(new MediaStream([track]));
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 64;
    analyser.smoothingTimeConstant = 0.78;
    src.connect(analyser);
    meterData = new Uint8Array(analyser.frequencyBinCount);
    document.body.classList.add("mic-on");
    els.micLabel.textContent = "microphone live";
    const bars = els.micMeter.children;
    const loop = () => {
      analyser.getByteFrequencyData(meterData);
      const step = Math.floor(meterData.length / METER_BARS) || 1;
      for (let i = 0; i < METER_BARS; i++) {
        const v = meterData[i * step] / 255;            // 0..1
        const h = Math.max(0.18, Math.min(1, v * 1.6)); // floor so idle bars stay visible
        bars[i].style.setProperty("--h", h.toFixed(2));
      }
      meterRAF = requestAnimationFrame(loop);
    };
    loop();
  } catch (err) {
    console.debug("[mic meter] unavailable", err);
  }
}
function stopMicMeter() {
  cancelAnimationFrame(meterRAF);
  meterRAF = 0;
  document.body.classList.remove("mic-on");
  els.micLabel.textContent = "microphone idle";
  for (const bar of els.micMeter.children) bar.style.setProperty("--h", "0.2");
  if (audioCtx) { try { audioCtx.close(); } catch (_) { /* ignore */ } audioCtx = null; }
  analyser = null; meterData = null;
}

// --- Connect / disconnect -------------------------------------------------
async function startCall() {
  setCallBtn({ busy: true });
  els.callLabel.textContent = "Connecting…";
  setStatus("Connecting", "connecting");
  resetTranscript();

  client = new PipecatClient({
    transport: new SmallWebRTCTransport(),
    enableMic: true,
    enableCam: false,
    callbacks: {
      onConnected: () => { connected = true; },
      onBotReady: () => {
        setStatus("Live", "live");
        setCallBtn({ label: "End call", mode: "end", busy: false });
      },
      onDisconnected: () => endCall(),
      onTransportStateChanged: (s) => console.debug("[transport]", s),

      // Audio: bot track -> <audio>; local mic track -> level meter.
      onTrackStarted: (track, participant) => {
        if (track.kind !== "audio") return;
        if (participant && participant.local) startMicMeter(track);
        else els.botAudio.srcObject = new MediaStream([track]);
      },

      // Transcript: user ASR (interim + final) and streamed bot LLM tokens.
      onUserTranscript: (data) => onUserText(data.text, !!data.final),
      onBotLlmText: (data) => onBotText(data.text),

      // Turn-taking / latency / speaking state
      onUserStoppedSpeaking: () => {
        userStoppedAt = performance.now();
        awaitingReply = true;
      },
      onBotStartedSpeaking: () => {
        document.body.classList.add("bot-speaking");
        if (awaitingReply && userStoppedAt != null) {
          recordLatency(performance.now() - userStoppedAt);
          awaitingReply = false;
          userStoppedAt = null;
        }
      },
      onBotStoppedSpeaking: () => {
        document.body.classList.remove("bot-speaking");
        botBubble = null; // finalize this bot turn's bubble
      },

      onError: (e) => console.error("[pipecat error]", e),
    },
  });

  try {
    await client.connect({ connection_url: "/api/offer" });
  } catch (err) {
    console.error(err);
    setStatus("Connection failed", "error");
    setCallBtn({ label: "Start call", mode: "start", busy: false });
    client = null;
  }
}

async function endCall() {
  if (!connected && !client) return;
  stopMicMeter();
  document.body.classList.remove("bot-speaking");
  hideThinking();
  setCallBtn({ label: "Start call", mode: "start", busy: false });
  setStatus("Idle", "");
  connected = false;
  userBubble = botBubble = null;
  awaitingReply = false;
  userStoppedAt = null;
  if (client) {
    try { await client.disconnect(); } catch (_) { /* ignore */ }
    client = null;
  }
}

// --- Wiring ---------------------------------------------------------------
els.callBtn.addEventListener("click", () => (connected ? endCall() : startCall()));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && connected) endCall();
});
window.addEventListener("beforeunload", () => { if (client) client.disconnect(); });

// Initialize idle meter bars so the footer never looks empty.
buildMeterBars();
for (const bar of els.micMeter.children) bar.style.setProperty("--h", "0.2");
