# Support Topic OBOB

Date: 2026-03-20

Scope:
- Support-topic routing and ranking
- Keep support main quotas ahead of surface-process quotas
- Reduce manual review by making support results converge earlier

Discussion summary:
- Support items often contain multiple quota signals: main support work, rust removal, painting, and measure text.
- The main problem is not only retrieval. It is also that support-specific reranking does not consistently protect the main quota.
- For explicit support items, the system should treat rust removal and painting as attached process noise during main-quota selection.
- Generic support text should stay conservative. If the item does not clearly say pipe support, bridge support, or equipment support, the matcher should avoid forcing a family.

Implementation decisions:
- In support-family reranking, skip support candidates that are process-only surface-treatment quotas.
- Extend support-family reranking to handle explicit equipment-support items.
- Extend support query routing to explicit equipment-support items, but only when the item name itself is a support item, so device main items are not hijacked.
- Extend support query routing to explicit bridge-support items, so bridge support no longer depends on generic fallback terms.
- Keep bridge/duct/pipe system hints inside aseismic-support queries, reducing cross-system collisions inside the same aseismic family.
- In plain pipe-support reranking, explicitly down-rank bridge-support candidates, preventing high lexical similarity from stealing pipe-support top-1.

Expected outcome:
- Pipe/equipment support main quotas become more stable.
- Rust-removal and painting quotas stop stealing top-1 in support-topic cases.
- Bridge-support and aseismic-support retrieval becomes less dependent on rerank-only correction.
- Manual review can shift from item-by-item checking to grouped review on the remaining high-risk cases.
