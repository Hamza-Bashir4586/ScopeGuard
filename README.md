# ScopeGuard

An AI assistant for construction subcontractors. ScopeGuard reads a project folder in Box (original scope /
subcontract, revised specs and written instructions, daily reports, timesheets, material receipts, labor rate
sheets, the existing change-order log), finds **potential extra work that is missing from the change-order log**,
assembles source-cited evidence and a cost breakdown, raises open questions (missing authorization, unclear scope,
conflicting records, missing rate or quantity), and drafts a change-request packet. The project manager reviews,
edits, confirms and exports the packet as a PDF saved back to Box.

**The LLM proposes, Python verifies, the human decides.** Flagged work never implies entitlement to payment, and
missing authorization stays an open question.

## Architecture

```
Next.js (web/, Amplify)  --HTTPS-->  API Gateway HTTP API  -->  ApiFunction (Python 3.12, arm64, 29 s)
                                                                  |  POST /scans  -> S3 meta.json (queued) + async invoke
                                                                  |  GET /scans/{id}, PATCH /findings/{id}, POST /packets (fpdf2 -> Box)
                                                                  v
                                                     WorkerFunction (900 s, 2 GB, no async retries)
                                                       Box (box-sdk-gen, CCG) -> pypdf text per page
                                                       -> Bedrock Converse (tool use = structured output, pydantic-validated)
                                                          1. per document: classify + extract facts with quotes/pages
                                                          2. all facts: group, compare to scope, match CO log, propose findings
                                                          3. per finding: narrative + timeline from verified facts only
                                                       -> Python verification -> S3 JSON
```

- API contract: [`docs/api-contract.md`](docs/api-contract.md) (binding for backend and frontend).
- Python verification (`backend/scopeguard/validate.py`): quotes are checked against the cited page text
  (whitespace, hyphenation, ligature and curly-quote tolerant; fuzzy ratio >= 0.95; wrong page numbers corrected),
  all money math uses `Decimal` with ROUND_HALF_UP, labor rates come only from rate-sheet/subcontract facts and
  material prices only from receipts, CO matches must exist in the extracted change-order log, and findings without
  verified written instruction get a `missing_authorization` question. Unverified evidence is shown greyed out
  and excluded from packets.
- S3 layout (`DATA_BUCKET`): `scans/<id>/meta.json`, `files/<box_file_id>.json`, `findings/<finding_id>/ai.json`
  (written only by the worker), `findings/<finding_id>/review.json` (created once, then written only by PATCH),
  `packets/<finding_id>/<ts>.json`. Finding IDs are a stable hash of
  scan ID + change key, so re-runs never duplicate findings or overwrite reviewer edits.

```
backend/
  api_handler.py        API Lambda (router + 4 endpoints)
  worker_handler.py     Worker Lambda
  local_server.py       stdlib dev server -> api_handler.handler
  scopeguard/           config, models, store, box_client, extract, llm, prompts, analyze, validate, packet, serialize
  tests/                pytest suite + fixtures/sample_project (fictional documents)
template.yaml           AWS SAM
samconfig.toml
web/                    Next.js frontend
demo-data/              put the fictional demo documents here (see its README)
```

## Local development (no AWS, no Box)

Backend (memory store, local folder instead of Box, deterministic fake LLM):

```bash
cd backend
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                      # unit + end-to-end tests

STORE=memory BOX_MODE=local LLM_MODE=fake .venv/bin/python local_server.py   # http://localhost:8000
```

- `LOCAL_DOCS_DIR` (default `demo-data/`, falling back to `backend/tests/fixtures/sample_project/` while
  demo-data has no documents). Local packets are written to `<dir>/ScopeGuard Packets/`.
- Modes can be mixed, e.g. `LLM_MODE=bedrock BEDROCK_MODEL_ID=... ` with local files and memory store if you have
  AWS credentials.
- The fake LLM produces the three demo scenarios on the fixtures: a new overlooked change with verified evidence and
  labor/material/markup costs, one already recorded in the CO log, and one that needs info.

Smoke test:

```bash
curl -s -X POST localhost:8000/scans -d '{}'
curl -s localhost:8000/scans/<scan_id>
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

Upload the demo documents from `demo-data/` to the Box folder before running a scan.

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
