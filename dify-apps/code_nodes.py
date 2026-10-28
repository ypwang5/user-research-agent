"""Dify 代码节点的实现。每个函数自包含（import 写在函数内），
build_dsl.py 用 inspect 取源码并把函数名改成 main 写进 DSL；本地可直接 pytest。
"""


# ===================== 应用 A：提纲生成 =====================
def mem_summary(body: str, status_code: int) -> dict:
    """把 /v1/overview 结果整理成给 LLM 看的文字。"""
    import json

    if status_code != 200:
        return {"memory_text": f"（记忆服务不可用，status={status_code}，按首轮调研处理）",
                "insight_count": 0}
    data = json.loads(body)
    rows = data.get("insights", [])
    if not rows:
        return {"memory_text": "（暂无历史记忆，这是该方向的首轮调研）", "insight_count": 0}
    lines = [f"已访谈 {data.get('total_interviewees', 0)} 人、{data.get('total_sessions', 0)} 次，"
             f"累计 {data.get('total_insights', 0)} 条洞察。按提及人数排序："]
    for r in rows:
        quotes = "；".join(f"“{e['quote']}”" for e in r.get("evidence", [])[:2] if e.get("quote"))
        lines.append(f"- [{r['type']}] {r['summary']}（{r['mention_count']} 人提到）{quotes}")
    return {"memory_text": "\n".join(lines), "insight_count": len(rows)}


# ===================== 应用 B：访谈分析 =====================
def merge_inputs(transcript: str, extract_body: str, extract_status: int, real_names: str) -> dict:
    """合并文字记录与文件转写结果，生成脱敏请求。"""
    import json
    import re

    parts, errors = [], []
    if transcript and transcript.strip():
        parts.append("### 访谈记录（文字）\n" + transcript.strip())
    if extract_status == 200:
        data = json.loads(extract_body or "{}")
        if data.get("text"):
            parts.append(data["text"])
        errors = [f"{i['filename']}：{i['error']}" for i in data.get("items", []) if "error" in i]
    elif extract_status:
        errors = [f"文件处理接口异常 status={extract_status}"]
    names = [n.strip() for n in re.split(r"[,，、;；\s]+", real_names or "") if n.strip()]
    payload = {"text": "\n\n".join(parts), "names": names}
    return {"redact_payload": json.dumps(payload, ensure_ascii=False),
            "media_errors": "\n".join(errors)}


def prep_check(redact_body: str, project: str) -> dict:
    import json

    r = json.loads(redact_body)
    text = r.get("text", "")
    counts = r.get("counts", {})
    return {"clean_text": text,
            "redact_summary": "、".join(f"{k}×{v}" for k, v in counts.items()) or "无",
            "check_payload": json.dumps({"project": project, "content": text}, ensure_ascii=False)}


def route(check_body: str, check_status: int, clean_text: str) -> dict:
    """ok：继续分析；empty：没有任何内容；duplicate：同一份材料已分析过。"""
    import json

    if not clean_text.strip():
        return {"route": "empty", "skip_message": "没有可分析的内容：请填写访谈记录或上传文件（若上传了文件，请检查文件处理错误）。"}
    if check_status != 200:
        return {"route": "error", "skip_message": f"记忆服务异常（status={check_status}），未做分析。"}
    r = json.loads(check_body)
    if r.get("duplicate"):
        return {"route": "duplicate",
                "skip_message": f"这份材料之前已分析过（session {r.get('session_id')}），为避免重复计数已跳过。"}
    return {"route": "ok", "skip_message": ""}


