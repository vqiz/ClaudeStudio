# Voice Assistant

ClaudeStudio includes a hands-free **voice assistant** — speak to drive sessions, ask about your projects, and trigger actions without touching the keyboard. The pipeline runs in the macOS app for capture/playback and routes recognized intents to the core like any other command.

> **Status.** The voice assistant is a Phase 3 (Intelligence) feature; the pipeline design below is the target. See [roadmap.md](roadmap.md).

---

## 1. The pipeline

```mermaid
flowchart LR
    Mic[Microphone] --> Wake[Wake-word detector]
    Wake -->|wake detected| STT[Speech-to-Text]
    STT --> Intent[Intent → IPC Request]
    Intent --> Core[Core processes]
    Core -->|answer text| TTS[Text-to-Speech]
    TTS --> Spk[Speaker]
```

| Stage | What it does | Where it runs |
| --- | --- | --- |
| **Wake-word** | Listens locally for an activation phrase; nothing is transcribed or sent until it fires. | On-device. |
| **STT (Speech-to-Text)** | Transcribes the utterance after the wake word. | On-device by default. |
| **Intent** | The transcript becomes an IPC request to the core (same path as typed input). | App → Core. |
| **Core** | Runs the session/turn or action and produces a concise answer. | Core. |
| **TTS (Text-to-Speech)** | Speaks the answer back. | On-device. |

By keeping wake-word and STT on-device, the assistant stays idle-quiet and private — audio isn't streamed anywhere until you intentionally invoke it.

---

## 2. Latency target

The end-to-end goal is **conversational latency** — from end-of-speech to the start of the spoken answer in roughly **under ~1.5 seconds** for short interactions. Techniques:

- Stream STT partials so intent resolution can start before you finish speaking.
- Stream the model's answer token-by-token into TTS so speech begins before the full answer is ready.
- Keep wake-word and STT local to avoid network round-trips on the hot path.

(Long-running actions — building, running a Team — return a short spoken acknowledgement immediately and notify you when the underlying work completes.)

---

## 3. What the assistant knows

The voice assistant is *the same brain* as the rest of ClaudeStudio. It can draw on:

- The **active project** — files, diffs, current session.
- **Semantic memory** — recall across past sessions and the Definition Library (see [memory-and-vector.md](memory-and-vector.md)).
- **Agentic OS state** — running agents, monitor alerts, the queue, budgets (see [agentic-os.md](agentic-os.md)).
- **Telemetry** — current cost/token burn.

So "what did we change in the auth module yesterday?" or "is anything failing right now?" are answerable from real state, not guesses.

---

## 4. Answer style

Voice answers are tuned for listening, not reading:

- **Concise first** — lead with the answer, then offer to elaborate.
- **No code dumps** — code is shown in the UI, not read aloud; the assistant describes it and points to it.
- **Confirm before consequential actions** — destructive or gated actions are spoken back for confirmation, honoring the active trust mode (see [security.md](security.md)).
- **Graceful uncertainty** — if recall is thin, it says so rather than fabricating.

---

## 5. Voice actions

Examples of what you can do hands-free:

| Category | Examples |
| --- | --- |
| **Ask** | "What does this function do?" · "Did we already fix this bug?" · "What's our test convention?" |
| **Drive a session** | "Implement input validation on the signup form." · "Run the tests." |
| **Navigate** | "Open the Brain View." · "Show me the OS View." · "Switch to the API project." |
| **Agentic OS** | "What's running?" · "Pause the queue." · "Approve the pending permission." |
| **Cost** | "How much have I spent today?" |
| **Git** | "What's the diff on this branch?" · "Create a worktree for the refactor." |

All voice actions pass through the same permission gates as typed actions — the microphone does not bypass security.

---

## See also

- [Memory & Vector](memory-and-vector.md) — what the assistant can recall.
- [Agentic OS](agentic-os.md) — the live state it can report on.
- [Security](security.md) — confirmation and gating of spoken actions.
