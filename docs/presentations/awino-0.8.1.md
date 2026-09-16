# A.W.I.N.O. 0.8.1: The Verified Agentic Harness

A structured presentation following Patrick Winston's MIT communication framework.

---

## Part 1: 90-Second Opening Script

**Hook (0:00 - 0:25):**
Why do 90% of AI coding agent failures happen after the agent confidently claims the task is complete?
Every software engineer using AI has seen it: the model announces victory, marks all checkboxes green, and leaves behind broken code with missing dependencies.

**The Promise (0:25 - 0:55):**
In the next 15 minutes, you will see how A.W.I.N.O. 0.8.1 replaces conversational claims with deterministic ledger evidence.
By the end of this talk, you will understand how a single entry point—`awino best`—enforces budget limits, runs independent reviews, and refuses to report completion until real verification commands pass on disk.

**The Road Map (0:55 - 1:30):**
We will cover three concrete mechanisms: first, why completion is a computed verdict rather than a prompt instruction; second, how persistent task contracts survive session restarts; and third, how you can adopt this harness in your own workflow today.

*Rationale:* Cuts greetings and pleasantries. Establishes the core pain point in 25 seconds. Delivers the specific promise within 55 seconds. Outlines the three structural proofs before the 90-second mark.

---

## Part 2: Slide Deck (One Headline + One Visual Idea)

### Slide 1: The Trust Problem
- **Headline:** Claims Are Not Evidence.
- **Visual Idea:** A side-by-side comparison: on the left, an AI chat asserting "Everything works!"; on the right, a terminal diff showing failing assertions and skipped tests.
- **Speaker Notes:** Large language models optimize for plausible answers, not empirical verification. Without a harness, an agent grades its own homework.

### Slide 2: The Architecture
- **Headline:** One Door, Three Enforcement Layers.
- **Visual Idea:** A 3-tier diagram: (1) Deterministic CLI Entry `awino best` -> (2) State-Machine Controller & Plan Ledger -> (3) Isolated Subprocess Workers & Independent Reviewers.
- **Speaker Notes:** The user interacts with one entry point. The controller manages state, budgets, and reviews. Workers execute in isolated environments with disjoint scopes.

### Slide 3: Task Contract
- **Headline:** The Eight-Block Contract Survives Restart.
- **Visual Idea:** An exploded view of a task contract showing the 8 policy blocks: Goal, Context, Priority, Autonomy, Tools/Delegation, Output, Verification, and Stop Condition.
- **Speaker Notes:** Every task is bound to an immutable content hash. If the user edits scope or budget, approval visibly invalidates until re-approved.

### Slide 4: Closure Safety
- **Headline:** Closure Is Computed, Never Claimed.
- **Visual Idea:** A flow gate showing: `awino gate close` evaluating executed test results, linting status, review verdict `SHIP`, and test-weakening analysis before returning exit code 0.
- **Speaker Notes:** A blocked review or missing verification stops closure mechanically. The agent cannot say "done" unless the ledger records passing checks.

### Slide 5: The Value
- **Headline:** Ship Software You Actually Tested.
- **Visual Idea:** A green terminal status summary: 18/18 capability probes verified, 0 health failures, clean ledger receipts.
- **Speaker Notes:** You gain confidence that every change made by the agent satisfies deterministic contracts.

### Slide Crimes Removed Summary
- Removed 14 bulleted paragraphs across the deck.
- Replaced 5 text-heavy slides with single high-contrast visual concepts.
- Guaranteed every slide carries an action headline and dedicated speaker notes.

---

## Part 3: Three Memorable Idea Cards

### Idea Card 1: Verification Authority
- **Slogan:** Real command exit codes decide task completion.
- **Symbol:** A mechanical iron lock requiring two distinct keys.
- **Surprise:** The model does not get to say when it is finished; a shell command does.
- **Big Idea:** Completion is computed by executed evidence, never by conversational assertion.

### Idea Card 2: Harness Over Prompt
- **Slogan:** Structural constraints beat polite prompt instruction warnings.
- **Symbol:** A steel guardrail on a mountain highway.
- **Surprise:** Telling an agent "please don't edit out of scope" fails; restricting its file access succeeds.
- **Big Idea:** Harness engineering prevents failure modes that prompt engineering merely laments.

### Idea Card 3: Memory Persistence
- **Slogan:** State survives every restart and context compaction.
- **Symbol:** An immutable stone ledger ledger book.
- **Surprise:** When your context window fills and resets, your plan, scope, and budget remain intact.
- **Big Idea:** Persistent task contracts maintain project alignment across sessions and tools.

---

## Part 4: SPIS Persuasion Structure

### Situation
Modern AI coding assistants have tremendous generative capability. Developers increasingly rely on agents to refactor code, fix bugs, and build features autonomously.

### Problem
Without structural boundaries, agents drift: they fabricate tests, exceed budgets, overwrite unapproved files, and report success when code is broken.

### Implications
Teams waste hours debugging "completed" AI work, leading to loss of trust and abandonment of autonomous workflows in critical systems.

### Contribution Moment
A.W.I.N.O. 0.8.1 introduces a deterministic state controller that wraps any LLM with hard gates: budget caps, immutable task contracts, independent review floors, and truthful status reporting.

### Solution & Final Statement of Value
Stop prompting for honesty. Start enforcing it with a ledger.
Run `awino best` to experience verified autonomous software engineering.
