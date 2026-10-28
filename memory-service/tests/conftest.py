import pytest
from pymilvus import MilvusClient

from memory_service.embedder import FakeEmbedder
from memory_service.store import MemoryStore

MILVUS_URI = "http://127.0.0.1:19530"


@pytest.fixture(scope="session")
def milvus():
    try:
        c = MilvusClient(uri=MILVUS_URI, token="root:Milvus", timeout=5)
        c.list_collections()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Milvus 不可用: {e}")
    return c


@pytest.fixture
def store(milvus):
    s = MemoryStore(milvus, FakeEmbedder(256), "urmtest_", merge_threshold=0.85, max_evidence=3)
    s.drop_all()
    s._ensure_collections()
    yield s
    s.drop_all()
