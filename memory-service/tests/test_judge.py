import pytest

from memory_service.embedder import FakeEmbedder
from memory_service.judge import parse_match
from memory_service.store import MemoryStore

P = "p"
PAIN = {"type": "pain_point", "summary": "每月手工对账要花两天时间，经常对错", "quote": "q1"}
# FakeEmbedder 下与 PAIN 相似度约 0.775，落在模糊区 [0.70, 0.90)
GRAY = {"type": "pain_point", "summary": "每月手工对账要花两天，还经常对错", "quote": "q2"}
FAR = {"type": "goal", "summary": "希望能自动生成小红书文案", "quote": "q3"}


class ScriptedJudge:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def pick(self, new, candidates):
        self.calls.append((new, candidates))
        return self.answer


@pytest.fixture
def make_store(milvus):
    created = []

    def _make(judge):
        s = MemoryStore(milvus, FakeEmbedder(256), "urmtest_", merge_threshold=0.85,
                        judge=judge, auto_threshold=0.90, candidate_threshold=0.70)
        s.drop_all()
        s._ensure_collections()
        created.append(s)
        return s

    yield _make
    for s in created:
        s.drop_all()


def test_gray_zone_judge_says_same(make_store):
    j = ScriptedJudge(0)
    s = make_store(j)
    s.ingest(P, "U1", "c1", [PAIN])
    r = s.ingest(P, "U2", "c2", [GRAY])["results"][0]
    assert r["action"] == "merged" and r["decided_by"] == "judge"
    assert j.calls == [(GRAY["summary"], [PAIN["summary"]])]


def test_gray_zone_judge_says_different(make_store):
    s = make_store(ScriptedJudge(None))
    s.ingest(P, "U1", "c1", [PAIN])
    r = s.ingest(P, "U2", "c2", [GRAY])["results"][0]
    assert r["action"] == "created" and r["decided_by"] == "judge"
    assert s.overview(P)["total_insights"] == 2


def test_above_auto_skips_judge(make_store):
    j = ScriptedJudge(None)
    s = make_store(j)
    s.ingest(P, "U1", "c1", [PAIN])
    r = s.ingest(P, "U2", "c2", [{**PAIN, "quote": "x"}])["results"][0]
    assert r["action"] == "merged" and r["decided_by"] == "auto"
    assert j.calls == []


def test_below_candidate_skips_judge(make_store):
    j = ScriptedJudge(0)
    s = make_store(j)
    s.ingest(P, "U1", "c1", [PAIN])
    r = s.ingest(P, "U2", "c2", [FAR])["results"][0]
    assert r["action"] == "created" and r["decided_by"] == "none"
    assert j.calls == []


@pytest.mark.parametrize("text,n,expected", [
    ('{"match": 0}', 3, 0),
    ('{"match": 2}', 3, 2),
    ('{"match": -1}', 3, None),
    ('{"match": 5}', 3, None),       # 越界
    ('好的 ```json\n{"match": 1}\n```', 3, 1),
    ("看不懂", 3, None),
    ('{"match": "x"}', 3, None),
    ('{"other": 1}', 3, None),
])
def test_parse_match(text, n, expected):
    assert parse_match(text, n) == expected
