# `docs/updates`

Point-in-time review documents: comparisons against external projects, decisions
about what to adopt or reject, and the seeds those decisions produced. Distinct
from `docs/*.md` (the current, living reference docs) - files here are a dated
record of a specific review session and are not updated after the fact.

## Contents

- `third-party-skills-review.md` — 2026-08-23 review of `obra/superpowers` and
  `muratcankoylan/Agent-Skills-for-Context-Engineering`, with adopt/reject
  decisions and the seeds each decision produced.

## Usage

Read a file here to understand *why* a seed exists and what source material it
came from. Do not treat these as current documentation of A.W.I.N.O.'s
behavior - once a seed closes, the resulting behavior is documented in the normal
`docs/*.md` files, and this file becomes historical record only.

## Format

Markdown, one file per review session, named `<topic>.md` (no date prefix
required, but the review date is stated inside the file).

## Stability

Written by hand, not regenerated. Never edit a past review to reflect later
decisions - open a new file instead, so the record of what was actually decided
at the time stays intact.
