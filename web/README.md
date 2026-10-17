# ScopeGuard web

Next.js App Router frontend, statically exported for AWS Amplify Hosting. The default workspace consumes
[`docs/api-contract-v2.md`](../docs/api-contract-v2.md). No user authentication is required for the hackathon.

## Run

```bash
cd web
npm install
cp .env.example .env.local
npm run dev
```

In PowerShell, use `Copy-Item .env.example .env.local`. Set `NEXT_PUBLIC_API_URL` to the running backend
(default `http://localhost:8000`), then open `http://localhost:3000/`. Projects and all workflow state come
from the backend. An unavailable API shows a retryable error, never silently substituted mock records.

For the local Python backend, run from the repository root in a second PowerShell terminal:

```powershell
$env:STORE = "memory"
$env:BOX_MODE = "local"
$env:LLM_MODE = "fake"
.\.venv\Scripts\python.exe backend/local_server.py
```

Install `backend/requirements.txt` into that virtual environment first (see the root README).
Set `NEXT_PUBLIC_LOCAL_DEMO=1` in `web/.env.local` for this mode. The UI then labels fictional records,
deterministic analysis, and local output saves accurately. Browser reloads preserve API-saved reviews;
restarting a memory-store backend clears them. Use `NEXT_PUBLIC_LOCAL_DEMO=0` for AWS/Box/Bedrock.

## Connected workflow

- `/` loads project cards; `/?project=riverside` opens the project dashboard and signed agreement.
- Run/refresh audit submits a cutoff date and polls real backend stages, events, and coverage.
- Review source-linked discrepancies, save internal comments, dismiss flags, and select invoice items.
- Changed evidence requires reconfirmation. Unresolved authorization requires acknowledgment and stays
  labeled as a proposal. Revision conflicts reload server state and ask the user to review it.
- Source records use backend categories. The centered viewer displays pinned page text with verified
  passages highlighted, page navigation, an optional original PDF view, and original-file download.
- Upload PDF/TXT/Markdown records one file per request (4 MiB limit), then explicitly run the audit.
- Invoice selection, eligibility, and subtotal come from Python. Generation produces an immutable PDF
  with its evidence appendix; download and save-to-Box call the actual backend endpoints.
- Already billed balances and retainage remain separate from new invoice charges. Nothing is sent to
  the customer; sign-off is a draft document, not an electronic-signature service.

Only Riverside is enabled by the current backend manifest. Add project and the other project cards
remain visual placeholders as planned. API credentials are never included in the browser.

## Layout

- `app/page.tsx` — v2 connected workspace
- `components/workspace/ConnectedWorkspace.tsx` — approved styled project and invoice workflow
- `components/workspace/useProjectWorkspace.ts` — API state, polling, revisions, and mutations
- `components/workspace/SourceRecordViewer.tsx`, `WorkspaceModal.tsx` — source review and centered dialogs
- `lib/workspace-types.ts`, `lib/workspace-api.ts` — v2 contract types and typed HTTP client
- `app/design/page.tsx`, `components/workspace/ScopeGuardWorkspace.tsx` — separate fictional design preview
- `app/finding/`, `components/Dashboard.tsx`, `components/FindingDetail.tsx`, `lib/api.ts`, `lib/types.ts`,
  `lib/mock.ts` — retained legacy v1 surface; not used by the new project workflow

## Design preview

`/design/` is independent of the backend. `NEXT_PUBLIC_DESIGN_PREVIEW=1` also displays it at `/`.
Its simulated audit, browser-local reviews, printable invoice, uploads, and Box saves are fictional and
do not synchronize with the connected workspace. `NEXT_PUBLIC_USE_MOCK` and `NEXT_PUBLIC_MOCK_FAIL`
apply only to the retained v1 client; they do not enable mocks in the v2 workspace.

The supplied pastel backgrounds live in `public/images/`. Manrope is self-hosted in `public/fonts/`
with its SIL Open Font License. Frosted surfaces, a restrained dot overlay, pale red unreviewed cards,
and centered dialogs carry the approved design into the connected flow.

## Build and deploy

`npm run build` writes static files to `out/`; the repository-root `amplify.yml` builds these on AWS
Amplify Hosting using `appRoot: web`. `web/amplify.yml` mirrors that configuration.
Set `NEXT_PUBLIC_API_URL` to the stack's `ApiUrl` **before building**; public Next.js variables are baked
into the exported bundle. Use `NEXT_PUBLIC_DESIGN_PREVIEW=0` and `NEXT_PUBLIC_LOCAL_DEMO=0` in AWS.
Configure the backend `AllowedOrigin` for the Amplify origin. AWS region is **us-east-2**.

Live Box/Bedrock calls depend on the deployed backend configuration. See
[`docs/deployment.md`](../docs/deployment.md) for the deployed URLs, environment wiring, and team handoff.
