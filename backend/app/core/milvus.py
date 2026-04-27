from pathlib import Path

from pymilvus import MilvusClient

from app.config.settings import get_settings


def create_milvus_client() -> MilvusClient:
    settings = get_settings()
    db_path = Path(settings.milvus_uri)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return MilvusClient(uri=str(db_path))
