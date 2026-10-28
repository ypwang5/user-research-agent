"""用真实向量校准合并阈值。

same：同一洞察的不同说法，应合并；
hard：话题相近但其实是不同痛点/相反态度，不应合并（最容易误合并）；
unrelated：无关，不应合并。
输出各组相似度分布，以及每个候选阈值下的误合并/漏合并数。
"""

from memory_service.config import settings
from memory_service.embedder import build_embedder
from memory_service.judge import build_judge

SAME = [
    ("每月手工对账要花两天时间，经常出错", "月底对账全靠手工，得折腾两天还老对不上"),
    ("找不到靠谱的设计师，外包质量参差不齐", "外包设计师水平不稳定，很难找到靠谱的"),
    ("客户咨询太多，回复不过来", "每天客户消息量太大，客服根本回不完"),
    ("愿意每月花两三百块买能自动记账的工具", "如果有自动记账软件，每月付200-300元可以接受"),
    ("写小红书文案很费时间", "每篇小红书笔记的文案都要憋很久"),
    ("视频剪辑太耗时，一个视频要剪一整天", "剪一条视频得花一天，太费时间"),
    ("报销流程繁琐，要贴很多发票", "报销要贴一堆发票，流程很麻烦"),
    ("招聘时筛选简历工作量大", "每天要看几百份简历，筛选很累"),
    ("目前用Excel手工记录库存，经常算错", "库存靠Excel表手动登记，数字老是对不上"),
    ("家长每天花一小时辅导作业，很焦虑", "每晚陪孩子写作业一个小时，压力很大"),
    # 来自端到端评测的漏合并：同一痛点、不同场景表述（相似度仅 0.63）
    ("售后咨询量巨大且高度重复（尺码、发货时间），单人每天需回复两三百条消息至深夜",
     "直播后私信集中涌入，大量重复问题需人工逐条回复，回慢影响体验分"),
]
HARD = [
    ("每月手工对账要花两天时间，经常出错", "开发票的流程很繁琐"),
    ("写小红书文案很费时间", "小红书笔记发出去没有流量"),
    ("客户咨询太多，回复不过来", "客户咨询的问题很专业，答不上来"),
    ("愿意每月花两三百块买能自动记账的工具", "觉得记账工具太贵，不愿意付费"),
    ("视频剪辑太耗时，一个视频要剪一整天", "不知道拍什么视频选题"),
    ("招聘时筛选简历工作量大", "招来的人很快就离职"),
    ("报销流程繁琐，要贴很多发票", "公司报销打款太慢"),
    ("找不到靠谱的设计师，外包质量参差不齐", "外包设计师价格太贵"),
    ("目前用Excel手工记录库存，经常算错", "仓库空间不够放货"),
    ("家长每天花一小时辅导作业，很焦虑", "孩子课外班费用太高"),
    # 来自冒烟测试的误合并：同一流程不同步骤、频率不同
    ("月底需手工导出多平台订单与到账逐笔核对，耗时两天且对不上三十多笔",
     "多平台订单需手工导出并复制到表格汇总，每天耗时约四十分钟"),
]
UNRELATED = [
    ("每月手工对账要花两天时间，经常出错", "孩子课外班费用太高"),
    ("写小红书文案很费时间", "仓库空间不够放货"),
    ("招聘时筛选简历工作量大", "视频剪辑太耗时"),
]


def cos(a, b):
    return sum(x * y for x, y in zip(a, b))


def scores(emb, pairs):
    flat = [t for p in pairs for t in p]
    v = emb.embed(flat)
    return [cos(v[2 * i], v[2 * i + 1]) for i in range(len(pairs))]


def main():
    emb = build_embedder(settings)
    groups = {"same": SAME, "hard": HARD, "unrelated": UNRELATED}
    res = {k: scores(emb, p) for k, p in groups.items()}
    for k, pairs in groups.items():
        print(f"\n== {k} ==")
        for (a, b), s in sorted(zip(pairs, res[k]), key=lambda x: x[1]):
            print(f"{s:.3f}  {a} | {b}")
    print("\n阈值   漏合并(same<t)  误合并(hard/unrelated>=t)")
    for t in [0.60, 0.65, 0.70, 0.72, 0.75, 0.78, 0.80, 0.85]:
        miss = sum(s < t for s in res["same"])
        fp = sum(s >= t for s in res["hard"] + res["unrelated"])
        print(f"{t:.2f}   {miss:>2}/{len(SAME)}            {fp:>2}/{len(HARD) + len(UNRELATED)}")

    # 两段式：>=auto 直接合并，[candidate, auto) 交 LLM 裁判
    judge = build_judge(settings)
    if not judge:
        return
    auto, cand = settings.auto_merge_threshold, settings.candidate_threshold
    print(f"\n== 两段式（auto={auto}, candidate={cand}, judge={settings.judge_model}）==")
    wrong = 0
    for k, pairs in groups.items():
        for (a, b), s in zip(pairs, res[k]):
            if s >= auto:
                merged, how = True, "auto"
            elif s >= cand:
                merged, how = judge.pick(b, [a]) == 0, "judge"
            else:
                merged, how = False, "none"
            ok = merged == (k == "same")
            wrong += not ok
            print(f"{'✓' if ok else '✗'} {k:<9} {s:.3f} {how:<5} {'合并' if merged else '新建'}  {a} | {b}")
    total = sum(len(p) for p in groups.values())
    print(f"\n两段式错误：{wrong}/{total}")


if __name__ == "__main__":
    main()
