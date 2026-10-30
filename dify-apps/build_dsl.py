"""生成两个 Dify 应用的 DSL（YAML）。

用法：uv run --with pyyaml python build_dsl.py  →  输出 dist/interview_guide.yml、dist/interview_analysis.yml
"""

import inspect
import re
from pathlib import Path

import yaml

import code_nodes as C
import prompts as P

MEMORY = "http://host.docker.internal:8000"
AUTH_HEADER = "Authorization:Bearer {{#env.MEMORY_API_KEY#}}"
FLASH = {"provider": "langgenius/deepseek/deepseek", "name": "deepseek-v4-flash", "mode": "chat"}
PRO = {"provider": "langgenius/deepseek/deepseek", "name": "deepseek-v4-pro", "mode": "chat"}


class Graph:
    def __init__(self):
        self.nodes, self.edges, self.col = [], [], 0
        self.types = {}

    def add(self, nid: str, data: dict, x: int | None = None, y: int = 200):
        self.types[nid] = data["type"]
        if x is None:
            x = 40 + 320 * self.col
            self.col += 1
        self.nodes.append({
            "id": nid, "type": "custom", "data": {"selected": False, "desc": "", **data},
            "position": {"x": x, "y": y}, "positionAbsolute": {"x": x, "y": y},
            "width": 244, "height": 90, "selected": False,
            "sourcePosition": "right", "targetPosition": "left",
        })
        return nid

    def link(self, src: str, dst: str, handle: str = "source"):
        self.edges.append({
            "id": f"{src}-{handle}-{dst}", "type": "custom", "source": src, "target": dst,
            "sourceHandle": handle, "targetHandle": "target", "zIndex": 0,
            "data": {"sourceType": self.types[src], "targetType": self.types[dst],
                     "isInIteration": False, "isInLoop": False},
        })

    def chain(self, *ids):
        for a, b in zip(ids, ids[1:]):
            self.link(a, b)


# ---------- 节点工厂 ----------
def text_var(name, label, required=True, max_length=256):
    return {"variable": name, "label": label, "type": "text-input", "required": required,
            "max_length": max_length, "options": []}


def para_var(name, label, required=False, max_length=200000):
    return {"variable": name, "label": label, "type": "paragraph", "required": required,
            "max_length": max_length, "options": []}


def files_var(name, label):
    return {"variable": name, "label": label, "type": "file-list", "required": False,
            "max_length": 10, "options": [],
            "allowed_file_types": ["document", "image", "audio", "video"],
            "allowed_file_extensions": [], "allowed_file_upload_methods": ["local_file", "remote_url"]}


def start(variables):
    return {"type": "start", "title": "开始", "variables": variables}


def code(title, fn, inputs: dict, outputs: dict):
    src = inspect.getsource(fn)
    src = re.sub(rf"^def {fn.__name__}\(", "def main(", src, count=1)
    return {"type": "code", "title": title, "code_language": "python3", "code": src,
            "variables": [{"variable": k, "value_selector": v} for k, v in inputs.items()],
            "outputs": {k: {"type": t, "children": None} for k, t in outputs.items()}}


def http(title, method, path, body=None, params=""):
    return {"type": "http-request", "title": title, "method": method, "url": MEMORY + path,
            "authorization": {"type": "no-auth", "config": None},
            "headers": AUTH_HEADER, "params": params,
            "body": body or {"type": "none", "data": []},
            "timeout": {"connect": 10, "read": 600, "write": 600,
                        "max_connect_timeout": 0, "max_read_timeout": 0, "max_write_timeout": 0},
            "ssl_verify": True,
            "retry_config": {"enabled": True, "max_retries": 2, "retry_interval": 1000,
                             "exponential_backoff": {"enabled": False, "multiplier": 2, "max_interval": 10000}}}


def json_body(selector: str):
    return {"type": "json", "data": [{"key": "", "type": "text", "value": "{{#" + selector + "#}}"}]}


def llm(title, model, system, user, params):
    return {"type": "llm", "title": title, "model": {**model, "completion_params": params},
            "prompt_template": [{"role": "system", "text": system}, {"role": "user", "text": user}],
            "context": {"enabled": False, "variable_selector": []},
            "vision": {"enabled": False},
            "memory": None,
            "structured_output_enabled": False,
            "reasoning_format": "separated",
            "retry_config": {"enabled": True, "max_retries": 2, "retry_interval": 2000,
                             "exponential_backoff": {"enabled": False, "multiplier": 2, "max_interval": 10000}}}


