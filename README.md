# Riverside Voice Scheduling Agent

A live, **voice-to-voice** AI agent that books, reschedules, and cancels a medical
**consultation** over a browser-mic call. Speak to it, hear it reply, and watch it
call tools mid-conversation to actually change the calendar.

Built with **FastAPI + [Pipecat](https://github.com/pipecat-ai/pipecat)** and a
low-latency cascade: **Deepgram** (STT) → **Claude** (reasoning + tool calls) →
**Cartesia Sonic** (TTS), over **WebRTC** (no phone number needed). Bookings live
in a local **SQLite** store.

> Built for the Alris technical test. See [`docs/WRITEUP.md`](docs/WRITEUP.md) for the
> latency breakdown, turn-taking approach, and the biggest production reliability obstacle.

---

## What it does

- **Real two-way voice** in the browser — barge-in capable, sentence-streamed replies.
- **Agentic tool calls** fire live: `check_availability`, `book_appointment`,
  `reschedule_appointment`, `cancel_appointment`.
- **Four voice failure modes** handled and demonstrable:
  | Mode | Behaviour |
  |---|---|
  | **Barge-in** | Interrupt the agent mid-sentence; it stops talking and listens. |
  | **Transcription ambiguity** | Confirms ambiguous times ("10:00 or 10:30?"), spelled names, and reads phone numbers back before booking. |
  | **Caller goes silent** | Nudges twice, then ends the call politely. |
  | **Change of mind** | Abandons a half-finished booking to follow a new intent. |
- **Latency HUD**: the browser shows silence-to-first-audio per turn; the server logs
  per-stage TTFB.

---

## Architecture

```
Browser (mic + speaker)
   │  WebRTC (Opus audio + RTVI data channel)
   ▼
FastAPI  ── POST /api/offer ──►  SmallWebRTCConnection ──► run_bot()
                                                            │
  Pipecat pipeline (allow_interruptions=True, enable_metrics=True):
    transport.input()  → Silero VAD (turn-taking + barge-in)
      → RTVI            → transcript/latency events to the browser
      → UserIdle        → silence escalation
      → Deepgram STT    → streaming, smart_format + numerals
      → context.user()
      → Claude LLM      → 4 booking tools
      → Cartesia TTS    → streaming Sonic
      → transport.output()
      → context.assistant()
                                                            │
   tool handlers ──► booking.repository ──► SQLite (seeded consultation slots)
```

Project layout:

```
src/voiceai/
  main.py            FastAPI app: UI, /health, /api/offer signaling
  bot.py             Builds & runs the Pipecat pipeline per call
  settings.py        Typed config from .env
  pipeline/          prompts · service factories · idle handling · observers
  tools/             FunctionSchemas + async handlers
  booking/           models · sqlite db/seed · pure repository logic
  web/               index.html + app.js (custom mic UI)
scripts/seed_db.py   reset & seed availability
tests/               repository + tool-handler unit tests
docs/WRITEUP.md      the half-page write-up
```

---

## Setup & run

**Prerequisites:** Python 3.10–3.12 and API keys for Deepgram, Anthropic, and Cartesia.

```bash
# 1. Install (creates an editable install of the `voiceai` package)
python -m pip install -e ".[dev]"          # or: make install

# 2. Configure keys
cp .env.example .env                        # then fill in the three API keys

# 3. Seed the local availability store
python scripts/seed_db.py                   # or: make seed

# 4. Run
uvicorn voiceai.main:app --host 0.0.0.0 --port 7860   # or: make run
```

Open **http://localhost:7860**, click **Start call**, allow the microphone, and say
*"I'd like to book a consultation."*

> **Windows note:** the `make` targets assume Git Bash; otherwise run the raw commands
> above. The `webrtc` extra builds on `aiortc`/`av`, which ship prebuilt wheels for
> Windows/macOS/Linux on Python 3.10–3.12.

If the CDN-loaded browser client ever fails to initialise, a zero-config fallback UI is
served at **http://localhost:7860/client** (when `pipecat-ai-small-webrtc-prebuilt` is
installed). `GET /health` reports which keys are configured and active call count.

---

## Try the edge cases

1. **Barge-in** — while Robin is talking, start speaking; it stops and listens.
2. **Ambiguity** — *"Book me for ten."* → it asks "10:00 or 10:30?". Spell your last
   name and read a phone number aloud; it reads them back to confirm.
3. **Silence** — stop responding; it nudges, then ends the call gracefully.
4. **Change of mind** — start a booking, then say *"actually, cancel my appointment
   instead."*

---

## Testing

```bash
pytest -q          # or: make test
```

`tests/test_repository.py` covers booking/reschedule/cancel logic and date/time
resolution against an in-memory DB (no keys, no network). `tests/test_tools.py` drives
the async tool handlers end-to-end over a temp SQLite file.

---

## Configuration knobs (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `LLM_MODEL` | `claude-haiku-4-5-20251001` | Swap to `claude-sonnet-4-6` for stronger tool reasoning. |
| `STT_MODEL` | `nova-3` | Deepgram model. |
| `TTS_MODEL` / `TTS_VOICE_ID` | `sonic-2` / stock voice | Cartesia Sonic. |
| `IDLE_TIMEOUT_SECONDS` | `6.0` | Silence before the first "still there?" nudge. |
| `ALLOW_INTERRUPTIONS` | `true` | Barge-in. |
| `CLINIC_TIMEZONE` | `America/New_York` | Used to resolve "tomorrow"/slot times. |
