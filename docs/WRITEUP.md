# Write-up — Voice Appointment-Booking Agent

**Stack:** FastAPI + Pipecat, browser-mic over WebRTC. Cascade: Deepgram STT →
Claude Haiku 4.5 (tools) → Cartesia Sonic TTS. Bookings in local SQLite.

## 1. Latency: where the time goes

I treat **silence-to-first-audio** (caller stops talking → first word of the reply) as
the number that matters, and measure it two ways: the browser HUD times it per turn from
RTVI speaking events, and the server logs per-service TTFB via Pipecat metrics
(`enable_metrics=True`).

Approximate budget for a normal (no-tool) turn:

| Stage | Typical | Notes |
|---|---|---|
| Endpointing (Silero VAD `stop_secs=0.6`) | ~250–500 ms | The biggest, most tunable chunk. |
| Deepgram final transcript | ~tens of ms after endpoint | Streaming with interim results. |
| Claude Haiku 4.5 TTFT | ~300–600 ms | Haiku is chosen specifically for low TTFT. |
| Cartesia Sonic first audio | ~40–90 ms | Among the fastest production TTS. |
| **Total (no tool)** | **~0.7–1.1 s** | Feels conversational. |
| Tool turn (e.g. `check_availability`) | **~1.3–1.8 s** | Extra LLM round-trip after the tool result. |

> _Replace these with the numbers from your own run — read the browser HUD and the
> `TTFB:` lines in the server log._

**What keeps it fast:** everything streams; TTS is sentence-aggregated so speech starts
on the first sentence while the LLM is still generating; Haiku minimises TTFT; the system
prompt makes the agent say a short filler ("Let me check that…") right before a tool call,
which hides the tool round-trip; the SQLite work runs in a thread and is sub-millisecond.

## 2. Turn-taking & interruptions

Turn-taking is driven by **Silero VAD** in the input transport. `stop_secs` (silence
after speech before the turn is considered over) is the central lever — too short cuts
people off, too long feels laggy; 0.6 s is a reasonable middle for a call where people
read out numbers.

**Barge-in** is on (`allow_interruptions=True`): if the caller speaks while the agent is
talking, Pipecat interrupts the TTS output and the new user audio flows straight to STT.
**Silence** is handled by a `UserIdleProcessor` that escalates — two gentle nudges, then a
polite goodbye that ends the call — instead of dead air or an abrupt hang-up.

**Ambiguity** is handled at the dialog layer: the prompt forces explicit confirmation of
ambiguous times, spelled names, and phone numbers (read back grouped) before
`book_appointment` fires, and Deepgram's `smart_format`+`numerals` turn spoken digits into
real numbers. **Change-of-mind** is handled by instructing the agent to drop a partial
task and follow the new intent rather than railroading the caller to completion.

## 3. The single biggest obstacle to production reliability

**Endpointing / turn detection** — reliably knowing when the caller is *actually finished*
versus just pausing mid-thought. It's the make-or-break of perceived quality: a naive
silence threshold either talks over people (threshold too short, especially while they
read out a phone number or think) or feels like a walkie-talkie (threshold too long). It
interacts with everything else — barge-in, latency, and ASR accuracy on the very tokens
(numbers, names) where pauses are longest.

**How I'd attack it:**
- **Semantic / contextual turn detection** instead of pure silence — e.g. Pipecat's Smart
  Turn model — so "my number is five five five…" *(pause)* "…one two three four" isn't
  chopped in half.
- **State-aware endpointing:** widen the silence window when the agent is expecting a long
  utterance (a phone number or spelled name) and tighten it for yes/no answers.
- **Confidence-gated responding:** if the transcript looks mid-thought (trailing
  conjunction, partial number), wait a beat longer before committing a turn.
- **Graceful interruption recovery:** treat a barge-in that turns out to be a false trigger
  (a cough, "uh-huh") without losing the agent's place.

Runner-up risks I'd budget for next: **ASR errors on names/medications/numbers** (mitigated
here with mandatory read-back + spelling, and extendable with a domain vocabulary/biasing),
and **tool-call tail latency** (mitigated with the filler phrase and, in production, async
"I'll text you the confirmation" patterns for slow backends).

## What I'd do next (given more time)

- Add a **Twilio Media Streams** adapter sharing the same pipeline (the transport is
  already isolated behind `run_bot(transport)`).
- Swap the mock store for **Google Calendar**.
- Add **Smart Turn** detection and per-state endpointing as above.
- Capture structured **per-turn latency traces** server-side (not just logs) for a metrics
  dashboard.
