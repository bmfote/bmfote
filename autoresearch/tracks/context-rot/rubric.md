# Context-Rot Track — Judge Rubric

Score each proposed problem definition on three axes. Integer scores only (1-10). Lower tier if unsure.

## Axis 1: Legibility (weight 0.35)

Can a non-technical buyer understand what context rot is after reading this? Does it name the failure mode without jargon?

| Tier | Score | Criteria |
|------|-------|----------|
| Bottom | 1-2 | Technical jargon (tokens, embeddings, RAG, vector), only an ML engineer would understand. Names technology, not pain. |
| Low | 3-4 | Somewhat accessible but uses AI-insider language. A VP of Ops would need a glossary. |
| Mid | 5-6 | Clear language, names a real problem, but abstract. "AI tools lose context" — true but not vivid. |
| High | 7-8 | Vivid and specific. Names a concrete scenario. A buyer who uses AI tools weekly would recognize it immediately. |
| Top | 9-10 | One-sentence definition a skeptical VP of Ops would nod at. Names what breaks, when, and what it costs — in plain English. Would work as a LinkedIn post headline. |

## Axis 2: Economic concreteness (weight 0.35)

Does it quantify the cost? Not "productivity loss" but specific numbers a CFO could put in a spreadsheet.

| Tier | Score | Criteria |
|------|-------|----------|
| Bottom | 1-2 | No cost mentioned, or purely qualitative ("bad," "frustrating," "inefficient"). |
| Low | 3-4 | Vague cost language ("wasted time," "reduced productivity," "lower accuracy"). No numbers. |
| Mid | 5-6 | One number but generic (e.g., "hours per week" without saying how many, or "X% less accurate" without grounding). |
| High | 7-8 | Specific numbers tied to a workflow (e.g., "8 minutes per re-explanation × 12 times/week"). Plausible and verifiable. |
| Top | 9-10 | Full cost chain: frequency × duration × rate = dollar impact. A CFO could build an ROI model from this. Numbers are grounded in the named persona's reality. |

## Axis 3: Inevitability (weight 0.30)

Does it feel structural and worsening? Or could you fix it with better training, note-taking, or discipline?

| Tier | Score | Criteria |
|------|-------|----------|
| Bottom | 1-2 | Sounds like user error. "People forget to save context" — fixable with training. |
| Low | 3-4 | Real problem but seems static. "AI tools don't share memory" — true today but doesn't feel like it's getting worse. |
| Mid | 5-6 | Structural but person-scoped. "Each new session starts from scratch" — true but doesn't compound across the organization. |
| High | 7-8 | Organizational ceiling that worsens with adoption. More AI tools = more silos = more rot. Names the compounding mechanism. |
| Top | 9-10 | Frames context rot as an inevitability of the current architecture, not a feature gap. The more you use AI, the worse it gets — until the architecture changes. Cites evidence (Chroma/dbreunig/Spotify). |

## Counter-narrative requirement

Every proposal MUST name a specific "obvious fix" and explain why it fails. Valid targets:
- "Just use a bigger context window"
- Anthropic memory_stores
- Per-seat licenses (ChatGPT, Copilot)
- "Just take better notes" / prompt engineering
- File-based memory (CLAUDE.md, .cursorrules)

**Graduated scoring adjustments:**
- Missing counter-narrative entirely → cap all axes at 3
- Named but no evidence cited → cap all axes at 5
- Named with evidence but generic ("bigger windows don't help") → no cap, but score inevitability conservatively
- Named with specific evidence citation (practitioner quote or paper) → no cap, score normally

Note: these are graduated, not binary. Partial credit for partial effort.

## Anti-pattern words

Penalize ALL axes by -1 if 3 or more found in any text field:
`powerful`, `seamless`, `intelligent`, `advanced`, `robust`, `next-generation`, `cutting-edge`, `enterprise-grade`, `platform`, `ecosystem`, `orchestration`, `engine`, `solutions`, `capabilities`, `offerings`, `empower`, `enable`, `streamline`, `transform`, `leverage`, `unlock`, `unified`, `holistic`, `end-to-end`, `scalable`

## Solution-pitching penalty

This track defines the PROBLEM, not the solution. If the proposal pitches cctx, bmfote, or any specific product as the answer, cap legibility at 4. The strongest problem definitions make the buyer want a solution without naming one.

## Composite score

```
composite = 0.35 × legibility + 0.35 × economic + 0.30 × inevitability
```

## Promotion gate

- `composite >= 8.0`
- `min(legibility, economic, inevitability) >= 6`
- Both conditions must hold
