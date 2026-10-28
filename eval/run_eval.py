"""端到端评测：通过 Dify 正式 API 依次跑评测集，再对最终记忆和每次输出打分。

用法：uv run --no-project --python 3.12 --with httpx python run_eval.py [--skip-runs]
结果：results/<时间>/ 下保存每次运行原始输出、最终记忆、指标 summary.json 和 report.md
"""

import json
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

import httpx

import dataset as D

HERE = Path(__file__).parent
ENV = dict(l.split("=", 1) for l in (HERE / ".env").read_text().split() if "=" in l)
MS_ENV = dict(l.split("=", 1) for l in (HERE.parent / "memory-service/.env").read_text(encoding="utf-8").splitlines()
              if "=" in l and not l.startswith("#"))
PII = ["陈丽华", "王建国", "刘芳", "13812349876", "shuma_zhang88"]
http = httpx.Client(timeout=900, trust_env=False)


# ---------------- 运行 ----------------
def upload(path: Path) -> dict:
    kind = {".wav": "audio", ".mp3": "audio", ".png": "image", ".jpg": "image", ".mp4": "video"}[path.suffix]
    r = http.post(f"{ENV['DIFY_BASE']}/files/upload", headers={"Authorization": f"Bearer {ENV['DIFY_ANALYSIS_KEY']}"},
                  files={"file": (path.name, path.read_bytes())}, data={"user": "eval"})
    r.raise_for_status()
    return {"type": kind, "transfer_method": "local_file", "upload_file_id": r.json()["id"]}


def run_workflow(key: str, inputs: dict) -> dict:
    """streaming 模式跑工作流，收集节点耗时与最终输出。"""
    t = time.time()
    nodes, fin = [], None
    with http.stream("POST", f"{ENV['DIFY_BASE']}/workflows/run",
                     headers={"Authorization": f"Bearer {key}"},
                     json={"inputs": inputs, "response_mode": "streaming", "user": "eval"}) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:])
            if ev.get("event") == "node_finished":
                d = ev["data"]
                nodes.append({"id": d["node_id"], "status": d["status"], "s": round(d.get("elapsed_time") or 0, 1),
                              "error": d.get("error")})
            elif ev.get("event") == "workflow_finished":
                fin = ev["data"]
    return {"status": fin and fin["status"], "error": fin and fin.get("error"),
            "outputs": (fin or {}).get("outputs") or {}, "tokens": (fin or {}).get("total_tokens"),
            "elapsed": round(time.time() - t, 1), "nodes": nodes}


