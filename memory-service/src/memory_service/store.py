"""调研长期记忆：Milvus 里两张表。

- sessions：每次访谈一条，存内容指纹，用来识别"同一份材料重复上传"。
- insights：提炼出的洞察。语义相同的洞察只存一条，用 mention_count 记录
  有多少个不同受访者提到过 —— 重复不是噪音，是需求频率的信号。
"""

import hashlib
import time
import uuid

from pymilvus import DataType, MilvusClient

from .embedder import Embedder
from .judge import Judge

INSIGHT_FIELDS = [
    "id", "project", "type", "summary", "mention_count",
    "interviewees", "evidence", "created_at", "updated_at",
]


# 只在同组类型之间找合并候选：痛点不会和"土办法"合并（评测中出现过）。
# workaround 与 current_solution 本质都是"现在怎么解决"，模型常混用，归一组。
TYPE_GROUPS = [
    {"pain_point"}, {"workaround", "current_solution"}, {"willingness_to_pay"},
    {"goal"}, {"context"}, {"other"},
]


def type_group(t: str) -> list[str]:
    for g in TYPE_GROUPS:
        if t in g:
            return sorted(g)
    return ["other"]


def _rows(result) -> list[dict]:
    """把 pymilvus 结果立刻物化成普通 dict。

    pymilvus 3.x 的 query 返回 HybridExtraList，JSON 字段按列表下标惰性解码；
    原地排序/过滤后再访问，会把别的行的 JSON 字段填进来（摘要和原话错位）。
    """
    return [dict(r) for r in result]


def content_hash(text: str) -> str:
    """忽略空白差异后的 sha256，避免换行/空格不同被当成新材料。"""
    return hashlib.sha256("".join(text.split()).encode("utf-8")).hexdigest()


