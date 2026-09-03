# -*- coding: utf-8 -*-
"""RAG 重排评测：纯向量 Top-K vs 多召回 + RRF 混合重排。

运行: python python_backend/eval_rag.py
"""
import io, os, sys, tempfile
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guide_store import GuideStore
from rerank import rrf_rerank

# ---------- 构造知识库（不同主题的攻略片段） ----------
DOCS = {
    "chengdu-food": "成都美食以麻辣著称，火锅、串串香、担担面、龙抄手都很出名，宽窄巷子和锦里能找到地道小吃。吃火锅推荐九宫格老灶，价格人均几十到一百多。",
    "chengdu-panda": "成都大熊猫繁育研究基地在市区北郊，上午参观最佳，能看到大熊猫幼崽，建议早入园、坐景区观光车。门票约55元。",
    "beijing-gugong": "北京故宫博物院旧称紫禁城，是明清两代皇宫。游览需提前在官网或小程序预约门票，旺季一票难求，建议从午门进入、沿中轴线游览。",
    "beijing-greatwall": "八达岭长城距市区约70公里，可乘京张高铁或S2线，索道与徒步都可，建议清晨出发避开人流，注意防晒。",
    "hangzhou-xihu": "杭州西湖十景包括苏堤春晓、曲院风荷等，环湖骑行约15公里很舒服，雷峰塔可俯瞰全景，断桥残雪在冬季最美。",
    "hangzhou-food": "杭州菜偏清淡鲜美，西湖醋鱼、龙井虾仁、东坡肉、片儿川是代表菜，楼外楼、外婆家等餐厅较出名。",
    "dali": "大理古城背靠苍山、面朝洱海，环洱海骑行是经典玩法，双廊适合看海发呆，喜洲有白族民居和破酥粑粑。",
    "sanya": "三亚亚龙湾沙滩细软适合游泳，蜈支洲岛适合潜水，冬季气候温暖是避寒胜地，海鲜可去第一市场加工。",
    "qingdao": "青岛栈桥和八大关是经典景点，啤酒博物馆可了解啤酒文化，海边烧烤和海鲜便宜，夏天有啤酒节。",
    "xiamen": "厦门鼓浪屿需提前订船票，厦门大学与南普陀寺相邻，环岛路适合骑行，沙茶面和海蛎煎是特色小吃。",
    "xian-bingmayong": "西安兵马俑是世界第八大奇迹，位于临潼区，建议请讲解或租讲解器，铜车马展厅不要错过，门票约120元。",
    "guilin": "桂林漓江精华在兴坪段，阳朔西街夜生活丰富，遇龙河竹筏漂流很惬意，龙脊梯田适合春秋观赏。",
    "chengdu-hotpot2": "成都火锅店推荐：小龙坎、蜀大侠等大牌店排队久，建议提前线上取号，人均100-150元，锅底可选微辣、中辣或重辣，配冰粉解辣。",
    "beijing-gugong2": "故宫旺季周一闭馆，珍宝馆与钟表馆需单独购票各10元，门票建议提前7天在官网预约，午门是唯一入口。",
    "hangzhou-xihu2": "西湖游船分画舫与手划船，三潭印月位于湖心需乘船上岛，苏堤春晓是十景之首，白堤适合傍晚散步看日落。",
    "qingdao-beer": "青岛啤酒博物馆在登州路老啤酒厂，门票约60元含两杯啤酒，可了解青啤百年历史，旺季建议工作日去。",
    "xiamen-snack": "厦门沙茶面与海蛎煎是特色，曾厝垵小吃集中但价格偏高，八市更有本地烟火气，花生汤配油条也好吃。",
}

# ---------- 金标准问题集 ----------
GOLDEN = [
    ("成都有什么地道美食推荐？", "chengdu-food"),
    ("去成都哪里看大熊猫？", "chengdu-panda"),
    ("北京故宫需要预约门票吗？", "beijing-gugong"),
    ("北京爬长城推荐去哪段？", "beijing-greatwall"),
    ("杭州西湖有哪些经典景点？", "hangzhou-xihu"),
    ("杭州有什么招牌菜？", "hangzhou-food"),
    ("大理怎么玩比较经典？", "dali"),
    ("冬天想去暖和的海边避寒去哪？", "sanya"),
    ("青岛夏天有什么好玩的？", "qingdao"),
    ("厦门鼓浪屿怎么去？", "xiamen"),
    ("西安兵马俑值得去吗？门票多少？", "xian-bingmayong"),
    ("桂林漓江和阳朔怎么安排？", "guilin"),
    ("成都吃火锅人均大概多少钱？", "chengdu-food"),
    ("故宫预约难不难，从哪进比较好？", "beijing-gugong"),
    ("西湖适合骑行吗？", "hangzhou-xihu"),
    ("成都火锅人均多少钱？哪些店要排队？", "chengdu-hotpot2"),
    ("故宫哪个馆要单独买票？什么时候闭馆？", "beijing-gugong2"),
    ("西湖坐什么船？三潭印月在哪里？", "hangzhou-xihu2"),
    ("青岛啤酒博物馆门票多少？", "qingdao-beer"),
    ("厦门吃小吃去哪里比较地道？", "xiamen-snack"),
]

