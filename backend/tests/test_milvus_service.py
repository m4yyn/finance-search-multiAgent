from uuid import uuid4

import pytest
from pymilvus import MilvusClient

from app.service.milvus_service import (
    MEMORY_COLLECTION_NAME,
    batch_insert,
    create_collection,
    create_memory_collection,
    delete_collection,
    delete_document_chunks,
    delete_memory_vectors,
    insert_memory_vectors,
    search_memory_vectors,
    vector_search,
)


def make_client(tmp_path) -> MilvusClient:
    return MilvusClient(uri=str(tmp_path / "milvus_service_test.db"))


def test_milvus_service_create_insert_search_and_delete(tmp_path) -> None:
    client = make_client(tmp_path)
    collection_name = f"test_kb_{uuid4().hex}"
    document_id = str(uuid4())
    other_document_id = str(uuid4())

    try:
        assert create_collection(collection_name, dimension=3, client=client) is True
        assert create_collection(collection_name, dimension=3, client=client) is False
        assert client.has_collection(collection_name)

        insert_result = batch_insert(
            collection_name,
            [
                {
                    "chunk_id": "chunk-1",
                    "document_id": document_id,
                    "kb_id": "kb-1",
                    "filename": "report.pdf",
                    "content": "贵州茅台主营白酒业务",
                    "vector": [1.0, 0.0, 0.0],
                    "page_number": 1,
                },
                {
                    "chunk_id": "chunk-2",
                    "document_id": document_id,
                    "kb_id": "kb-1",
                    "filename": "report.pdf",
                    "content": "贵州茅台位于贵州省",
                    "vector": [0.9, 0.1, 0.0],
                    "page_number": 2,
                },
                {
                    "chunk_id": "chunk-3",
                    "document_id": other_document_id,
                    "kb_id": "kb-1",
                    "filename": "data.csv",
                    "content": "银行板块数据",
                    "vector": [0.0, 1.0, 0.0],
                    "row_number": 5,
                },
            ],
            client=client,
            dimension=3,
        )
        assert insert_result["insert_count"] == 3

        results = vector_search(
            collection_name,
            [1.0, 0.0, 0.0],
            client=client,
            limit=2,
        )
        assert len(results) == 2
        assert results[0]["entity"]["document_id"] == document_id
        assert "贵州茅台" in results[0]["entity"]["content"]

        filtered_results = vector_search(
            collection_name,
            [1.0, 0.0, 0.0],
            client=client,
            limit=3,
            filter_expr=f'document_id == "{other_document_id}"',
        )
        assert len(filtered_results) == 1
        assert filtered_results[0]["entity"]["document_id"] == other_document_id

        delete_result = delete_document_chunks(
            collection_name,
            document_id,
            client=client,
        )
        assert delete_result["delete_count"] == 2

        remaining_results = vector_search(
            collection_name,
            [1.0, 0.0, 0.0],
            client=client,
            limit=5,
        )
        assert [result["entity"]["document_id"] for result in remaining_results] == [
            other_document_id
        ]

        assert delete_collection(collection_name, client=client) is True
        assert delete_collection(collection_name, client=client) is False
        assert not client.has_collection(collection_name)
    finally:
        if client.has_collection(collection_name):
            client.drop_collection(collection_name)


def test_milvus_service_validates_inputs(tmp_path) -> None:
    client = make_client(tmp_path)
    collection_name = f"test_kb_{uuid4().hex}"
    try:
        with pytest.raises(ValueError):
            create_collection("   ", dimension=3, client=client)
        with pytest.raises(ValueError):
            create_collection(collection_name, dimension=1, client=client)

        create_collection(collection_name, dimension=3, client=client)

        with pytest.raises(ValueError):
            batch_insert(
                collection_name,
                [
                    {
                        "chunk_id": "chunk-1",
                        "document_id": "document-1",
                        "kb_id": "kb-1",
                        "filename": "report.pdf",
                        "content": "content",
                        "vector": [1.0, 0.0],
                    }
                ],
                client=client,
                dimension=3,
            )

        with pytest.raises(ValueError):
            vector_search(collection_name, [], client=client)
        with pytest.raises(ValueError):
            vector_search(collection_name, [1.0, 0.0, 0.0], client=client, limit=0)
        with pytest.raises(ValueError):
            delete_document_chunks(collection_name, "   ", client=client)
    finally:
        if client.has_collection(collection_name):
            client.drop_collection(collection_name)


def test_milvus_service_memory_collection_insert_search_and_delete(tmp_path) -> None:
    client = make_client(tmp_path)
    user_id = str(uuid4())
    other_user_id = str(uuid4())
    memory_id = str(uuid4())
    other_memory_id = str(uuid4())

    try:
        assert create_memory_collection(dimension=3, client=client) is True
        assert create_memory_collection(dimension=3, client=client) is False
        assert client.has_collection(MEMORY_COLLECTION_NAME)

        insert_result = insert_memory_vectors(
            [
                {
                    "memory_vector_id": "memory-vector-1",
                    "memory_id": memory_id,
                    "user_id": user_id,
                    "session_id": str(uuid4()),
                    "summary": "用户关注白酒行业财务指标",
                    "content": "摘要：用户关注白酒行业财务指标",
                    "created_at": "2026-04-29T00:00:00+00:00",
                    "vector": [1.0, 0.0, 0.0],
                },
                {
                    "memory_vector_id": "memory-vector-2",
                    "memory_id": other_memory_id,
                    "user_id": other_user_id,
                    "session_id": "",
                    "summary": "其他用户关注银行板块",
                    "content": "摘要：其他用户关注银行板块",
                    "created_at": "2026-04-29T00:00:00+00:00",
                    "vector": [0.0, 1.0, 0.0],
                },
            ],
            client=client,
            dimension=3,
        )
        assert insert_result["insert_count"] == 2

        results = search_memory_vectors(
            [1.0, 0.0, 0.0],
            user_id,
            client=client,
            limit=5,
        )
        assert len(results) == 1
        assert results[0]["entity"]["memory_id"] == memory_id
        assert results[0]["entity"]["user_id"] == user_id

        delete_result = delete_memory_vectors(memory_id, client=client)
        assert delete_result["delete_count"] == 1
        assert (
            search_memory_vectors([1.0, 0.0, 0.0], user_id, client=client, limit=5)
            == []
        )
    finally:
        if client.has_collection(MEMORY_COLLECTION_NAME):
            client.drop_collection(MEMORY_COLLECTION_NAME)