def verify_quotes(llm_text: str, clean_text: str, project: str, interviewee_id: str) -> dict:
    """确定性核对：每条洞察的 quote 必须真的出现在材料里，不靠模型自觉。

    归一化（去空白和标点）后做子串匹配；不命中再做近似匹配兜底（容忍 ASR 个别错字），
    仍不命中的洞察不入库。
    """
    import difflib
    import json
    import re

    allowed = {"pain_point", "workaround", "willingness_to_pay", "current_solution",
               "goal", "context", "other"}

    def norm(s):
        return re.sub(r"[\s\W_]+", "", s or "")

    def approx(src, q, threshold=0.85):
        """用 4 字锚点定位候选位置，再比较等长窗口的字符相似度。
        不用"最长公共子串"：中间错 1 个字就会把匹配切成两半，误判为编造。"""
        n = len(q)
        starts = set()
        for k in range(0, max(1, n - 3), 2):
            piece, pos, hits = q[k:k + 4], 0, 0
            while hits < 50:
                pos = src.find(piece, pos)
                if pos < 0:
                    break
                starts.add(pos - k)
                pos, hits = pos + 1, hits + 1
        best = 0.0
        for s in starts:
            for off in (-2, -1, 0, 1, 2):
                a = max(0, s + off)
                best = max(best, difflib.SequenceMatcher(None, src[a:a + n], q, autojunk=False).ratio())
        return best >= threshold

    def parse(t):
        t = re.sub(r"^```(?:json)?|```$", "", (t or "").strip(), flags=re.M).strip()
        try:
            return json.loads(t)
        except ValueError:
            m = re.search(r"\{.*\}", t, re.S)
            return json.loads(m.group(0)) if m else {}

    data = parse(llm_text)
    src = norm(clean_text)
    ok, bad = [], []
    for ins in data.get("insights", []) or []:
        summary = (ins.get("summary") or "").strip()
        q = norm(ins.get("quote"))
        if not summary or not q:
            bad.append({**ins, "reason": "缺少 summary 或 quote"})
            continue
        if q in src:
            match = "exact"
        elif approx(src, q):
            match = "approx"
        else:
            # 模型偶尔用省略号拼接多处原文：每一段都能在材料里找到才算通过
            segs = [norm(s) for s in re.split(r"…+|\.{3,}", ins.get("quote") or "")]
            segs = [s for s in segs if len(s) >= 4]
            ok_segs = len(segs) >= 2 and all(s in src or approx(src, s) for s in segs)
            match = "segments" if ok_segs else None
        if match:
            ins["type"] = ins.get("type") if ins.get("type") in allowed else "other"
            ins["match"] = match
            ok.append(ins)
        else:
            bad.append({**ins, "reason": "原话在材料中找不到"})

    ingest = {"project": project, "interviewee_id": interviewee_id, "content": clean_text,
              "insights": [{"type": i["type"], "summary": i["summary"][:2000],
                            "quote": i.get("quote", "")} for i in ok]}
    return {
        "ingest_payload": json.dumps(ingest, ensure_ascii=False),
        "verified_json": json.dumps({"profile": data.get("profile", ""), "insights": ok,
                                     "rejected": bad,
                                     "open_questions": data.get("open_questions", [])},
                                    ensure_ascii=False),
        "verified_count": len(ok),
        "rejected_count": len(bad),
    }


def build_context(ingest_body: str, ingest_status: int, overview_body: str,
                  verified_json: str) -> dict:
    """把"本次洞察 + 入库结果 + 累计记忆"拼成综合分析的输入。"""
    import json

    v = json.loads(verified_json)
    ins = v.get("insights", [])
    res = json.loads(ingest_body).get("results", []) if ingest_status == 200 else []
    label = {"created": "新发现", "merged": "与历史洞察合并", "same_interviewee": "同一受访者重复提及"}
    lines = [f"## 受访者画像\n{v.get('profile') or '（未知）'}", "\n## 本次访谈洞察（原话已核对）"]
    stats = {"created": 0, "merged": 0, "same_interviewee": 0}
    for i, x in enumerate(ins):
        r = res[i] if i < len(res) else {}
        act = r.get("action")
        if act in stats:
            stats[act] += 1
        tag = label.get(act, "未入库")
        if act in ("merged", "same_interviewee"):
            tag += f"：{r.get('matched_summary')}，现累计 {r.get('mention_count')} 人"
        lines.append(f"- [{x['type']}] {x['summary']}｜严重度{x.get('severity', '')}"
                     f"｜频率:{x.get('frequency') or '-'}｜代价:{x.get('cost') or '-'}"
                     f"\n  原话：“{x.get('quote', '')}”\n  记忆：{tag}")
    ov = json.loads(overview_body) if overview_body else {}
    lines.append(f"\n## 累计调研记忆（{ov.get('total_interviewees', 0)} 位受访者、"
                 f"{ov.get('total_sessions', 0)} 次访谈、{ov.get('total_insights', 0)} 条洞察，按提及人数排序）")
    for r in ov.get("insights", []):
        q = "；".join(f"“{e['quote']}”" for e in r.get("evidence", [])[:2] if e.get("quote"))
        lines.append(f"- [{r['type']}] {r['summary']}（{r['mention_count']} 人）{q}")
    if v.get("open_questions"):
        lines.append("\n## 本次材料中没说清的点\n" + "\n".join(f"- {q}" for q in v["open_questions"]))
    stats.update(total_interviewees=ov.get("total_interviewees", 0),
                 total_sessions=ov.get("total_sessions", 0),
                 total_insights=ov.get("total_insights", 0))
    return {"synthesis_context": "\n".join(lines), "stats_json": json.dumps(stats, ensure_ascii=False)}


