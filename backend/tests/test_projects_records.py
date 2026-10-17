"""Projects, seeded baseline before audit, records inventory/categories/upload/content/pages."""

from tests.conftest import by_code

from scopeguard.box_client import LocalSource


def test_project_cards_before_audit(local_env, api):
    status, body = api("GET", "/projects")
    assert status == 200
    cards = body["projects"]
    assert [c["code"] for c in cards] == ["SG-024", "SG-025", "SG-019"]
    riverside, harbor, cedar = cards
    assert riverside == {
        "project_id": "riverside", "code": "SG-024", "name": "Riverside Office Renovation", "contractor": "Summit Electrical",
        "customer": "Northline Builders", "contract_label": "Electrical contract", "start_date": "2026-10-05",
        "end_date": "2026-11-20", "status": "active", "preview_only": False, "review_label": "Not audited yet",
        "to_review_count": None,
    }
    assert harbor["preview_only"] and harbor["customer"] == "Beacon Construction" and harbor["start_date"] == "2026-09-14"
    assert cedar["status"] == "completed" and cedar["review_label"] == "All reviewed" and cedar["preview_only"]
    assert api("GET", "/projects/harbor-view")[0] == 404
    assert api("GET", "/projects/nope")[0] == 404


def test_agreement_baseline_available_before_first_audit(local_env, api):
    status, d = api("GET", "/projects/riverside")
    assert status == 200
    assert d["audit_action"] == "run" and d["latest_scan"] is None and d["active_scan"] is None
    assert d["currency"] == "USD" and d["records_count"] == 11 and d["new_input_available"] is False
    assert d["invoice_draft"] == {"revision": 0, "item_count": 0, "subtotal": "0.00"}
    ag = d["agreement"]
    assert ag["status"] == "seeded" and ag["contract_value"] == "125000.00" and ag["signed_date"] == "2026-09-28"
    inventory = {r.name: r for r in LocalSource(local_env).list_files()}
    agreement_rec = inventory["Signed scope and agreement.txt"]
    assert ag["source"]["file_id"] == agreement_rec.box_file_id and ag["source"]["version_id"] == agreement_rec.box_version_id
    assert ag["baseline_version"] == agreement_rec.box_version_id
    # every material field carries a real source reference [PRJ-03]
    for lst in ("inclusions", "exclusions", "pricing_basis", "change_terms", "payment_terms"):
        assert ag[lst] and all(t["source"] and t["source"]["file_id"] for t in ag[lst])
    pricing_src = ag["pricing_basis"][0]["source"]
    assert pricing_src["file_name"] == "Exhibit B — Labor and pricing.txt"
    assert any("Retainage: 5%" in t["text"] for t in ag["payment_terms"])
    # the original-scope button opens the actual record with highlights before any audit
    status, page = api("GET", f"/projects/riverside/records/{ag['source']['file_id']}/pages/3")
    assert status == 200 and page["page_count"] == 3 and page["available"] and page["warning"] is None
    assert page["highlights"] == ["Weekend installation excluded.", "Modifications to existing service equipment excluded.",
                                  "Additional work and proposed pricing are subject to the contractual change and approval procedure."]
    assert page["category_label"] == "Original agreement"


def test_records_listing_categories_and_filter(local_env, api):
    status, body = api("GET", "/projects/riverside/records")
    assert status == 200 and body["total"] == 11 and len(body["records"]) == 11
    cats = {c["category_id"]: c["count"] for c in body["categories"]}
    assert cats == {"original_agreement": 2, "approvals": 1, "field_records": 2, "billing": 2, "change_instructions": 2,
                    "pricing": 1, "payments": 1}
    rec = next(r for r in body["records"] if r["name"].startswith("INV-014"))
    assert rec["ingestion_status"] == "pending" and rec["classification_basis"] == "filename"
    assert rec["extension"] == "txt" and rec["mime_type"] == "text/plain" and rec["size_bytes"] > 0 and rec["modified_at"]
    assert rec["box_url"] == f"https://app.box.com/file/{rec['file_id']}"
    agreement = next(r for r in body["records"] if r["name"] == "Signed scope and agreement.txt")
    assert agreement["classification_basis"] == "seeded"

    status, billing = api("GET", "/projects/riverside/records", query={"category_id": "billing"})
    assert [r["name"][:7] for r in billing["records"]] == ["INV-014", "INV-015"]
    assert billing["total"] == 11 and len(billing["categories"]) == 7

    api.audit()
    _, after = api("GET", "/projects/riverside/records")
    inv = next(r for r in after["records"] if r["name"].startswith("INV-014"))
    assert inv["ingestion_status"] == "ingested" and inv["classification_basis"] == "ai" and inv["page_count"] == 1
    assert inv["document_date"] == "2026-10-13"


