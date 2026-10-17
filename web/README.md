# ScopeGuard web

Next.js (App Router, static export) frontend for ScopeGuard. Implements `../docs/api-contract.md`.

## Run

```bash
npm install
cp .env.example .env.local        # set NEXT_PUBLIC_API_URL
npm run dev                       # http://localhost:3000

# No backend? Use the in-browser mock API:
NEXT_PUBLIC_USE_MOCK=1 npm run dev
# Exercise the failed-scan state:
NEXT_PUBLIC_USE_MOCK=1 NEXT_PUBLIC_MOCK_FAIL=1 npm run dev
```

`npm run build` writes static files to `out/` (deployed by `amplify.yml` on AWS Amplify Hosting).
Set `NEXT_PUBLIC_API_URL` in the Amplify environment variables; it is baked in at build time.

## Layout

- `app/design/page.tsx` – styled project selection, dashboard, evidence viewer and invoice preview (`/design/`)
- `components/workspace/` – isolated presentation components and approved fictional preview fixtures
- `app/page.tsx` – dashboard (`/?scan=<id>`)
- `app/finding/page.tsx` – finding detail (`/finding/?scan=<id>&id=<finding_id>`)
- `lib/types.ts`, `lib/api.ts` – contract types and typed fetch client
- `lib/mock.ts` – mock API (fictional data)

## Design preview

Open `http://localhost:3000/design/` to review the pastel/frosted design independently of backend development.
Set `NEXT_PUBLIC_DESIGN_PREVIEW=1` locally to show that surface at `/` as well. The existing connected frontend,
API client, shared contract types and mock API are preserved when the flag is absent or `0`.

The design preview carries the approved Riverside project data and supports project navigation, a simulated audit,
discrepancy review, internal comments, invoice selection, category filtering, source-record overlays and a printable
invoice/evidence appendix. Preview state is stored only in this browser. Uploads and Box saves are explicitly simulated.
It makes no backend requests and introduces no new backend fields or endpoints. Live integration continues to follow
`docs/api-contract.md` and the already agreed `docs/backend-requirements.md`.

Backgrounds were supplied by the user and are bundled in `public/images/`. Manrope is self-hosted under
`public/fonts/` with its SIL Open Font License, so fonts and background assets do not require external runtime calls.