def end(title, outputs: dict):
    return {"type": "end", "title": title,
            "outputs": [{"variable": k, "value_selector": v[0], "value_type": v[1]} for k, v in outputs.items()]}


def app_doc(name, desc, icon, graph: Graph):
    return {
        "app": {"name": name, "description": desc, "icon": icon, "icon_background": "#E4FBCC",
                "mode": "workflow", "use_icon_as_answer_icon": False},
        "kind": "app", "version": "0.7.0", "dependencies": [],
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [{"id": "env-memory-key", "name": "MEMORY_API_KEY",
                                       "value_type": "secret", "value": "", "description": "记忆服务 API Key"}],
            "features": {"file_upload": {"enabled": False}, "opening_statement": "",
                         "retriever_resource": {"enabled": False},
                         "sensitive_word_avoidance": {"enabled": False},
                         "speech_to_text": {"enabled": False}, "suggested_questions": [],
                         "suggested_questions_after_answer": {"enabled": False},
                         "text_to_speech": {"enabled": False}},
            "graph": {"nodes": graph.nodes, "edges": graph.edges,
                      "viewport": {"x": 0, "y": 0, "zoom": 0.6}},
        },
    }


# ---------- 应用 A：访谈提纲生成 ----------
def build_guide():
    g = Graph()
    g.add("start", start([
        text_var("project", "调研方向（项目名，两个应用要一致）", max_length=128),
        para_var("target_users", "目标用户", required=True, max_length=2000),
        para_var("hypotheses", "当前假设（可选）", max_length=4000),
        para_var("interview_context", "访谈形式/时长/补充说明（可选）", max_length=2000),
    ]))
    g.add("http_overview", http("读取调研记忆", "get", "/v1/overview",
                                params="project:{{#start.project#}}\nlimit:30"))
    g.add("code_mem", code("整理记忆", C.mem_summary,
                           {"body": ["http_overview", "body"], "status_code": ["http_overview", "status_code"]},
                           {"memory_text": "string", "insight_count": "number"}))
    g.add("llm_guide", llm("生成访谈提纲", FLASH, P.GUIDE_SYSTEM, P.GUIDE_USER,
                           {"temperature": 0.5, "max_tokens": 8000, "thinking": False}))
    g.add("end", end("结束", {"guide": (["llm_guide", "text"], "string"),
                             "memory_insights": (["code_mem", "insight_count"], "number")}))
    g.chain("start", "http_overview", "code_mem", "llm_guide", "end")
    return app_doc("① 访谈提纲生成", "结合历史调研记忆，按 Mom Test 原则生成访谈提纲", "🧭", g)