def test_records_exclude_output_folder_and_list_unsupported(local_env, api):
    (local_env / "ScopeGuard Packets").mkdir()
    (local_env / "ScopeGuard Packets" / "old.pdf").write_bytes(b"%PDF-1.4")
    (local_env / "photo.jpg").write_bytes(b"\xff\xd8\xff")
    _, body = api("GET", "/projects/riverside/records")
    names = [r["name"] for r in body["records"]]
    assert "old.pdf" not in names
    jpg = next(r for r in body["records"] if r["name"] == "photo.jpg")
    assert jpg["ingestion_status"] == "unsupported" and jpg["category_id"] == "unclassified"
    scan = api.audit()
    assert scan["status"] == "completed"
    assert scan["coverage"]["complete"] is False and any("photo.jpg" in w for w in scan["coverage"]["warnings"])
    assert next(f for f in scan["files"] if f["name"] == "photo.jpg")["ingestion_status"] == "unsupported"


def test_upload_validation(local_env, api):
    assert api.multipart("notes.docx", b"PK\x03\x04")[0] == 415
    status, err = api.multipart("empty.txt", b"")
    assert status == 400 and err["code"] == "empty_file"
    status, err = api.multipart("big.txt", b"a" * (4 * 1024 * 1024 + 1))
    assert status == 413 and err["code"] == "file_too_large"
    status, err = api.multipart("fake.pdf", b"not a pdf")
    assert status == 400 and err["code"] == "malformed_file"
    status, err = api.multipart("bad.txt", b"\xff\xfe\xfa invalid utf8")
    assert status == 400 and err["code"] == "malformed_file"
    status, err = api.multipart("x.txt", b"hello", field="other")
    assert status == 400 and err["code"] == "malformed_upload"
    status, err = api("POST", "/projects/riverside/records", raw=b"{}", headers={"content-type": "application/json"})
    assert status == 400


def test_upload_success_marks_new_input(local_env, api):
    api.audit()
    _, before = api("GET", "/projects/riverside")
    assert before["new_input_available"] is False
    status, rec = api.multipart("../Site photo log: day 3?.md", "# Day 3\nAll quiet.".encode(), "text/markdown")
    assert status == 201
    assert rec["name"] == "Site photo log- day 3-.md" and rec["ingestion_status"] == "pending"
    assert (local_env / rec["name"]).read_text() == "# Day 3\nAll quiet."
    _, after = api("GET", "/projects/riverside")
    assert after["new_input_available"] is True and after["records_count"] == 12
    # upload alone does not start an audit [DOC-04]
    assert after["active_scan"] is None


def test_content_endpoint_pinned_versions(local_env, api):
    _, d = api("GET", "/projects/riverside")
    src = d["agreement"]["source"]
    status, data, headers = api("GET", f"/projects/riverside/records/{src['file_id']}/content", query={"version_id": src["version_id"]})
    assert status == 200 and headers["Content-Type"] == "text/plain; charset=utf-8"
    assert data.startswith("Electrical subcontract".encode())
    assert api("GET", "/projects/riverside/records/999/content")[0] == 404  # never arbitrary file IDs
    status, err = api("GET", f"/projects/riverside/records/{src['file_id']}/content", query={"version_id": "123"})
    assert status == 404 and err["code"] == "record_not_found"
    assert api("GET", f"/projects/riverside/records/{src['file_id']}/pages/9")[0] == 404


def test_pdf_content_and_pages(pdf_env, api):
    _, d = api("GET", "/projects/riverside")
    src = d["agreement"]["source"]
    assert src["file_name"] == "Signed scope and agreement.pdf"
    status, data, headers = api("GET", f"/projects/riverside/records/{src['file_id']}/content")
    assert status == 200 and headers["Content-Type"] == "application/pdf" and data.startswith(b"%PDF")
    assert headers["Content-Disposition"].startswith('inline; filename="Signed scope and agreement.pdf"')
    _, page = api("GET", f"/projects/riverside/records/{src['file_id']}/pages/2")
    assert page["page_count"] == 3 and "Supply and install 24 LED fixtures." in page["text"]
    assert "Supply and install 24 LED fixtures." in page["highlights"]


def test_pages_highlight_finding_evidence_and_old_versions(local_env, api):
    scan = api.audit()
    d002 = by_code(scan)["D-002"]
    si009 = next(e for g in d002["evidence_groups"] for e in g["evidence"] if e["file_name"].startswith("SI-009"))
    q = {"version_id": si009["version_id"], "scan_id": scan["scan_id"], "finding_id": d002["finding_id"]}
    status, page = api("GET", f"/projects/riverside/records/{si009['file_id']}/pages/1", query=q)
    assert status == 200 and page["highlights"] == ["Revised installation requires 26 LED fixtures."]
    assert page["warning"] is None
    # a newer version is uploaded: the analyzed version stays viewable, with a warning [DOC-06]
    status, _ = api.multipart(si009["file_name"], b"Site instruction SI-009\nRevised installation requires 27 LED fixtures.\n")
    assert status == 201
    status, page = api("GET", f"/projects/riverside/records/{si009['file_id']}/pages/1", query=q)
    assert status == 200 and page["version_id"] == si009["version_id"] and "26 LED fixtures" in page["text"]
    assert page["available"] is False and "captured during the audit" in page["warning"]
    status, err = api("GET", f"/projects/riverside/records/{si009['file_id']}/content", query={"version_id": si009["version_id"]})
    assert status == 404 and err["code"] == "source_unavailable"
    assert api("GET", f"/projects/riverside/records/{si009['file_id']}/pages/1", query={**q, "finding_id": "f_nope"})[0] == 404
