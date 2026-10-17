# demo-data

Put the **fictional demo project documents** for ScopeGuard here (`.pdf`, `.txt`, `.md`): subcontract / scope,
labor rate sheet, GC instructions (emails, ASIs, RFI responses), revised specs, daily reports, timesheets,
material receipts, and the existing change-order log.

- Local dev (`BOX_MODE=local`) reads this folder non-recursively. This README is ignored. Packets generated
  locally are written to `demo-data/ScopeGuard Packets/` (gitignored).
- While this folder has no documents, the local server falls back to the sample fixtures in
  `backend/tests/fixtures/sample_project/`.
- For the deployed app, upload the **same files** to the Box folder configured as `BoxFolderId`
  (ScopeGuard creates a `ScopeGuard Packets` subfolder there for exported PDFs).

Use only fictional names, companies and amounts.
