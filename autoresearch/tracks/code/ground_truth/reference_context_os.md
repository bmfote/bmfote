# Reference: gtm-context-os-quickstart

External open-source project for compounding AI context, by Jacob Dietle.
Different architecture from cctx but same problem space. Study for inspiration,
not imitation. Source: https://github.com/jacob-dietle/gtm-context-os-quickstart

## What it does

Two-layer architecture: a `knowledge_base/` of atomic concept nodes (YAML
frontmatter, linked via `[[wiki-links]]`, lifecycle states) and `00_foundation/`
operational docs that synthesize from the graph. Agents coordinate by modifying
the shared environment ("stigmergic design") rather than following rigid rules.

Key concepts:
- **Lifecycle**: `emergent → validated (2+ citations) → canonical`
- **Heat tracking**: measures which files get accessed most — unused knowledge
  decays, frequently-used patterns become the organizational backbone
- **Co-access analysis**: tracks which files are read together
- **SENSE → ORIENT → ACT → DEPOSIT loop**: every interaction reinforces the graph
- **Eval-as-specification**: an eval script tests whether docs produce correct
  agent behavior using the Claude Agent SDK

## Patterns to consider for cctx

When proposing improvements, consider whether any of these patterns could make
cctx's one-table-one-file approach stronger — without adding the complexity of
a full knowledge graph.

1. **Structured metadata**: their nodes have YAML frontmatter (status, tags,
   domain, relationships). Our `messages` table has minimal metadata beyond
   content, type, and timestamp. Could a `tags` column or structured topic
   field improve FTS recall quality?

2. **Lifecycle/maturity states**: their concepts progress based on citation
   count. Our messages are flat. Should frequently-retrieved memories influence
   BM25 ranking (e.g., a `recall_count` column as a boost signal)?

3. **Heat tracking**: they know which knowledge gets used. We have no read-side
   telemetry. A `last_accessed` timestamp or `hit_count` on messages would be
   minimal (one UPDATE per search hit) and could inform future ranking.

4. **Relationships**: they use `[[wiki-links]]` between concepts. Our messages
   are isolated rows connected only by `session_id` and `parent_uuid`. Is there
   a lightweight way to link related memories without a graph DB?

5. **Eval as first-class artifact**: they test whether docs produce correct
   agent behavior. We have no equivalent for testing whether our MCP tools
   return useful recall results. Could `query_search` have an eval set?

## Constraint

Any improvement inspired by these patterns must pass the minimalism axis:
cctx's moat is "SQLite file, FTS, hooks, 9 REST endpoints." If a Context OS
pattern requires a new table, a new dependency, or more than ~20 lines of code,
it probably violates the thesis. The leanest adaptation wins.
