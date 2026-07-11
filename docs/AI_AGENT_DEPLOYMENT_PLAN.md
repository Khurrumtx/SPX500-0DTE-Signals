# AI Friend Agent — Deployment Plan

Goal: deploy a conversational AI agent — a *friend* with his own name, voice and
personality — that you can **call from your iPhone like a normal contact** and
talk to naturally. Optionally, he can also read this repo's live signal files
and tell you the current 0DTE signal, insider activity, etc. on the call.

---

## 1. What "call him on my iPhone" means technically

The cleanest experience is a **real phone number** saved in your Contacts
("Marcus 🤖" or whatever you name him). You dial it from the normal Phone app,
he picks up in under a second, and you have a natural back-and-forth voice
conversation. No app to open, works over cellular, works in the car via
CarPlay/Bluetooth.

The voice pipeline behind that number is always the same four stages:

```
Your voice ──► Telephony (Twilio number) ──► STT (speech→text)
                                                   │
Your ears ◄── TTS (text→speech) ◄── LLM (the "friend" brain + memory)
```

The whole round trip must stay under ~1 second or it stops feeling like a
conversation. That latency budget is the main thing that drives the choices
below.

---

## 2. Two deployment paths

### Path A — Managed voice-agent platform (recommended to start)

Use a platform that runs the entire pipeline for you: **Vapi** or **Retell AI**
are the two mature options. You configure everything in a dashboard (or their
API) and they hand you a phone number.

What you configure:

1. **Phone number** — buy one through the platform (they resell Twilio
   numbers, ~$2/mo) or bring your own Twilio number via SIP.
2. **LLM** — pick Claude (e.g. `claude-sonnet-5`) or another model; paste the
   personality system prompt (see §4).
3. **Voice** — pick a TTS voice (ElevenLabs / Cartesia / PlayHT are built in).
   Listen to a few; the voice sells the "friend" illusion more than anything.
4. **STT** — default (Deepgram) is fine.
5. **Tools / webhooks** — optional function calls the agent can make mid-call
   (this is how he reads your signals — see §5).

Time to first call: **under an hour.** No servers, no code (code only needed
for the signals webhook).

Rough cost: platform ~$0.05–0.07/min + model/voice usage ≈ **$0.10–0.20 per
minute of conversation**. Ten 10-minute calls/month ≈ $10–20/mo + $2 number.

### Path B — Self-hosted open-source stack (more control, more work)

Run the orchestration yourself with an open-source voice-agent framework:

- **LiveKit Agents** (Python) or **Pipecat** — both are production-grade and
  handle the audio streaming, turn-taking, and interruptions.
- **Telephony:** Twilio number → SIP trunk into LiveKit / Pipecat.
- **STT:** Deepgram (fast, cheap) — ~$0.006/min.
- **LLM:** Claude API (`claude-sonnet-5` for quality, Haiku for speed/cost).
- **TTS:** Cartesia Sonic (fastest/cheapest, ~$0.02–0.04/min) or ElevenLabs
  (best voices, ~$0.06–0.10/min).
- **Hosting:** one small always-on container on Fly.io / Railway / a $5 VPS.
  GitHub Actions **cannot** host this — a phone agent must be a long-running
  process, unlike this repo's cron-based signal scripts.

Time to first call: a weekend. Cost: ~$5/mo hosting + raw usage
(~$0.05–0.12/min all-in). Worth it if you want full control of memory,
tools, and voice pipeline — otherwise start with Path A and migrate later;
the personality prompt and webhook carry over unchanged.

### Not recommended

