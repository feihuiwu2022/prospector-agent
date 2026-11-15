"""Platform registry: one table for role, access level and search targeting.

Every platform the Agent can touch is described here exactly once. Query
planning, the connector catalogue and the profile form all read this table, so
adding a platform is a data change rather than a code change scattered over
the discovery prompts.

Roles:
  demand   content and trend platforms used to discover demand
  sales    marketplaces where the product is sold (market validation)
  sourcing supply platforms used to find goods and cost (sourcing validation)

Access levels:
  web_search              reachable only as public web page mentions
  manual_source           a human records the page or pastes the data
  authorization_required  needs an official app / API scope before real data
"""

from __future__ import annotations


PLATFORMS = {
    # ---- 需求与内容侧（国内）----
    "douyin": {
        "id": "douyin",
        "name": "抖音",
        "role": "demand",
        "region": "国内",
        "access": "web_search",
        "capabilities": ["public_trend_mentions", "content_discovery"],
        "accessText": "MiniMax Web Search 搜索公开页面；热点榜、视频数据与商品数据需抖音开放平台应用与对应 scope",
        "note": "官方开放平台把热点、视频与电商能力拆成不同接口，未授权前只有公开页面文案可用。",
        "sites": ["douyin.com"],
        "keyword": "抖音",
        "searchLabel": "抖音公开网页线索",
        "authorizationChannel": "抖音开放平台（应用创建 + OAuth + 对应 scope）",
    },
    "kuaishou": {
        "id": "kuaishou",
        "name": "快手",
        "role": "demand",
        "region": "国内",
        "access": "web_search",
        "capabilities": ["public_trend_mentions", "content_discovery"],
        "accessText": "MiniMax Web Search 搜索公开页面；直播与广告投放数据需快手开放平台 / 磁力引擎资质授权",
        "note": "公开页面线索不等于官方热榜、直播成交或达人带货数据。",
        "sites": ["kuaishou.com", "gifshow.com"],
        "keyword": "快手",
        "searchLabel": "快手公开网页线索",
        "authorizationChannel": "快手开放平台 / 磁力引擎（开发者资质 + 账号授权）",
    },
    # ---- 需求与内容侧（海外）----
    "google_trends": {
        "id": "google_trends",
        "name": "Google Trends",
        "role": "demand",
        "region": "海外",
        "access": "web_search",
        "capabilities": ["public_trend_mentions", "keyword_discovery"],
        "accessText": "MiniMax Web Search 可发现公开页面；不读取 Google Trends 原始趋势序列",
        "note": "Google Trends 官方 API 处于 Alpha 申请阶段；公开搜索摘要只作需求线索。",
        "sites": [],
        "keyword": "Google Trends",
        "searchLabel": "Google Trends 公开页面线索",
        "authorizationChannel": "Google Trends 官方 API（Alpha 申请）",
    },
    "tiktok": {
        "id": "tiktok",
        "name": "TikTok / Creative Center",
        "role": "demand",
        "region": "海外",
        "access": "web_search",
        "capabilities": ["public_trend_mentions", "creative_discovery"],
        "accessText": "MiniMax Web Search 搜索公开页面；账号、实时榜单和完整视频数据需平台权限",
        "note": "TikTok Research API 仅面向符合条件且获批的研究用途，不作为商业默认数据源。",
        "sites": ["tiktok.com"],
        "keyword": "TikTok",
        "searchLabel": "TikTok / 抖音公开网页线索",
        "authorizationChannel": "TikTok Research API / 商业账号授权",
    },
    # ---- 销售市场侧 ----
    "amazon_jp": {
        "id": "amazon_jp",
        "name": "Amazon.co.jp",
        "role": "sales",
        "region": "海外",
        "access": "authorization_required",
        "capabilities": ["catalog_match", "authorized_listing_data", "authorized_pricing"],
        "accessText": "公开页面可通过搜索发现；正式商品与定价数据需要 SP-API 开发者注册和卖家/供应商授权",
        "note": "目录匹配和卖家定价权限分属不同 API 能力，不能视为公开全站价格接口。",
        "sites": ["amazon.co.jp"],
        "keyword": "Amazon.co.jp",
        "searchLabel": "Amazon.co.jp 商品/竞品搜索",
        "authorizationChannel": "Amazon SP-API（开发者注册 + 角色授权）",
    },
    "rakuten_yahoo_jp": {
        "id": "rakuten_yahoo_jp",
        "name": "乐天 / Yahoo! Shopping",
        "role": "sales",
        "region": "海外",
        "access": "web_search",
        "capabilities": ["public_listing_mentions"],
        "accessText": "MiniMax Web Search 搜索公开商品页面；价格、库存和销量仍需授权 API 或页面复核",
        "note": "公开搜索摘要用于发现和初筛，不代表当前可购买价格或真实销量。",
        "sites": ["rakuten.co.jp", "rakuten.com", "shopping.yahoo.co.jp"],
        "keyword": "楽天 Yahoo!ショッピング",
        "searchLabel": "乐天 / Yahoo! Shopping 搜索线索",
        "authorizationChannel": "各平台官方 API / 开放平台应用",
    },
    "mercari_jp": {
        "id": "mercari_jp",
        "name": "Mercari Japan",
        "role": "sales",
        "region": "海外",
        "access": "web_search",
        "capabilities": ["public_listing_mentions", "second_hand_price_context"],
        "accessText": "MiniMax Web Search 搜索公开商品页；账号与交易数据需平台授权",
        "note": "个人闲置与代卖平台，标价是二手/个人定价，只作价格背景，不等于新品可复制的定价。",
        "sites": ["mercari.com"],
        "keyword": "Mercari",
        "searchLabel": "Mercari Japan 二手价格线索",
        "authorizationChannel": "Mercari 官方 API / 商业合作（不作为默认数据源）",
    },
    # ---- 货源侧（国内电商）----
    "1688": {
        "id": "1688",
        "name": "1688",
        "role": "sourcing",
        "region": "国内",
        "access": "authorization_required",
        "capabilities": ["supplier_search", "offer_detail", "quote_capture"],
        "accessText": "公开页面可通过搜索发现；搜索/详情/阶梯价需要 1688 开放平台应用与买家身份授权",
        "note": "上线前须核对当前应用可申请的搜索、详情、阶梯价和库存权限。",
        "sites": ["1688.com"],
        "keyword": "1688",
        "searchLabel": "1688 供应链搜索",
        "authorizationChannel": "1688 开放平台（应用 + 买家授权 + 接口权限）",
    },
    "taobao": {
        "id": "taobao",
        "name": "淘宝 / 天猫",
        "role": "sourcing",
        "region": "国内",
        "access": "web_search",
        "capabilities": ["public_offer_mentions", "authorized_account_data", "manual_url_import"],
        "accessText": "MiniMax Web Search 搜索公开商品页；正式商品与店铺数据需淘宝开放平台按场景授权",
        "note": "官方 taobao.products.search 文档说明该接口不是全站商品搜索接口，授权后也要按应用类型核对范围。",
        "sites": ["taobao.com", "tmall.com"],
        "keyword": "淘宝 天猫",
        "searchLabel": "淘宝 / 天猫货源搜索",
        "authorizationChannel": "淘宝开放平台（应用 + 场景授权）",
    },
    "jd": {
        "id": "jd",
        "name": "京东",
        "role": "sourcing",
        "region": "国内",
        "access": "web_search",
        "capabilities": ["public_offer_mentions", "authorized_catalog_or_seller_data"],
        "accessText": "MiniMax Web Search 搜索公开商品页；正式数据需从京东零售开放平台按应用类型核对接口范围",
        "note": "旧平台与业务子平台（POP、自营、拍拍等）接口不同，不能假设为通用消费者商品搜索。",
        "sites": ["jd.com"],
        "keyword": "京东",
        "searchLabel": "京东货源搜索",
        "authorizationChannel": "京东零售开放平台 open.jd.com（应用类型 + 接口授权）",
    },
    "pdd": {
        "id": "pdd",
        "name": "拼多多",
        "role": "sourcing",
        "region": "国内",
        "access": "web_search",
        "capabilities": ["public_offer_mentions", "authorized_marketing_data"],
        "accessText": "MiniMax Web Search 搜索公开页面；商品与营销数据需拼多多开放平台主体资质与对应权限",
        "note": "公开页面多为营销页，零售低价不等于可复制的源头成本；销量口径需另行核实。",
        "sites": ["pinduoduo.com", "yangkeduo.com"],
        "keyword": "拼多多",
        "searchLabel": "拼多多货源搜索",
        "authorizationChannel": "拼多多开放平台（主体资质 + 接口权限）",
    },
    "yiwugo": {
        "id": "yiwugo",
        "name": "义乌购 / 中国商品网",
        "role": "sourcing",
        "region": "国内",
        "access": "web_search",
        "capabilities": ["public_offer_mentions", "wholesale_market_context"],
        "accessText": "MiniMax Web Search 搜索公开货源页面；批量报价与档期需与供应商直接确认",
        "note": "义乌系批发货源页面，价格多为档期或区间价，起批量与实际出货价需单独核实。",
        "sites": ["yiwugo.com", "chinagoods.com", "juzhigou.com"],
        "keyword": "义乌购 中国商品网",
        "searchLabel": "义乌购 / 中国商品网货源搜索",
        "authorizationChannel": "义乌购 / 中国商品网开放平台（主体资质 + 接口权限）",
    },
    # ---- 其他已登记平台 ----
    "tiktok_shop": {
        "id": "tiktok_shop",
        "name": "TikTok Shop",
        "role": "sales",
        "region": "海外",
        "access": "authorization_required",
        "capabilities": ["authorized_shop_products", "creator_showcase"],
        "accessText": "需要开发者应用、店铺或达人授权及接口 scope",
        "note": "官方接口范围绑定获授权店铺/达人上下文，不作为全站热销榜接口。",
        "sites": [],
        "keyword": "TikTok Shop",
        "searchLabel": "TikTok Shop",
        "authorizationChannel": "TikTok Shop 开放平台（店铺/达人授权）",
    },
}


