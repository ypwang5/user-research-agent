from functools import lru_cache
from typing import Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from pymilvus import MilvusClient

from .config import settings
from .embedder import build_embedder
from .judge import build_judge
from .media import MediaProcessor
from .redact import redact
from .store import MemoryStore

InsightType = Literal[
    "pain_point", "workaround", "willingness_to_pay", "current_solution",
    "goal", "context", "other",
]

app = FastAPI(title="User Research Memory", version="0.1.0")


@lru_cache
def get_store() -> MemoryStore:
    client = MilvusClient(uri=settings.milvus_uri, token=settings.milvus_token)
    return MemoryStore(client, build_embedder(settings), settings.collection_prefix,
                       settings.merge_threshold, settings.max_evidence,
                       judge=build_judge(settings),
                       auto_threshold=settings.auto_merge_threshold,
                       candidate_threshold=settings.candidate_threshold)


def auth(authorization: str = Header(default="")) -> None:
    if settings.api_key and authorization != f"Bearer {settings.api_key}":
        raise HTTPException(401, "invalid api key")


class RedactIn(BaseModel):
    text: str
    names: list[str] = []


class CheckIn(BaseModel):
    project: str
    content: str


class InsightIn(BaseModel):
    type: InsightType = "other"
    summary: str = Field(min_length=1, max_length=2000)
    quote: str = ""


class IngestIn(BaseModel):
    project: str
    interviewee_id: str
    content: str
    insights: list[InsightIn]


class SearchIn(BaseModel):
    project: str
    query: str
    top_k: int = Field(5, ge=1, le=50)
    min_score: float = 0.0


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/v1/redact", dependencies=[Depends(auth)])
def api_redact(body: RedactIn):
    text, counts = redact(body.text, body.names)
    return {"text": text, "counts": counts}


@app.post("/v1/sessions/check", dependencies=[Depends(auth)])
def api_check(body: CheckIn, store: MemoryStore = Depends(get_store)):
    hit = store.find_session(body.project, body.content)
    return {"duplicate": bool(hit), "session_id": hit["id"] if hit else None}


@app.post("/v1/ingest", dependencies=[Depends(auth)])
def api_ingest(body: IngestIn, store: MemoryStore = Depends(get_store)):
    r = store.ingest(body.project, body.interviewee_id, body.content,
                     [i.model_dump() for i in body.insights])
    acts = [x["action"] for x in r["results"]]
    r["stats"] = {a: acts.count(a) for a in ("created", "merged", "same_interviewee")}
    return r


@app.post("/v1/search", dependencies=[Depends(auth)])
def api_search(body: SearchIn, store: MemoryStore = Depends(get_store)):
    return {"results": store.search(body.project, body.query, body.top_k, body.min_score)}


@lru_cache
def get_media() -> MediaProcessor:
    return MediaProcessor(settings.dashscope_api_key, settings.dashscope_base_url,
                          settings.asr_model, settings.vl_model)


@app.post("/v1/media/extract", dependencies=[Depends(auth)])
def api_media(files: list[UploadFile] = File(default=[]),
              media: MediaProcessor = Depends(get_media)):
    """文档/图片/音频/视频转文字。单个文件失败记在 items[].error，不影响其它文件。
    允许不带文件（Dify 里用户只填了文字记录时），返回空结果。"""
    items = [media.extract(f.filename or "file", f.file.read(), f.content_type) for f in files]
    text = "\n\n".join(f"### 文件：{i['filename']}（{i['kind']}）\n{i['text']}"
                       for i in items if "text" in i)
    return {"text": text, "items": items, "errors": sum("error" in i for i in items)}


@app.delete("/v1/projects", dependencies=[Depends(auth)])
def api_delete_project(project: str, store: MemoryStore = Depends(get_store)):
    return store.delete_project(project)


@app.get("/v1/overview", dependencies=[Depends(auth)])
def api_overview(project: str, limit: int = 50, store: MemoryStore = Depends(get_store)):
    return store.overview(project, limit)
