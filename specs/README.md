# Specifications

Approved implementation contracts live here. They define behavior and acceptance checks before code changes begin; drafts, generated output, and runtime state do not belong in this directory.

## Contents

Each topic is a discoverable Markdown file with a descriptive kebab-case name such as `graph-engine-spec.md`. Search this directory by feature name, then treat the matching specification as the source of truth for that implementation.

## Usage

Read the relevant specification before planning or building. A specification is accessible directly as UTF-8 Markdown and requires no generator or specialized viewer. If implementation requirements change, obtain approval and update the specification before changing the corresponding behavior.

## Format

Files are interoperable GitHub-flavored Markdown. Specifications should state their objective, scope, required behavior, terminal or failure outcomes, acceptance checks, and grounding so humans, agents, tests, and review workflows can consume the same contract.

## Stability

Specifications are reusable, version-controlled project records rather than ephemeral prompts. Edit them only when the approved contract changes, preserve enough context for a fresh implementer or reviewer, and keep runtime artifacts under `.smith/state/` instead.

## FAIR

- **Findable:** descriptive filenames and explicit contents make each approved contract easy to locate.
- **Accessible:** plain UTF-8 Markdown is readable locally without credentials or proprietary tooling.
- **Interoperable:** consistent sections can be consumed by humans, agents, tests, and review tooling.
- **Reusable:** versioned requirements and acceptance checks support independent implementation and later review.