# ---------- 应用 B：访谈分析 ----------
def build_analysis():
    g = Graph()
    g.add("start", start([
        text_var("project", "调研方向（项目名，两个应用要一致）", max_length=128),
        text_var("interviewee_id", "受访者编号（如 U03，不要填真名）", max_length=64),
        para_var("interviewee_profile", "受访者画像（可选：行业/角色/规模）", max_length=2000),
        text_var("real_names", "需要脱敏的真实姓名（可选，逗号分隔）", required=False, max_length=512),
        para_var("transcript", "访谈记录文字（可选，与文件二选一或都填）"),
        files_var("files", "访谈文件（文档/图片/音频/视频，可多个）"),
    ]))
    g.add("http_extract", http("文件转文字", "post", "/v1/media/extract", body={
        "type": "form-data",
        "data": [{"key": "files", "type": "file", "file": ["start", "files"]},
                 {"key": "project", "type": "text", "value": "{{#start.project#}}"}]}))
    g.add("code_merge", code("合并材料", C.merge_inputs,
                             {"transcript": ["start", "transcript"],
                              "extract_body": ["http_extract", "body"],
                              "extract_status": ["http_extract", "status_code"],
                              "real_names": ["start", "real_names"]},
                             {"redact_payload": "string", "media_errors": "string"}))
    g.add("http_redact", http("脱敏", "post", "/v1/redact", body=json_body("code_merge.redact_payload")))
    g.add("code_prep", code("准备查重", C.prep_check,
                            {"redact_body": ["http_redact", "body"], "project": ["start", "project"]},
                            {"clean_text": "string", "redact_summary": "string", "check_payload": "string"}))
    g.add("http_check", http("查重", "post", "/v1/sessions/check", body=json_body("code_prep.check_payload")))
    g.add("code_route", code("路由判断", C.route,
                             {"check_body": ["http_check", "body"], "check_status": ["http_check", "status_code"],
                              "clean_text": ["code_prep", "clean_text"]},
                             {"route": "string", "skip_message": "string"}))
    g.add("if_ok", {"type": "if-else", "title": "是否继续", "cases": [{
        "case_id": "true", "id": "true", "logical_operator": "and",
        "conditions": [{"id": "cond-route", "varType": "string", "variable_selector": ["code_route", "route"],
                        "comparison_operator": "is", "value": "ok"}]}]})
    x_skip = 40 + 320 * g.col
    g.add("end_skip", end("跳过", {"message": (["code_route", "skip_message"], "string")}), x=x_skip, y=420)
    g.add("llm_extract", llm("提取洞察", FLASH, P.EXTRACT_SYSTEM, P.EXTRACT_USER,
                             {"temperature": 0.2, "max_tokens": 8000, "thinking": False,
                              "response_format": "json_object"}))
    g.add("code_verify", code("原话核对", C.verify_quotes,
                              {"llm_text": ["llm_extract", "text"], "clean_text": ["code_prep", "clean_text"],
                               "project": ["start", "project"], "interviewee_id": ["start", "interviewee_id"]},
                              {"ingest_payload": "string", "verified_json": "string",
                               "verified_count": "number", "rejected_count": "number"}))
    g.add("http_ingest", http("写入记忆（去重/计数）", "post", "/v1/ingest",
                              body=json_body("code_verify.ingest_payload")))
    g.add("http_overview", http("读取累计记忆", "get", "/v1/overview",
                                params="project:{{#start.project#}}\nlimit:40"))
    g.add("code_ctx", code("组装上下文", C.build_context,
                           {"ingest_body": ["http_ingest", "body"], "ingest_status": ["http_ingest", "status_code"],
                            "overview_body": ["http_overview", "body"], "verified_json": ["code_verify", "verified_json"]},
                           {"synthesis_context": "string", "stats_json": "string"}))
    g.add("llm_synth", llm("综合分析与计划", PRO, P.SYNTH_SYSTEM, P.SYNTH_USER,
                           {"temperature": 0.3, "max_tokens": 16000, "thinking": True,
                            "reasoning_effort": "high", "response_format": "json_object"}))
    g.add("code_report", code("校验与渲染报告", C.render_report,
                              {"synth_text": ["llm_synth", "text"], "stats_json": ["code_ctx", "stats_json"],
                               "verified_json": ["code_verify", "verified_json"],
                               "media_errors": ["code_merge", "media_errors"],
                               "redact_summary": ["code_prep", "redact_summary"]},
                              {"report": "string", "plan_json": "string"}))
    g.add("end", end("结束", {"report": (["code_report", "report"], "string"),
                             "plan_json": (["code_report", "plan_json"], "string"),
                             "verified_json": (["code_verify", "verified_json"], "string")}))
    g.chain("start", "http_extract", "code_merge", "http_redact", "code_prep", "http_check", "code_route", "if_ok")
    g.link("if_ok", "llm_extract", "true")
    g.link("if_ok", "end_skip", "false")
    g.chain("llm_extract", "code_verify", "http_ingest", "http_overview", "code_ctx", "llm_synth", "code_report", "end")
    return app_doc("② 访谈分析与计划", "多模态访谈材料 → 脱敏 → 洞察提取（原话核对）→ 长期记忆去重计数 → 机会评分与行动计划", "🔎", g)


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper, s):
    style = "|" if "\n" in s else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", s, style=style)


_Dumper.add_representer(str, _str)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--memory-url", help="记忆服务的公网地址（如 https://urm.wyphub.com）；给了就生成 Dify 云版到 dist/cloud/")
    args = ap.parse_args()
    out = Path(__file__).parent / "dist"
    if args.memory_url:
        MEMORY = args.memory_url.rstrip("/")
        out = out / "cloud"
    out.mkdir(parents=True, exist_ok=True)
    for fname, doc in [("interview_guide.yml", build_guide()), ("interview_analysis.yml", build_analysis())]:
        if args.memory_url:   # 公开演示：访客的材料会进同一个记忆库，写在应用说明里
            doc["app"]["description"] += "（公开演示：请勿上传真实隐私；同一调研方向的数据会共享，可能被定期清理）"
        (out / fname).write_text(yaml.dump(doc, Dumper=_Dumper, allow_unicode=True, sort_keys=False,
                                           width=10000), encoding="utf-8")
        print("wrote", out / fname, len(doc["workflow"]["graph"]["nodes"]), "nodes")
