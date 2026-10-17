# ScopeGuard

## Box credentials from AWS Secrets Manager

The Python backend reads one JSON secret. Create it in `us-east-1` with the name
`scopeguard/box/ccg` and populate these exact fields as strings:

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
It configures `AWS_PROFILE=default`, `AWS_DEFAULT_REGION=us-east-1`, and
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
