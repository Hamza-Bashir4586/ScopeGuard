# Deployment and team handoff

## Live endpoints

- Website: https://main.d3aoo1sj9tvhat.amplifyapp.com/
- API: https://bvgm3lzxde.execute-api.us-east-2.amazonaws.com
- Region: us-east-2
- CloudFormation stack: scopeguard
- Amplify app: d3aoo1sj9tvhat / branch main
- Riverside Box folder: 428359026380

These are deployment identifiers, not credentials. Box CCG credentials remain in Secrets Manager
(`scopeguard/box/ccg`); the frontend receives no Box credentials or AWS credentials.

## Connection

Amplify serves the static Next.js export. Its browser client calls API Gateway, which invokes the API
Lambda. Audits invoke the worker Lambda asynchronously. The backend reads version-pinned records from
Box, extracts facts using Bedrock, verifies and reconciles them in Python, and saves audit/review/draft
state to S3. Generated invoice PDFs can be downloaded or saved into Box's ScopeGuard Packets folder.
No user authentication is required for this hackathon deployment.

## Amplify build

The canonical build specification is `amplify.yml` in the repository root. It selects `appRoot: web`,
runs `npm ci` and `npm run build`, and deploys `web/out`. `web/amplify.yml` is a matching copy.
GitHub main is connected with automatic builds enabled. There is no separate frontend server Lambda.

App environment variables:

| Variable | Value |
| --- | --- |
| AMPLIFY_MONOREPO_APP_ROOT | web |
| AMPLIFY_DIFF_DEPLOY | false |
| NEXT_PUBLIC_API_URL | https://bvgm3lzxde.execute-api.us-east-2.amazonaws.com |
| NEXT_PUBLIC_DESIGN_PREVIEW | 0 |
| NEXT_PUBLIC_LOCAL_DEMO | 0 |
| NEXT_PUBLIC_USE_MOCK | 0 |

Next.js embeds these values when it builds: changing them requires a new build. Local `.env.local`
is gitignored and is not the deployment configuration. `/design/` remains a separate fictional preview.

The backend stack's AllowedOrigin is https://main.d3aoo1sj9tvhat.amplifyapp.com. It is applied to API
Gateway CORS and the API Lambda configuration. `samconfig.toml` preserves that origin for future deploys.
CORS determines permitted browser origins; it is not user authentication.

## Backend deployment

The deployed Lambdas use STORE=s3, BOX_MODE=box, and LLM_MODE=bedrock. The model is
us.anthropic.claude-sonnet-4-6. Both use the configured Riverside Box folder and Secrets Manager secret;
the API also has the worker function name. Deployment stack outputs are the source for the API URL,
data bucket, secret ARN, and worker name.

Use the existing SAM template for future backend code deployments (`sam build`, then `sam deploy` in
us-east-2). Frontend pushes do not deploy Python Lambda code. Do not replace the existing secret with
placeholder values or recreate resources already managed by the scopeguard stack.

## Replacement Riverside records

On October 17 the team replaced the initial 11 files with 15 Riverside PDFs. The replacement agreement
uses a $25,000 deposit (20%), completion billing, and net-30 terms. The seeded overview now matches those
terms; filename matching accepts typographic dash differences in the pricing exhibit. Historical local
fixtures and the independent /design/ preview still describe the earlier fictional scenario.

- Upload the replacement input documents directly into folder 428359026380; inventory is non-recursive.
- The signed baseline must be named `Signed scope and agreement.pdf` (or the same stem in TXT/Markdown).
- Preserve the `Exhibit B - Labor and pricing` stem for the pricing exhibit; common dash styles are accepted.
- Project name, customer, agreement summary, and baseline terms are seeded in
  `backend/scopeguard/projects.py`; reconcile those with any changed replacement agreement.
- Use readable PDF text, TXT, or Markdown. Image-only scans do not have an OCR path in this MVP.
- Current limits: 20 supported records per scan, 4 MiB per file, 40 MiB and 200 pages per dataset.
- Generated output stays in ScopeGuard Packets and is excluded from input inventory.
- After upload, refresh the site and run Refresh audit with the intended cutoff date. Review changed
  evidence and invoice selections before generating a new customer draft.

## Release walkthrough

Use the live Amplify site to open Riverside, view the agreement and source pages, run an audit, review
findings, save an internal comment, select eligible items, and generate/download the evidence-backed PDF.
Check proposal labels and authorization acknowledgments. Save the final demo draft to Box only after
confirming it uses the replacement documents. Nothing sends an invoice or captures an electronic signature.

The replacement dataset is processed with real Box, Bedrock, and S3. Reconciliation version v2.2 avoids
deducting a printed deposit twice when the invoice net total already includes it. Additional field work
without a matched instruction is surfaced as an unpriced needs-information finding, rather than silently
ignored or assumed approved. Changed source evidence invalidates earlier reviews, even for selected items.

Expected replacement scenario:

- CO-001: $8,500 approved change, completion recorded, billing not found in supplied invoices.
- SI-009: $1,600 fixture proposal requiring customer acceptance.
- SI-008: 9 total hours at $100/h = $900, authorization unresolved. Individual worker rows are not added
  again to the printed timesheet total.
- Corridor receptacles: field record and possible $1,350 quotation need scope/approval/billing review;
  no invoice amount is assigned automatically.
- INV-014: $100,000 net amount due after the deposit, less $60,000 payment = $40,000 existing balance.
  The $25,000 deposit is not subtracted twice.
- CO-002: $1,500 already billed on INV-015; not a new charge. INV-015 payment status remains unknown
  without allocated payment evidence.

The three priced findings total $11,000 only after the user reviews/reconfirms them. The corridor item
is excluded until supported. Existing balances are separate from new charges. API changes require no
frontend/backend contract changes. The backend release is deployed through the existing CloudFormation
stack with its existing runtime dependencies; future SAM builds use the committed source.

## Reset and presenter guide

The live fictional Riverside deployment enables the archived **Reset demo** action. It returns to the initial
Run audit workflow, keeping all Box files and saved PDFs. See [demo-presenter-guide.md](demo-presenter-guide.md)
for the $11,000 walkthrough, decoy, proposal disclosures, and limits on the full-ledger story totals.
