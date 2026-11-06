# Interface direction

Purpose: an engineer's working desk for a changing case queue. The queue and next customer commitment matter more than a portfolio KPI.

Palette: ink `#172d40`, steel `#5b7082`, fog `#f3f6f8`, paper `#ffffff`, maritime blue `#216b96`, good green `#247259`. Amber and red are reserved for attention and urgent case risk.

Typography: locally installed Avenir Next with system sans fallbacks; tabular numbers for scores and time. No remote font or analytics requests.

Layout: a narrow navigation rail, a searchable case queue, and a large case workspace. The workspace has a restrained blue case header, an actionable next-step strip, a compact score grid, and evidence/draft tabs. The queue is a continuous list, not a set of repeated floating cards. On small screens, queue and detail become separate full-width views.

```
navigation | case queue          | case / status / updated time
           | search / scope     | next action / owner / due
           | account / concern  | score strip / history
           | account / concern  | evidence | drafts | activity
```

Review: a generic statistics hero would delay the daily task, so the primary surface is the selected case. Score colors express the agreed rubric (8+ good), while freshness and missing data get their own text labels. External actions are represented by editable, copyable drafts and evidence links. No send buttons.
