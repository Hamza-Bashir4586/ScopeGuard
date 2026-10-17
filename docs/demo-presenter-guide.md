# Riverside demo: presenter guide

## The demo to show

1. Open Riverside and the original agreement: $125,000 fixed price, 20% deposit, weekend work excluded,
   and written change approval required.
2. Run audit. It reads the 15 Box PDFs, uses Bedrock to extract/link evidence, and uses Python to verify
   quotes, calculate amounts, and decide eligibility. Box AI is not used.
3. Review D-001 ($8,500), D-002 ($1,600), and D-003 ($900), opening the source overlays.
   The SI documents contain written work instructions; their signatures alone do not accept the quoted prices.
4. Add these three findings to the draft. Acknowledge the pricing uncertainty on D-003. The draft subtotal
   is $11,000, prepared for customer review/sign-off.
5. Open D-004: corridor receptacles have a $1,350 quotation and a field record, but no matched authorized
   change. Its quotation is shown for context; the item is excluded and Add to invoice is disabled. Dismiss it.
6. Generate/download the $11,000 draft and evidence appendix, optionally save it to Box. Nothing is sent.

## What to say

"Over a long construction project, requested changes, field records, and invoices end up scattered.
ScopeGuard connects the agreement to the work and billing evidence, so the contractor can review missed
billing and prepare a supported supplemental invoice without reconstructing the entire project by hand."

"It also catches work that should not simply become a new charge. This corridor quotation looks billable,
but the authorization is missing, so it stays out of the total."

## Numbers and wording

- D-001: signed CO-001 supports $8,500; completion is recorded and matching billing is not found in the supplied invoices.
- D-002: written SI-009 changes 24 fixtures to 26; the $1,600 price is a proposal until accepted.
- D-003: signed SI-008 requests Saturday work; 9 total hours x $100/h supports a $900 price proposal.
- D-004: $1,350 quoted, excluded. The app does not assert it is payable or definitely outside the signed scope.
- CO-002: $1,500 already billed on INV-015, so it is not added again.
- INV-014: $100,000 net due after its deposit credit - $60,000 paid = $40,000 existing balance.
- No allocated payment for INV-015 is documented. Do not present $41,500 as a confirmed open balance without
  that additional evidence. If the fictional scenario assumes INV-015 is wholly unpaid, label that assumption.
- Likewise, $137,500 revised contract value and $52,500 total owed are conditional on accepting the two price
  proposals and assuming INV-015 remains unpaid. The main demo can focus on the separate $11,000 draft.
- Absence from the supplied records does not prove something never happened elsewhere.

## Rehearsal reset

Use **Reset demo** in Riverside's header, then confirm. It clears review statuses/comments, pricing
acknowledgments, invoice selections, and the current audit pointer. It returns to Projects; opening Riverside
shows the agreement and Run audit, with no previous findings or selected items.

All Box inputs, extraction caches, historical audit outputs, and saved invoice PDFs remain. Previous review/draft
state is archived under `projects/riverside/demo-resets/` in S3. Reset is enabled only through
`EnableDemoReset=true` / `DEMO_RESET_ENABLED=true`, rejects an active audit, and checks the draft revision.
Because this is a shared no-auth demo, reset affects everyone using Riverside. Do not reset during a teammate's run.

## Optional upload moment

All 15 files are already in Box. Do not pretend an existing record is a new upload. For a live upload moment,
prepare an additional fictional record or explicitly arrange a smaller starting dataset beforehand. Reset does
not remove or hold back files. Repeated audits reuse cached extraction for unchanged Box versions honestly.
