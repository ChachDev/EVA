# EVA review desk

- Style: Framed sheet, seed 1628491281. No redraws.
- Use: a quality auditor reads a conversation and checks each criterion against evidence.
- Question: why did this conversation receive its score?
- First answer: the transcript and criteria appear side by side; the score never obscures its source.
- Face: blue-grey working frame, white sheet, IBM Plex Sans bundled locally, a simple EVA stroke mark.
- Signature: every criterion can reveal message IDs pointing into the actual transcript.
- Objects: one synthetic chat, 4 messages, one versioned checklist, 8 criteria, one manually authored example evaluation.
- Data source: contracts/examples, read-only. Counts are computed; scores are supplied by the contract, never recomputed in the browser.
- No charts: a chart would duplicate the assessment. Empty, loading, failed and unassessed states stay explicit.
- Next work is text, not fake navigation or buttons. The main real action displays the known demo result or requests the backend evaluation.
- Canvas: width = transcript 1fr + 24px + criteria 1.1fr. Tablet stacks panes; phone folds the sidebar into a compact form.
- Motion: control feedback only, reduced-motion respected.

## Final visual review, 2026-11-07

All six saved screenshots (fold and page at 1440, 768, 390) were opened after the last styling change. Inspector: 0 FAIL. The one remaining warning, `no-data-hue`, treats the small SVG logo as a chart; this screen intentionally contains no chart. Lint: 0 FAIL; the dash warning comes from honest missing-value placeholders plus two prose dashes.

1. The main job is visible in the title action; transcript and criteria carry most of the desktop canvas. The only large figure is the assessment, with no invented metric.
2. The two panes use the full sheet width with the same 24px gap. Their content lengths are close without forcing empty card space.
3. Blue-grey message surfaces distinguish operator messages. Warning colour communicates the synthetic or unassessed state. The wordmark and frame give the screen a consistent face.
4. Message and criterion counts come from the displayed arrays. Evaluation totals and normalized contributions come directly from scoring, not duplicate frontend arithmetic. No comparative figures or charts exist.
5. Missing totals remain a dash; unresolved flags remain unknown. The demo assessment is explicitly a manually authored sample, not a measured model result.
6. There are no legends or charts. Criterion evidence links point to actual message IDs and focus the corresponding transcript entry.
7. On tablet and phone, the sidebar folds into selectors and the content becomes one column without horizontal overflow. The phone uses 44px controls. Future work can reduce travel between long transcripts and evidence with a dedicated pane switcher; this starter uses document flow.

Manual browser verification: demo loading, sample evaluation, expandable reasoning, evidence navigation, refresh/reset; HTTP loading and evaluation through Backend and ML stub. HTTP displays `needs_review` for all eight criteria, unknown total and gross-error flag, and the explicit STUB_PROVIDER warning. Browser console had no errors on the HTTP flow.
