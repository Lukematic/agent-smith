# API Keys and Custom Gateways

Where Smith's subagent spawning looks for credentials, and how to point it at a
corporate gateway instead of Anthropic directly.

**Grounding:** chapters/6-harnesses/6-security-permissions-trust.md — credentials
are a trust boundary. Smith never reads, stores, or transmits them on your behalf;
it only detects their *absence* and tells you the remedy.

---

## The default path

`smith delegate` spawns subagents through `claude`, `goose`, or `codex`, in that
order (`src/smith/spawn.py::detect_runner`). Each is a CLI that manages its own
auth:

```bash
claude /login                    # interactive OAuth
export ANTHROPIC_API_KEY=sk-...  # or a direct key
```

If none of the three CLIs are on `PATH`, or the one found is not authenticated,
`smith heal` and `smith delegate` diagnose it as `AUTH_MISSING` and refuse to guess
at credentials — that refusal is deliberate, not a gap.

---

## Using a corporate gateway instead

Some environments route through an internal endpoint rather than Anthropic
directly, for example:

```
LLM_BASE_URL=https://your-gateway.example.com
LLM_API_KEY=...
LLM_MODEL=claude-sonnet-5-project
```

`claude` supports this via its own environment variables:

```bash
export ANTHROPIC_BASE_URL="$LLM_BASE_URL"
export ANTHROPIC_API_KEY="$LLM_API_KEY"
export ANTHROPIC_MODEL="$LLM_MODEL"
claude /login   # or skip login if the gateway accepts the API key directly
```

Smith does not read your `.env` file and does not translate `LLM_*` variable names
to `ANTHROPIC_*` automatically. That translation is one line you run once, kept
explicit rather than hidden inside `spawn.py`, because silently reading arbitrary
`.env` files across a filesystem is the kind of behavior that turns a harness into
a liability.

---

## Honest limits, as of this writing

- **No native OpenAI, Gemini, or generic-gateway runner.** `Runner` in
  `spawn.py` only shells out to `claude`, `goose`, or `codex`. A gateway that is
  Anthropic-API-compatible works through `claude`'s own env vars above; a gateway
  with a different wire format does not work yet.
- **`smith limits`** reports this honestly:

  ```bash
  smith limits
  #   ABSENT   spawn scoped subagents: no agent CLI found ...
  ```

  If your CLI is present but unauthenticated, the probe reports it as usable
  (`available` checks `PATH`, not login state) — `smith delegate` is what catches
  the auth gap at spawn time, with the diagnosis above.

- **Adding a fourth runner is a real, trackable piece of work**, not a one-line
  fix: `Runner` needs a new enum member, a `.command()` implementation, and
  `detect_runner`'s search order updated. If you need Gemini natively, open that as
  a `smith gate open authoring` task rather than expecting `.env` autodetection to
  appear silently.
