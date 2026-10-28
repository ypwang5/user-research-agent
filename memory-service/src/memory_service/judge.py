"""模糊区裁判：向量相似度落在 [candidate, auto) 之间时，由 LLM 判断是否同一洞察。

校准发现：同义改写(0.80~0.92)与"相近但不同"(0.70~0.81，如愿意付费 vs 不愿付费)
的相似度区间重叠，单一阈值必然误判，所以模糊区交给 LLM。
"""

import json
import re
from typing import Protocol

import httpx

PROMPT = """你在做用户调研洞察的去重。判断【新洞察】是否和某个【候选】描述的是同一件事。

"同一件事"的标准：同一个用户问题/需求，且态度方向一致。
以下都算"不同"：
- 同一领域的不同环节（如"回复不过来"vs"答不上来"、"剪辑耗时"vs"不知道拍什么"）
- 同一流程里的不同步骤，或发生频率/场景明显不同（如"每天汇总订单"vs"月底核对账目"）
- 相反态度（如"愿意付费"vs"不愿付费"）
- 不同原因（如"外包质量差"vs"外包太贵"）
措辞、数字细节不同但说的是同一件事，算"相同"。

【新洞察】{new}

【候选】
{cands}

只输出 JSON：{{"match": 候选编号}}，都不相同则 {{"match": -1}}。"""


class Judge(Protocol):
    def pick(self, new: str, candidates: list[str]) -> int | None: ...


class LLMJudge:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.model = model
        self._http = httpx.Client(base_url=base_url, timeout=60, trust_env=False,
                                  headers={"Authorization": f"Bearer {api_key}"})

    def pick(self, new: str, candidates: list[str]) -> int | None:
        cands = "\n".join(f"{i}. {c}" for i, c in enumerate(candidates))
        r = self._http.post("/chat/completions", json={
            "model": self.model,
            "messages": [{"role": "user", "content": PROMPT.format(new=new, cands=cands)}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        })
        r.raise_for_status()
        return parse_match(r.json()["choices"][0]["message"]["content"], len(candidates))


def parse_match(text: str, n: int) -> int | None:
    """解析裁判输出；格式不对或越界一律当"不相同"——宁可多存一条，不可错合并。"""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        idx = int(json.loads(m.group(0))["match"]) if m else -1
    except (ValueError, KeyError, TypeError):
        return None
    return idx if 0 <= idx < n else None


def build_judge(settings) -> Judge | None:
    if not settings.judge_model or not settings.dashscope_api_key:
        return None
    return LLMJudge(settings.dashscope_api_key, settings.dashscope_base_url, settings.judge_model)
