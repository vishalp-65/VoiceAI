# Riverside Voice Scheduling Agent

A voice AI agent that books, reschedules, and cancels medical consultations through a real-time conversation in the browser. You talk, it listens, replies back, and updates the calendar live — no forms, no buttons.

Built with **FastAPI + [Pipecat](https://github.com/pipecat-ai/pipecat)** using a streaming pipeline: **Deepgram** (STT) → **Claude** (reasoning + tools) → **Cartesia Sonic** (TTS), connected over **WebRTC**. Bookings stored in local **SQLite**.

---

## How it works

```
Your mic  -->  Deepgram STT  -->  Claude (brain)  -->  Cartesia TTS  -->  Your speaker
              (streaming)       (tool calls)          (streaming)
                                     |
                              SQLite (bookings)
```

Each stage streams — Deepgram transcribes while you talk, Claude starts thinking the moment transcript arrives, Cartesia speaks the first sentence while Claude is still writing the second. End-to-end silence-to-first-audio is **~0.7-1.1s** for normal turns.

**Silero VAD** handles turn-taking: it waits for 0.6s of silence to know you're done. Barge-in is on — interrupt the agent anytime and it stops talking immediately.

### Architecture

```
Browser (mic + speaker)
   |  WebRTC audio
   v
FastAPI  --POST /api/offer-->  WebRTC Connection  -->  Pipecat Pipeline:
                                                        |
  transport.input() → Silero VAD → Deepgram STT → Claude LLM (4 tools) → Cartesia TTS → transport.output()
                                                        |
                                                  SQLite (slots + appointments)
```

The agent has four tools: `check_availability`, `book_appointment`, `reschedule_appointment`, `cancel_appointment`. It says a filler phrase ("Let me check that...") before each tool call to mask the latency.

---

## Setup

**Need:** Python 3.10-3.12, API keys for [Deepgram](https://deepgram.com), [Anthropic](https://console.anthropic.com), [Cartesia](https://cartesia.ai).

```bash
# Install
python -m pip install -e ".[dev]"

# Configure keys
cp .env.example .env        # then fill in the three API keys

# Seed the database with appointment slots
python scripts/seed_db.py

# Run
uvicorn voiceai.main:app --host 0.0.0.0 --port 7860
```

Open **http://localhost:7860**, click **Start call**, allow mic, and say *"I'd like to book a consultation."*

`GET /health` shows configured keys and active call count.

---

## Voice failure modes handled

| Scenario | What happens |
|---|---|
| **Barge-in** | Interrupt the agent mid-sentence — it stops and listens |
| **Ambiguous input** | Confirms times ("10 AM or 10:30?"), reads back phone numbers, asks to spell names |
| **Caller goes silent** | Nudges after 6s, nudges again, then politely ends the call |
| **Change of mind** | Drop a half-finished booking and follow the new intent |

---

## Latency breakdown

| Stage | Time | Notes |
|---|---|---|
| VAD endpointing | ~250-500ms | 0.6s silence threshold — the most tunable knob |
| Deepgram transcript | ~tens of ms | Was already streaming while you spoke |
| Claude TTFT | ~300-600ms | Haiku chosen for speed; prompt caching cuts repeat cost |
| Cartesia first audio | ~40-90ms | One of the fastest production TTS engines |
| **Total (no tool)** | **~0.7-1.1s** | |
| **With tool call** | **~1.3-1.8s** | Filler phrase covers the extra round-trip |

What keeps it fast: everything streams end-to-end, TTS starts on the first sentence, prompt caching avoids re-encoding ~2K static tokens each turn, and the agent says a short filler before tool calls to hide latency.

---

## Turn-taking and interruptions

Turn-taking is driven by **Silero VAD**. `stop_secs=0.6` is the silence threshold — too short cuts people off (especially during phone numbers), too long feels laggy. 0.6s is a middle ground.

**Barge-in** is always on. When you speak over the agent, Pipecat stops TTS output and routes your audio to STT immediately.

**Silence escalation** is a custom `SilenceMonitor`: nudge → nudge → polite goodbye. Nudges are fixed phrases (no LLM call), so they're instant. Timer resets the moment you speak.

**Ambiguity** is prompt-driven: the system prompt forces explicit confirmation of times, spelled names, and phone numbers before booking. Deepgram's `smart_format` + `numerals` convert spoken numbers to digits.

---

## Biggest production reliability obstacle

**Endpointing / turn detection** — knowing when the caller is actually done vs. just pausing mid-thought. A static 0.6s silence threshold can't tell the difference between "I'm finished" and "I'm pausing to remember my phone number."

It's the make-or-break of perceived quality. It interacts with barge-in, latency, and ASR accuracy.

**How I'd attack it:**
- **Semantic turn detection** (like Pipecat's Smart Turn) instead of pure silence — so "my number is 555... 1234" isn't split into two turns
- **State-aware endpointing** — widen the silence window when expecting phone numbers, tighten it for yes/no answers
- **Confidence gating** — if the transcript looks mid-thought (trailing conjunction, partial number), wait longer before committing the turn
- **Barge-in filtering** — check if the interruption is a real new turn vs. a cough or "uh-huh" before killing the agent's response

Runner-up risks: ASR errors on names/numbers (mitigated with read-back confirmation) and tool-call tail latency (mitigated with filler phrases, extensible with async "I'll text you" patterns).

---

## Project structure

```
src/voiceai/
  main.py             FastAPI app, WebRTC signaling, UI serving
  bot.py              Builds the Pipecat pipeline per caller
  settings.py         Config from .env
  pipeline/           prompts, service factories, silence handler, observers
  tools/              Tool schemas + async handlers for the 4 booking operations
  booking/            models, SQLite DB, repository logic
  web/                Browser UI (index.html + app.js with transcript, mic meter, latency HUD)
scripts/seed_db.py    Reset and seed availability
tests/                Repository + tool handler unit tests (no keys needed)
docs/WRITEUP.md       Detailed write-up
```

---

## Testing

```bash
pytest -q
```

Covers booking/reschedule/cancel logic against in-memory SQLite. No API keys, no network.

---

## Config (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `LLM_MODEL` | `claude-haiku-4-5-20251001` | Swap to `claude-sonnet-4-6` for stronger reasoning |
| `STT_MODEL` | `nova-3` | Deepgram model |
| `TTS_MODEL` / `TTS_VOICE_ID` | `sonic-2` / stock voice | Cartesia Sonic |
| `IDLE_TIMEOUT_SECONDS` | `6.0` | Silence before first nudge |
| `ALLOW_INTERRUPTIONS` | `true` | Barge-in toggle |
| `CLINIC_TIMEZONE` | `America/New_York` | For resolving "tomorrow"/slot times |
