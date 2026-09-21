# Research Findings

Compiled 2026-09-20. Everything below is sourced; see bottom.

> **Source claims vs project decisions.** This document records **what the sources say**, not what
> we decided to do. Several patterns reported here were evaluated and then *rejected* for this
> project — notably weighted composite scoring, which conflicts with the documented unreliability
> of score interpolation. Where a source claim no longer describes our design, it is marked
> **[not adopted]** or **[superseded]** inline. The design of record is `02-ARCHITECTURE.md`;
> where the two disagree, the architecture document wins.
>
> All API details, benchmark figures, and repository behavior below still require verification
> against pinned versions during Phase 0.

---

## 1. What JEV actually is

JEV is TypeSafe AI's flagship **System One model**, launched 2026-09-15. It is *not* an LLM
and does not generate text. You send it **state** plus **typed questions**; it returns
**typed answers with calibrated probabilities**, in a single non-autoregressive pass.

Founder: Diego Almeida (previously ChatGPT / RLHF at OpenAI). Training method is
"Reinforcement Learning for Calibrated Decisions" (RLCD), which is why the probabilities are
meaningfully calibrated rather than vibes.

### Hard numbers (Jev 1.13)

| Property | Value |
|---|---|
| Model ID | `jev-1.13.0` (aliases `jev-latest`, `jev-preview`) |
| Endpoint | `POST https://api.typesafe.ai/v1/systemone` |
| Auth | `Authorization: Bearer <API_KEY>` |
| Latency | **70-500 ms** end to end |
| Price | **$0.042 / M input tokens. Output tokens free.** |
| Context | 64k total (32k state + longest question) |
| Rate limits | 250,000 tokens/sec, **1,200 requests/min (= 20 req/s)** |
| Input | Text only. No images, audio, video. |
| Hosting | **Cloud only - no self-hosting or local deployment offered.** |

### The three question primitives

```jsonc
{
  "model": "jev-1.13.0",
  "state": { "any": "json object, string, or array" },
  "questions": {
    "is_urgent":   { "type": "noul",   "instructions": "...",
                     "criteria": { "true": "...", "false": "..." } },    // optional
    "department":  { "type": "choice", "instructions": "...",
                     "criteria": { "billing": "desc", "tech": "desc" } }, // required, max 255 options
    "frustration": { "type": "score",  "instructions": "...",
                     "criteria": ["Calm", "Frustrated", "Very angry"] }   // required, 2-10 ordered levels
  }
}
```

Response:

```jsonc
{
  "model": "jev-1.13.0",
  "answers": {
    "is_urgent":   { "type": "noul",   "noul": 0.94 },
    "department":  { "type": "choice", "choice": "billing",
                     "confidence": 0.88, "probabilities": { "billing": 0.91, "tech": 0.06 } },
    "frustration": { "type": "score",  "score": 1.4, "confidence": 0.77,
                     "legend": {}, "probabilities": {} }
  },
  "usage": { "input_tokens": 1180, "output_tokens": 0 }
}
```

Errors: `401` bad key, `422` validation, `429` rate limit, `529` overloaded.

**Confidence is not probability.** Confidence is derived from the *shape* of the distribution
(roughly `(n * p_max - 1) / (n - 1)`). Concentrated distribution = confident; spread = uncertain.
Noul answers return a probability but **no confidence field**.

### Python SDK

```bash
pip install typesafe-sdk        # env var: TYPESAFE_API_KEY
```

```python
from typesafe_sdk import AsyncTypeSafeClient, Choice, Noul, Score

async with AsyncTypeSafeClient() as client:
    r = await client.system_one(state={...}, questions={...})

r.nouls["x"].noul          # float 0-1
r.choices["y"].choice      # option key
r.choices["y"].confidence
r.scores["z"].score        # may be fractional
```

---

## 2. JEV's documented weaknesses - these dictate the whole design

From the official `jev-1.13` jaggedness page. Every one of these matters for a game loop:

