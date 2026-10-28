from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    milvus_uri: str = "http://127.0.0.1:19530"
    milvus_token: str = "root:Milvus"
    collection_prefix: str = "urm_"

    # dashscope：真实向量；fake：字符 n-gram 哈希，仅用于离线测试
    embed_provider: str = "dashscope"
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embed_model: str = "text-embedding-v4"
    embed_dim: int = 1024

    # 无裁判时：余弦相似度 >= 该值视为"同一个洞察"，合并而不新建
    merge_threshold: float = 0.85
    # 有裁判时（推荐，阈值来自 scripts/calibrate.py 的真实向量校准）：
    # >= auto 直接合并；[candidate, auto) 交 LLM 裁判；< candidate 新建
    judge_model: str = "qwen-plus"
    auto_merge_threshold: float = 0.90
    # 0.70 时端到端评测漏合并了"同一痛点不同场景"（相似度 0.63）；降到 0.55 后校准集 25 对 3 轮全对
    candidate_threshold: float = 0.55
    # 多模态：语音转写、图片/视频帧理解
    asr_model: str = "qwen3-asr-flash"
    vl_model: str = "qwen3-vl-plus"

    # 每条洞察最多保留多少条原话证据，防止记录无限膨胀
    max_evidence: int = 20

    # 非空时要求请求头 Authorization: Bearer <key>
    api_key: str = ""


settings = Settings()
