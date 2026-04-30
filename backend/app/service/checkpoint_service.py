import logging
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DeepResearchCheckpoint
from app.models.chat import utc_now
from app.service.deep_research.state import (
    CheckpointStatus,
    clean_state_for_checkpoint,
    to_serializable,
)


logger = logging.getLogger(__name__)


def _as_uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _clean_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    return to_serializable(payload)


async def get_checkpoint(
    db: AsyncSession,
    user_id: UUID | str,
    session_id: UUID | str,
) -> DeepResearchCheckpoint | None:
    """Return a user-owned Deep Research checkpoint by chat session ID."""

    result = await db.execute(
        select(DeepResearchCheckpoint).where(
            DeepResearchCheckpoint.user_id == _as_uuid(user_id),
            DeepResearchCheckpoint.session_id == _as_uuid(session_id),
        )
    )
    return result.scalar_one_or_none()


async def save_checkpoint(
    db: AsyncSession,
    user_id: UUID | str,
    session_id: UUID | str,
    state: dict[str, Any],
    ui_state: dict[str, Any] | None = None,
    final_report: str | None = None,
    status: CheckpointStatus = "running",
) -> str | None:
    """
    Upsert the latest Deep Research checkpoint for one agent/phase boundary.

    This service stores multi-agent ResearchState only. Ordinary chat messages
    remain owned by session_service/chat_service and are not read or persisted here.
    """

    user_uuid = _as_uuid(user_id)
    session_uuid = _as_uuid(session_id)
    clean_state = clean_state_for_checkpoint(state)
    clean_ui_state = _clean_payload(ui_state)
    query = str(clean_state.get("query") or state.get("query") or "")
    phase = str(clean_state.get("phase") or state.get("phase") or "init")
    iteration = int(clean_state.get("iteration") or state.get("iteration") or 0)
    max_iterations = int(
        clean_state.get("max_iterations") or state.get("max_iterations") or 3
    )
    report = final_report
    if report is None:
        state_report = clean_state.get("final_report")
        report = str(state_report) if state_report else None

    try:
        checkpoint = await get_checkpoint(db, user_uuid, session_uuid)
        if checkpoint is None:
            checkpoint = DeepResearchCheckpoint(
                user_id=user_uuid,
                session_id=session_uuid,
                query=query,
                phase=phase,
                iteration=iteration,
                max_iterations=max_iterations,
                state_json=clean_state,
                ui_state_json=clean_ui_state,
                final_report=report,
                status=status,
            )
            db.add(checkpoint)
            await db.flush()
        else:
            checkpoint.query = query
            checkpoint.phase = phase
            checkpoint.iteration = iteration
            checkpoint.max_iterations = max_iterations
            checkpoint.state_json = clean_state
            if clean_ui_state is not None:
                checkpoint.ui_state_json = clean_ui_state
            if report is not None:
                checkpoint.final_report = report
            checkpoint.status = status
            checkpoint.error_message = None if status != "failed" else checkpoint.error_message
            checkpoint.updated_at = utc_now()

        await db.commit()
        await db.refresh(checkpoint)
        logger.info(
            "Saved Deep Research checkpoint: session_id=%s phase=%s status=%s",
            session_uuid,
            phase,
            status,
        )
        return str(checkpoint.id)
    except Exception:
        await db.rollback()
        logger.exception("Failed to save Deep Research checkpoint.")
        return None


async def load_checkpoint(
    db: AsyncSession,
    user_id: UUID | str,
    session_id: UUID | str,
) -> dict[str, Any] | None:
    """Load only the backend Deep Research state for a user-owned session."""

    checkpoint = await get_checkpoint(db, user_id, session_id)
    if checkpoint is None:
        return None
    return checkpoint.state_json


async def load_full_checkpoint(
    db: AsyncSession,
    user_id: UUID | str,
    session_id: UUID | str,
) -> dict[str, Any] | None:
    """Load state, UI state, report, and metadata for a user-owned checkpoint."""

    checkpoint = await get_checkpoint(db, user_id, session_id)
    if checkpoint is None:
        return None
    return checkpoint.to_dict(include_state=True)


async def get_checkpoint_info(
    db: AsyncSession,
    user_id: UUID | str,
    session_id: UUID | str,
) -> dict[str, Any] | None:
    """Load checkpoint metadata without the full persisted state JSON."""

    checkpoint = await get_checkpoint(db, user_id, session_id)
    if checkpoint is None:
        return None
    return checkpoint.to_dict(include_state=False)


async def list_checkpoints(
    db: AsyncSession,
    user_id: UUID | str,
    status: CheckpointStatus | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List recent Deep Research checkpoints for one user."""

    if limit < 1:
        raise ValueError("limit must be greater than 0.")

    statement = select(DeepResearchCheckpoint).where(
        DeepResearchCheckpoint.user_id == _as_uuid(user_id)
    )
    if status is not None:
        statement = statement.where(DeepResearchCheckpoint.status == status)
    statement = statement.order_by(DeepResearchCheckpoint.updated_at.desc()).limit(limit)

    result = await db.execute(statement)
    return [
        checkpoint.to_dict(include_state=False)
        for checkpoint in result.scalars().all()
    ]


async def update_status(
    db: AsyncSession,
    user_id: UUID | str,
    session_id: UUID | str,
    status: CheckpointStatus,
    error_message: str | None = None,
) -> bool:
    """Update the lifecycle status for a user-owned Deep Research checkpoint."""

    checkpoint = await get_checkpoint(db, user_id, session_id)
    if checkpoint is None:
        return False

    try:
        checkpoint.status = status
        checkpoint.error_message = error_message
        checkpoint.updated_at = utc_now()
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        logger.exception("Failed to update Deep Research checkpoint status.")
        return False


async def delete_checkpoint(
    db: AsyncSession,
    user_id: UUID | str,
    session_id: UUID | str,
) -> bool:
    """Delete a user-owned Deep Research checkpoint."""

    try:
        result = await db.execute(
            delete(DeepResearchCheckpoint).where(
                DeepResearchCheckpoint.user_id == _as_uuid(user_id),
                DeepResearchCheckpoint.session_id == _as_uuid(session_id),
            )
        )
        await db.commit()
        return bool(result.rowcount)
    except Exception:
        await db.rollback()
        logger.exception("Failed to delete Deep Research checkpoint.")
        return False
