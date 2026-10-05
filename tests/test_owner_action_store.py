"""Serialized confirmation/idempotency journal using existing schema 2."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.owner_actions import StagedAction
from app.owner_identity import OwnerInterfaceError
from app.owner_services import OwnerServices
from core.models import AuditLog, OwnerApproval
from tests.owner_helpers import actor, owner_services


def challenge(services, *, action="approve_suggestion", parameters=None, binding=None, request_id=None):
    parameters = {"suggestion_id": 7} if parameters is None else parameters
    request_id = request_id or str(uuid4())
    who = actor(services)
    response = services.actions.prepare(
        who,
        services.scope(),
        action,
        parameters,
        request_id,
        binding or {"suggestion_hash": "a" * 64},
        "Fixed trusted summary",
    )
    return who, parameters, response


def test_raw_nonce_never_persists_and_live_approval_purpose_not_used(tmp_path):
    services = owner_services(tmp_path)
    who, parameters, q = challenge(services)
    with services.database.session() as session:
        row = session.scalar(select(OwnerApproval))
        logs = session.scalars(select(AuditLog)).all()
        assert row.purpose == "owner_action" and row.status == "pending"
        assert q["confirmation_token"] not in str([r.details for r in logs])
        assert q["confirmation_token"] != row.nonce_hash
        assert all(r.purpose != "live_session" for r in session.scalars(select(OwnerApproval)).all())
    staged = services.actions.stage(
        who, services.scope(), "approve_suggestion", parameters, q["request_id"], q["confirmation_token"]
    )
    assert isinstance(staged, StagedAction)
    with services.database.session() as session:
        rows = session.scalars(select(OwnerApproval).order_by(OwnerApproval.time)).all()
        assert {r.purpose for r in rows} == {"owner_action", "owner_action_run"}
        assert all(r.status == "consumed" for r in rows)


def test_completed_same_request_cached_without_new_effect(tmp_path):
    services = owner_services(tmp_path)
    who = actor(services)
    request_id = str(uuid4())
    staged = services.actions.stage(who, services.scope(), "pause", {}, request_id)
    finished = services.actions.finish(staged, {"status": "completed", "effect_applied": True})
    replay = services.actions.stage(who, services.scope(), "pause", {}, request_id)
    assert replay["replayed"] and replay["status"] == finished["status"]
    with services.database.session() as session:
        assert len(session.scalars(select(OwnerApproval)).all()) == 1
        assert (
            len(session.scalars(select(AuditLog).where(AuditLog.action == "owner.action_staged")).all()) == 1
        )


@pytest.mark.parametrize("changed", ["kill", "reject_suggestion"])
def test_reused_request_id_changed_action_conflicts(tmp_path, changed):
    services = owner_services(tmp_path)
    who = actor(services)
    request_id = str(uuid4())
    staged = services.actions.stage(who, services.scope(), "pause", {}, request_id)
    services.actions.finish(staged, {"status": "completed"})
    with pytest.raises(OwnerInterfaceError, match="idempotency_payload_conflict"):
        services.actions.stage(
            who, services.scope(), changed, {} if changed == "kill" else {"suggestion_id": 1}, request_id
        )


def test_changed_parameters_under_same_key_conflicts(tmp_path):
    services = owner_services(tmp_path)
    who = actor(services)
    key = str(uuid4())
    run = services.actions.stage(who, services.scope(), "reject_suggestion", {"suggestion_id": 1}, key)
    services.actions.finish(run, {"status": "rejected"})
    with pytest.raises(OwnerInterfaceError, match="idempotency_payload_conflict"):
        services.actions.stage(who, services.scope(), "reject_suggestion", {"suggestion_id": 2}, key)


@pytest.mark.parametrize("status", [None, "uncertain", "rejected"])
def test_pending_uncertain_or_rejected_never_resubmits(tmp_path, status):
    services = owner_services(tmp_path)
    who = actor(services)
    key = str(uuid4())
    staged = services.actions.stage(who, services.scope(), "pause", {}, key)
    if status is None:
        with pytest.raises(OwnerInterfaceError, match="do_not_retry"):
            services.actions.stage(who, services.scope(), "pause", {}, key)
    else:
        services.actions.finish(staged, {"status": status})
        assert services.actions.stage(who, services.scope(), "pause", {}, key)["status"] == status


def test_completed_same_key_returns_historical_outcome_after_interface_restart(tmp_path):
    services = owner_services(tmp_path)
    who = actor(services)
    key = str(uuid4())
    staged = services.actions.stage(who, services.scope(), "pause", {}, key)
    services.actions.finish(staged, {"status": "completed"})
    second = OwnerServices(services.database, services.settings, clock=services.clock)
    replay = second.actions.stage(actor(second), second.scope(), "pause", {}, key)
    assert replay["replayed"] and replay["historical_runtime"]


@pytest.mark.parametrize(
    "change",
    [
        "interface_session_id",
        "runtime_session_id",
        "account_key",
        "config_hash",
        "code_hash",
        "model_sha256",
        "data_source",
        "mode",
    ],
)
def test_nonce_scope_change_denies(tmp_path, change):
    services = owner_services(tmp_path)
    who, params, q = challenge(services)
    scope = replace(services.scope(), **{change: str(uuid4()) if "session_id" in change else "changed"})
    with pytest.raises(OwnerInterfaceError, match="confirmation_scope_changed"):
        services.actions.stage(
            who, scope, "approve_suggestion", params, q["request_id"], q["confirmation_token"]
        )


@pytest.mark.parametrize("change", ["credential", "transport", "owner"])
def test_nonce_bound_to_derived_identity_not_integer_body(tmp_path, change):
    services = owner_services(tmp_path)
    who, params, q = challenge(services)
    changed = (
        actor(services, fields={"query_id": "new-session"})
        if change == "credential"
        else replace(who, **({"transport": "telegram"} if change == "transport" else {"owner_id": 43}))
    )
    with pytest.raises(OwnerInterfaceError):
        services.actions.stage(
            changed, services.scope(), "approve_suggestion", params, q["request_id"], q["confirmation_token"]
        )


@pytest.mark.parametrize("change", ["parameters", "action", "key", "token"])
def test_nonce_payload_cannot_be_repurposed(tmp_path, change):
    services = owner_services(tmp_path)
    who, params, q = challenge(services)
    with pytest.raises(OwnerInterfaceError):
        services.actions.stage(
            who,
            services.scope(),
            "close_all" if change == "action" else "approve_suggestion",
            {"suggestion_id": 8} if change == "parameters" else params,
            str(uuid4()) if change == "key" else q["request_id"],
            "A" * 43 if change == "token" else q["confirmation_token"],
        )


def test_expired_nonce_does_not_stage_effect(tmp_path):
    services = owner_services(tmp_path)
    who, params, q = challenge(services)
    services.clock.advance(timedelta(seconds=45))
    with pytest.raises(OwnerInterfaceError, match="confirmation_expired"):
        services.actions.stage(
            who, services.scope(), "approve_suggestion", params, q["request_id"], q["confirmation_token"]
        )
    with services.database.session() as session:
        assert not session.scalar(select(OwnerApproval.id).where(OwnerApproval.purpose == "owner_action_run"))


def test_cancel_is_one_way_no_effect(tmp_path):
    services = owner_services(tmp_path)
    who, params, q = challenge(services)
    assert services.actions.cancel(who, services.scope(), q["confirmation_token"])["status"] == "canceled"
    with pytest.raises(OwnerInterfaceError):
        services.actions.stage(
            who, services.scope(), "approve_suggestion", params, q["request_id"], q["confirmation_token"]
        )


def test_pending_challenge_cap_and_expiry(tmp_path):
    services = owner_services(tmp_path, owner_max_pending_confirmations=4)
    for _ in range(4):
        challenge(services)
    with pytest.raises(OwnerInterfaceError, match="too_many_pending_confirmations"):
        challenge(services)
    services.clock.advance(timedelta(seconds=46))
    assert challenge(services)[2]["status"] == "confirmation_required"
    with services.database.session() as session:
        assert len(session.scalars(select(OwnerApproval).where(OwnerApproval.status == "expired")).all()) == 4


@pytest.mark.parametrize("key", ["", "x", 12, True, None, "0" * 64, "12345678-1234-1234-1234-123456789ABF"])
def test_request_id_canonical_uuid_only(tmp_path, key):
    services = owner_services(tmp_path)
    with pytest.raises(OwnerInterfaceError, match="invalid_request_id"):
        services.actions.stage(actor(services), services.scope(), "pause", {}, key)


def test_finish_is_append_only_and_once(tmp_path):
    services = owner_services(tmp_path)
    who = actor(services)
    run = services.actions.stage(who, services.scope(), "pause", {}, str(uuid4()))
    services.actions.finish(run, {"status": "completed"})
    with pytest.raises(OwnerInterfaceError):
        services.actions.finish(run, {"status": "uncertain"})
