import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import tiktoken
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config.settings import get_settings
from app.core.database import get_sessionmaker
from app.models import Document, KnowledgeBase
from app.models.knowledge import utc_now
from app.service.docmind_service import parse_pdf
from app.service.embedding_service import generate_embedding
from app.service.milvus_service import (
    batch_insert,
    create_collection,
    delete_document_chunks,
)
from app.service.xlsx_service import parse_xls_to_chunks, parse_xlsx_to_chunks


PDF_CHUNK_TOKENS = 800
PDF_CHUNK_OVERLAP = 100


def _encoding():
    settings = get_settings()
    try:
        return tiktoken.encoding_for_model(settings.embedding_model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def chunk_text(
    text: str,
    max_tokens: int = PDF_CHUNK_TOKENS,
    overlap: int = PDF_CHUNK_OVERLAP,
) -> list[dict[str, Any]]:
    if max_tokens < 1:
        raise ValueError("max_tokens must be greater than 0.")
    if overlap < 0 or overlap >= max_tokens:
        raise ValueError("overlap must be greater than or equal to 0 and less than max_tokens.")

    normalized = text.strip()
    if not normalized:
        raise ValueError("text cannot be blank.")

    encoding = _encoding()
    token_ids = encoding.encode(normalized)
    chunks: list[dict[str, Any]] = []
    start = 0
    chunk_index = 0
    while start < len(token_ids):
        end = min(start + max_tokens, len(token_ids))
        content = encoding.decode(token_ids[start:end]).strip()
        if content:
            chunks.append(
                {
                    "content": content,
                    "source_type": "pdf",
                    "chunk_index": chunk_index,
                }
            )
            chunk_index += 1
        if end == len(token_ids):
            break
        start = max(end - overlap, 0)
    return chunks


def parse_document_to_chunks(file_path: str, file_name: str) -> list[dict[str, Any]]:
    suffix = Path(file_name).suffix.lower() or Path(file_path).suffix.lower()
    if suffix == ".pdf":
        return chunk_text(parse_pdf(file_path, file_name))
    if suffix in {".xlsx", ".xlsm"}:
        chunks = parse_xlsx_to_chunks(file_path, file_name)
    elif suffix == ".xls":
        chunks = parse_xls_to_chunks(file_path, file_name)
    else:
        raise ValueError(f"Unsupported document type: {suffix or file_name}")

    if not chunks:
        raise RuntimeError("解析结果为空")
    return chunks


async def _mark_failed(
    db: AsyncSession,
    document: Document,
    error: Exception,
) -> None:
    document.status = "failed"
    document.error_message = str(error)
    document.updated_at = utc_now()
    await db.commit()


async def process_document(
    document_id: UUID | str,
    *,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> Document:
    session_factory = sessionmaker or get_sessionmaker()
    document_uuid = UUID(str(document_id))

    async with session_factory() as db:
        document = await db.get(Document, document_uuid)
        if document is None:
            raise RuntimeError("Document not found.")
        knowledge_base = await db.get(KnowledgeBase, document.kb_id)
        if knowledge_base is None:
            error = RuntimeError("Knowledge base not found.")
            await _mark_failed(db, document, error)
            raise error

        document.status = "processing"
        document.error_message = None
        document.chunk_count = None
        document.updated_at = utc_now()
        await db.commit()
        await db.refresh(document)

        try:
            chunks = await asyncio.to_thread(
                parse_document_to_chunks,
                document.file_path,
                document.filename,
            )
            texts = [str(chunk["content"]) for chunk in chunks]
            vectors = await generate_embedding(texts)
            if not isinstance(vectors, list) or (
                vectors and not isinstance(vectors[0], list)
            ):
                raise RuntimeError("Embedding batch returned invalid vector shape.")

            await asyncio.to_thread(create_collection, knowledge_base.collection_name)
            try:
                await asyncio.to_thread(
                    delete_document_chunks,
                    knowledge_base.collection_name,
                    str(document.id),
                )
            except Exception:
                pass

            milvus_chunks = []
            for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
                milvus_chunks.append(
                    {
                        "chunk_id": f"{document.id}_{index}_{uuid4().hex[:8]}",
                        "document_id": str(document.id),
                        "kb_id": str(knowledge_base.id),
                        "filename": document.filename,
                        "content": str(chunk["content"]),
                        "vector": vector,
                        "chunk_index": int(chunk.get("chunk_index", index)),
                        "source_type": chunk.get("source_type"),
                        "page_number": chunk.get("page_number"),
                        "sheet_name": chunk.get("sheet_name"),
                        "row_start": chunk.get("row_start"),
                        "row_end": chunk.get("row_end"),
                    }
                )
            await asyncio.to_thread(
                batch_insert,
                knowledge_base.collection_name,
                milvus_chunks,
            )

            document.status = "success"
            document.chunk_count = len(milvus_chunks)
            document.error_message = None
            document.updated_at = utc_now()
            await db.commit()
            await db.refresh(document)
            return document
        except Exception as exc:
            try:
                await asyncio.to_thread(
                    delete_document_chunks,
                    knowledge_base.collection_name,
                    str(document.id),
                )
            except Exception:
                pass
            await _mark_failed(db, document, exc)
            await db.refresh(document)
            return document
