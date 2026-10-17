<!-- Source: https://docs.google.com/document/d/1KT8B7j0jKlHshF5zzJ1tFn_yBeuwlTdnFEwFPxdmKjQ -->
<!-- Google Docs revision: AHj4eMTiBHa6D-CiKjDX01PuxX3x5MgixCcCeKln9kEjXrhwPa648w5YLKdf_1hoPRsx-jlHcvItL5dzkdAun3JJjK-tb704OkvJECCReWM -->
<!-- Synced: 2026-10-17. Product plan copied from the Google Doc; see README for current implementation and deployment settings. -->

# ScopeGuard — Reconcile Project Work, Billing, and Payment

## What it is

ScopeGuard helps construction companies audit whether recorded project work and changes have been accounted for in billing and payment. It connects the agreement, work records, approvals, invoices, and payment evidence, then explains gaps with source references.

## Who uses it and why

The primary user is a construction company project manager working with accounting during milestone or final closeout reviews. The customer is the party that hired the company: a building owner or general contractor. The owner or finance lead is the likely buyer.

On long projects, instructions, approvals, work reports, invoices, and payments accumulate across many records. A completed change may never reach an invoice; an invoice may be partially paid; or a balance may be explained by retainage or credits. ScopeGuard reduces the work of reconstructing that history and identifies items needing action.

## The workflow

- Establish the baseline: add the signed agreement, original scope, prices, payment terms, and existing change log.
- Build the history: add work reports, revised instructions, change approvals, invoices or pay applications, and payment/remittance records.
- Run an audit for a defined cutoff date: connect each work item through authorization, billing, and payment.
- Review findings: inspect the evidence, correct uncertain matches, explain deductions, and add missing records.
- Prepare the next action: export a reviewed reconciliation report, missing-billing worksheet, payment-query evidence packet, or change-request draft. Submission and payment collection stay in the existing workflow.

## What goes into it

- Signed contract, scope exhibits, referenced drawings/specifications, pricing, and payment/retainage terms.
- Revised instructions, work reports, timesheets, receipts, change logs, and signed approvals.
- Invoices/pay applications and their revisions, credit notes, payment records, remittance allocations, and documented deductions or disputes.

The user identifies which records are included and the audit cutoff date. Missing records mean the result is incomplete; absence from the supplied files does not prove an invoice, approval, or payment never existed.

## What the app shows

- Project selection: compact cards with project name, customer, dates, and audit findings awaiting review. Add project may be a placeholder in the demo.
- Project overview: explain the original agreement, show the available project history and audit cutoff, and provide Add documents and Run audit actions.
- Audit results: organize findings as approved but not found in billing, billed with payment unresolved, work awaiting authorization, or reconciled. Retainage, credits, partial payments, and uncertain matches remain explicit.
- Work-item detail: work performed → approval → invoice → payment, with amounts, dates, evidence passages, missing links, and reviewer corrections.
- Audit history and change log: retain reviewed findings and known changes; distinguish a draft, external approval, invoice status, and payment status.

## Value and decision boundaries

The value is faster, traceable identification of missed billing and unresolved compensation. Work performed is not automatically billable; an unpaid balance is not automatically overdue. ScopeGuard does not grant approval, determine legal entitlement, or guarantee recovery.

Report approved-unbilled amounts, invoice balances, documented withholding, and unpriced work separately. Never combine them into a single guaranteed recovery figure or count the same work twice.

## Hackathon demonstration

Use one fictional project with several changes and a small set of text-readable documents. Demonstrate an executed and approved change missing from billing, a partially paid invoice with an unexplained remainder, a legitimate retainage amount, and work lacking authorization. Include a fully reconciled control item.

Success: correct source-linked findings, accurate arithmetic, fewer reviewer corrections, and less time preparing the reconciliation report. Long project histories are the broader vision; the demo does not establish production-scale or multi-decade processing capacity.

## MVP tech stack and components

Planned demo architecture: Next.js frontend, Python backend, and AWS + Box hosted services. One configured project folder, one audit at a time, no user authentication. This section describes the intended build; the existing repository configuration loader alone does not implement or deploy the application.

