# ScopeGuard — repo structure (Jimmy)

Not a file-by-file tour — a lookup map: "if you need to look something up, where do you go."
Builds on `00-overview.md`. Filled in top-down as we go.

## Top level

| Path | What it is |
| --- | --- |
| `README.md` | Project overview, local dev + deploy instructions |
| `LICENSE` | — |
| `template.yaml` | SAM: all backend AWS infra (Lambdas, S3 bucket, Secrets Manager secret, HTTP API) |
| `samconfig.toml` | SAM deploy config (stack name, region, parameters) |
| `docs/` | API contract: the shared source of truth between backend and frontend |
| `backend/` | Python: the two Lambdas + the `scopeguard/` pipeline package |
| `web/` | Next.js frontend, static export, deployed to Amplify |
| `demo-data/` | Where to put fictional demo documents for local `BOX_MODE=local` runs |
| `notes/` | These notes |

Not part of the ScopeGuard project itself — local Claude Code tooling/config, left alone:
`.agents/`, `.claude/`, `skills-lock.json`.
