# Task Contract Reference Examples

Seven production-ready task contract families demonstrating failure-mode-driven
policy blocks, evidence hierarchies, autonomy boundaries, and stop conditions.

---

## Example 1: Support Message Classification

### TASK
Classify each support message.

### DECISION RULES
- "critical" means the customer cannot use a core paid function.
- A feature request without breakage is not critical.
- Access or payment failures that block product use are high severity.
- Do not infer a failure that is not supported by the message.

### OUTPUT MEANING
For each message determine:
- `topic`: main functional area
- `sentiment`: positive | neutral | negative
- `severity`: low | medium | high | critical
- `action_required`: true | false
- `concise_reason`: one short sentence explaining the rating

### NEGATIVE FIXTURE
A feature request ("Please add dark mode") must NEVER be classified as critical.
Ambiguous text must not invent imaginary system breakage.

---

## Example 2: Product-Gap Market Research

### GOAL
Identify meaningful product gaps in the current AI prompt-tool market.

### EVIDENCE
- Prioritize current first-party product pages and documentation.
- Use community discussions as qualitative evidence, not verified product facts.
- Label uncertainty when a claim cannot be confirmed.

### ANALYZE
- positioning
- target users
- core workflows
- pricing and packaging
- repeated pain points
- underserved use cases

### AUTONOMY
Continue when non-critical details are missing. Ask only if a missing business
constraint would materially change the recommendation.

### OUTPUT
Executive summary, comparison table, opportunity gaps, three opportunities,
risks, and one recommended direction.

### NEGATIVE FIXTURE
Unverified pricing claims must remain labeled unknown. Non-critical missing
details must not trigger unnecessary clarification stops.

---

## Example 3: Coding Bug Fix

### GOAL
Fix the issue where authenticated users are occasionally redirected to login
after refreshing the dashboard.

### SCOPE
Inspect session restoration, route protection, authentication state, and
relevant middleware.

### CONSTRAINTS
- Preserve the current auth provider and public API.
- Avoid unrelated refactors.

### AUTONOMY
Investigate independently and make reversible code changes needed to solve the
bug. Ask before an architectural change that affects unrelated authentication flows.

### VERIFICATION
Verify login, refresh, expired session, logout, and direct navigation. Run
relevant existing checks. Do not expand testing unless the change reveals a
broader dependency.

### OUTPUT
Root cause, files changed, solution, verification output, remaining uncertainty.

### NEGATIVE FIXTURE
Modifying tests to make them pass without fixing the code is strictly rejected.
An unverified positive claim ("it should work") is invalid evidence.

---

## Example 4: Tool-Using Support Agent

### GOAL
Resolve the customer's account-access issue.

### TOOL RULES
- Retrieve the customer record before making account-specific claims.
- Use internal policy search for support-policy questions.
- Never invent an account ID, subscription state, or payment status.

### AUTHORIZATION
- Read-only investigation is allowed.
- Drafting a customer response is allowed.
- Changing billing state requires explicit authorization.

### RECOVERY
If a tool fails, do not claim the requested action succeeded. Retry only when
the failure appears transient and the action is safe to retry.

### STOP
Stop when the issue is resolved or the next required action needs user authorization.

### NEGATIVE FIXTURE
A tool failure cannot be hidden by claiming success. Billing modifications
without explicit authorization are strictly blocked.

---

## Example 5: Multi-Agent Competitive Research

### GOAL
Map the competitive landscape for a new developer productivity product.

### DELEGATION POLICY
Delegate only workstreams that are materially independent and can benefit from
parallel research. Do not create subagents merely to increase parallelism.

Good workstreams:
- competitor positioning
- pricing and packaging
- developer complaints
- integration patterns

Each subagent returns: sources, verified findings, material uncertainties,
contradictory evidence, concise synthesis.

### PRIMARY AGENT
The primary agent owns the final answer. Reconcile duplicates and factual
conflicts before writing. Resolve conflicting claims using source authority and
freshness, not by averaging agent conclusions. Prefer current first-party
evidence for product capabilities and pricing.

### STOP CONDITION
Do not launch additional research workstreams after major competitive questions
have been answered unless a remaining gap could materially change the recommendation.

### OUTPUT
One unified landscape analysis, evidence-backed opportunity gaps, recommended
positioning, and unresolved uncertainties.

### NEGATIVE FIXTURE
Conflicting claims must not be resolved by averaging. Subagents must not write
to overlapping files or execute redundant searches.

---

## Example 6: Long-Context Document Analysis

### TASK
Compare the current contract with the previous version and identify meaningful
commercial changes.

### SOURCE AUTHORITY
The two supplied contracts are the primary evidence. Reference notes explain
terminology but cannot override contract text.

### FOCUS
- payment terms
- renewal
- termination
- liability
- data handling
- service levels

### RULES
Quote only short relevant phrases. Do not infer a legal consequence that is not
supported by the text. If a clause is ambiguous, describe the ambiguity.

### OUTPUT
Table of changes, practical significance, unchanged high-risk clauses, and
questions that require legal review.

### NEGATIVE FIXTURE
Reference notes or external commentary cannot override explicit contract clauses.
Do not invent legal conclusions unsupported by the text.

---

## Example 7: Production-Ready Delegated Assignment

### OBJECTIVE
Research the current market for AI developer tools and recommend the strongest
positioning opportunity for a small new product.

### CONTEXT
The product is being built by a small team with limited engineering and
marketing capacity. Prefer opportunities that can be validated without
enterprise-scale infrastructure.

### SOURCE AUTHORITY
1. Current first-party product documentation
2. Current first-party pricing and release notes
3. Reputable current secondary reporting
4. Public community discussions as qualitative evidence

### REQUIREMENTS
Analyze positioning, target users, core workflow, pricing and packaging,
differentiators, repeated user pain points, switching barriers, underserved jobs.

### CONSTRAINTS
Do not invent pricing, adoption numbers, feature availability, customer counts,
or roadmap claims. Do not recommend an opportunity that requires capabilities
the stated team cannot realistically validate.

### AUTONOMY POLICY
Continue independently through non-critical ambiguity. Make reasonable
assumptions only when they do not materially change opportunity ranking.
Label every material assumption.

### CLARIFICATION POLICY
Ask a question only when a missing constraint could materially change target
market, feasible scope, budget, legal boundaries, or recommendation.

### TOOL POLICY
Use first-party sources for product/pricing claims. Use broader web research for
market context. Do not repeat searches when facts are already established.

### DELEGATION
Parallelize independent research workstreams when doing so improves coverage or
latency. Do not delegate tightly coupled work. Primary agent owns synthesis.

### FAILURE HANDLING
If a source is inaccessible, record the gap and continue with other evidence.
Do not silently replace missing first-party evidence with lower-authority sources.

### VERIFICATION
Verify material claims, check cited evidence, remove duplicates, identify
contradictions, verify opportunity satisfies team constraints.

### OUTPUT CONTRACT
1. Executive summary
2. Evidence table
3. Market patterns
4. Opportunity shortlist
5. Recommended positioning
6. Sensitivity analysis
7. Major risks
8. Confidence and unresolved uncertainties
9. Next validation actions

### STOP CONDITION
Stop when major market questions are answered well enough to rank opportunities
and remaining gaps are documented. Do not continue researching merely to
increase source counts.
