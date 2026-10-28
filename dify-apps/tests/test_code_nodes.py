import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import code_nodes as C  # noqa: E402

TEXT = "我是开淘宝店的。最头疼的是售后，每天晚上要回两三百条消息，可我一个人根本回不过来。一个月三百块我是愿意出的。"


# ---------- 应用 A ----------
def test_mem_summary_cases():
    assert C.mem_summary("", 500)["insight_count"] == 0
    r = C.mem_summary(json.dumps({"insights": []}), 200)
    assert "首轮" in r["memory_text"]
    body = {"total_interviewees": 3, "total_sessions": 3, "total_insights": 1, "insights": [
        {"type": "pain_point", "summary": "售后回复不过来", "mention_count": 3,
         "evidence": [{"quote": "回不过来"}, {"quote": ""}, {"quote": "第三条"}]}]}
    r = C.mem_summary(json.dumps(body), 200)
    assert r["insight_count"] == 1
    assert "（3 人提到）“回不过来”" in r["memory_text"] and "第三条" not in r["memory_text"]


# ---------- 应用 B ----------
def test_merge_inputs():
    ext = {"text": "### 文件：a.wav（audio）\n转写", "items": [{"filename": "b.mp4", "error": "坏了"}]}
    r = C.merge_inputs("手写记录", json.dumps(ext), 200, "张三，李四、 王五")
    p = json.loads(r["redact_payload"])
    assert p["names"] == ["张三", "李四", "王五"]
    assert p["text"].startswith("### 访谈记录（文字）\n手写记录") and "转写" in p["text"]
    assert r["media_errors"] == "b.mp4：坏了"
    r = C.merge_inputs("", "", 500, "")
    assert json.loads(r["redact_payload"])["text"] == "" and "500" in r["media_errors"]
    r = C.merge_inputs(None, json.dumps({"text": "", "items": []}), 200, None)
    assert json.loads(r["redact_payload"]) == {"text": "", "names": []}


def test_prep_and_route():
    r = C.prep_check(json.dumps({"text": "t", "counts": {"[手机号]": 2}}), "p")
    assert r["redact_summary"] == "[手机号]×2"
    assert json.loads(r["check_payload"]) == {"project": "p", "content": "t"}
    assert C.route("", 0, "  ")["route"] == "empty"
    assert C.route("", 502, "x")["route"] == "error"
    assert C.route('{"duplicate": true, "session_id": "s1"}', 200, "x")["route"] == "duplicate"
    assert C.route('{"duplicate": false}', 200, "x") == {"route": "ok", "skip_message": ""}


def _llm(insights, fenced=False):
    s = json.dumps({"profile": "女装店主", "insights": insights, "open_questions": ["客单价？"]},
                   ensure_ascii=False)
    return f"```json\n{s}\n```" if fenced else s


def test_verify_exact_approx_reject():
    ins = [
        {"type": "pain_point", "summary": "售后消息回复不过来", "quote": "每天晚上要回两三百条消息"},
        # 标点/空格不同 → 归一化后仍是子串
        {"type": "willingness_to_pay", "summary": "愿付 300/月", "quote": "一个月 三百块 我是愿意出的"},
        # 个别字不同（ASR 误差）→ 近似匹配
        {"type": "pain_point", "summary": "一人忙不过来", "quote": "可我一个人根本会不过来"},
        # 编造的原话 → 拒绝
        {"type": "goal", "summary": "想扩张", "quote": "我明年想开三家分店"},
        {"type": "bogus", "summary": "类型非法", "quote": "我是开淘宝店的"},
        {"type": "other", "summary": "", "quote": "x"},
    ]
    r = C.verify_quotes(_llm(ins, fenced=True), TEXT, "p", "U1")
    v = json.loads(r["verified_json"])
    assert r["verified_count"] == 4 and r["rejected_count"] == 2
    assert [i["match"] for i in v["insights"]] == ["exact", "exact", "approx", "exact"]
    assert v["insights"][3]["type"] == "other"
    assert {x["reason"] for x in v["rejected"]} == {"原话在材料中找不到", "缺少 summary 或 quote"}
    p = json.loads(r["ingest_payload"])
    assert p["interviewee_id"] == "U1" and len(p["insights"]) == 4 and p["content"] == TEXT


def test_verify_long_text_fast_and_strict():
    import random
    import time
    random.seed(0)
    filler = "".join(random.choice("今天我们聊了很多关于工作流程和工具使用的情况") for _ in range(100_000))
    text = filler[:50_000] + "每个月底要花两天时间手工核对三个平台的账单" + filler[50_000:]
    ins = [
        {"type": "pain_point", "summary": "对账耗时", "quote": "每个月底要花两天时间手工核对三个平台的帐单"},  # 错 1 字
        {"type": "pain_point", "summary": "编造", "quote": "每个季度要花一周时间请会计核对五个平台"},  # 部分相似但编造
    ]
    t = time.time()
    r = C.verify_quotes(_llm(ins), text, "p", "U1")
    assert time.time() - t < 5
    v = json.loads(r["verified_json"])
    assert [i["summary"] for i in v["insights"]] == ["对账耗时"]
    assert [x["summary"] for x in v["rejected"]] == ["编造"]