| Component | Technology | Responsibility |
| --- | --- | --- |
| Frontend | Next.js on AWS Amplify Hosting | Project overview, audit findings, evidence review, corrections, and progress polling. |
| API routing | Amazon API Gateway HTTP API | Expose the Python endpoints to the frontend. |
| Backend | Python API Lambda | Manage audits and reviewer edits; invoke the worker; generate and upload reports. |
| Analysis worker | Python worker Lambda | Read Box history, extract and match records, and reconcile work through payment. |
| AI analysis | Amazon Bedrock via boto3 | Extract document facts, propose evidence links, and explain financial discrepancies. |
| Application data | Amazon S3 (JSON objects) | Store audit status, work-item links, allocations, findings, and reviewer decisions. |
| Documents | Box + official Python SDK | Store contracts, work records, approvals, invoices, payment evidence, and reports. |
| Credentials | AWS Secrets Manager + IAM roles | Keep Box credentials server-side and authorize AWS service access. |

### Processing flow

- Start audit: API Gateway calls the Python API Lambda, which writes a scan manifest to S3, invokes the worker Lambda asynchronously, and returns a scan ID.
- Extract and match: the worker reads Box records and calls Bedrock to structure facts and propose links across work, approvals, invoice lines, and payment records.
- Reconcile: Python validates source references and computes approved, billed, credited, paid, and withheld amounts. Ambiguous allocations require review. Persist progress and results as S3 JSON.
- Review and export: Next.js polls the API, shows the project context and audit findings, and saves reviewer corrections. Python generates the reviewed report and uploads a new output file to Box.

### Application components

- Dashboard and evidence review: project baseline, audit scope, work-to-payment timeline, discrepancy table, source passages, and editable matches and decisions.
- Extraction and matching: Bedrock reads document meaning and proposes work-item relationships despite different wording. Retain Box file/version IDs and page or passage references; do not silently accept ambiguous matches.
- Reconciliation engine: Python uses decimal arithmetic and explicit rules for partial payments, credits, retainage, approved change amounts, and revised/cumulative invoices. Invoice totals and individual payments must not be allocated twice.
- Review persistence: separate machine-generated audit results from human edits. Rerunning analysis preserves decisions and flags changed evidence for review.
- Report generator: export a reconciliation summary and evidence-backed next actions. Generate a change-request draft only when the finding requires one; keep authorization gaps visible.

### Backend API

- POST /scans — start a project audit with a cutoff date and selected Box record IDs.
- GET /scans/{scan_id} — return progress, audit coverage, summaries, and findings.
- GET /scans/{scan_id}/items/{item_id} — return linked work, approval, invoice, and payment evidence.
- PATCH /findings/{finding_id} — save corrected links, allocations, reviewer notes, and disposition.
- POST /packets — generate the reviewed audit report or selected action packet and return its Box link.

### Data and execution

S3 stores JSON under project and scan IDs: manifest/status, extracted work items, approval links, invoice lines, payment allocations, machine findings, reviewer edits, and Box output references. Each amount retains its currency and source. Keep raw results and human review in separate objects; use conditional writes to prevent lost updates. Source records and completed reports stay in Box.

Invoke the worker Lambda asynchronously; no SQS queue is needed for the demo. Use stable scan IDs, idempotent writes, and bounded processing so retries do not duplicate results or reports. Keep each scan within a single worker invocation. Larger archives will need incremental ingestion and processing beyond this demo.

Python libraries: boto3 for AWS, an official Box SDK, a PDF text extraction library, and a PDF generation library. AWS IAM roles authorize service access; Box credentials stay on the backend.

### Build sequence

Build in this order: retrieve Box records; extract work/approval/invoice/payment facts; match records with source references; verify reconciliation rules against the fictional case; show findings and accept corrections; export the report to Box; connect progress polling and AWS deployment.

Initial inputs: text-readable PDFs or plain text. No OCR, vector database, automatic emails, job queue, or dedicated logging setup is needed for the demo.

### Box CCG configuration

AWS Secrets Manager secret: scopeguard/box/ccg in us-east-2. Required JSON keys: BOX_ENTERPRISE_ID, BOX_CLIENT_ID, BOX_CLIENT_SECRET. Store credential values only in Secrets Manager.

Local development uses the ignored .env for AWS_PROFILE=default, AWS_DEFAULT_REGION=us-east-2, and BOX_SECRET_ID=scopeguard/box/ccg. Lambda uses its execution role and configured secret ID. The role needs secret retrieval permission, with organization policies permitting access. The Box CCG app must be authorized and have access to the project folder.

Account recovery codes and live credentials do not belong in this shared document. Any previously pasted recovery codes should be regenerated in Box; removing this text does not erase revision history.