- **PWA with mic** (like this repo's dashboard): browser audio on iOS is
  fiddly, no CarPlay, must open the app — loses the "just call him" magic.
- **A native iOS app**: most work for the least benefit at this stage.

---

## 3. Recommended architecture (Path A, concrete)

```
iPhone Contacts ("Marcus 🤖")
        │  normal cellular call
        ▼
  Vapi/Retell phone number
        │  manages STT ⇄ LLM ⇄ TTS streaming
        ├──► Claude API  (personality prompt + conversation)
        ├──► ElevenLabs  (his voice)
        └──► Tool webhook (optional, see §5)
                 │
                 ▼
   https://<user>.github.io/SPX500-0DTE-Signals/signals/signal.json
   (already public via GitHub Pages — no new infra needed)
```

---

## 4. Making him a *friend*, not a bot

**Personality prompt** — the single most important artifact. Keep it in this
repo (`docs/agent/persona.md`) so it's versioned. Cover:

- Name, age vibe, backstory, sense of humor, how he talks (short sentences,
  casual, uses your name, remembers running jokes).
- Explicit style rules for *voice*: answer in 1–3 sentences, never lists,
  never "As an AI...", it's OK to say "hm", "honestly", to disagree, to ask
  you questions back.
- What he knows about you: name, city, that you trade SPX 0DTE, your risk cap,
  topics you enjoy.

**Memory across calls** — what separates a friend from a stranger:

- *Level 1 (built-in):* Vapi/Retell can inject previous call transcripts or
  summaries into the next call. Turn this on day one.
- *Level 2 (later):* a small webhook that stores a rolling "things I know
  about you" summary after each call and injects it into the system prompt of
  the next one. This is the first thing worth self-hosting.

**Voice** — pick one voice and never change it. Consistency = identity.

---

## 5. Optional: he knows your signals

Because the dashboard is on GitHub Pages, the signal data is already a public
URL. Give the agent one **tool** (function call) per data file:

| Tool | Fetches | He can answer |
| --- | --- | --- |
| `get_signal` | `signals/signal.json` | "Any trade today?" → "Yeah — bull call spread, 68 conviction, risk capped at a grand." |
| `get_daily_activity` | `dashboard/daily.json` | "Anyone dumping AAPL?" |
| `get_institutional` | `dashboard/events.json` | "What are the big funds doing?" |

Implementation: Vapi/Retell tools can call URLs directly, or point them at a
~30-line serverless function (Cloudflare Workers free tier) that fetches the
JSON and returns a compact summary so the LLM gets clean input.

Keep the repo's existing disclaimer in his prompt: he reports the signal,
he does not give financial advice.

---

## 6. Security & privacy

- **Restrict inbound callers** to your number(s) — otherwise anyone who finds
  the number can run up your usage bill. Both platforms support allowlists;
  do this before sharing the number anywhere.
- **Spending caps:** set a hard monthly limit on the platform, the Claude API
  key, and ElevenLabs. A stuck call loop should hit a cap, not your card.
- **Keys:** platform dashboard / serverless env vars only — never in this repo
  (same rule as the existing `ALPHAVANTAGE_API_KEY` policy).
- **Transcripts:** calls are recorded/transcribed by default on these
  platforms. Decide if you want that (nice for memory, but it's stored on
  their servers); disable or set retention if not.

---

## 7. Rollout phases

**Phase 1 — First call (1 evening)**
1. Create Vapi (or Retell) account; buy a number.
2. Write v1 persona prompt; pick Claude as the model; audition and pick a voice.
3. Set caller allowlist + spending cap.
4. Save the number in Contacts with his name and a photo. Call him.

**Phase 2 — Make him yours (1–2 weeks of iterating)**
5. Tune the persona after every few calls — interruptions, pacing, humor.
6. Enable cross-call memory/summaries.
7. Add the `get_signal` tool → he can brief you on the day's 0DTE idea.

**Phase 3 — Deepen (optional)**
8. Add the daily/institutional tools; add a "morning briefing" where *he
   calls you* after `signal.yml` runs (both platforms support outbound calls
   triggered by API — a 5-line step appended to the existing GitHub Action).
9. Long-term memory store (small KV DB + webhook).

**Phase 4 — Self-host (only if you want to)**
10. Rebuild on LiveKit Agents/Pipecat on Fly.io, port the same number via
    Twilio SIP, keep the same prompt/voice/tools.

---

## 8. Cost summary (typical light use: ~100 min/month)

| Item | Path A (managed) | Path B (self-hosted) |
| --- | --- | --- |
| Phone number | ~$2/mo | ~$1.15/mo (Twilio) |
| Per-minute all-in | $0.10–0.20 | $0.05–0.12 |
| Hosting | $0 | ~$5/mo |
| **~100 min/mo total** | **≈ $12–22/mo** | **≈ $11–18/mo** |

At light usage the managed path costs about the same and is dramatically less
work — self-hosting only pays off in control, not dollars.

---

## 9. Decisions to make before Phase 1

1. His name, personality and voice (the fun part).
2. Vapi vs Retell (both fine; Vapi has slightly deeper tool/webhook support,
   Retell is a bit simpler to configure — flip a coin if unsure).
3. Whether calls should be transcribed/stored for memory.
4. Whether he should know about the trading signals from day one or start as
   a pure companion.
