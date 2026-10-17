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

- `app/page.tsx` – dashboard (`/?scan=<id>`)
- `app/finding/page.tsx` – finding detail (`/finding/?scan=<id>&id=<finding_id>`)
- `lib/types.ts`, `lib/api.ts` – contract types and typed fetch client
- `lib/mock.ts` – mock API (fictional data)
