"""生成「大知识库」评测语料（用于评估 Reranker 的真实价值）。

为什么需要它：生产知识库只有 9 个 chunk，top-4 就覆盖了全库 44%，
向量检索在这个规模上已接近上限，重排器**没有可发挥的空间**
（实测 Hit 均为 1.00，MRR 差异仅来自个别题目的排名 1↔2）。

本脚本生成一个同构但**大得多**的知识库，让检索出现真实的
"高召回、低精度"压力：

- **12 个商品品类 × 9 个政策维度 = 108 条事实**，每格一条独立事实，
  数值互不相同（如各品类保修期 12/14/16…个月）
- 片段之间**高度相似**（同一模板 + 相同的条款样板文字），构成**难负例**：
  "耳机保修期"的正确答案与"音箱保修期""耳机退货运费"等片段在字面上极为接近
- 另加 12 条品类总览 + 若干通用条款，构成约 130 个 chunk

产物（均落在 `data/eval/`，**不触碰生产知识库**）：

- `kb_large/*.md`         人可读语料（每品类一个文件，每维度一段）
- `kb_large_questions.json` 标注问题集（每条含期望命中的事实定位串）

用法：
    uv run python -m app.scripts.gen_large_kb
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from app.seed import DATA_DIR

# ---- 语料结构定义（确定性，无随机） ----
#
# 关键设计（决定评测是否有判别力）：
# 1. 品类用**共享词根的变体**（蓝牙耳机 / 有线耳机 / 头戴式耳机 / 蓝牙音箱 …），
#    同一域下多个片段共享"耳机""音箱""蓝牙"等词 → 词面无法区分，必须靠语义。
# 2. 每个事实片段**只提一次**品类名，不重复堆砌关键词，避免字面匹配"送分"。
# 3. 提问混入**口语化转述**（"蓝牙连着用的那个耳机保修多久"），
#    让检索无法靠字符串匹配命中——这才是重排器真正有价值的场景。
VARIANTS: list[dict] = [
    {"name": "蓝牙耳机", "family": "耳机", "paraphrase": "蓝牙连着用的那款耳机"},
    {"name": "有线耳机", "family": "耳机", "paraphrase": "插线用的那款耳机"},
    {"name": "头戴式耳机", "family": "耳机", "paraphrase": "戴在头上的那款耳机"},
    {"name": "蓝牙音箱", "family": "音箱", "paraphrase": "蓝牙连着用的那款音箱"},
    {"name": "桌面音箱", "family": "音箱", "paraphrase": "摆在桌上的那款音箱"},
    {"name": "便携音箱", "family": "音箱", "paraphrase": "能装兜里带出门的那款音箱"},
    {"name": "机械键盘", "family": "键盘", "paraphrase": "敲起来咔哒响的那款键盘"},
    {"name": "薄膜键盘", "family": "键盘", "paraphrase": "敲起来很安静的那款键盘"},
    {"name": "无线键盘", "family": "键盘", "paraphrase": "不用插线的那款键盘"},
    {"name": "电竞显示器", "family": "显示器", "paraphrase": "打游戏用的那款屏幕"},
    {"name": "办公显示器", "family": "显示器", "paraphrase": "办公看表格的那款屏幕"},
    {"name": "便携显示器", "family": "显示器", "paraphrase": "能塞进包里带走的屏幕"},
]

# 每个维度的取值公式：base + 品类序号 → 全库数值互不重复
DOMAINS: list[dict] = [
    {
        "key": "return_days",
        "label": "无理由退货期限",
        "question": ["无理由退货期限是多久", "支持几天无理由退货", "无理由退货可以几天内申请"],
        "base": 7,
        "step": 1,
        "unit": "天",
        "template": "支持自签收之日起 {v} 天内无理由退货，商品需保持完好且配件齐全",
    },
    {
        "key": "quality_return_days",
        "label": "质量问题退换期限",
        "question": ["质量问题退换期限是多久", "出现质量问题几天内能退换", "质量问题的退换时效"],
        "base": 15,
        "step": 1,
        "unit": "天",
        "template": "出现非人为损坏的质量问题时，可在签收后 {v} 天内申请退货或换货",
    },
    {
        "key": "warranty_months",
        "label": "保修期",
        "question": ["保修期是多久", "能保修多长时间", "免费保修期多长"],
        "base": 12,
        "step": 2,
        "unit": "个月",
        "template": "提供自签收之日起 {v} 个月的免费保修服务，非人为损坏可免费维修",
    },
    {
        "key": "ship_hours",
        "label": "发货时效",
        "question": ["下单后多久发货", "付款后几个小时发货", "发货时效是多久"],
        "base": 12,
        "step": 1,
        "unit": "小时",
        "template": "买家付款后 {v} 小时内完成出库发货，遇大促期间顺延最多 24 小时",
    },
    {
        "key": "freight_subsidy",
        "label": "退货运费补贴上限",
        "question": ["退货运费补贴是多少", "退货能补贴多少运费", "运费补贴上限"],
        "base": 20,
        "step": 1,
        "unit": "元",
        "template": "退货时运费补贴上限为 {v} 元，超出部分由买家自行承担",
    },
    {
        "key": "trade_in_rate",
        "label": "以旧换新折价比例",
        "question": ["以旧换新能折价多少", "以旧换新的折价比例是多少", "旧机折价比例"],
        "base": 30,
        "step": 1,
        "unit": "%",
        "template": "以旧换新按同型号新品售价的 {v}% 折价回收，成色影响最终评估价",
    },
    {
        "key": "price_protection_days",
        "label": "价格保护期限",
        "question": ["价格保护是几天", "下单后几天内可以价保", "价保期限"],
        "base": 5,
        "step": 1,
        "unit": "天",
        "template": "支持下单后 {v} 天内的价格保护，降价可申请退还差价",
    },
    {
        "key": "points_per_yuan",
        "label": "会员积分规则",
        "question": ["消费一元累计多少积分", "积分怎么算", "会员积分规则"],
        "base": 1,
        "step": 1,
        "unit": "积分",
        "template": "普通会员每消费 1 元累计 {v} 积分，积分可在下单时抵扣现金",
    },
    {
        "key": "exchange_workdays",
        "label": "换货处理时限",
        "question": ["换货多久处理完", "换货处理时限是几个工作日", "换货需要几个工作日"],
        "base": 3,
        "step": 1,
        "unit": "个工作日",
        "template": "换货申请在收到寄回商品后 {v} 个工作日内处理完成并发货",
    },
]

# 扩展维度（`--scale full` 时启用）：把语料放大约 3 倍，用于观察
# "知识库规模"对重排价值的影响。base 与主维度错开 100 以上，保证数值全库唯一。
_EXTRA_DOMAINS_RAW: list[tuple[str, str, str, int, str]] = [
    ("refund_arrive_days", "退款到账时限", "退款一般几天到账", 101, "个工作日"),
    ("exchange_freight", "换货运费承担", "换货的运费谁出", 111, "元封顶"),
    ("install_service", "上门安装服务", "能不能上门安装", 121, "元服务费"),
    ("debug_service", "远程调试服务", "能不能远程调试", 131, "元服务费"),
    ("cleaning_service", "清洁保养服务", "有没有清洁保养", 141, "元服务费"),
    ("extend_warranty", "延保服务价格", "延保要多少钱", 151, "元"),
    ("parts_reship", "配件补寄时限", "配件补寄要多久", 161, "个工作日"),
    ("invoice_days", "发票开具时限", "发票多久能开出来", 171, "个工作日"),
    ("invoice_reissue", "发票重开时限", "发票能重开吗", 181, "个工作日"),
    ("member_birthday", "会员生日权益", "会员生日有什么权益", 191, "积分"),
    ("points_deduct", "积分抵扣比例", "积分能抵多少钱", 201, "积分抵 1 元"),
    ("coupon_valid_days", "优惠券有效期", "优惠券有效期多久", 211, "天"),
    ("pickup_keep_hours", "门店自提保留时长", "自提能保留多久", 221, "小时"),
    ("urgent_ship_fee", "加急发货费用", "能加急发货吗", 231, "元"),
    ("remote_area_fee", "偏远地区运费", "偏远地区要加运费吗", 241, "元"),
    ("bulk_discount", "批量采购折扣", "批量采购有折扣吗", 251, "%"),
    ("trade_in_limit_days", "以旧换新申请时限", "以旧换新多久内可申请", 261, "天"),
    ("price_protect_scope", "价格保护适用范围", "价保适用哪些商品", 271, "天"),
    ("quality_check_days", "质检出具时限", "质检报告多久出", 281, "个工作日"),
    ("lost_package_days", "丢件补发时限", "快递丢件多久补发", 291, "个工作日"),
    ("order_cancel_hours", "订单取消时限", "下单后多久能取消", 301, "小时"),
]


def extra_domains() -> list[dict]:
    """把扩展维度展开为与主维度同构的结构（数值随品类递增，保持唯一）。"""
    return [
        {
            "key": key,
            "label": label,
            "question": [q, q, q],
            "base": base,
            "step": 1,
            "unit": unit,
            "template": f"{label}标准为 {{v}} {unit}，具体以商城公示与本条款为准",
        }
        for key, label, q, base, unit in _EXTRA_DOMAINS_RAW
    ]

# 统一的条款样板（所有片段共享，制造字面相似度 → 难负例）
BOILERPLATE = (
    "本条款自发布之日起生效，适用于星辰数码商城全部自营商品；"
    "如与法律规定存在冲突，以法律规定为准，最终解释权归星辰数码商城所有。"
)

# 每段最短长度：保证任意两段拼接都会超过 chunk_size，从而"一段一片"
MIN_PARAGRAPH_CHARS = 105
# 段落长度上限（须小于评测时使用的 chunk_size，见 eval 脚本）
MAX_PARAGRAPH_CHARS = 195

KB_DIR = DATA_DIR / "eval" / ("kb" + "_large")
QUESTIONS_PATH = DATA_DIR / "eval" / "kb_large_questions.json"

# 取样评测的 (变体序号, 维度序号)：覆盖不同品类族与维度，
# 且**刻意包含同族变体**（如同时取 0/1/2 三种耳机），制造"同词根干扰"
SAMPLE_CELLS: list[tuple[int, int]] = [
    (0, 2), (1, 2), (2, 2),      # 三种耳机 × 保修期（同族最难区分）
    (3, 2), (4, 2), (5, 2),      # 三种音箱 × 保修期
    (0, 0), (1, 0), (2, 0),      # 三种耳机 × 无理由退货
    (6, 2), (7, 2), (8, 2),      # 三种键盘 × 保修期
    (9, 2), (10, 2), (11, 2),    # 三种显示器 × 保修期
    (0, 3), (1, 3), (2, 3),      # 三种耳机 × 发货时效
    (3, 0), (4, 0), (5, 0),      # 三种音箱 × 无理由退货
    (6, 0), (7, 0), (8, 0),      # 三种键盘 × 无理由退货
    (9, 3), (10, 3), (11, 3),    # 三种显示器 × 发货时效
    (0, 4), (3, 4), (6, 4),      # 跨族 × 运费补贴
    (0, 5), (2, 5), (9, 5),      # 跨族 × 以旧换新
    (1, 6), (4, 6), (7, 6),      # 跨族 × 价格保护
    (2, 7), (5, 7), (8, 7),      # 跨族 × 会员积分
    (0, 8), (3, 8), (9, 8),      # 跨族 × 换货时限
]

# full 规模追加的取样格子（维度序号 ≥ 9 指向扩展维度）
FULL_SCALE_CELLS: list[tuple[int, int]] = [
    (0, 9), (1, 9), (2, 9),      # 三种耳机 × 退款到账
    (3, 10), (4, 10), (5, 10),   # 三种音箱 × 换货运费
    (6, 11), (7, 11),            # 键盘 × 上门安装
    (8, 12), (9, 12),            # 键盘/显示器 × 远程调试
    (10, 13), (11, 13),          # 显示器 × 清洁保养
    (0, 14), (2, 14),            # 耳机 × 延保价格
    (3, 15), (5, 15),            # 音箱 × 配件补寄
    (6, 16), (8, 16),            # 键盘 × 发票开具
    (9, 17), (11, 17),           # 显示器 × 发票重开
    (1, 18), (4, 18),            # 耳机/音箱 × 会员生日
    (7, 19), (10, 19),           # 键盘/显示器 × 积分抵扣
    (2, 20), (5, 20),            # 耳机/音箱 × 优惠券有效期
    (0, 21), (6, 21),            # 耳机/键盘 × 门店自提保留
    (3, 22), (9, 22),            # 音箱/显示器 × 加急发货费
    (1, 23), (8, 23),            # 耳机/键盘 × 偏远地区运费
    (4, 24), (11, 24),           # 音箱/显示器 × 批量折扣
    (2, 25), (7, 25),            # 耳机/键盘 × 以旧换新时限
    (5, 26), (10, 27),           # 音箱 × 价保范围；显示器 × 质检时限
    (0, 28), (6, 29),            # 耳机 × 丢件补发；键盘 × 订单取消
]


def fact_text(variant_index: int, domain: dict) -> str:
    """某品类在某维度上的事实句（只提一次品类名，不堆砌关键词）。"""
    value = domain["base"] + variant_index * domain["step"]
    return domain["template"].format(v=value)


def paragraph(variant: dict, domain: dict, variant_index: int) -> str:
    """一个片段 = 一个段落（维度 + 品类 + 事实 + 共享样板条款）。"""
    body = fact_text(variant_index, domain)
    return (
        f"【{domain['label']}】{variant['name']}：{body}。"
        f"上述标准适用于{BOILERPLATE}"
    )


def overview_paragraph(variant: dict, variant_index: int) -> str:
    """品类总览段（不承载具体事实，作为额外干扰项）。"""
    return (
        f"【品类总览】{variant['name']}在售型号共 {variant_index % 4 + 2} 款，"
        f"覆盖入门与旗舰定位，均支持全国联保与七天无理由退货，"
        f"售后分项政策见后续各条款。{BOILERPLATE}"
    )


def general_paragraphs() -> list[str]:
    """通用条款段（跨品类，不承载被测事实）。"""
    topics = [
        ("发票说明", "下单时可选择电子普通发票或增值税专用发票，发票在发货后 3 个工作日内开具"),
        ("支付方式", "支持微信支付、支付宝、银行卡与商城余额支付，分期免息以活动页说明为准"),
        ("会员等级", "会员分为普通会员、银卡会员与金卡会员，等级按自然年累计消费金额判定"),
        ("积分有效期", "会员积分自获得之日起 24 个月内有效，过期未使用部分不予补发"),
        ("客服渠道", "可通过在线客服、客服热线与邮件工单三种渠道联系，工作日 9 时至 21 时在线"),
        ("物流合作", "与顺丰、京东物流、中通合作，偏远地区可能转由邮政配送"),
        ("包装要求", "退货需使用原包装或等效防护包装，因包装不当造成的运输损坏可能影响退款"),
        ("赠品处理", "退货时需一并寄回赠品，赠品缺失将按赠品标价扣除相应金额"),
        ("上门取件", "质量问题退货支持上门取件，无理由退货需买家自行寄回"),
        ("数据隐私", "订单与聊天记录仅用于售后服务，不会提供给第三方用于营销"),
    ]
    return [
        f"【{label}】{body}。本说明面向星辰数码商城全部自营商品，"
        f"具体执行以对应专项条款为准。{BOILERPLATE}"
        for label, body in topics
    ]


def build_corpus(domains: list[dict] | None = None) -> tuple[dict[str, list[str]], list[dict]]:
    """返回 (文件名 → 段落列表, 片段标注列表)。

    文件名按**品类族**聚合（同一族的变体放同一文件），段落顺序即文档顺序。
    """
    domains = domains or DOMAINS
    files: dict[str, list[str]] = {}
    labels: list[dict] = []

    for i, variant in enumerate(VARIANTS):
        file_name = f"{variant['family']}类商品服务条款.md"
        paragraphs = files.setdefault(file_name, [])
        paragraphs.append(overview_paragraph(variant, i))
        for domain in domains:
            paragraphs.append(paragraph(variant, domain, i))
            labels.append(
                {
                    "variant": variant["name"],
                    "family": variant["family"],
                    "paraphrase": variant["paraphrase"],
                    "variant_index": i,
                    "domain": domain["key"],
                    "domain_label": domain["label"],
                    "fact": fact_text(i, domain),
                    "marker": fact_text(i, domain)[:24],
                }
            )

    files["商城通用条款说明.md"] = general_paragraphs()
    return files, labels


def _questions_for(label: dict, domain: dict, style: str) -> str:
    """按提问风格生成问句。

    - exact：直接点名型号（字面可匹配）
    - paraphrase：口语化转述（无型号字面，必须靠语义）
    - prefixed：带商城前缀的直接提问
    """
    if style == "exact":
        return f"{label['variant']}的{domain['question'][0]}？"
    if style == "prefixed":
        return f"星辰数码商城的{label['variant']}{domain['question'][1]}？"
    return f"{label['paraphrase']}，{domain['question'][2]}？"


def build_questions(labels: list[dict], cells: list[tuple[int, int]]) -> list[dict]:
    """按取样格子生成标注问题集（三种提问风格轮换，便于分风格对比）。"""
    index = {(lb["variant_index"], lb["domain"]): lb for lb in labels}
    all_domains = DOMAINS + extra_domains()
    styles = ["exact", "paraphrase", "prefixed"]
    questions: list[dict] = []
    seen: set[str] = set()
    for n, (variant_index, domain_index) in enumerate(cells):
        domain = all_domains[domain_index]
        label = index[(variant_index, domain["key"])]
        style = styles[n % len(styles)]
        qid = f"kb_{variant_index:02d}_{domain['key']}"
        if qid in seen:
            raise SystemExit(f"取样格子重复，问题 id 冲突：{qid}")
        seen.add(qid)
        questions.append(
            {
                "id": qid,
                "question": _questions_for(label, domain, style),
                "style": style,
                "expected_marker": label["marker"],
                "variant": label["variant"],
                "domain": domain["key"],
                "domain_label": label["domain_label"],
                "fact": label["fact"],
            }
        )
    return questions


def main() -> None:
    parser = argparse.ArgumentParser(description="生成大知识库评测语料")
    parser.add_argument(
        "--scale",
        choices=["base", "full"],
        default="base",
        help="base=9 个维度（130 片段）；full=全部 30 个维度（约 380 片段）",
    )
    args = parser.parse_args()

    domains = DOMAINS + extra_domains() if args.scale == "full" else list(DOMAINS)
    cells = SAMPLE_CELLS + FULL_SCALE_CELLS if args.scale == "full" else list(SAMPLE_CELLS)

    files, labels = build_corpus(domains)
    questions = build_questions(labels, cells)

    # 长度自检：保证"一段一片"（任意两段拼接必超过 chunk_size）
    too_short, too_long = [], []
    for name, paragraphs in files.items():
        for text in paragraphs:
            if len(text) < MIN_PARAGRAPH_CHARS:
                too_short.append((name, len(text), text[:30]))
            if len(text) > MAX_PARAGRAPH_CHARS:
                too_long.append((name, len(text), text[:30]))
    if too_short or too_long:
        raise SystemExit(
            f"段落长度不合规：过短 {too_short[:3]}（需 ≥{MIN_PARAGRAPH_CHARS}），"
            f"过长 {too_long[:3]}（需 ≤{MAX_PARAGRAPH_CHARS}）"
        )

    # 清理本脚本自有目录中的旧文件：语料结构变更后残留的旧文件会与
    # 新语料产生**数值冲突**（同一数值出现在两个片段中），使标注定位串失效，
    # 进而污染评测的 ground truth。此目录由本脚本独占，故整体重建。
    if KB_DIR.exists():
        assert KB_DIR.name == "kb_large" and KB_DIR.parent.name == "eval", (
            f"拒绝清理非评测语料目录：{KB_DIR}"
        )
        shutil.rmtree(KB_DIR)
    KB_DIR.mkdir(parents=True, exist_ok=True)
    for name, paragraphs in files.items():
        (KB_DIR / name).write_text("\n\n".join(paragraphs) + "\n", encoding="utf-8")

    # 标注唯一性自检：定位串必须在全库中**唯一**，否则问题会指向错误片段
    markers: dict[str, int] = {}
    for label in labels:
        markers[label["marker"]] = markers.get(label["marker"], 0) + 1
    duplicated = [m for m, c in markers.items() if c > 1]
    if duplicated:
        raise SystemExit(f"标注定位串重复（数值在不同维度间冲突）：{duplicated[:3]}")

    total_paragraphs = sum(len(p) for p in files.values())
    QUESTIONS_PATH.write_text(
        json.dumps(
            {
                "dataset_name": "星辰数码商城大知识库检索评测集",
                "purpose": "评估 Reranker 在大知识库（高召回低精度）下的真实价值",
                "kb_dir": str(KB_DIR.relative_to(DATA_DIR.parent)),
                "documents": len(files),
                "chunks_expected": total_paragraphs,
                "questions": questions,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"已生成语料：{KB_DIR}")
    print(f"  文档数 {len(files)}，段落（= 期望 chunk）数 {total_paragraphs}")
    print(f"  事实片段 {len(labels)} 条，品类变体 {len(VARIANTS)} × 维度 {len(DOMAINS)}")
    styles = {}
    for q in questions:
        styles[q["style"]] = styles.get(q["style"], 0) + 1
    print(f"已生成问题集：{QUESTIONS_PATH}（{len(questions)} 题，风格分布 {styles}）")


if __name__ == "__main__":
    main()
