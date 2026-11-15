"""Product discovery strategies and the ordered validation workflow."""

STRATEGIES = {
    "pain_point": {
        "id": "pain_point",
        "code": "A",
        "title": "痛点解决型",
        "subtitle": "从差评与消费者抱怨里找微创新",
        "description": "围绕现有商品的清洗、耐用、收纳、安装和使用麻烦，寻找能被明确改进的产品机会。",
        "searchTerms": "消费者痛点 差评 产品改进 家居 清洁 宠物 露营",
        "tags": ["差评主题", "产品缺陷", "微创新"],
        "sourceHint": "Google Trends · 抖音 / 快手内容线索 · 消费者评论",
        "accent": "sage",
    },
    "emotion_aesthetic": {
        "id": "emotion_aesthetic",
        "code": "B",
        "title": "情绪与审美驱动型",
        "subtitle": "从悦己、设计和文化表达找溢价",
        "description": "寻找适合短视频表达、有礼赠或收藏属性、视觉差异明显的生活方式商品。",
        "searchTerms": "日系生活方式 设计感 礼物 香薰 手作 潮玩 色彩",
        "tags": ["设计感", "礼赠场景", "文化符号"],
        "sourceHint": "TikTok · 抖音 / 快手热点 · Pinterest 灵感",
        "accent": "rose",
    },
    "niche_long_tail": {
        "id": "niche_long_tail",
        "code": "C",
        "title": "细分长尾赛道",
        "subtitle": "从小众人群与垂直爱好找高粘性",
        "description": "围绕异宠、特定运动、专业兴趣和小众生活场景，避开没有差异的通用标品。",
        "searchTerms": "爬宠 异宠 匹克球 飞盘 陆冲板 小众运动 专业配件",
        "tags": ["异宠", "小众运动", "专业兴趣"],
        "sourceHint": "Google Trends 细分词 · 抖音 / 快手垂类话题",
        "accent": "blue",
    },
    "supply_chain": {
        "id": "supply_chain",
        "code": "D",
        "title": "供应链红利与成本降维",
        "subtitle": "从产业带、工艺和源头成本找空间",
        "description": "寻找中国产业带成熟供给、可控质量和可复用工艺，并验证日本市场是否仍有差异化空间。",
        "searchTerms": "中国 产业带 工厂 1688 源头供货 小商品 成本优势",
        "tags": ["产业带", "工艺", "源头成本"],
        "sourceHint": "1688 · 淘宝 / 京东 / 拼多多 · 工厂与产业带报道",
        "accent": "amber",
    },
}

WORKFLOW_STAGES = (
    {
        "id": "demand",
        "number": 1,
        "title": "发现需求",
        "sources": ["Google Trends", "TikTok", "抖音", "消费者评论"],
        "description": "捕捉上升需求、内容热度和重复出现的消费者痛点。",
    },
    {
        "id": "market",
        "number": 2,
        "title": "验证市场",
        "sources": ["Amazon", "乐天", "Yahoo! Shopping"],
        "description": "核对商品、价格带、竞品数量、评论和市场空位。",
    },
    {
        "id": "sourcing",
        "number": 3,
        "title": "寻找货源",
        "sources": ["1688", "工厂", "产业带", "MOQ"],
        "description": "比较供货、起订量、规格、交期和可改进能力。",
    },
    {
        "id": "economics",
        "number": 4,
        "title": "利润核算",
        "sources": ["采购", "物流", "平台费", "广告", "税费", "退货"],
        "description": "只用可追溯价格和明确假设计算落地利润。",
    },
    {
        "id": "pilot",
        "number": 5,
        "title": "小批量测试",
        "sources": ["真实订单", "转化率", "退款率", "净利润"],
        "description": "由经营者决定是否下样或小批量测试，Agent 跟踪验证指标。",
    },
    {
        "id": "scale",
        "number": 6,
        "title": "扩大经营",
        "sources": ["供应链", "品牌", "库存", "营销"],
        "description": "只有真实测试结果支持时，才建议扩大库存和营销投入。",
    },
)


def strategy_catalog() -> list[dict]:
    return [dict(item) for item in STRATEGIES.values()]
