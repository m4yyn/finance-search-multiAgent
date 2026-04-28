from typing import Any

from pymilvus import DataType, MilvusClient

from app.config.settings import get_settings
from app.core.milvus import create_milvus_client


CHUNK_ID_FIELD = "chunk_id"
DOCUMENT_ID_FIELD = "document_id"
KB_ID_FIELD = "kb_id"
FILENAME_FIELD = "filename"
CONTENT_FIELD = "content"
VECTOR_FIELD = "vector"
DEFAULT_METRIC_TYPE = "COSINE"
MAX_CONTENT_LENGTH = 65535


def _get_client(client: MilvusClient | None = None) -> MilvusClient:
    return client if client is not None else create_milvus_client()


def _close_owned_client(client: MilvusClient, owned: bool) -> None:
    if owned:
        client.close()


def _validate_collection_name(collection_name: str) -> str:
    normalized = collection_name.strip()
    if not normalized:
        raise ValueError("collection_name cannot be blank.")
    return normalized


def _validate_dimension(dimension: int) -> int:
    if dimension <= 1:
        raise ValueError("dimension must be greater than 1.")
    return dimension


def _escape_expr_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _normalize_chunk(chunk: dict[str, Any], dimension: int) -> dict[str, Any]:
    required_fields = {
        CHUNK_ID_FIELD,
        DOCUMENT_ID_FIELD,
        KB_ID_FIELD,
        FILENAME_FIELD,
        CONTENT_FIELD,
        VECTOR_FIELD,
    }
    missing_fields = required_fields - set(chunk)
    if missing_fields:
        missing = ", ".join(sorted(missing_fields))
        raise ValueError(f"Milvus chunk is missing required fields: {missing}.")

    vector = list(chunk[VECTOR_FIELD])
    if len(vector) != dimension:
        raise ValueError(
            f"Vector dimension mismatch: expected {dimension}, got {len(vector)}."
        )

    normalized = dict(chunk)
    for field_name in [
        CHUNK_ID_FIELD,
        DOCUMENT_ID_FIELD,
        KB_ID_FIELD,
        FILENAME_FIELD,
        CONTENT_FIELD,
    ]:
        value = str(normalized[field_name]).strip()
        if not value:
            raise ValueError(f"{field_name} cannot be blank.")
        normalized[field_name] = value

    if len(normalized[CONTENT_FIELD]) > MAX_CONTENT_LENGTH:
        raise ValueError(f"{CONTENT_FIELD} exceeds {MAX_CONTENT_LENGTH} characters.")
    normalized[VECTOR_FIELD] = vector
    return normalized