class MemoryStore:
    def __init__(self, client: MilvusClient, embedder: Embedder, prefix: str,
                 merge_threshold: float, max_evidence: int = 20,
                 judge: Judge | None = None, auto_threshold: float = 0.90,
                 candidate_threshold: float = 0.70):
        self.c = client
        self.emb = embedder
        self.ins = f"{prefix}insights"
        self.ses = f"{prefix}sessions"
        self.threshold = merge_threshold
        self.max_evidence = max_evidence
        self.judge = judge
        self.auto_threshold = auto_threshold
        self.candidate_threshold = candidate_threshold
        self._ensure_collections()

    # ---------- 建表 ----------
    def _ensure_collections(self) -> None:
        if not self.c.has_collection(self.ins):
            s = self.c.create_schema(auto_id=False)
            s.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
            s.add_field("vector", DataType.FLOAT_VECTOR, dim=self.emb.dim)
            s.add_field("project", DataType.VARCHAR, max_length=128)
            s.add_field("type", DataType.VARCHAR, max_length=32)
            s.add_field("summary", DataType.VARCHAR, max_length=2000)
            s.add_field("mention_count", DataType.INT64)
            s.add_field("interviewees", DataType.JSON)
            s.add_field("evidence", DataType.JSON)
            s.add_field("created_at", DataType.INT64)
            s.add_field("updated_at", DataType.INT64)
            idx = self.c.prepare_index_params()
            idx.add_index("vector", index_type="AUTOINDEX", metric_type="COSINE")
            self.c.create_collection(self.ins, schema=s, index_params=idx,
                                     consistency_level="Strong")
        if not self.c.has_collection(self.ses):
            s = self.c.create_schema(auto_id=False)
            s.add_field("id", DataType.VARCHAR, is_primary=True, max_length=64)
            # Milvus 要求至少一个向量字段；会话表只按指纹查，放个占位向量
            s.add_field("vector", DataType.FLOAT_VECTOR, dim=2)
            s.add_field("project", DataType.VARCHAR, max_length=128)
            s.add_field("content_hash", DataType.VARCHAR, max_length=64)
            s.add_field("interviewee_id", DataType.VARCHAR, max_length=64)
            s.add_field("created_at", DataType.INT64)
            idx = self.c.prepare_index_params()
            idx.add_index("vector", index_type="AUTOINDEX", metric_type="L2")
            self.c.create_collection(self.ses, schema=s, index_params=idx,
                                     consistency_level="Strong")

    def drop_all(self) -> None:
        for name in (self.ins, self.ses):
            if self.c.has_collection(name):
                self.c.drop_collection(name)

    # ---------- 会话 ----------
    def find_session(self, project: str, text: str) -> dict | None:
        h = content_hash(text)
        rows = self.c.query(
            self.ses,
            filter=f'project == "{_esc(project)}" and content_hash == "{h}"',
            output_fields=["id", "interviewee_id", "created_at"],
            limit=1,
        )
        rows = _rows(rows)
        return rows[0] if rows else None

    def register_session(self, project: str, interviewee_id: str, text: str) -> str:
        sid = uuid.uuid4().hex
        self.c.insert(self.ses, [{
            "id": sid, "vector": [0.0, 0.0], "project": project,
            "content_hash": content_hash(text), "interviewee_id": interviewee_id,
            "created_at": int(time.time()),
        }])
        return sid

    # ---------- 洞察 ----------
    def ingest(self, project: str, interviewee_id: str, content: str,
               insights: list[dict]) -> dict:
        """一次访谈入库。返回每条洞察的处理结果。"""
        dup = self.find_session(project, content)
        if dup:
            return {"duplicate_session": True, "session_id": dup["id"], "results": []}
        sid = self.register_session(project, interviewee_id, content)
        vectors = self.emb.embed([i["summary"] for i in insights]) if insights else []
        results = [self._upsert_one(project, interviewee_id, sid, ins, vec)
                   for ins, vec in zip(insights, vectors)]
        return {"duplicate_session": False, "session_id": sid, "results": results}

    def _upsert_one(self, project: str, interviewee_id: str, sid: str,
                    ins: dict, vec: list[float]) -> dict:
        now = int(time.time())
        evidence = {"interviewee_id": interviewee_id, "session_id": sid,
                    "quote": ins.get("quote", "")}
        hit, decided_by, top = self._match(project, ins.get("type", "other"), ins["summary"], vec)
        if hit:
            row = hit["entity"]
            interviewees = list(row["interviewees"])
            ev = list(row["evidence"])
            same_person = interviewee_id in interviewees
            if evidence["quote"] and all(e["quote"] != evidence["quote"] for e in ev):
                ev = (ev + [evidence])[-self.max_evidence:]
            if not same_person:
                interviewees.append(interviewee_id)
            row.update(interviewees=interviewees, evidence=ev,
                       mention_count=len(interviewees), updated_at=now)
            self.c.upsert(self.ins, [{**row, "vector": hit["vector"]}])
            return {"action": "same_interviewee" if same_person else "merged",
                    "insight_id": row["id"], "mention_count": row["mention_count"],
                    "similarity": round(hit["score"], 4), "matched_summary": row["summary"],
                    "decided_by": decided_by}

        iid = uuid.uuid4().hex
        self.c.insert(self.ins, [{
            "id": iid, "vector": vec, "project": project,
            "type": ins.get("type", "other")[:32], "summary": ins["summary"][:2000],
            "mention_count": 1, "interviewees": [interviewee_id],
            "evidence": [evidence], "created_at": now, "updated_at": now,
        }])
        return {"action": "created", "insight_id": iid, "mention_count": 1,
                "similarity": round(top, 4) if top is not None else None,
                "matched_summary": None, "decided_by": decided_by}

    def _match(self, project: str, itype: str, summary: str, vec: list[float]):
        """返回 (命中的候选或 None, 决策方式, 最高相似度)。只在同组类型中找候选。

        决策方式：threshold(无裁判) / auto(高于自动合并线) / judge(LLM 判定) / none(未达候选线或库空)
        """
        types = ", ".join(f'"{t}"' for t in type_group(itype))
        r = self.c.search(
            self.ins, data=[vec], limit=3,
            filter=f'project == "{_esc(project)}" and type in [{types}]',
            output_fields=INSIGHT_FIELDS,
            search_params={"metric_type": "COSINE"},
        )
        hits = self._hits(r)
        if not hits:
            return None, "none", None
        top = hits[0]["distance"]
        if self.judge is None:
            if top >= self.threshold:
                return self._full(hits[0]), "threshold", top
            return None, "threshold", top
        if top >= self.auto_threshold:
            return self._full(hits[0]), "auto", top
        cands = [h for h in hits if h["distance"] >= self.candidate_threshold]
        if not cands:
            return None, "none", top
        idx = self.judge.pick(summary, [h["entity"]["summary"] for h in cands])
        return (self._full(cands[idx]) if idx is not None else None), "judge", top

    @staticmethod
    def _hits(r) -> list[dict]:
        """search 结果同样先物化，再做过滤/按下标取（原因见 _rows）。"""
        if not r or not r[0]:
            return []
        return [{"id": h["id"], "distance": h["distance"], "entity": dict(h["entity"])}
                for h in r[0]]

    def _full(self, h) -> dict:
        # upsert 需要完整实体（含向量），单独按主键取一次
        full = self.c.get(self.ins, ids=[h["id"]], output_fields=["vector"])
        return {"score": h["distance"], "entity": dict(h["entity"]),
                "vector": list(full[0]["vector"])}

    def search(self, project: str, query: str, top_k: int = 5,
               min_score: float = 0.0) -> list[dict]:
        vec = self.emb.embed([query])[0]
        r = self.c.search(
            self.ins, data=[vec], limit=top_k,
            filter=f'project == "{_esc(project)}"',
            output_fields=INSIGHT_FIELDS,
            search_params={"metric_type": "COSINE"},
        )
        return [{**h["entity"], "score": round(h["distance"], 4)}
                for h in self._hits(r) if h["distance"] >= min_score]

    def delete_project(self, project: str) -> dict:
        """清空一个调研方向的全部记忆（评测重置、或用户放弃某个方向时用）。"""
        f = f'project == "{_esc(project)}"'
        ins = _rows(self.c.query(self.ins, filter=f, output_fields=["id"], limit=16384))
        ses = _rows(self.c.query(self.ses, filter=f, output_fields=["id"], limit=16384))
        if ins:
            self.c.delete(self.ins, ids=[r["id"] for r in ins])
        if ses:
            self.c.delete(self.ses, ids=[r["id"] for r in ses])
        return {"deleted_insights": len(ins), "deleted_sessions": len(ses)}

    def overview(self, project: str, limit: int = 50) -> dict:
        rows = _rows(self.c.query(self.ins, filter=f'project == "{_esc(project)}"',
                                  output_fields=INSIGHT_FIELDS, limit=16384))
        rows.sort(key=lambda r: (-r["mention_count"], -r["updated_at"]))
        sessions = _rows(self.c.query(self.ses, filter=f'project == "{_esc(project)}"',
                                      output_fields=["interviewee_id"], limit=16384))
        return {
            "project": project,
            "total_insights": len(rows),
            "total_sessions": len(sessions),
            "total_interviewees": len({s["interviewee_id"] for s in sessions}),
            "insights": rows[:limit],
        }


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')
