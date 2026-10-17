# demo-data

Fictional demo records for ScopeGuard. Use only fictional names, companies and amounts.

- `riverside/` - the Riverside Office Renovation record set as text-readable PDFs, generated from
  `backend/tests/fixtures/riverside/*.txt` by `backend/scripts/make_demo_pdfs.py`. Re-run the script after editing a
  fixture.
- Local dev (`BOX_MODE=local`) reads `demo-data/riverside/` non-recursively by default (`LOCAL_DOCS_DIR` overrides it).
  Uploads and locally saved invoice packs are written into that folder (`ScopeGuard Packets/` is gitignored).
- For the deployed app, upload the **same files, with the same names** to the Box folder configured as `BoxFolderId`.
  ScopeGuard saves generated invoice packs in a `ScopeGuard Packets` subfolder, which is never treated as input.