def create_collection(
    collection_name: str,
    dimension: int | None = None,
    *,
    client: MilvusClient | None = None,
    metric_type: str = DEFAULT_METRIC_TYPE,
) -> bool:
    """Create a local Milvus collection for knowledge chunks, skipping existing ones."""
    collection_name = _validate_collection_name(collection_name)
    settings = get_settings()
    dimension = _validate_dimension(dimension or settings.embedding_dim)
    owned_client = client is None
    milvus_client = _get_client(client)
    try:
        if milvus_client.has_collection(collection_name):
            return False

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=True)
        schema.add_field(
            field_name=CHUNK_ID_FIELD,
            datatype=DataType.VARCHAR,
            is_primary=True,
            max_length=128,
        )
        schema.add_field(
            field_name=DOCUMENT_ID_FIELD,
            datatype=DataType.VARCHAR,
            max_length=64,
        )
        schema.add_field(field_name=KB_ID_FIELD, datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(
            field_name=FILENAME_FIELD,
            datatype=DataType.VARCHAR,
            max_length=512,
        )
        schema.add_field(
            field_name=CONTENT_FIELD,
            datatype=DataType.VARCHAR,
            max_length=MAX_CONTENT_LENGTH,
        )
        schema.add_field(
            field_name=VECTOR_FIELD,
            datatype=DataType.FLOAT_VECTOR,
            dim=dimension,
        )

        index_params = milvus_client.prepare_index_params()
        index_params.add_index(
            field_name=VECTOR_FIELD,
            index_type="AUTOINDEX",
            metric_type=metric_type,
        )
        milvus_client.create_collection(
            collection_name=collection_name,
            schema=schema,
            index_params=index_params,
            consistency_level="Strong",
        )
        return True
    finally:
        _close_owned_client(milvus_client, owned_client)


def batch_insert(
    collection_name: str,
    chunks: list[dict[str, Any]],
    *,
    client: MilvusClient | None = None,
    dimension: int | None = None,
) -> dict[str, Any]:
    """Insert knowledge chunks into a Milvus collection."""
    collection_name = _validate_collection_name(collection_name)
    if not chunks:
        return {"insert_count": 0, "ids": []}

    settings = get_settings()
    dimension = _validate_dimension(dimension or settings.embedding_dim)
    data = [_normalize_chunk(chunk, dimension) for chunk in chunks]
    owned_client = client is None
    milvus_client = _get_client(client)
    try:
        result = milvus_client.insert(collection_name=collection_name, data=data)
        milvus_client.flush(collection_name)
        return result
    finally:
        _close_owned_client(milvus_client, owned_client)


def vector_search(
    collection_name: str,
    query_vector: list[float],
    *,
    client: MilvusClient | None = None,
    limit: int = 5,
    filter_expr: str = "",
    output_fields: list[str] | None = None,
    metric_type: str = DEFAULT_METRIC_TYPE,
) -> list[dict[str, Any]]:
    """Search one dense vector against a Milvus chunk collection."""
    collection_name = _validate_collection_name(collection_name)
    if limit < 1:
        raise ValueError("limit must be greater than 0.")
    if not query_vector:
        raise ValueError("query_vector cannot be empty.")

    owned_client = client is None
    milvus_client = _get_client(client)
    try:
        if not milvus_client.has_collection(collection_name):
            return []
        results = milvus_client.search(
            collection_name=collection_name,
            data=[query_vector],
            anns_field=VECTOR_FIELD,
            filter=filter_expr,
            limit=limit,
            output_fields=output_fields
            or [
                CHUNK_ID_FIELD,
                DOCUMENT_ID_FIELD,
                KB_ID_FIELD,
                FILENAME_FIELD,
                CONTENT_FIELD,
            ],
            search_params={"metric_type": metric_type},
        )
        return list(results[0]) if results else []
    finally:
        _close_owned_client(milvus_client, owned_client)


def count_collection_rows(
    collection_name: str,
    *,
    client: MilvusClient | None = None,
) -> int:
    """Return the current row count for a collection."""
    collection_name = _validate_collection_name(collection_name)
    owned_client = client is None
    milvus_client = _get_client(client)
    try:
        if not milvus_client.has_collection(collection_name):
            return 0
        stats = milvus_client.get_collection_stats(collection_name)
        return int(stats.get("row_count") or 0)
    finally:
        _close_owned_client(milvus_client, owned_client)


def delete_document_chunks(
    collection_name: str,
    document_id: str,
    *,
    client: MilvusClient | None = None,
) -> dict[str, int]:
    """Delete all chunks belonging to one document_id."""
    collection_name = _validate_collection_name(collection_name)
    document_id = document_id.strip()
    if not document_id:
        raise ValueError("document_id cannot be blank.")

    owned_client = client is None
    milvus_client = _get_client(client)
    try:
        if not milvus_client.has_collection(collection_name):
            return {"delete_count": 0, "ids": []}
        milvus_client.load_collection(collection_name)
        result = milvus_client.delete(
            collection_name=collection_name,
            filter=f'{DOCUMENT_ID_FIELD} == "{_escape_expr_value(document_id)}"',
        )
        milvus_client.flush(collection_name)
        if isinstance(result, list):
            return {"delete_count": len(result), "ids": result}
        return result
    finally:
        _close_owned_client(milvus_client, owned_client)


def delete_collection(
    collection_name: str,
    *,
    client: MilvusClient | None = None,
) -> bool:
    """Drop a collection if it exists."""
    collection_name = _validate_collection_name(collection_name)
    owned_client = client is None
    milvus_client = _get_client(client)
    try:
        if not milvus_client.has_collection(collection_name):
            return False
        milvus_client.drop_collection(collection_name)
        return True
    finally:
        _close_owned_client(milvus_client, owned_client)