def test_verify_ellipsis_segments():
    ocr = "2026年9月 对账表（手工）\n09-01 抖音 8,920 8,412 -508 对不上，待查\n……共 312 行，每月底要花两天核对，经常对错"
    ins = [
        {"type": "pain_point", "summary": "对账耗时", "quote": "2026年9月 对账表（手工）……共 312 行，每月底要花两天核对，经常对错"},
        {"type": "pain_point", "summary": "半真半假", "quote": "对账表（手工）……每周要请会计来核对三次"},
        {"type": "pain_point", "summary": "碎片太短", "quote": "对账……核对"},
    ]
    v = json.loads(C.verify_quotes(_llm(ins), ocr, "p", "U1")["verified_json"])
    assert [(i["summary"], i["match"]) for i in v["insights"]] == [("对账耗时", "segments")]
    assert {r["summary"] for r in v["rejected"]} == {"半真半假", "碎片太短"}


def test_verify_garbage_llm_output():
    r = C.verify_quotes("抱歉，我无法完成", TEXT, "p", "U1")
    assert r["verified_count"] == 0 and json.loads(r["ingest_payload"])["insights"] == []


def test_build_context():
    v = {"profile": "店主", "insights": [
        {"type": "pain_point", "summary": "A", "quote": "qa", "severity": 4},
        {"type": "pain_point", "summary": "B", "quote": "qb"}], "open_questions": ["客单价？"]}
    ingest = {"results": [{"action": "created"},
                          {"action": "merged", "matched_summary": "B旧", "mention_count": 3}]}
    ov = {"total_interviewees": 4, "total_sessions": 4, "total_insights": 2,
          "insights": [{"type": "pain_point", "summary": "B旧", "mention_count": 3, "evidence": []}]}
    r = C.build_context(json.dumps(ingest), 200, json.dumps(ov), json.dumps(v, ensure_ascii=False))
    assert "记忆：新发现" in r["synthesis_context"]
    assert "与历史洞察合并：B旧，现累计 3 人" in r["synthesis_context"]
    assert "客单价？" in r["synthesis_context"]
    s = json.loads(r["stats_json"])
    assert s["created"] == 1 and s["merged"] == 1 and s["total_interviewees"] == 4
    # 入库失败时不崩，标"未入库"
    r = C.build_context("", 500, json.dumps(ov), json.dumps(v, ensure_ascii=False))
    assert "未入库" in r["synthesis_context"]


def test_render_report():
    plan = {"session_summary": "总结", "opportunities": [
        {"id": "O1", "name": "低分", "scores": {"pain": 1, "frequency": 1}},
        {"id": "O2", "name": "高分", "scores": {"pain": 9, "frequency": 5, "willingness_to_pay": "4",
                                               "reachability": 3, "competition_gap": None}}],
        "next_actions": [{"id": "A1", "type": "interview", "title": "再访谈", "depends_on": []},
                         {"id": "A2", "type": "build_app", "title": "做", "depends_on": ["A1", "A9"]}],
        "hypotheses": [{"id": "H1", "statement": "会付费", "status": "待验证"}]}
    stats = {"created": 1, "merged": 2, "same_interviewee": 0, "total_interviewees": 3, "total_insights": 5}
    v = {"insights": [{}], "rejected": [{"summary": "编的", "reason": "原话在材料中找不到"}]}
    r = C.render_report("```json\n" + json.dumps(plan, ensure_ascii=False) + "\n```",
                        json.dumps(stats), json.dumps(v, ensure_ascii=False), "x.mp4：坏了", "无")
    p = json.loads(r["plan_json"])
    assert [o["id"] for o in p["opportunities"]] == ["O2", "O1"]
    assert p["opportunities"][0]["scores"] == {"pain": 5, "frequency": 5, "willingness_to_pay": 4,
                                               "reachability": 3, "competition_gap": 1}
    assert p["opportunities"][0]["total_score"] == 4.1  # 5*.3+5*.2+4*.3+3*.1+1*.1
    assert p["next_actions"][1]["type"] == "other" and p["next_actions"][1]["depends_on"] == ["A1"]
    assert any("A9" in w for w in p["meta"]["warnings"])
    for sec in ["## 机会排序", "## 假设追踪", "## 下一步计划", "## 未通过原话核对", "## 处理告警", "x.mp4：坏了"]:
        assert sec in r["report"]


def test_render_report_invalid_json():
    r = C.render_report("不是JSON", json.dumps({}), json.dumps({}), "", "无")
    p = json.loads(r["plan_json"])
    assert p["meta"]["warnings"] and "不是合法 JSON" in p["meta"]["warnings"][0]
    assert "# 访谈分析报告" in r["report"]
