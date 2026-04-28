import asyncio
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models import Document, KnowledgeBase, User


def test_knowledge_models_can_persist_pdf_and_csv_documents() -> None:
    async def run_check() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with sessionmaker() as session:
            user = User(
                username="knowledge-user",
                email="knowledge-user@example.com",
                hashed_password="hashed",
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)

            knowledge_base = KnowledgeBase(
                user_id=user.id,
                name="行业研报",
                description="PDF and CSV documents",
                collection_name="kb_001",
            )
            session.add(knowledge_base)
            await session.commit()
            await session.refresh(knowledge_base)
            knowledge_base_id = knowledge_base.id

            pdf_document = Document(
                kb_id=knowledge_base_id,
                filename="report.pdf",
                file_path="./data/uploads/report.pdf",
                file_size=1024,
                mime_type="application/pdf",
            )
            csv_document = Document(
                kb_id=knowledge_base_id,
                filename="data.csv",
                file_path="./data/uploads/data.csv",
                file_size=2048,
                mime_type="text/csv",
                status="success",
                chunk_count=3,
            )
            session.add_all([pdf_document, csv_document])
            await session.commit()
            await session.refresh(pdf_document)
            await session.refresh(csv_document)

            assert isinstance(knowledge_base.id, UUID)
            assert knowledge_base.user_id == user.id
            assert knowledge_base.collection_name == "kb_001"
            assert knowledge_base.created_at is not None
            assert knowledge_base.updated_at is not None
            assert pdf_document.status == "pending"
            assert pdf_document.mime_type == "application/pdf"
            assert csv_document.mime_type == "text/csv"
            assert csv_document.chunk_count == 3
            assert csv_document.created_at is not None
            assert csv_document.updated_at is not None

            duplicate = KnowledgeBase(
                user_id=user.id,
                name="重复 collection",
                collection_name="kb_001",
            )
            session.add(duplicate)
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            invalid_status = Document(
                kb_id=knowledge_base_id,
                filename="bad.pdf",
                file_path="./data/uploads/bad.pdf",
                file_size=1,
                mime_type="application/pdf",
                status="invalid",
            )
            session.add(invalid_status)
            with pytest.raises(IntegrityError):
                await session.commit()

        await engine.dispose()

    asyncio.run(run_check())
