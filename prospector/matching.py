"""Conservative cross-market product matching for normalized listings."""

from __future__ import annotations

import re
import unicodedata


UNIVERSAL_IDS = ("gtin", "ean", "upc")
VARIANT_FIELDS = ("variant", "color", "size", "packCount")


def _norm(value) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.sub(r"[^\w\u3400-\u9fff]", "", text, flags=re.UNICODE)


def _title_similarity(left: str, right: str) -> float:
    left = _norm(left)
    right = _norm(right)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    def grams(value: str) -> set[str]:
        if len(value) < 3:
            return {value}
        return {value[index : index + 2] for index in range(len(value) - 1)}

    lhs, rhs = grams(left), grams(right)
    return len(lhs & rhs) / len(lhs | rhs)


def match_listings(left: dict, right: dict) -> dict:
    """Compare two normalized offers and expose the reasons behind the result."""
    left_ids = {key.lower(): _norm(value) for key, value in (left.get("identifiers") or {}).items() if value}
    right_ids = {key.lower(): _norm(value) for key, value in (right.get("identifiers") or {}).items() if value}
    reasons: list[str] = []

    mismatches = []
    for field in VARIANT_FIELDS:
        lhs, rhs = _norm(left.get(field)), _norm(right.get(field))
        if lhs and rhs and lhs != rhs:
            mismatches.append(field)
    if mismatches:
        labels = {"variant": "规格/变体", "color": "颜色", "size": "尺寸", "packCount": "包装数量"}
        return {
            "status": "no_match",
            "confidence": 0,
            "reasons": [f"两侧{labels.get(field, field)}不一致" for field in mismatches],
            "manualReviewRequired": True,
        }

    left_brand, right_brand = _norm(left.get("brand")), _norm(right.get("brand"))
    left_model, right_model = _norm(left.get("model")), _norm(right.get("model"))
    if left_brand and right_brand and left_brand != right_brand:
        return {"status": "no_match", "confidence": 0, "reasons": ["品牌信息不一致"], "manualReviewRequired": True}
    if left_model and right_model and left_model != right_model:
        return {"status": "no_match", "confidence": 0, "reasons": ["型号信息不一致"], "manualReviewRequired": True}

    left_codes = {value for key, value in left_ids.items() if key in UNIVERSAL_IDS and value}
    right_codes = {value for key, value in right_ids.items() if key in UNIVERSAL_IDS and value}
    if left_codes and right_codes:
        if left_codes & right_codes:
            return {
                "status": "exact_match",
                "confidence": 100,
                "reasons": ["两侧 GTIN/EAN/UPC 商品码一致"],
                "manualReviewRequired": False,
            }
        return {
            "status": "no_match",
            "confidence": 0,
            "reasons": ["两侧 GTIN/EAN/UPC 商品码不一致，按不同商品处理"],
            "manualReviewRequired": True,
        }

    score = 0.0
    if left_brand and right_brand:
        score += 15
        reasons.append("品牌一致")
    if left_model and right_model:
        score += 45
        reasons.append("型号一致")

    title_similarity = _title_similarity(left.get("title", ""), right.get("title", ""))
    score += 25 * title_similarity
    if title_similarity >= 0.55:
        reasons.append(f"标题相似度 {title_similarity:.0%}")

    left_attributes = {str(k): _norm(v) for k, v in (left.get("attributes") or {}).items() if v}
    right_attributes = {str(k): _norm(v) for k, v in (right.get("attributes") or {}).items() if v}
    common_attributes = set(left_attributes) & set(right_attributes)
    if common_attributes:
        same = sum(left_attributes[key] == right_attributes[key] for key in common_attributes)
        score += 15 * same / len(common_attributes)
        reasons.append(f"{same}/{len(common_attributes)} 项商品属性一致")

    left_pack, right_pack = _norm(left.get("packCount")), _norm(right.get("packCount"))
    if left_pack and right_pack and left_pack == right_pack:
        score += 10
        reasons.append("包装数量一致")

    score = round(min(score, 100))
    if score >= 80:
        status = "probable_match"
        review = True
    elif score >= 55:
        status = "needs_review"
        review = True
    else:
        status = "no_match"
        review = True
    if not reasons:
        reasons.append("缺少可比的品牌、型号或属性，主要依据标题做初筛")
    return {
        "status": status,
        "confidence": score,
        "reasons": reasons,
        "manualReviewRequired": review,
    }
