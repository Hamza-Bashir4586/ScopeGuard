"""Rehearsal reset for the explicitly enabled fictional demo project; no document deletion."""

from datetime import datetime, timedelta, timezone

from . import config, projects, reviews, scans
from .errors import bad_request, conflict, not_found
from .serialize import now_iso
from .store import BaseStore, PreconditionFailed, get_store


def reset(project_id: str, body: dict) -> dict:
    projects.get_project(project_id)
    if not config.demo_reset_enabled():
        raise not_found("Demo reset is not enabled", "demo_reset_disabled")
    request_id = body.get("request_id")
    expected = body.get("expected_revision")
    if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 200:
        raise bad_request("request_id must be a nonempty string of at most 200 characters")
    if isinstance(expected, bool) or not isinstance(expected, int):
        raise bad_request("expected_revision must be an integer")
    store = get_store()
    reset_id = "reset_" + BaseStore.hashed(request_id)
    archive_key = BaseStore.project_key(project_id, f"demo-resets/{reset_id}.json")
    previous = store.get_json(archive_key)
    if previous:
        if previous.get("status") == "completed":
            return previous["result"]
        raise conflict("This reset did not finish. Reload and start a new reset.", "reset_incomplete")
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    scans.acquire_lease(store, project_id, reset_id, deadline)
    archived = False
    try:
        draft, draft_etag = reviews.load_draft(store, project_id)
        if int(draft.get("revision", 0)) != expected:
            raise conflict("The invoice selection changed. Reload before resetting.", "revision_conflict")
        state_key = BaseStore.project_key(project_id, "project.json")
        state, state_etag = store.get_json_etag(state_key)
        saved_reviews = []
        for key in store.list_keys(BaseStore.project_key(project_id, "reviews/")):
            if key.endswith(".json"):
                value, etag = store.get_json_etag(key)
                if value and value.get("work_item_id"):
                    saved_reviews.append((key, value, etag))
        if len(saved_reviews) > 50:
            raise conflict("This project exceeds the small demo reset limit.", "reset_limit")
        snapshot = {"status": "pending", "created_at": now_iso(), "project_id": project_id,
                    "project_state": state, "draft": draft, "reviews": [value for _, value, _ in saved_reviews]}
        if not store.create_json(archive_key, snapshot):
            raise conflict("This reset is already processing.", "reset_active")
        archived = True
        now = now_iso()
        for key, value, etag in saved_reviews:
            clean = reviews.default_review(value["work_item_id"])
            clean.update(revision=int(value.get("revision", 0)) + 1, updated_at=now)
            store.put_json(key, clean, if_match=etag)
        clean_draft = reviews.empty_draft(project_id)
        clean_draft.update(revision=int(draft.get("revision", 0)) + 1, updated_at=now)
        store.put_json(reviews.draft_key(project_id), clean_draft,
                       if_match=draft_etag, if_none_match=draft_etag is None)
        clean_state = {**(state or {}), "project_id": project_id, "latest_completed_scan_id": None,
                       "latest_completed_started_at": None, "latest_completed_at": None,
                       "demo_reset_at": now, "demo_reset_id": reset_id}
        store.put_json(state_key, clean_state, if_match=state_etag, if_none_match=state_etag is None)
        result = {"project_id": project_id, "reset_id": reset_id, "reset_at": now,
                  "reviews_cleared": len(saved_reviews), "records_preserved": True,
                  "message": "Demo ready. Run the audit to begin."}
        store.put_json(archive_key, {**snapshot, "status": "completed", "result": result})
        return result
    except PreconditionFailed:
        if archived:
            store.update_json(archive_key, lambda value: {**value, "status": "incomplete"})
        raise conflict("The project changed during reset. Reload and reset again; the prior state was archived.",
                       "revision_conflict") from None
    finally:
        scans.release_lease(store, project_id, reset_id)
