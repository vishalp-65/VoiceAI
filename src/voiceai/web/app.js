// Browser client for the voice booking agent.
//
// Uses the Pipecat JS client over the SmallWebRTC transport to (a) stream mic
// audio to the FastAPI server and play the agent's reply, and (b) receive RTVI
// events that drive the live transcript and the latency HUD.
//
// Latency shown = time from "user stopped speaking" to "bot started speaking",
// i.e. the perceived silence-to-first-audio gap, measured client-side.

import { PipecatClient } from "https://esm.sh/@pipecat-ai/client-js";
import { SmallWebRTCTransport } from "https://esm.sh/@pipecat-ai/small-webrtc-transport";

// --- DOM ------------------------------------------------------------------
const els = {
  status: document.getElementById("status"),
  transcript: document.getElementById("transcript"),
  callBtn: document.getElementById("callBtn"),
  micDot: document.getElementById("micDot"),
  botAudio: document.getElementById("botAudio"),
  lat: document.getElementById("lat"),
  latAvg: document.getElementById("latAvg"),
  latN: document.getElementById("latN"),
  callState: document.getElementById("callState"),
};

// --- State ----------------------------------------------------------------
let client = null;
let connected = false;
let userStoppedAt = null;     // performance.now() when the caller stopped talking
let awaitingReply = false;    // true between user-stop and the next bot-start
let userBubble = null;        // live (interim) user transcript bubble
let botBubble = null;         // live bot transcript bubble for the current turn
const latencies = [];

// --- Transcript helpers ---------------------------------------------------
function clearEmpty() {
  const empty = els.transcript.querySelector(".empty");
  if (empty) empty.remove();
}
function makeBubble(kind, who) {
  clearEmpty();
  const b = document.createElement("div");
  b.className = `bubble ${kind}`;
  const w = document.createElement("div");
  w.className = "who";
  w.textContent = who;
  const t = document.createElement("div");
  t.className = "text";
  b.append(w, t);
  els.transcript.appendChild(b);
  els.transcript.scrollTop = els.transcript.scrollHeight;
  return b;
}
function setText(bubble, text) {
  bubble.querySelector(".text").textContent = text;
  els.transcript.scrollTop = els.transcript.scrollHeight;
}

function onUserText(text, isFinal) {
  if (!userBubble) userBubble = makeBubble("user interim", "You");
  setText(userBubble, text);
  if (isFinal) {
    userBubble.classList.remove("interim");
    userBubble = null;
  }
}
function onBotText(textChunk) {
  if (!botBubble) botBubble = makeBubble("bot", "Robin");
  setText(botBubble, botBubble.querySelector(".text").textContent + textChunk);
}

// --- Latency HUD ----------------------------------------------------------
function recordLatency(ms) {
  latencies.push(ms);
  const avg = Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length);
  els.lat.textContent = Math.round(ms);
  els.latAvg.textContent = `${avg} ms`;
  els.latN.textContent = latencies.length;
  const cls = ms < 1000 ? "latency-good" : ms < 1600 ? "latency-mid" : "latency-bad";
  els.lat.className = `big ${cls}`;
}

// --- Status ---------------------------------------------------------------
function setStatus(label, kind) {
  els.status.textContent = label;
  els.status.className = `pill ${kind || ""}`.trim();
  els.callState.textContent = label.toLowerCase();
}

// --- Connect / disconnect -------------------------------------------------
async function startCall() {
  els.callBtn.disabled = true;
  setStatus("Connecting", "connecting");

  client = new PipecatClient({
    transport: new SmallWebRTCTransport(),
    enableMic: true,
    enableCam: false,
    callbacks: {
      onConnected: () => { connected = true; },
      onBotReady: () => {
        setStatus("Live", "live");
        els.micDot.classList.add("on");
        els.callBtn.textContent = "End call";
        els.callBtn.classList.add("end");
        els.callBtn.disabled = false;
      },
      onDisconnected: () => endCall(),
      onTransportStateChanged: (s) => console.debug("[transport]", s),

      // Audio playback: attach the bot's audio track to the <audio> element.
      onTrackStarted: (track, participant) => {
        if (participant && participant.local) return;
        if (track.kind === "audio") {
          els.botAudio.srcObject = new MediaStream([track]);
        }
      },

      // Transcript: user ASR (interim + final) and streamed bot LLM tokens.
      onUserTranscript: (data) => onUserText(data.text, !!data.final),
      onBotLlmText: (data) => onBotText(data.text),

      // Turn-taking / latency
      onUserStartedSpeaking: () => { /* caller is talking (also drives barge-in) */ },
      onUserStoppedSpeaking: () => {
        userStoppedAt = performance.now();
        awaitingReply = true;
      },
      onBotStartedSpeaking: () => {
        // First audio of the reply — this is the latency we care about.
        if (awaitingReply && userStoppedAt != null) {
          recordLatency(performance.now() - userStoppedAt);
          awaitingReply = false;
          userStoppedAt = null;
        }
      },
      onBotStoppedSpeaking: () => { botBubble = null; }, // finalize this bot turn's bubble

      onError: (e) => console.error("[pipecat error]", e),
    },
  });

  try {
    await client.connect({ connection_url: "/api/offer" });
  } catch (err) {
    console.error(err);
    setStatus("Connection failed", "");
    els.callBtn.disabled = false;
    alert("Could not start the call. Check the server logs and your API keys.");
  }
}

async function endCall() {
  els.micDot.classList.remove("on");
  els.callBtn.textContent = "Start call";
  els.callBtn.classList.remove("end");
  els.callBtn.disabled = false;
  setStatus("Idle", "");
  connected = false;
  userBubble = botBubble = null;
  if (client) {
    try { await client.disconnect(); } catch (_) { /* ignore */ }
    client = null;
  }
}

els.callBtn.addEventListener("click", () => (connected ? endCall() : startCall()));
window.addEventListener("beforeunload", () => { if (client) client.disconnect(); });