# ---------- 指标 ----------
def recall_at(ranked_ids, gold_id, k):
    return 1.0 if gold_id in ranked_ids[:k] else 0.0


def mrr(ranked_ids, gold_id):
    for i, rid in enumerate(ranked_ids, start=1):
        if rid == gold_id:
            return 1.0 / i
    return 0.0


def run():
    # 使用临时知识库文件，不影响线上数据
    tmp = Path(tempfile.mkdtemp()) / "guides.json"
    gs = GuideStore(tmp)
    uid = "eval_user"
    for fid, text in DOCS.items():
        gs.add(uid, f"{fid}.txt", text)

    queries = [q for q, _ in GOLDEN]
    gold = [g for _, g in GOLDEN]

    # 方案 A：纯向量 Top-K（K=5，模拟旧逻辑直接取前5）
    # 方案 B：多召回 Top-10 + RRF 重排 → Top-5
    for top_k in (1, 3, 5):
        ra = recall_at if top_k in (1, 3, 5) else None
    metrics = {k: {"A": {"recall": 0.0, "mrr": 0.0}, "B": {"recall": 0.0, "mrr": 0.0}}
               for k in (1, 3, 5)}

    for q, g in zip(queries, gold):
        gold_id = g
        # A: 直接向量 Top-10（取前K用）
        hitsA = gs.search(uid, q, 10)
        idsA = [h["filename"].split(".")[0] for h in hitsA]
        # B: 多召回后 RRF 重排
        cands = [{"text": h["text"], "score": h["score"]} for h in hitsA]
        reranked = rrf_rerank(q, cands)
        bytext = {h["text"]: h["filename"].split(".")[0] for h in hitsA}
        idsB = [bytext[r["text"]] for r in reranked if r["text"] in bytext]
        # 补齐（理论上重排不丢候选）
        idsB = idsB + [x for x in idsA if x not in idsB]

        for k in (1, 3, 5):
            metrics[k]["A"]["recall"] += recall_at(idsA, gold_id, k)
            metrics[k]["B"]["recall"] += recall_at(idsB, gold_id, k)
            if k == 5:
                metrics[k]["A"]["mrr"] += mrr(idsA, gold_id)
                metrics[k]["B"]["mrr"] += mrr(idsB, gold_id)

    n = len(queries)
    print(f"评测集：{n} 条问题、{len(DOCS)} 个攻略片段\n")
    print("K      纯向量 Top-K(Recall)  多召回+RRF重排(Recall)   提升")
    for k in (1, 3, 5):
        a = metrics[k]["A"]["recall"] / n
        b = metrics[k]["B"]["recall"] / n
        delta = b - a
        print(f"@{k}      {a:.2%}                {b:.2%}                {delta:+.2%}")
    a_mrr = metrics[5]["A"]["mrr"] / n
    b_mrr = metrics[5]["B"]["mrr"] / n
    print(f"\nMRR(按Top10窗口)   {a_mrr:.4f}                {b_mrr:.4f}                {b_mrr - a_mrr:+.4f}")

    # 打印 1-2 个"重排改变了顺序"的例子
    print("\n排序变化示例：")
    shown = 0
    for q, g in zip(queries, gold):
        hitsA = gs.search(uid, q, 10)
        idsA = [h["filename"].split(".")[0] for h in hitsA]
        cands = [{"text": h["text"], "score": h["score"]} for h in hitsA]
        reranked = rrf_rerank(q, cands)
        bytext = {h["text"]: h["filename"].split(".")[0] for h in hitsA}
        idsB = [bytext[r["text"]] for r in reranked if r["text"] in bytext]
        if idsA[:3] != idsB[:3] and shown < 3:
            shown += 1
            print(f"  问：{q}")
            print(f"    纯向量Top3: {idsA[:3]}")
            print(f"    重排后Top3: {idsB[:3]}")
    if shown == 0:
        print("  （本评测集两路排序基本一致，说明检索已较稳）")

    import shutil
    shutil.rmtree(tmp.parent, ignore_errors=True)


if __name__ == "__main__":
    run()