def run_all(out: Path) -> list[dict]:
    ms = {"Authorization": f"Bearer {ENV['MEMORY_API_KEY']}"}
    print("重置评测记忆：", http.delete(f"{ENV['MEMORY_BASE']}/v1/projects", params={"project": D.PROJECT}, headers=ms).json())
    by_id = {r["id"]: r for r in D.RUNS}
    results = []
    for case in D.RUNS:
        transcript = case.get("transcript", "")
        if case.get("same_as"):
            transcript = "\n\n" + by_id[case["same_as"]]["transcript"].replace("\n", "\n\n") + "\n"
        files = [upload(HERE / "fixtures" / f) for f in case.get("files", [])]
        inputs = {"project": D.PROJECT, "interviewee_id": case["interviewee_id"],
                  "interviewee_profile": case["profile"], "real_names": case["real_names"],
                  "transcript": transcript}
        if files:
            inputs["files"] = files
        print(f"→ {case['id']} {case['interviewee_id']} ...", end=" ", flush=True)
        res = run_workflow(ENV["DIFY_ANALYSIS_KEY"], inputs)
        res.update(id=case["id"], transcript=transcript)
        print(res["status"], f"{res['elapsed']}s", res["error"] or "")
        (out / f"{case['id']}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(res)
    return results


# ---------------- 打分 ----------------
LABEL_PROMPT = """把每条"用户调研洞察"归类到下列标签之一，只输出 JSON：{{"labels": ["P1", ...]}}（与输入顺序一一对应）。
P1 月底/定期 多平台对账耗时、对不上、平台扣费算不清
P2 售后/客服咨询量大、重复问题多、回复不过来
P3 每天手工导出并汇总多平台订单（复制粘贴到总表、打单）
P4 选品难、找不到爆款、压货
P5 恶意差评、职业差评师、要挟退款
LOGISTICS 物流/快递方面的痛点
WTP_POS 愿意为某工具或服务付费 / 已经付费
WTP_NEG 嫌贵、不愿付费
OTHER 以上都不是（现有工具、背景信息等）
注意：只有描述"问题本身"的才归入 P1~P5；付费意愿归 WTP_*；工具/背景归 OTHER。

洞察：
{items}"""


def label(summaries: list[str]) -> list[str]:
    """按编号返回标签（{"labels": {"0": "P1", ...}}），每批 10 条，避免长列表漏项错位。"""
    out = []
    for b in range(0, len(summaries), 10):
        batch = summaries[b:b + 10]
        items = "\n".join(f"{i}. {s}" for i, s in enumerate(batch))
        prompt = LABEL_PROMPT.format(items=items).replace(
            '{"labels": ["P1", ...]}（与输入顺序一一对应）', '{"labels": {"0": "P1", "1": "OTHER", ...}}（键是洞察编号，每个编号都要有）')
        r = http.post(MS_ENV.get("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1") + "/chat/completions",
                      headers={"Authorization": f"Bearer {MS_ENV['DASHSCOPE_API_KEY']}"},
                      json={"model": "qwen-plus", "temperature": 0, "response_format": {"type": "json_object"},
                            "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        labels = json.loads(r.json()["choices"][0]["message"]["content"])["labels"]
        missing = [i for i in range(len(batch)) if str(i) not in labels]
        assert not missing, f"标注缺项 {missing}"
        out += [labels[str(i)] for i in range(len(batch))]
    return out


def norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s or "")


def quote_in(q: str, text: str) -> bool:
    q, t = norm(q), norm(text)
    if not q:
        return False
    if q in t:
        return True
    n = len(q)
    return any(SequenceMatcher(None, t[i:i + n], q).ratio() >= 0.85 for i in range(0, max(1, len(t) - n + 1)))


def score(results: list[dict], out: Path) -> dict:
    ms = {"Authorization": f"Bearer {ENV['MEMORY_API_KEY']}"}
    ov = http.get(f"{ENV['MEMORY_BASE']}/v1/overview", params={"project": D.PROJECT, "limit": 500}, headers=ms).json()
    (out / "memory.json").write_text(json.dumps(ov, ensure_ascii=False, indent=2), encoding="utf-8")
    ins = ov["insights"]
    for x, lab in zip(ins, label([i["summary"] for i in ins])):
        x["label"] = lab
    m: dict = {"runs": {}, "pains": {}, "checks": {}}
    by_id = {r["id"]: r for r in results}

    # 每次运行：状态、耗时、提取召回（该次 verified 洞察里是否出现了埋点痛点）
    for case in D.RUNS:
        r = by_id[case["id"]]
        row = {"status": r["status"], "elapsed_s": r["elapsed"], "tokens": r["tokens"]}
        if case.get("expect_skip"):
            row["skipped_ok"] = "已分析过" in (r["outputs"].get("message") or "")
        else:
            v = json.loads(r["outputs"].get("verified_json") or "{}")
            vi = v.get("insights", [])
            labs = label([i["summary"] for i in vi]) if vi else []
            row.update(verified=len(vi), rejected=len(v.get("rejected", [])),
                       found=sorted({l for l in labs if l.startswith("P")}),
                       recall=sum(p in labs for p in case["pains"]) / len(case["pains"]),
                       plan_ok=_plan_ok(r["outputs"].get("plan_json")))
            if r["transcript"]:  # 文字类：独立复核原话真实性（先对原文做同样的人名脱敏，否则含[受访者]的原话必然对不上）
                src = r["transcript"]
                for n in filter(None, (case["real_names"] or "").split(",")):
                    src = src.replace(n.strip(), "[受访者]")
                row["quote_fidelity"] = (sum(quote_in(i["quote"], src) for i in vi) / len(vi)) if vi else None
            row["insight_labels"] = [[l, i["type"], i["summary"], i.get("match")] for l, i in zip(labs, vi)]
        m["runs"][case["id"]] = row

    # 记忆：每个痛点的簇数（碎片化）、计数是否正确、是否混入不该有的受访者（误合并）
    for pid, p in D.PAINS.items():
        clusters = [x for x in ins if x["label"] == pid]
        people = sorted({u for c in clusters for u in c["interviewees"]})
        # 主簇：该痛点下 type=pain_point 中人数最多的一条（workaround/context/子原因单独成条是正常的）
        mains = [c for c in clusters if c["type"] == "pain_point"]
        best = max(mains or clusters, key=lambda c: c["mention_count"], default=None)
        m["pains"][pid] = {
            "name": p["name"], "expected": p["expected"], "clusters": len(clusters),
            "best_cluster_count": best["mention_count"] if best else 0,
            "union_people": people,
            "recall_people": len(set(people) & set(p["expected"])) / len(p["expected"]),
            "intruders": sorted(set(people) - set(p["expected"])),
            "main_cluster_people": best["interviewees"] if best else [],
            "count_exact": bool(best) and sorted(best["interviewees"]) == sorted(p["expected"]),
        }

    dump = json.dumps(ov, ensure_ascii=False)
    wtp = [x for x in ins if x["label"] in ("WTP_POS", "WTP_NEG")]
    m["checks"] = {
        # 只看"痛点"类型：如实记录"物流没问题"（type=other）是正确行为，不算被引导
        "no_logistics_pain(T_LEAD)": not any(x["label"] == "LOGISTICS" and x["type"] == "pain_point" for x in ins),
        "pii_absent(T_PII)": [p for p in PII if p in dump] == [],
        "dup_skipped(T_DUP)": m["runs"]["R10"].get("skipped_ok", False),
        # 不同受访者：U01~U06、U08、U09 共 8 人（R07 是 U01 回访，R10 重复被跳过）
        "same_person_not_counted(T_SAME)": all(len(set(x["interviewees"])) == x["mention_count"] for x in ins)
                                         and ov["total_interviewees"] == 8,
        # U01 愿付费、U05 嫌贵：任何一条付费意愿记录同时含两人 = 正反态度被合并
        "wtp_pos_neg_separate(T_WTP)": bool(wtp) and not any({"U01", "U05"} <= set(x["interviewees"]) for x in wtp),
        "all_runs_succeeded": all(r["status"] == "succeeded" for r in results),
        "all_plans_valid": all(v.get("plan_ok", True) for v in m["runs"].values()),
    }
    m["memory_totals"] = {k: ov[k] for k in ("total_insights", "total_sessions", "total_interviewees")}
    m["memory_dump"] = [[x["label"], x["mention_count"], x["interviewees"], x["type"], x["summary"]] for x in ins]
    return m


def _plan_ok(plan_json: str | None) -> bool:
    try:
        p = json.loads(plan_json or "")
    except ValueError:
        return False
    acts = p.get("next_actions") or []
    need = {"id", "type", "goal", "steps", "success_criteria", "priority", "owner"}
    return bool(acts) and all(need <= set(a) for a in acts) and not p.get("meta", {}).get("warnings")


def write_report(m: dict, out: Path):
    L = ["# 端到端评测报告", "", "## 每次运行", "| 运行 | 状态 | 耗时s | 核对通过 | 拒绝 | 召回 | 原话复核 | 计划合法 | 识别到 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for rid, r in m["runs"].items():
        if "skipped_ok" in r:
            L.append(f"| {rid} | {r['status']} | {r['elapsed_s']} | - | - | - | - | - | 重复跳过={r['skipped_ok']} |")
            continue
        fid = "-" if r.get("quote_fidelity") is None else f"{r['quote_fidelity']:.0%}"
        L.append(f"| {rid} | {r['status']} | {r['elapsed_s']} | {r['verified']} | {r['rejected']} | {r['recall']:.0%} | "
                 f"{fid} | {r['plan_ok']} | {','.join(r['found'])} |")
    L += ["", "## 记忆中的痛点计数", "| 痛点 | 期望受访者 | 主簇受访者 | 主簇计数正确 | 相关条数(含子原因/土办法) | 覆盖受访者 | 误混入 |",
          "|---|---|---|---|---|---|---|"]
    for pid, p in m["pains"].items():
        L.append(f"| {pid} {p['name']} | {','.join(p['expected'])} | {','.join(p['main_cluster_people'])} | "
                 f"{p['count_exact']} | {p['clusters']} | {p['recall_people']:.0%} | {','.join(p['intruders']) or '-'} |")
    L += ["", "## 陷阱与整体检查", "| 检查 | 结果 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in m["checks"].items()]
    L += ["", f"记忆总量：{m['memory_totals']}", "", "## 记忆明细（标签由 LLM 归类，供人工复核）",
          "| 标签 | 人数 | 受访者 | 类型 | 摘要 |", "|---|---|---|---|---|"]
    L += [f"| {a} | {b} | {','.join(c)} | {d} | {e} |" for a, b, c, d, e in m["memory_dump"]]
    (out / "report.md").write_text("\n".join(L), encoding="utf-8")
    return "\n".join(L)


if __name__ == "__main__":
    out = HERE / "results" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    if "--skip-runs" in sys.argv:
        prev = sorted((HERE / "results").glob("*/R01.json"))[-1].parent
        results = [json.loads((prev / f"{c['id']}.json").read_text(encoding="utf-8")) for c in D.RUNS]
    else:
        results = run_all(out)
    m = score(results, out)
    (out / "summary.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    print(write_report(m, out))
    print("\n结果目录：", out)
