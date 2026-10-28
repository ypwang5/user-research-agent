P = "副业调研"
PAIN = {"type": "pain_point", "summary": "每月手工对账要花两天时间，经常对错", "quote": "对账太折磨了"}
UNRELATED = {"type": "goal", "summary": "希望能自动生成小红书文案", "quote": "文案写不出来"}


def test_first_insight_created(store):
    r = store.ingest(P, "U1", "访谈一", [PAIN])
    assert r["duplicate_session"] is False
    assert r["results"][0]["action"] == "created"
    assert r["results"][0]["mention_count"] == 1


def test_other_interviewee_merges_and_counts(store):
    store.ingest(P, "U1", "访谈一", [PAIN])
    r = store.ingest(P, "U2", "访谈二", [{**PAIN, "quote": "每次对账都要熬夜"}])
    res = r["results"][0]
    assert res["action"] == "merged"
    assert res["mention_count"] == 2
    ov = store.overview(P)
    assert ov["total_insights"] == 1
    assert len(ov["insights"][0]["evidence"]) == 2
    assert ov["insights"][0]["interviewees"] == ["U1", "U2"]


# FakeEmbedder 只看字面重合度：下面两句与 PAIN 的相似度分别约 0.91 / 0.78，
# 用来验证阈值(0.85)两侧的行为。真实语义合并在接入真实向量后的评测里验证。
def test_paraphrase_above_threshold_merges(store):
    store.ingest(P, "U1", "访谈一", [PAIN])
    r = store.ingest(P, "U2", "访谈二", [{"type": "pain_point",
                                         "summary": "每月手工对账要花两天时间，还经常对错", "quote": "q"}])
    assert r["results"][0]["action"] == "merged"
    assert r["results"][0]["similarity"] >= 0.85


def test_below_threshold_creates_new(store):
    store.ingest(P, "U1", "访谈一", [PAIN])
    r = store.ingest(P, "U2", "访谈二", [{"type": "pain_point",
                                         "summary": "每月手工对账要花两天，还经常对错", "quote": "q"}])
    assert r["results"][0]["action"] == "created"
    assert r["results"][0]["similarity"] < 0.85


def test_same_interviewee_not_double_counted(store):
    store.ingest(P, "U1", "访谈一", [PAIN])
    r = store.ingest(P, "U1", "回访U1", [{**PAIN, "quote": "还是很折磨"}])
    assert r["results"][0]["action"] == "same_interviewee"
    assert r["results"][0]["mention_count"] == 1
    # 新原话仍作为证据保留
    assert len(store.overview(P)["insights"][0]["evidence"]) == 2


def test_same_session_repeat_within_batch(store):
    r = store.ingest(P, "U1", "访谈一", [PAIN, {**PAIN, "quote": "又提了一遍"}])
    assert [x["action"] for x in r["results"]] == ["created", "same_interviewee"]
    assert store.overview(P)["total_insights"] == 1


def test_duplicate_session_skipped(store):
    store.ingest(P, "U1", "第一段\n第二段", [PAIN])
    r = store.ingest(P, "U9", "第一段  第二段 ", [PAIN])  # 仅空白不同
    assert r["duplicate_session"] is True
    assert r["results"] == []
    ov = store.overview(P)
    assert ov["insights"][0]["mention_count"] == 1
    assert ov["total_sessions"] == 1


def test_unrelated_creates_new(store):
    store.ingest(P, "U1", "访谈一", [PAIN])
    r = store.ingest(P, "U2", "访谈二", [UNRELATED])
    assert r["results"][0]["action"] == "created"
    assert store.overview(P)["total_insights"] == 2


def test_projects_isolated(store):
    store.ingest(P, "U1", "访谈一", [PAIN])
    r = store.ingest("另一个方向", "U2", "访谈一", [PAIN])
    assert r["duplicate_session"] is False
    assert r["results"][0]["action"] == "created"


def test_evidence_capped(store):
    for i in range(5):
        store.ingest(P, f"U{i}", f"访谈{i}", [{**PAIN, "quote": f"原话{i}"}])
    top = store.overview(P)["insights"][0]
    assert top["mention_count"] == 5
    assert [e["quote"] for e in top["evidence"]] == ["原话2", "原话3", "原话4"]


def test_overview_fields_stay_aligned_after_sort(store):
    """回归：pymilvus 惰性 JSON 解码 + 排序会让摘要和原话/受访者错位。
    构造排序后顺序与写入顺序不同的数据，逐条核对字段归属。"""
    items = [{"type": "pain_point", "summary": f"独立问题{i}号：" + "甲乙丙丁戊己庚辛"[i] * 6,
              "quote": f"原话{i}"} for i in range(6)]
    store.ingest(P, "U1", "访谈一", items)
    # 让最后写入的两条被更多人提到，排序后跑到最前面
    store.ingest(P, "U2", "访谈二", [{**items[5], "quote": "U2说5"}, {**items[4], "quote": "U2说4"}])
    store.ingest(P, "U3", "访谈三", [{**items[5], "quote": "U3说5"}])
    rows = store.overview(P)["insights"]
    assert [r["mention_count"] for r in rows][:2] == [3, 2]
    for r in rows:
        i = r["summary"][4]  # "独立问题{i}号" 里的编号
        assert r["evidence"][0]["quote"] == f"原话{i}", r
        assert r["interviewees"][0] == "U1"
    assert rows[0]["interviewees"] == ["U1", "U2", "U3"]
    hits = store.search(P, items[2]["summary"], top_k=6)
    for h in hits:
        assert h["evidence"][0]["quote"] == f"原话{h['summary'][4]}"


def test_merge_only_within_type_group(store):
    """同样的文字，痛点与土办法不合并；workaround 与 current_solution 同组可合并。"""
    store.ingest(P, "U1", "访谈一", [PAIN])
    r = store.ingest(P, "U2", "访谈二", [{**PAIN, "type": "workaround", "quote": "x"}])
    assert r["results"][0]["action"] == "created"
    r = store.ingest(P, "U3", "访谈三", [{**PAIN, "type": "current_solution", "quote": "y"}])
    assert r["results"][0]["action"] == "merged" and r["results"][0]["mention_count"] == 2
    r = store.ingest(P, "U4", "访谈四", [{**PAIN, "quote": "z"}])
    assert r["results"][0]["action"] == "merged" and r["results"][0]["mention_count"] == 2
    assert store.overview(P)["total_insights"] == 2


def test_delete_project_only_affects_that_project(store):
    store.ingest(P, "U1", "访谈一", [PAIN, UNRELATED])
    store.ingest("别的方向", "U1", "访谈一", [PAIN])
    assert store.delete_project(P) == {"deleted_insights": 2, "deleted_sessions": 1}
    ov = store.overview(P)
    assert ov["total_insights"] == 0 and ov["total_sessions"] == 0
    assert store.overview("别的方向")["total_insights"] == 1
    # 清空后同一份材料可以重新入库
    assert store.ingest(P, "U1", "访谈一", [PAIN])["duplicate_session"] is False


def test_search_and_overview_order(store):
    store.ingest(P, "U1", "访谈一", [PAIN, UNRELATED])
    store.ingest(P, "U2", "访谈二", [PAIN])
    hits = store.search(P, "手工对账很花时间")
    assert hits[0]["summary"] == PAIN["summary"]
    ov = store.overview(P)
    assert [i["mention_count"] for i in ov["insights"]] == [2, 1]
    assert ov["total_interviewees"] == 2