| Weakness | Consequence for us |
|---|---|
| **Math and counting unreliable** | Never ask JEV "how many enemies" or "is HP below 30%". Compute in C#/Python, pass *semantic buckets*. |
| **Score interpolation unreliable** | Use Score only for threshold crossings, never as a magnitude to do arithmetic on. |
| **Large irrelevant state degrades accuracy** | State must be filtered per tick. Do not dump the whole game. |
| **Literal reading** | It answers the question you wrote, not what you meant. Criteria must be explicit. |
| **Indirection / double negatives** | Phrase every question positively and directly. |
| **No structural invariants** | `P(noul) != 1 - P(not noul)`. Do not transfer thresholds between primitives. |
| **Not trained to generate text** | All actions must be pre-enumerated as Choice criteria. Fine - our action space already is. |
| **Treats state as non-hostile** | Low risk here (state is our own mod's output), but keep the socket loopback-only. |

**The design rule that falls out of this:** the C# mod computes *derived typed facts*, and JEV
only ever exercises **judgment** over them. Zero arithmetic crosses the wire.

---

## 3. Patterns that map directly onto a game loop

- **Speculative Fan-Out** - send every question you *might* need in one call, including ones
  whose relevance depends on other answers; filter in code afterward. "All questions are
  evaluated in parallel, so adding more questions usually has little effect on response time."
- **Measured**: 13 questions batched = **0.27 s** vs 2.71 s sequential, i.e. **10.0x faster,
  12.2x cheaper, no accuracy change.** This is the single most important operational fact.
  One fat call per tick, always.
- **Confidence-Gated Routing** - confidence is the axis for *whether to act*. Suggested tiers:
  `>0.9` act automatically, `0.5-0.9` act cautiously / confirm, `<0.5` fall back. This is
  exactly the hook for "local LLM supplements JEV."
- **Composite Scoring** - decompose a big judgment into atomic Scores, weight them **in code**.
  **[not adopted]** Weighted arithmetic over raw scores conflicts with the same docs' warning that
  score interpolation is unreliable for magnitude reconstruction. Both Scores were subsequently
  cut from the MVP catalog for lack of a policy consumer and an unresolved interpretation rule —
  a legend maps integer indices to descriptions and cannot resolve a fractional score. See
  `03-QUESTION-SET.md` §2.
- **Function Calling cookbook** - a `Dispatcher` picks a function via one Choice, then fills
  arguments from **closed sets**, each with its own confidence. Overall confidence = the weakest
  judgment in the call, not the product. This is precisely our command dispatcher.

### Advanced question structure

Instructions can be objects/arrays, and can reference state fields with backticks:

```jsonc
{ "instructions": {
    "field": { "name": "threat", "type": "string", "description": "..." },
    "question": "Given `derived.hp_bucket` and `derived.enemies_near`, should the character retreat?"
}}
```

Choice criteria can be structured rubrics (`what` / `not_for` / `examples`), which is valuable
when options overlap - and several of ours do.

---

## 4. Prior art: JEV in real-time control loops

TypeSafe explicitly built JEV for this. Their launch demos and the community list include:

| Project | Rate | Notes |
|---|---|---|
| **Official Doom bot** | **~10 queries/sec** | On structured game state. **~$7/hour.** |
| Official Wikiracing | - | High-cardinality Choice (up to 255 options) |
| `fhshaik/typesafe-mario` | every 8 frames | NES state to JSON; 7 action macros; **measures observation-to-action delay and feeds it back into state** |
| `RomanSlack/jev-drone` | 2.5 Hz | Camera-only drone in MuJoCo, judgment model in the control loop |
| `grmkris/robo-harness` | - | SO-101 arm, bounded joint steps from typed candidate actions under a spend budget |
| `phyous/tsai-sc` | - | Drives original StarCraft, records action probabilities per decision |
| `milanboers/jev-plays-pokemon` | per turn | Reads game state as text, typed questions each turn |

**The Mario agent is the closest methodological match to what we want.** Two techniques to steal:

1. **Typed derived facts.** The parser precomputes things like `jump_must_start_this_decision`
   from timing math, so the model interprets a fact rather than doing arithmetic.
2. **Latency self-awareness.** It measures observation-to-action delay and includes it in the
   next state, so the model knows how stale its own view is.

Plus action **macros** that run for multiple frames - which is exactly how Rainflayer already works.

### Useful community tooling

- **`edgardcham/huncho`** (TypeScript) - decisions-as-code. Ships two things we need:
  - **hysteresis** via `{ enter: 0.8, exit: 0.6 }` thresholds, preventing state flapping;
  - **journaled answers + offline replay**, so you can retune thresholds against recorded
    decisions *with no further inference cost*.

  It is TS and our brain is Python, so we port the two ideas rather than the library.
- **`ariel-frischer/jevkit`** (Rust CLI) - 13 offline lint rules that validate questions
  *before* you pay for a call. Worth running in CI over our question catalog.

---

## 5. Risk of Rain 2 side: Rainflayer

`justindwang/rainflayer` - **MIT licensed**, Thunderstore v1.1.1, ~552 downloads, self-described
"experimental / early proof-of-concept." It already solves the genuinely hard parts.

> **Verified against the source in Phase 0** (cloned to `upstream/rainflayer`, HEAD `3d8da8c`).
> Four claims below were wrong; details and consequences in `08-PHASE0-REPORT.md` §3b.
>
>   Versions verified again by installing the stack; see `08-PHASE0-REPORT.md` §3d.
>
> - **Two version lines.** Thunderstore ships **1.1.1** (what we run and what the gate tests);
>   source HEAD is **2.0.0**, which adds a counterboss feature plus five files absent from the
>   module map below (`AdversaryAI`, `AICameraController`, `MithrixHeraldController`,
>   `AdversaryItemDisplay`, `QueryHandlers`). No git tags, so 1.1.1 is not checkoutable by tag.
> - **R2API is declared but never used.** The Thunderstore manifest requires
>   `R2API_Core-5.3.0`, so it gets installed; `git log --all -S R2API` finds it in no commit.
>   It is a package dependency, not a code one.
> - **BepInEx: 1.1.1 is BepInEx 5** (`bbepis-BepInExPack-5.4.2122`). The project file's BepInEx 6
>   references (`BepInEx.Core.dll`, `BepInEx.Unity.Mono.dll`) describe the 2.0.0 line and have
>   been present since the initial commit.
> - **Six state queries, not five**, each with a `QUERY_` prefix; and stock's Python side
>   deduplicates interactables to the closest of each type, so the stock agent cannot choose
>   between two chests at all. Interactable types are `chest, shrine, teleporter, shop, drone,
>   printer, portal, misc` — one `shrine`, not `shrine_chance`/`shrine_blood`.
>
> Confirmed correct: port 7777, NDJSON, mod-dials-Python with 2 s retry, the main-thread dispatch
> queue, the command vocabulary, and `MODE:defend_zone` already existing.

### Architecture

```
Python Brain (strategic, 0.25 Hz)  <--TCP 127.0.0.1:7777-->  BepInEx Mod (tactical, 50 Hz)
         LLM call, 1-5 s                   NDJSON                direct state override
```

### The critical insight: it does not simulate input

Rather than faking keyboard/mouse, the mod **writes directly to RoR2's internal state** -
`inputBank.aimDirection`, `moveVector`, `InputBank.skill1..4` button states, camera rotation.
The hook pattern is:

```
AI values -> save -> original update runs -> restore AI values
```

This eliminates input latency, timing complexity, and platform driver issues entirely. It is
also why the 50 Hz layer can be genuinely frame-accurate.

### Module map

**Mod (C#):** `RainflayerPlugin` (lifecycle/hooks) - `SocketBridge` (TCP + main-thread dispatch
queue, since Unity APIs crash off-thread) - `AIController` routing to `CombatController`,
`NavigationController`, `EntityDetector` - `PlayerAI` (wraps BaseAI) - `CounterbossController`.

**Brain (Python):** `BrainController` (4 s loop) - `CounterbossWorker` (asyncio, event-driven).

### 50 Hz tactical layer - already implemented, we keep all of it

- **CombatController**: writes aim/move vectors; "aim-confirm pulse" (alternating bool so
  `justPressed` fires on the rising edge) for two-stage abilities like Engineer turrets;
  sprint-blocking lists for states where sprinting is unsafe (Flamethrower, Missile Painter);
  per-character logic for Huntress sprint-while-shooting, MUL-T weapon modes, Captain orbitals.
- **NavigationController**: RoR2 NodeGraph pathfinding; **multi-height raycasting** (0.5/1.0/1.5/2.0 m,
  succeeds if any passes); pauses when enemies within 10 m; holds interact 1.5 s; 0.5 s settle
  timer against overshoot; stuck detection (velocity < 1 m/s for 5 s increments a frustration
  counter, and >= 10 frustration means navigation failure).
- **EntityDetector**: enemies within 50 m; interactables within 100 m at 180 degree FOV.

### Protocol (NDJSON over TCP 7777, newline-delimited, mod retries every 2 s)

State replies the brain can request:

```jsonc
INVENTORY     {type, money:int, equipment:str, items:[{name,count}]}
OBJECTIVE     {type, objective:str, teleporter_charged:bool, teleporter_charge:float, boss_active:bool}
COMBAT_STATUS {type, in_combat:bool, enemy_count:int, nearest_enemy_distance:float}
INTERACTABLES {type, interactables:[{type,name,cost,distance,locked,...}]}
PILLARS       {type, pillars:[{name,charged:bool}]}          // moon2 only
ALLIES        {type, allies:[{name,health,maxHealth,distance,isDowned}]}
```

Commands and events:

```jsonc
-> {type:"COMMAND", command:str, args:str}
<- {type:"COMMAND_RESPONSE", command:str, status:str}
<- {type:"EVENT", event_type:"action_complete", command:str, result:str}
      // result: success | pending | interrupted | failed(stuck/cancelled/no gold/...)
```

### Command vocabulary (the action space - already a closed set, perfect for Choice)

| Command | Args |
|---|---|
| `FIND_AND_INTERACT` | chest / shrine / teleporter / pillar / jump_pad / ship |
| `BUY_SHOP_ITEM` | item name |
| `GOTO` | CANCEL / blood / soul / mass / design (moon2 island resets) |
| `STRATEGY` | aggressive / defensive / balanced / support |
| `MODE` | roam / combat / follow / wait / defend_zone |

### Where a new decision engine plugs in

Documented explicitly: replace `BrainController` and the LLM call step. It must (1) query the
same five state endpoints, (2) drain the event queue, (3) emit the same command JSON, (4) queue
at 0.25 Hz *or faster* - the 50 Hz loop executes frame by frame regardless.

The README claims the tactical layer is reusable unchanged. **[superseded]** That holds for the
*motor* layer — aim, move, fire, raycast, NodeGraph pathing — which we do keep. It does **not**
hold for the interaction and command path: addressing specific entities, command correlation,
session and scene scoping, idempotency, preemption, and the two-stage commit all require real
changes to Rainflayer's interaction code. That work is Phase 1 (`04-ROADMAP.md`) and is the main
engineering content of this project, not a port.

Note: `BRAIN_MODEL` / `BRAIN_API_KEY` env vars use LiteLLM `provider/model-name` format. JEV is
**not** a LiteLLM-compatible chat API - it is a different request shape entirely - so this is a
real code swap, not a config change.

### Known limitations (inherited unless we fix them)

- Engineer Thermal Harpoons alt-fire does not fire correctly
- Navigation stalls on complex geometry; stuck detection incomplete
- **Commencement (moon) is by far the worst case for the pathfinder**
- Survivor coverage uneven; quality varies per character
- Extreme camera angles on flying enemies cause visibility loss
- **Movement speed above 2 Goat Hooves breaks navigation**
- `defend_zone` movement is choppy

### Environment risk

Seekers of the Storm reworked the codebase and broke a large swathe of legacy mods; the ecosystem
adapted, but BepInEx/R2API version drift after patches is routine. Rainflayer's Thunderstore page
states **no** specific game-version compatibility. Requires BepInEx 5.x and Python 3.10+.
**Assume the mod needs repair before it needs extending.**

---

## 6. Local LLM capacity (RTX 5070 Ti 16 GB)

For the optional counsel layer. Measured figures for this exact card:

| Model | Quant | Gen speed | Fit |
|---|---|---|---|
| Qwen3-8B | Q4_K_M (~9.9 GB) | ~112-120 tok/s @ 4k ctx | Comfortable |
| Qwen3-8B | Q4_K_M | 63 tok/s @ 32k ctx | Comfortable |
| Qwen3-14B | Q4 | ~54 tok/s | Fits |
| Qwen3.8-27B | Q4_K_M (~17.1 GB) | - | **Does not fit** once KV cache and buffers are counted |

Prompt processing is fast (5,557 tok/s @ 4k), so a short advisory prompt returns quickly.

**Conclusion:** Qwen3-8B Q4_K_M via Ollama is the right counsel model. A 200-token advisory
completes in roughly 2-4 s including prompt processing - far too slow for the control loop,
perfectly fine for stage-transition planning. This confirms the LLM must sit *outside* the loop.

---

## Sources

- [What Is Jev? A Guide to TypeSafe AI's System One Model - LangChain](https://www.langchain.com/blog/building-a-harness-with-jev)
- [Introducing System One Models & Jev - TypeSafe AI](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- [System One - TypeSafe docs](https://docs.typesafe.ai/concepts/system-one)
- [HTTP API Reference - TypeSafe docs](https://docs.typesafe.ai/api.md)
- [Models - TypeSafe docs](https://docs.typesafe.ai/models.md)
- [Confidence - TypeSafe docs](https://docs.typesafe.ai/confidence.md)
- [Jev 1.13 Jaggedness - TypeSafe docs](https://docs.typesafe.ai/model-jaggedness/jev-1.13.md)
- [Speculative Fan-Out - TypeSafe docs](https://docs.typesafe.ai/patterns/fan-out.md)
- [Confidence-Gated Routing - TypeSafe docs](https://docs.typesafe.ai/patterns/confidence-routing.md)
- [Composite Scoring - TypeSafe docs](https://docs.typesafe.ai/patterns/composite-scoring.md)
- [Parallel Questions cookbook - TypeSafe docs](https://docs.typesafe.ai/cookbooks/parallel_questions.md)
- [Function Calling cookbook - TypeSafe docs](https://docs.typesafe.ai/cookbooks/function_calling.md)
- [State - TypeSafe docs](https://docs.typesafe.ai/concepts/state.md)
- [Advanced primitives - TypeSafe docs](https://docs.typesafe.ai/primitives/advanced.md)
- [Python SDK - TypeSafe docs](https://docs.typesafe.ai/sdk/python.md)
- [Agent Skill - TypeSafe docs](https://docs.typesafe.ai/agent-skill.md)
- [Jev API reference - AI/ML API](https://docs.aimlapi.com/api-references/decision-models/typesafe/jev)
- [Jev AI Plays Minecraft, Subway Surfers, and Drives Cars - MindStudio](https://www.mindstudio.ai/blog/jev-real-time-game-demos)
- [awesome-jev](https://github.com/yibie/awesome-jev)
- [huncho](https://github.com/edgardcham/huncho) / [jevkit](https://github.com/ariel-frischer/jevkit) / [typesafe-mario](https://github.com/fhshaik/typesafe-mario)
- [Rainflayer - Thunderstore](https://thunderstore.io/package/Rainflayer/Rainflayer/) / [source](https://github.com/justindwang/rainflayer)
- [Risk of Rain 2 Modding Wiki - BepInEx](https://risk-of-thunder.github.io/R2Wiki/Tools/BepInEx/) / [R2API wiki](https://github.com/risk-of-thunder/R2API/wiki)
- [RTX 5070 Ti local LLM benchmarks - Hardware Corner](https://www.hardware-corner.net/gpu-llm-benchmarks/rtx-5070-ti/) / [modelfit](https://modelfit.io/gpu/rtx-5070-ti/)
