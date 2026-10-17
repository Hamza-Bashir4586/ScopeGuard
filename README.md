# ScopeGuard

ScopeGuard helps construction companies audit whether recorded project work and changes have been accounted
for in billing and payment. It connects the agreement, work records, approvals, invoices, and payment evidence,
then explains gaps with source references.

The primary user is a construction company project manager working with accounting during milestone or final
closeout reviews. The customer is the building owner or general contractor that hired the company. The value
is reducing the work of reconstructing project history and identifying missed billing or unresolved compensation.

## Project document

- [Backend requirements and component plan](docs/backend-requirements.md): detailed implementation requirements for the approved hackathon flow, with no user authentication.
- [Team project overview](docs/project-overview.md): formatted copy of the
  [Google Doc](https://docs.google.com/document/d/1KT8B7j0jKlHshF5zzJ1tFn_yBeuwlTdnFEwFPxdmKjQ), synced October 17, 2026.
- Planned workflow: establish the signed baseline; gather project, billing, and payment history; run an audit
  for a cutoff date; review the evidence and uncertain matches; export a reviewed report or action packet.
- Keep approved but unbilled amounts, unresolved invoice balances, retainage, credits, and unauthorized work
  separate. Missing records do not prove that approval, billing, or payment never occurred.
- [Interactive team prototype](https://scopeguard-team-prototype.elvenspice11.chatgpt.site): the newer approved
  UI adds discrepancy cards, source-record overlays and categories, review comments, invoice selection, and an
  evidence-backed draft for customer sign-off. This UI flow is not yet reflected in the Google Doc snapshot.

### Current implementation

The backend implements the approved project-audit and invoice-draft flow (API contract:
[`docs/api-contract-v2.md`](docs/api-contract-v2.md), which supersedes [`docs/api-contract.md`](docs/api-contract.md);
requirement IDs refer to [`docs/backend-requirements.md`](docs/backend-requirements.md)). The Next.js frontend in `web/`
is being moved to the v2 contract separately.

**The LLM extracts, Python verifies and decides, the human reviews.** A finding never implies entitlement to payment;
approved, proposed, already billed, partially paid and retained amounts stay separate, and unknown amounts stay
unknown (`null`, never `0.00`).

## Architecture

```
Next.js (web/, Amplify) --HTTPS, no user auth--> API Gateway HTTP API --> ApiFunction (Python 3.12, arm64, 29 s)
   projects + seeded agreement baseline, record inventory/upload/content/pages (Box, version-pinned),
   POST /scans (lease + manifest + async invoke), reviews, invoice draft, generations (fpdf2) + save to Box
                                                    |
                                                    v  {"scan_id"} only
                                   WorkerFunction (900 s, 2 GB, no async retries)
   1. reading_box_records  pinned Box versions -> pypdf page text (cached per file version)
   2. connecting_evidence  Bedrock Converse tool use -> typed facts with verbatim quotes (pydantic-validated,
                           cached per file version + prompt version) -> Python quote verification -> link proposals
   3. checking_billing     reconcile.py: approvals, completion, quantities x rates, invoices, payments, credits,
                           retainage -> findings + existing items (Decimal math)
   4. findings_ready       results persisted, then the project's latest-scan pointer moves
```

Key modules (`backend/scopeguard/`):

| Module | Responsibility |
| --- | --- |
| `projects.py` | Configured projects (Riverside -> `BOX_FOLDER_ID`; two preview-only cards) and the seeded, reviewed agreement baseline whose sources resolve by filename to real file/version IDs [PRJ-01..05] |
| `records.py` | Inventory (output folder excluded), categories (AI > seeded > filename > unclassified), upload validation, pinned content, page text + highlights [DOC-*, VIEW-*] |
| `scans.py` | One-active-scan lease (`projects/<id>/active-scan.json`, S3 conditional writes), `request_id` dedup, immutable manifest, stale-run detection, scan/project read models [AUD-*] |
| `analyze.py` | Worker: execution lease (duplicate deliveries are no-ops), stages + real events, coverage, extraction cache [AUD-05..11, AI-*] |
| `reconcile.py` | Deterministic rules deciding `approved_unbilled`, `scope_change_proposed`, `authorization_unresolved`, existing balances/retainage/already-billed items, stable `work_item_id`s and evidence fingerprints [FIN-*, REV-06/07] |
| `reviews.py` | Project-level reviews and the invoice draft (revisioned, 409 `revision_conflict`), eligibility, derived `needs_revalidation` [REV-*, INV-01/02/11] |
| `invoice.py` | Immutable generation snapshot + "SUPPLEMENTAL INVOICE · DRAFT" PDF with evidence appendix; idempotent save to Box [INV-03..12] |
| `store.py` | `MemoryStore` / `S3Store` with ETag conditional writes (`IfNoneMatch` / `IfMatch`); S3 layout per requirements §8 |
| `box_client.py` | Box CCG adapter (box-sdk-gen) and `LocalSource` (BOX_MODE=local) with the same interface |
| `llm.py`, `prompts.py`, `models.py` | Bedrock Converse structured output (bounded retry) and the deterministic `FakeLLM` |

How the Riverside walkthrough is decided (all from verified quotes):

- **D-001 approved_unbilled $8,500.00** - CO-001 has a verified approval with an amount, a completion record references
  CO-001, and no invoice line in the supplied invoices references CO-001 ("not found in supplied records").
- **D-002 scope_change_proposed $1,600.00** - SI-009 requires 26 LED fixtures vs 24 in the signed scope; the quotation
  prices 2 x $800 all-in (no markup); customer acceptance is not recorded, so it stays a labeled proposal.
- **D-003 authorization_unresolved $900.00** - SI-008 asks for Saturday work, which the agreement excludes ("Weekend
  installation excluded"); 9 recorded hours x the Exhibit B rate of $100/h; no price acceptance, so the user must
  acknowledge the unresolved authorization before adding it to the draft.
- **Existing items, never new charges** - INV-015 $1,500 billed - $1,000 allocated = $500.00 unresolved balance;
  INV-014 $125,000 - $118,750 allocated = $6,250.00, identified as retainage (consistent with the 5% term);
  CO-002 already invoiced on INV-015. Selecting all three findings gives a $11,000.00 draft subtotal.

```
backend/
  api_handler.py        API Lambda (router; contract v2 + v1 POST /packets)
  worker_handler.py     Worker Lambda
  local_server.py       stdlib dev server -> api_handler.handler (multipart + binary responses, CORS)
  scopeguard/           modules above
  scripts/make_demo_pdfs.py   renders the Riverside fixtures into demo PDFs
  tests/                pytest suite + fixtures/riverside (fictional records, one text file per document)
template.yaml           AWS SAM (us-east-2)
demo-data/riverside/    generated demo PDFs (upload these to the Box folder)
web/                    Next.js frontend
```

## Demo data

The fictional Riverside record set lives in `backend/tests/fixtures/riverside/` (one `.txt` per document, pages
separated by form feeds). Render the PDFs the demo uses with:

```bash
backend/.venv/bin/python backend/scripts/make_demo_pdfs.py   # -> demo-data/riverside/*.pdf
```

Files: `Signed scope and agreement.pdf` (3 pages), `Exhibit B — Labor and pricing.pdf`, `CO-001 — Signed change
approval.pdf`, `Conference-room completion record.pdf`, `INV-014 — Original scope invoice.pdf`, `INV-015 — Lighting
upgrade invoice.pdf`, `SI-009 — Revised fixture quantity.pdf`, `Additional fixture quotation.pdf`, `SI-008 — Saturday
access instruction.pdf`, `Saturday crew timesheet.pdf`, `REM-2026-031 — Payment remittance.pdf`. For the live demo,
upload exactly these to the Box folder configured as `BoxFolderId` (the agreement baseline resolves records by these
filenames).

## Local development (no AWS, no Box)

```bash
cd backend
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                                   # unit + end-to-end tests
cd .. && backend/.venv/bin/python -m unittest backend.test_config && cd backend

STORE=memory BOX_MODE=local LLM_MODE=fake .venv/bin/python local_server.py     # http://localhost:8000
```

- `LOCAL_DOCS_DIR` defaults to `demo-data/riverside/` (falls back to the text fixtures if it is empty). Uploads and
  "Save to Box" write into that folder (outputs under `ScopeGuard Packets/`, gitignored), so point it at a scratch
  copy if you want the repository folder untouched.
- `LLM_MODE=fake` reproduces the walkthrough deterministically with verbatim quotes. Modes can be mixed, e.g.
  `LLM_MODE=bedrock BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-6 AWS_REGION=us-east-2` with local files and the
  memory store.
- Optional settings: `SCAN_DEADLINE_SECONDS` (stale-run deadline, default 960), `EXTRACTION_WORKERS` (parallel
  Bedrock extractions, default 4).

Smoke test (write JSON bodies to files; zsh `echo` mangles escapes):

```bash
curl -s localhost:8000/projects
curl -s localhost:8000/projects/riverside
curl -s -X POST -H 'content-type: application/json' -d '{"project_id":"riverside"}' localhost:8000/scans
curl -s localhost:8000/scans/<scan_id>
curl -s -F "file=@notes.txt" localhost:8000/projects/riverside/records
```

Frontend:

```bash
cd web
npm install
NEXT_PUBLIC_API_URL=http://localhost:8000 npm run dev    # or copy web/.env.example to web/.env.local
```

## Deploy (AWS SAM)

Prerequisites: AWS credentials, SAM CLI, Bedrock model access enabled for the chosen Claude model in the
region, and a Box Platform app using **Client Credentials Grant** (authorized by the enterprise admin, with access
to the project folder - e.g. collaborate the app's service account on the folder).

1. Build and deploy (first time, guided; Docker is not required - `sam build` uses the local python3.12):

   ```bash
   sam build
   sam deploy --guided      # stack "scopeguard", us-east-2 (the only region our AWS org allows); set BoxFolderId, BedrockModelId, AllowedOrigin
   ```

   `BedrockModelId` defaults to `us.anthropic.claude-sonnet-4-6` (cross-region inference profile); override it
   with `--parameter-overrides BedrockModelId=...` to use another model. Later deploys: `sam build && sam deploy`.

2. Store the Box credentials in the secret created by the stack, `scopeguard/box/ccg` in **us-east-2**
   (placeholder values until you do this; see "Box credentials" below):

   ```bash
   aws secretsmanager put-secret-value --region us-east-2 --secret-id scopeguard/box/ccg \
     --secret-string '{"BOX_ENTERPRISE_ID":"...","BOX_CLIENT_ID":"...","BOX_CLIENT_SECRET":"..."}'
   ```

3. Frontend on Amplify: set the environment variable `NEXT_PUBLIC_API_URL` to the stack output `ApiUrl`, then
   redeploy the Amplify app. Set the stack parameter `AllowedOrigin` to the Amplify domain for production CORS.

Upload the demo PDFs from `demo-data/riverside/` to the Box folder before running an audit. `sam build` vendors
`boto3>=1.35` (needed for S3 conditional writes) instead of relying on the Lambda runtime's copy. Generated invoice
PDFs are tagged as temporary in S3 and expire after a day via a bucket lifecycle rule; saved copies live in Box.

## Box credentials from AWS Secrets Manager

The Python backend reads one JSON secret. It lives in `us-east-2` (the only region our AWS
organization allows) with the name `scopeguard/box/ccg`; the SAM stack creates it with placeholders and populate these exact fields as strings:

```json
{
  "BOX_ENTERPRISE_ID": "",
  "BOX_CLIENT_ID": "",
  "BOX_CLIENT_SECRET": ""
}
```

Empty fields are rejected until populated. Keep real values in Secrets Manager,
not in this repository or the Next.js frontend.

### Local setup (PowerShell)

Run from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m backend.config
```

The backend automatically loads the root `.env` during local development.
It configures `AWS_PROFILE=default`, `AWS_DEFAULT_REGION=us-east-2`, and
`BOX_SECRET_ID=scopeguard/box/ccg`. Existing shell variables take precedence.
The actual `BOX_ENTERPRISE_ID`, `BOX_CLIENT_ID`, and `BOX_CLIENT_SECRET` values
are JSON fields inside that AWS secret, not local environment variables.
The `.env` file is ignored by Git; `.env.example` is the shareable template.
Lambda skips `.env` loading and uses its configured environment and execution role.

The AWS SDK uses your AWS login/profile. If the login has expired, refresh it
with the same AWS CLI login command you used to sign in. The check only prints
whether loading succeeded; it never prints credentials and does not call Box.

### Use in Python

```python
from backend.config import get_box_credentials

credentials = get_box_credentials()
# Use credentials.enterprise_id, credentials.client_id, and
# credentials.client_secret in the backend's Box CCG authentication code.
```

Reads are cached in memory for five minutes, including across warm Lambda
invocations. `get_box_credentials(force_refresh=True)` reloads immediately.
Values are excluded from the credentials object's representation. Do not log,
serialize, or return the credentials from API handlers.

### Lambda configuration

- Package `backend/` and its installed dependencies in the deployment artifact.
- Set `BOX_SECRET_ID` to the secret name or full ARN. Lambda supplies `AWS_REGION`.
- Use the Lambda execution role for AWS access; do not deploy local AWS profiles
  or AWS access keys.
- Grant the execution role `secretsmanager:GetSecretValue` on the actual secret
  ARN. A customer-managed encryption key also requires `kms:Decrypt` and an
  appropriate key policy.
- Organization service control policies must permit retrieval. An IAM allow
  cannot override an explicit organization deny.

This module loads configuration only. Box token creation, SDK client setup, API
handlers, and deployment are separate implementation steps. The Box CCG app
must be authorized in Box and have access to the intended project folder.
