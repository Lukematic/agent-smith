# Documentation

Long-form, task-oriented documentation for A.W.I.N.O.

## Contents

## Canonical guides

| Guide | Audience | Covers |
| --- | --- | --- |
| [User guide](user-guide.md) | People pairing with A.W.I.N.O. | Setup, first session, plans, Seeds, evidence, resume, updates, and troubleshooting |
| [Agent and harness guide](agent-guide.md) | AI agents and harness authors | Startup contract, routing, skills, run protocol, checkpoints, verification, and status reporting |

## Deeper reference

| Document | Topic |
| --- | --- |
| [Mission](MISSION.md) | Product mission and naming rationale |
| [Installation](install.md) | Installer behavior and harness destinations |
| [Architecture](architecture.md) | System layers and boundaries |
| [Deployment](deployment.md) | Global and project-local deployment |
| [Distribution](distribution.md) | Packaging and release workflow |
| [Enforcement](enforcement.md) | Gate-ledger design |
| [Harness](harness.md) | Mental models, guides, and sensors |
| [Skills](skills.md) | Generated canonical skill catalog |
| [API keys](api-keys.md) | Provider credentials and custom gateways |
| [Nuclear engineer walkthrough](walkthrough-nuclear-engineer.md) | Domain-support example |
| [Credits and sources](credits-and-sources.md) | Attribution and upstream sources |

The root [README](../README.md) is the landing page. Add long-form material here and
link every topic document from both this index and the root README.

## Usage

Start with the guide for your role, then follow links to deeper reference material.
Every top-level topic in this directory must remain reachable from the root README.

## Format

Documentation is Markdown. Use canonical `awino` commands in executable examples.

## Stability

Edit human-authored guides directly. `skills.md` is generated from the installed
skill catalog and must be refreshed through the repository's generation workflow.