ROLE_LABELS = {
    "demand": "需求与内容侧",
    "sales": "销售市场侧",
    "sourcing": "货源侧",
}


def platform_catalog() -> list[dict]:
    """Public metadata for every registered platform."""
    return [dict(item, roleLabel=ROLE_LABELS.get(item["role"], item["role"])) for item in PLATFORMS.values()]


def platforms_by_role(role: str) -> list[dict]:
    return [dict(item) for item in PLATFORMS.values() if item["role"] == role]


def platform_ids_by_role(role: str) -> list[str]:
    return [item["id"] for item in PLATFORMS.values() if item["role"] == role]


def site_filter(platform_ids: list[str]) -> str:
    """Build a search-engine site filter for the given platforms."""
    sites: list[str] = []
    for platform_id in platform_ids:
        platform = PLATFORMS.get(platform_id)
        if not platform:
            continue
        for site in platform.get("sites", []):
            if site not in sites:
                sites.append(site)
    if not sites:
        return ""
    if len(sites) == 1:
        return f"site:{sites[0]}"
    return " OR ".join(f"site:{site}" for site in sites)


def sourcing_platform_options() -> list[dict]:
    """Domestic supply platforms an operator can pick as a sourcing preference."""
    return [
        {"id": item["id"], "name": item["name"], "note": item["note"], "searchLabel": item["searchLabel"]}
        for item in PLATFORMS.values()
        if item["role"] == "sourcing" and item["region"] == "国内"
    ]


def platform_for_url(url: str) -> dict | None:
    """Identify the platform a result actually came from.

    Search backends treat ``site:`` as a hint, not a filter, so a query aimed at
    one platform can return pages from another. Evidence must be labelled by the
    host it actually came from, never by the platform we asked for.
    """
    if not url:
        return None
    lowered = url.lower()
    best = None
    best_length = 0
    for item in PLATFORMS.values():
        for site in item.get("sites", []):
            if site in lowered and len(site) > best_length:
                best = {"id": item["id"], "name": item["name"], "role": item["role"], "region": item["region"]}
                best_length = len(site)
    return best
