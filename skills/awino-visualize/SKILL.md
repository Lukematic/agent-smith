---
name: awino-visualize
description: Create concrete visual explanations from architecture, processes, timelines, comparisons, and quantitative data. Use whenever the user asks for a diagram, chart, visualization, image, schematic, map, dashboard, workflow, sequence, or visual comparison. Prefer Mermaid in chat, then SVG or self-contained HTML when richer output materially improves understanding.
---

# A.W.I.N.O. Visualize

Turn inspectable facts into the clearest visual the active Claude Code surface can
present. A visual is an information model, not decoration: every node, edge, axis,
label, and color must communicate something supported by the available evidence.

## Route by information shape

Choose the smallest format that preserves the meaning:

| Information | Default | Escalate when |
| --- | --- | --- |
| architecture, hierarchy, dependency | Mermaid flowchart | dense layout needs precise SVG placement |
| request path, handoff, conversation | Mermaid sequence diagram | timing or concurrency needs a custom SVG |
| lifecycle, states, decisions | Mermaid state or flow diagram | interactivity materially aids exploration |
| timeline or roadmap | Mermaid timeline or compact table | scale or overlap needs SVG |
| comparison or status | Markdown table | spatial comparison is the point |
| quantitative data | SVG chart | filters or controls justify self-contained HTML |
| technical schematic | SVG | a real image-generation tool is available and requested |
| interactive dashboard | self-contained HTML | never when a static visual communicates as well |
| photorealistic or illustrative image | image-generation MCP tool | fall back to an SVG concept diagram, not fake imagery |

## Surface rules

1. Prefer a fenced `mermaid` block for visuals that belong directly in chat.
2. Keep Mermaid diagrams readable: one idea, short labels, and normally no more
   than 15 nodes. Split a crowded diagram into overview plus detail.
3. Use Markdown tables for exact values and Mermaid for relationships. Do not
   force numbers into a flowchart.
4. When Mermaid is insufficient, create a standalone `.svg` with a viewBox,
   semantic groups, readable text, accessible contrast, and a title plus
   description. Tell the user the exact path.
5. Use standalone `.html` only for controls, animation, or linked views that a
   static SVG cannot express. Inline CSS and JavaScript; do not depend on a CDN.
6. Use Claude Code's `Artifact` tool only when it is present and the user wants a
   published interactive page. If unavailable, return the local HTML/SVG path.
7. Use `SendUserFile` with `display: render` only when that tool is present. Its
   absence is a presentation constraint, not a reason to omit the visual.
8. Use an image-generation MCP only when its tool is actually available. Never
   imply that a generated image exists when no image tool was called.

## Visual contract

Before drawing, extract:

- the question the visual must answer;
- the entities or measures shown;
- relationship direction, units, scale, and timeframe;
- provenance for factual values;
- assumptions and unknowns.

Then produce:

1. a one-sentence takeaway;
2. the visual;
3. a compact legend only when symbols or colors are not self-explanatory;
4. source or assumption notes for factual visuals;
5. an accessible text summary for clients that do not render the visual.

## Quality gates

- Labels describe domain concepts, not implementation placeholders.
- Directed edges point in the real direction of flow or dependency.
- Axes include labels and units; scales do not exaggerate differences.
- Colors are redundant with labels or shapes and remain distinguishable without
  relying on red versus green alone.
- Do not invent missing values to make a chart complete. Mark gaps explicitly.
- For data-derived visuals, reconcile plotted values with the source table before
  presenting them.
- For SVG or HTML, open or render the output and verify that text is not clipped,
  elements do not overlap, and the result works at a normal viewport.

## Failure Modes

| Mode | Definition |
| --- | --- |
| `DECORATION_OVER_INFORMATION` | attractive output that does not answer a concrete question |
| `WRONG_VISUAL_GRAMMAR` | using a relationship diagram for quantitative comparison, or the reverse |
| `VISUAL_HALLUCINATION` | adding unsupported nodes, values, geography, or causal links |
| `UNREADABLE_DENSITY` | one diagram contains too many labels or crossings to inspect |
| `CHANNEL_ASSUMPTION` | claiming inline rendering, Artifact publishing, or image generation without the required tool |
| `COLOR_ONLY_MEANING` | information disappears for monochrome or color-vision-deficient readers |
| `UNVERIFIED_RENDER` | returning SVG or HTML without checking its rendered layout |

## Completion

Done when the format matches the information shape, the visual answers one named
question, factual content is sourced or marked as assumed, an accessible text
summary is included, and any SVG or HTML output has been rendered and inspected.

Grounding: chapters/5-tool-use/2-tool-selection.md,
chapters/6-harnesses/7-designing-for-your-context.md,
chapters/8-practices/2-evaluation.md
