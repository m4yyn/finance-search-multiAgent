from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.core.database import get_db
from app.models import Document, User
from app.router.auth_router import get_current_user_required
from app.router.knowledge_router import get_owned_knowledge_base
from app.schemas.knowledge import DocumentResponse
from app.service.document_service import process_document
from app.service.milvus_service import delete_document_chunks


router = APIRouter(prefix="/knowledge", tags=["documents"])
SUPPORTED_EXTENSIONS = {".pdf", ".xlsx", ".xlsm", ".xls"}
DEFAULT_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xls": "application/vnd.ms-excel",
}


async def ingest_document(document_id: UUID) -> None:
    await process_document(document_id)


def _safe_filename(filename: str | None) -> str:
    safe_name = Path(filename or "upload").name
    if not safe_name or safe_name in {".", ".."}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename.",
        )
    return safe_name


def _validate_extension(filename: str) -> str:
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file type.",
        )
    return extension


async def _save_upload_file(kb_id: UUID, upload_file: UploadFile) -> tuple[str, int, str]:
    filename = _safe_filename(upload_file.filename)
    extension = _validate_extension(filename)
    target_dir = Path(get_settings().upload_dir) / str(kb_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{uuid4().hex}_{filename}"
    file_size = 0
    with target_path.open("wb") as target_file:
        while chunk := await upload_file.read(1024 * 1024):
            file_size += len(chunk)
            target_file.write(chunk)
    if file_size == 0:
        target_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )
    return str(target_path), file_size, DEFAULT_MIME_TYPES[extension]


async def _get_owned_document(
    db: AsyncSession,
    kb_id: UUID,
    doc_id: UUID,
) -> Document | None:
    result = await db.execute(
        select(Document).where(
            Document.id == doc_id,
            Document.kb_id == kb_id,
        )
    )
    return result.scalar_one_or_none()


@router.post(
    "/bases/{kb_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    kb_id: UUID,
    background: BackgroundTasks,
    current_user: Annotated[User, Depends(get_current_user_required)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
) -> Document:
    knowledge_base = await get_owned_knowledge_base(db, current_user.id, kb_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found.",
        )

    filename = _safe_filename(file.filename)
    file_path, file_size, default_mime_type = await _save_upload_file(kb_id, file)
    document = Document(
        kb_id=knowledge_base.id,
        filename=filename,
        file_path=file_path,
        file_size=file_size,
        mime_type=file.content_type or default_mime_type,
        status="pending",
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    background.add_task(ingest_document, document.id)
    return document


@router.get("/bases/{kb_id}/documents", response_model=list[DocumentResponse])
async def list_documents(
    kb_id: UUID,
    current_user: Annotated[User, Depends(get_current_user_required)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[Document]:
    knowledge_base = await get_owned_knowledge_base(db, current_user.id, kb_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found.",
        )
    result = await db.execute(
        select(Document)
        .where(Document.kb_id == kb_id)
        .order_by(Document.created_at.desc())
    )
    return list(result.scalars().all())


@router.delete(
    "/bases/{kb_id}/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    kb_id: UUID,
    doc_id: UUID,
    current_user: Annotated[User, Depends(get_current_user_required)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Response:
    knowledge_base = await get_owned_knowledge_base(db, current_user.id, kb_id)
    if knowledge_base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Knowledge base not found.",
        )
    document = await _get_owned_document(db, kb_id, doc_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found.",
        )

    delete_document_chunks(knowledge_base.collection_name, str(document.id))
    Path(document.file_path).unlink(missing_ok=True)
    await db.execute(delete(Document).where(Document.id == doc_id))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