def render_report(synth_text: str, stats_json: str, verified_json: str,
                  media_errors: str, redact_summary: str) -> dict:
    """校验综合分析 JSON、计算机会总分、渲染 Markdown 报告，输出给工作 Agent 的计划 JSON。"""
    import json
    import re

    weights = {"pain": 0.3, "frequency": 0.2, "willingness_to_pay": 0.3,
               "reachability": 0.1, "competition_gap": 0.1}
    action_types = {"interview", "desk_research", "prototype", "landing_page", "pricing_test", "other"}

    def parse(t):
        t = re.sub(r"^```(?:json)?|```$", "", (t or "").strip(), flags=re.M).strip()
        try:
            return json.loads(t), None
        except ValueError as e:
            m = re.search(r"\{.*\}", t, re.S)
            if m:
                try:
                    return json.loads(m.group(0)), None
                except ValueError:
                    pass
            return {}, str(e)

    def clamp(x):
        try:
            return max(1, min(5, int(x)))
        except (TypeError, ValueError):
            return 1

    plan, err = parse(synth_text)
    stats = json.loads(stats_json)
    v = json.loads(verified_json)
    warnings = []
    if err:
        warnings.append(f"综合分析输出不是合法 JSON：{err}")

    opps = plan.get("opportunities") or []
    for o in opps:
        s = {k: clamp((o.get("scores") or {}).get(k)) for k in weights}
        o["scores"] = s
        o["total_score"] = round(sum(s[k] * w for k, w in weights.items()), 2)
    opps.sort(key=lambda o: -o["total_score"])
    plan["opportunities"] = opps

    ids = {a.get("id") for a in plan.get("next_actions") or []}
    for a in plan.get("next_actions") or []:
        if a.get("type") not in action_types:
            a["type"] = "other"
        missing = [d for d in a.get("depends_on") or [] if d not in ids]
        if missing:
            warnings.append(f"{a.get('id')} 依赖了不存在的 action：{missing}")
            a["depends_on"] = [d for d in a["depends_on"] if d in ids]
    plan["meta"] = {"stats": stats, "verified_insights": len(v.get("insights", [])),
                    "rejected_insights": len(v.get("rejected", [])), "warnings": warnings}

    md = ["# 访谈分析报告", "", plan.get("session_summary", ""), ""]
    md.append(f"> 本次：新洞察 {stats.get('created', 0)} 条，与历史合并 {stats.get('merged', 0)} 条，"
              f"重复提及 {stats.get('same_interviewee', 0)} 条｜累计 {stats.get('total_interviewees', 0)} 位受访者、"
              f"{stats.get('total_insights', 0)} 条洞察｜脱敏：{redact_summary}")
    if plan.get("key_findings"):
        md += ["", "## 关键发现", "| 发现 | 提及人数 | 置信度 | 证据 |", "|---|---|---|---|"]
        for f in plan["key_findings"]:
            md.append(f"| {f.get('finding', '')} | {f.get('mention_count', '')} | {f.get('confidence', '')} | {f.get('evidence', '')} |")
    if opps:
        md += ["", "## 机会排序", "| # | 机会 | 总分 | 痛度 | 频率 | 付费 | 触达 | 空白 | 证据数 |",
               "|---|---|---|---|---|---|---|---|---|"]
        for o in opps:
            s = o["scores"]
            md.append(f"| {o.get('id')} | **{o.get('name')}**<br>{o.get('target_user', '')}：{o.get('problem', '')} | "
                      f"{o['total_score']} | {s['pain']} | {s['frequency']} | {s['willingness_to_pay']} | "
                      f"{s['reachability']} | {s['competition_gap']} | {o.get('evidence_count', '')} |")
    if plan.get("hypotheses"):
        md += ["", "## 假设追踪", "| ID | 假设 | 状态 | 如何验证 |", "|---|---|---|---|"]
        for h in plan["hypotheses"]:
            md.append(f"| {h.get('id')} | {h.get('statement')} | {h.get('status')} | {h.get('how_to_validate', '')} |")
    if plan.get("next_actions"):
        md += ["", "## 下一步计划", "| ID | 优先级 | 类型 | 负责 | 任务 | 成功标准 | 依赖 |", "|---|---|---|---|---|---|---|"]
        for a in plan["next_actions"]:
            md.append(f"| {a.get('id')} | {a.get('priority')} | {a.get('type')} | {a.get('owner')} | "
                      f"{a.get('title')} | {a.get('success_criteria', '')} | {','.join(a.get('depends_on') or []) or '-'} |")
    if plan.get("next_interview_focus"):
        md += ["", "## 下轮访谈重点"] + [f"- {q}" for q in plan["next_interview_focus"]]
    if plan.get("caveats"):
        md += ["", "## 局限"] + [f"- {c}" for c in plan["caveats"]]
    if v.get("rejected"):
        md += ["", "## 未通过原话核对（未入库）"] + [f"- {r.get('summary')}（{r.get('reason')}）" for r in v["rejected"]]
    if media_errors or warnings:
        md += ["", "## 处理告警"] + [f"- {x}" for x in (media_errors.splitlines() + warnings) if x]
    return {"report": "\n".join(md), "plan_json": json.dumps(plan, ensure_ascii=False, indent=2)}
