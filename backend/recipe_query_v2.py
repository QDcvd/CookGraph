"""菜谱知识图谱查询系统 V2.0 — 纯参数驱动版。

四种查询模式：dish / ingredients / combo / missing
已后端化清理：无 argparse，无 sys.exit，无 emoji 核心依赖，返回 dict。
"""

from __future__ import annotations

import csv
import json
import os
import pickle
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from difflib import SequenceMatcher

# ── 配置 ──

ROOT = Path(__file__).resolve().parent.parent
ENTITY_CONFIG_PATH = ROOT / "config" / "recepi" / "entity_config.csv"
RELATION_CONFIG_PATH = ROOT / "config" / "recepi" / "relation_config.csv"
ATTRIBUTE_CONFIG_PATH = ROOT / "config" / "recepi" / "attribute_config.csv"
KEYWORD_CONFIG_PATH = ROOT / "config" / "recepi" / "keyword_config.json"
DEFAULT_KG_PATH = ROOT / "config" / "2kg_chem+recipe_fire_12K.pkl"
FUZZY_THRESHOLD = 0.6
DISH_FUZZY_THRESHOLD = 0.85

# 关系名兼容映射
RELATION_ALIASES = {
    "USES_AUXILIARY": "USES_AUXILIARY_INGREDIENT",
}

PANTRY_WHITELIST = {
    "姜", "生姜", "老姜", "嫩姜", "仔姜",
    "蒜", "大蒜", "蒜头", "蒜瓣", "姜蒜", "葱姜蒜",
    "葱", "葱花", "香葱", "小葱", "大葱", "洋葱",
    "香菜", "香菜末", "芫荽",
    "盐", "食盐", "细盐", "粗盐",
    "糖", "白糖", "冰糖", "红糖", "砂糖",
    "酱油", "生抽", "老抽", "味极鲜", "蒸鱼豉油",
    "料酒", "黄酒", "花雕酒", "米酒", "白酒",
    "醋", "米醋", "陈醋", "香醋", "白醋",
    "蚝油", "鱼露", "豉油",
    "鸡精", "味精", "鸡粉", "高汤精",
    "胡椒粉", "白胡椒粉", "黑胡椒粉", "花椒粉", "五香粉", "十三香",
    "淀粉", "生粉", "玉米淀粉", "土豆淀粉", "红薯淀粉",
    "食用油", "植物油", "花生油", "菜籽油", "玉米油", "葵花籽油", "橄榄油",
    "香油", "芝麻油", "花椒油", "辣椒油", "红油",
    "豆瓣酱", "甜面酱", "番茄酱", "沙茶酱", "芝麻酱", "花生酱", "辣椒酱", "剁椒",
    "孜然", "孜然粉", "辣椒粉", "花椒", "八角", "桂皮", "香叶", "草果", "丁香",
    "咖喱粉", "咖喱块", "芥末", "芥末酱",
    "蜂蜜", "糖浆", "麦芽糖",
    "芝麻", "白芝麻", "黑芝麻",
    "水", "清水", "开水", "温水", "凉水",
    "高汤", "鸡汤", "骨头汤", "牛骨汤", "猪骨汤",
    "鸡蛋", "蛋清", "蛋黄",
}

_RECOMMENDATION_FAMILY_KEYS: tuple[str, ...] | None = None
_MEAT_FAMILY_MARKERS = ("猪肉", "牛肉", "鸡肉", "羊肉", "鸭肉")


def _ingredient_family_keys() -> tuple[str, ...]:
    global _RECOMMENDATION_FAMILY_KEYS
    if _RECOMMENDATION_FAMILY_KEYS is not None:
        return _RECOMMENDATION_FAMILY_KEYS
    path = ROOT / "config" / "recommendation_aliases.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    groups = data.get("ingredient", {}) if isinstance(data, dict) else {}
    keys = groups.keys() if isinstance(groups, dict) else []
    _RECOMMENDATION_FAMILY_KEYS = tuple(
        sorted(
            (str(key).lower().replace("（", "(").replace("）", ")") for key in keys),
            key=len,
            reverse=True,
        )
    )
    return _RECOMMENDATION_FAMILY_KEYS


def _ingredient_family(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("（", "(").replace("）", ")")
    for key in _ingredient_family_keys():
        if not key:
            continue
        if key in _MEAT_FAMILY_MARKERS:
            if normalized == key or normalized.startswith(key + "("):
                return key
            continue
        if key in normalized:
            return key
    return ""


def _ingredient_family_root(value: str) -> str:
    family = _ingredient_family(value)
    return family.split("(", 1)[0]


def _meat_family_root(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("（", "(").replace("）", ")")
    for marker in _MEAT_FAMILY_MARKERS:
        if normalized == marker or normalized.startswith(marker + "("):
            return marker
    return ""


# ── 工具 ──

def _normalize_relation(rel: str) -> str:
    """关系名兼容处理。"""
    return RELATION_ALIASES.get(rel, rel)


# ── ConfigLoader ──

class ConfigLoader:
    def __init__(self, entity_path: str, relation_path: str,
                 attr_path: str, keyword_path: str):
        self.entity_config = self._load_csv(entity_path)
        self.relation_config = self._load_csv(relation_path)
        self.attr_config = self._load_csv(attr_path)
        self.keyword_config = self._load_json(keyword_path)

    def _load_csv(self, path: str) -> list[dict]:
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def _load_json(self, path: str) -> dict:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)


# ── QueryExecutor ──

class QueryExecutor:
    def __init__(self, graph, config: ConfigLoader):
        self.graph = graph
        self.config = config
        self._build_indices()

    def _build_indices(self):
        self.dish_nodes = {}
        self.all_nodes_by_label = defaultdict(dict)
        for node_id, attrs in self.graph.nodes(data=True):
            label = attrs.get("label", "")
            name = attrs.get("name", "")
            if not label or not name:
                continue
            self.all_nodes_by_label[label][name] = node_id
            if label == "Dish":
                self.dish_nodes[name] = node_id

    def fuzzy_match(self, query: str, candidates: list[str]) -> tuple[str | None, float]:
        best_match = None
        best_score = 0
        query_lower = query.lower()
        for candidate in candidates:
            if candidate.lower() == query_lower:
                return candidate, 1.0
            if query_lower in candidate.lower():
                score = 0.9
                if score > best_score:
                    best_score = score
                    best_match = candidate
            elif candidate.lower() in query_lower:
                score = 0.8
                if score > best_score:
                    best_score = score
                    best_match = candidate
        if best_score < FUZZY_THRESHOLD:
            for candidate in candidates:
                score = SequenceMatcher(None, query_lower, candidate.lower()).ratio()
                if score > best_score and score >= FUZZY_THRESHOLD:
                    best_score = score
                    best_match = candidate
        return best_match, best_score

    def find_dish(self, dish_name: str) -> tuple[str | None, str, float]:
        if dish_name in self.dish_nodes:
            return self.dish_nodes[dish_name], dish_name, 1.0
        if len(str(dish_name or "").strip()) < 3:
            return None, dish_name, 0.0
        matched_name, score = self.fuzzy_match(dish_name, list(self.dish_nodes.keys()))
        if matched_name and score >= DISH_FUZZY_THRESHOLD:
            return self.dish_nodes[matched_name], matched_name, score
        return None, dish_name, 0.0

    def get_dish_relations(self, dish_id: str, rel_type: str | None = None) -> list[dict]:
        results = []
        for _, target_id, edge_data in self.graph.edges(dish_id, data=True):
            edge_rel = _normalize_relation(edge_data.get("relation") or edge_data.get("type") or "")
            if rel_type and edge_rel != rel_type:
                continue
            target_node = self.graph.nodes[target_id]
            results.append({
                "name": target_node.get("name", "未知"),
                "amount": edge_data.get("amount", ""),
                "relation_type": edge_rel,
                "label": target_node.get("label", ""),
            })
        return results

    def get_dish_attribute(self, dish_id: str, attr_name: str) -> Any:
        return self.graph.nodes[dish_id].get(attr_name, "无数据")

    def get_all_ingredients(self, dish_id: str) -> list[dict]:
        ingredients = []
        rel_map = {
            "USES_MAIN_INGREDIENT": "主料",
            "USES_AUXILIARY_INGREDIENT": "配料",
            "USES_SEASONING": "调味料",
        }
        for _, target_id, edge_data in self.graph.edges(dish_id, data=True):
            rel_type = _normalize_relation(edge_data.get("relation") or edge_data.get("type") or "")
            if rel_type in rel_map:
                target_node = self.graph.nodes[target_id]
                name = target_node.get("name", "未知")
                ingredients.append({
                    "name": name,
                    "amount": edge_data.get("amount", "适量"),
                    "relation_type": rel_type,
                    "relation_label": rel_map.get(rel_type, "其他"),
                    "is_pantry": name in PANTRY_WHITELIST,
                    "node_id": target_id,
                })
        return ingredients


class ComboQueryExecutor(QueryExecutor):
    @staticmethod
    def _ingredient_match(req_ing: str, ingredient: dict) -> tuple[float, float]:
        """Return (match quality, role weight) for one requested ingredient."""
        name = str(ingredient.get("name") or "")
        req = str(req_ing or "")
        if not name or not req:
            return 0.0, 0.0
        req_lower = req.lower()
        name_lower = name.lower()
        req_family = _ingredient_family(req)
        name_family = _ingredient_family(name)
        req_root = _ingredient_family_root(req)
        name_root = _ingredient_family_root(name) or _meat_family_root(name)
        if req_root and name_root and req_root in _MEAT_FAMILY_MARKERS and req_root != name_root:
            return 0.0, 0.0
        if req_root in _MEAT_FAMILY_MARKERS and any(marker in name_lower for marker in _MEAT_FAMILY_MARKERS):
            if name_root != req_root:
                return 0.0, 0.0
        if req_lower == name_lower:
            quality = 1.0
        elif req_lower in name_lower or name_lower in req_lower:
            quality = 0.88
        else:
            quality = SequenceMatcher(None, req_lower, name_lower).ratio()
            if quality < FUZZY_THRESHOLD:
                return 0.0, 0.0

        role_weight = {
            "USES_MAIN_INGREDIENT": 1.0,
            "USES_AUXILIARY_INGREDIENT": 0.62,
            "USES_SEASONING": 0.22,
        }.get(str(ingredient.get("relation_type") or ""), 0.35)
        return quality, role_weight

    def execute_combo(self, ingredients: list[str] | None = None,
                      technique: str | None = None,
                      taste: str | None = None,
                      cuisine: str | None = None,
                      mealtime: str | None = None,
                      exclude: list[str] | None = None,
                      limit: int = 20) -> dict:
        candidates = []
        total_count = 0
        exclude = exclude or []

        for dish_name, dish_id in self.dish_nodes.items():
            if exclude:
                dish_ing_names = {ing["name"] for ing in self.get_all_ingredients(dish_id)}
                should_exclude = False
                for ex in exclude:
                    for din in dish_ing_names:
                        if ex.lower() in din.lower() or din.lower() in ex.lower():
                            should_exclude = True
                            break
                    if should_exclude:
                        break
                if should_exclude:
                    continue

            if ingredients:
                dish_ing = self.get_all_ingredients(dish_id)
                matched_scores = []
                all_matched = True
                for req_ing in ingredients:
                    matches = [self._ingredient_match(req_ing, ing) for ing in dish_ing]
                    best_quality, best_role = max(matches, default=(0.0, 0.0))
                    if best_quality <= 0:
                        all_matched = False
                        break
                    matched_scores.append((best_quality, best_role))
                if not all_matched:
                    continue
                match_score = sum(quality * (0.65 + 0.35 * role) for quality, role in matched_scores)
                name_lower = dish_name.lower()
                for req_ing in ingredients:
                    if str(req_ing).lower() in name_lower:
                        match_score += 0.18

            if technique:
                techs = self.get_dish_relations(dish_id, "USES_TECHNIQUE")
                matched_tech, _ = self.fuzzy_match(technique, [t["name"] for t in techs])
                if not matched_tech:
                    continue

            if taste:
                tastes = self.get_dish_relations(dish_id, "HAS_TASTE")
                matched_taste, _ = self.fuzzy_match(taste, [t["name"] for t in tastes])
                if not matched_taste:
                    continue

            if cuisine:
                cuisines = self.get_dish_relations(dish_id, "BELONGS_TO_CUISINE")
                matched_cuisine, _ = self.fuzzy_match(cuisine, [c["name"] for c in cuisines])
                if not matched_cuisine:
                    continue

            if mealtime:
                mealtimes = self.get_dish_relations(dish_id, "SUITABLE_FOR")
                matched_mealtime, _ = self.fuzzy_match(mealtime, [m["name"] for m in mealtimes])
                if not matched_mealtime:
                    continue

            total_count += 1
            candidates.append({
                    "dish_name": dish_name,
                    "dish_id": dish_id,
                    "ingredients": [ing["name"] for ing in self.get_all_ingredients(dish_id)],
                    "techniques": [t["name"] for t in self.get_dish_relations(dish_id, "USES_TECHNIQUE")],
                    "tastes": [t["name"] for t in self.get_dish_relations(dish_id, "HAS_TASTE")],
                    "cuisines": [c["name"] for c in self.get_dish_relations(dish_id, "BELONGS_TO_CUISINE")],
                    "match_score": round(match_score, 4) if ingredients else 0.0,
                })

        candidates.sort(key=lambda item: (-float(item.get("match_score", 0.0)), str(item.get("dish_name") or "")))
        results = candidates[: max(0, int(limit))]

        cond_parts = []
        if ingredients:
            cond_parts.append(f"包含食材：{', '.join(ingredients)}")
        if technique:
            cond_parts.append(f"技法：{technique}")
        if taste:
            cond_parts.append(f"味道：{taste}")
        if cuisine:
            cond_parts.append(f"菜系：{cuisine}")

        lines = []
        if not results:
            lines.append(f"未找到符合条件的菜式。查询条件：{'；'.join(cond_parts) or '（无）'}")
        else:
            if total_count > len(results):
                lines.append(f"找到 {total_count} 道符合条件的菜式（展示前 {len(results)} 道）：")
            else:
                lines.append(f"找到 {total_count} 道符合条件的菜式：")
            for idx, dish in enumerate(results, 1):
                lines.append(f"{idx}. {dish['dish_name']}")
                if dish.get("techniques"):
                    lines.append(f"   技法：{', '.join(dish['techniques'])}")
                if dish.get("tastes"):
                    lines.append(f"   味道：{', '.join(dish['tastes'])}")
                if dish.get("cuisines"):
                    lines.append(f"   菜系：{', '.join(dish['cuisines'])}")

        return {
            "success": total_count > 0,
            "count": total_count,
            "dishes": results,
            "human_readable": "\n".join(lines),
        }


class MissingQueryExecutor(QueryExecutor):
    def execute_missing(self, dish_name: str, user_ingredients: list[str]) -> dict:
        dish_id, matched_name, score = self.find_dish(dish_name)
        if not dish_id:
            return {"success": False, "error": f"未找到菜品: {dish_name}",
                    "human_readable": f"未找到菜品「{dish_name}」。"}

        all_ingredients = self.get_all_ingredients(dish_id)
        needed_items = [ing for ing in all_ingredients if not ing["is_pantry"]]

        matched_items = []
        matched_names = set()
        for user_ing in user_ingredients:
            best_match = None
            best_score = 0
            for ing in needed_items:
                if ing["name"] in matched_names:
                    continue
                ing_name = ing["name"]
                if ing_name.lower() == user_ing.lower():
                    best_match = ing
                    best_score = 1.0
                    break
                if user_ing.lower() in ing_name.lower() or ing_name.lower() in user_ing.lower():
                    score = 0.9
                    if score > best_score:
                        best_score = score
                        best_match = ing
                if best_score < FUZZY_THRESHOLD:
                    score = SequenceMatcher(None, user_ing.lower(), ing_name.lower()).ratio()
                    if score > best_score and score >= FUZZY_THRESHOLD:
                        best_score = score
                        best_match = ing
            if best_match:
                matched_items.append({**best_match, "user_input": user_ing, "match_score": best_score})
                matched_names.add(best_match["name"])

        missing_items = [ing for ing in needed_items if ing["name"] not in matched_names]
        total_needed = len(needed_items)
        matched_count = len(matched_items)
        coverage = matched_count / total_needed if total_needed > 0 else 0

        lines = [f"【{matched_name} 食材清单】"]
        lines.append(f"已有食材：{'、'.join(user_ingredients)}")
        if matched_items:
            lines.append(f"已匹配（{matched_count}/{total_needed}）：")
            for m in matched_items:
                tag = "（模糊匹配）" if m.get("match_score", 1.0) < 1.0 else ""
                lines.append(f"  - {m['name']}（{m['amount']}）{tag}")
        if missing_items:
            lines.append(f"还缺：")
            for m in missing_items:
                lines.append(f"  - {m['name']}（{m['amount']}）")
        else:
            lines.append("无需额外购买食材。")

        return {
            "success": True,
            "dish_name": matched_name,
            "coverage": round(coverage, 2),
            "matched_count": matched_count,
            "total_needed": total_needed,
            "matched": [{"name": m["name"], "amount": m["amount"]} for m in matched_items],
            "missing": [{"name": m["name"], "amount": m["amount"]} for m in missing_items],
            "human_readable": "\n".join(lines),
        }


class RecipeQuerySystem:
    def __init__(self, kg_path: str | Path | None = None):
        resolved = Path(kg_path or DEFAULT_KG_PATH).resolve()
        self.kg_path = str(resolved)
        self.config = ConfigLoader(
            str(ENTITY_CONFIG_PATH), str(RELATION_CONFIG_PATH),
            str(ATTRIBUTE_CONFIG_PATH), str(KEYWORD_CONFIG_PATH),
        )
        self.graph = self._load_graph()
        self.executor = QueryExecutor(self.graph, self.config)
        self.combo_executor = ComboQueryExecutor(self.graph, self.config)
        self.missing_executor = MissingQueryExecutor(self.graph, self.config)

    def _load_graph(self):
        if not os.path.exists(self.kg_path):
            raise FileNotFoundError(f"知识图谱文件不存在: {self.kg_path}")
        with open(self.kg_path, "rb") as f:
            return pickle.load(f)

    def query_dish(self, dish_name: str, field: str | None = None,
                   show_ingredients: bool = False,
                   show_techniques: bool = False,
                   show_seasonings: bool = False,
                   show_all: bool = False) -> dict:
        dish_id, matched_name, score = self.executor.find_dish(dish_name)
        if not dish_id:
            return {"success": False, "error": f"未找到菜品: {dish_name}",
                    "human_readable": f"未找到菜品「{dish_name}」。"}

        node_data = self.graph.nodes[dish_id]
        is_fuzzy = score < 1.0
        valid_attrs = {}
        for k, v in node_data.items():
            if k not in ("label", "name", "created_at") and not k.startswith("_"):
                if v and str(v).lower() not in ("nan", "none", "null", ""):
                    valid_attrs[k] = v

        relations = defaultdict(list)
        for _, target_id, edge_data in self.graph.edges(dish_id, data=True):
            rel_type = _normalize_relation(edge_data.get("relation") or edge_data.get("type") or "")
            target_node = self.graph.nodes[target_id]
            target_name = target_node.get("name", "未知")
            amount = edge_data.get("amount", "")
            rel_label = rel_type
            for row in self.config.relation_config:
                if row.get("relation_type") == rel_type:
                    rel_label = row.get("query_keywords_forward", rel_type).split(";")[0]
                    break
            relations[rel_label].append({"name": target_name, "amount": amount, "type": rel_type})

        if field:
            attr_value = node_data.get(field, "无数据")
            fuzzy_warn = f"模糊匹配到「{matched_name}」\n\n" if is_fuzzy else ""
            return {"success": True, "dish_name": matched_name, "field": field, "value": attr_value,
                    "human_readable": f"{fuzzy_warn}【{matched_name} - {field}】\n{attr_value}"}

        if show_ingredients or show_all:
            ings = self.executor.get_all_ingredients(dish_id)
            relations["食材清单"] = [{"name": ing["name"], "amount": ing["amount"], "type": ing["relation_type"]} for ing in ings]
        if show_techniques or show_all:
            techs = self.executor.get_dish_relations(dish_id, "USES_TECHNIQUE")
            relations["技法"] = [{"name": t["name"], "amount": t["amount"], "type": t["relation_type"]} for t in techs]
        if show_seasonings or show_all:
            seas = self.executor.get_dish_relations(dish_id, "USES_SEASONING")
            relations["调味料"] = [{"name": s["name"], "amount": s["amount"], "type": s["relation_type"]} for s in seas]

        fuzzy_warn = f"模糊匹配到「{matched_name}」\n\n" if is_fuzzy else ""
        lines = [f"{fuzzy_warn}{matched_name} 完整档案"]
        lines.append("基本信息：")
        for attr, val in valid_attrs.items():
            lines.append(f"{attr}: {val}")
        if relations:
            lines.append("关联信息：")
            for rel_label, items in relations.items():
                for item in items:
                    amt = f"（{item['amount']}）" if item["amount"] else ""
                    lines.append(f"  - {item['name']}{amt} [{rel_label}]")

        return {"success": True, "dish_name": matched_name, "attributes": valid_attrs,
                "relations": dict(relations), "is_fuzzy": is_fuzzy,
                "human_readable": "\n".join(lines)}

    def query_ingredients(self, ingredients: list[str], exclude: list[str] | None = None,
                          limit: int = 20) -> dict:
        result = self.combo_executor.execute_combo(ingredients=ingredients, exclude=exclude, limit=limit)
        if not result["success"]:
            return {"success": False, "human_readable": f"未找到包含食材「{', '.join(ingredients)}」的菜式。"}
        return result

    def query_combo(self, ingredients: list[str] | None = None,
                    technique: str | None = None, taste: str | None = None,
                    cuisine: str | None = None, mealtime: str | None = None,
                    exclude: list[str] | None = None, limit: int = 20) -> dict:
        return self.combo_executor.execute_combo(
            ingredients=ingredients, technique=technique, taste=taste,
            cuisine=cuisine, mealtime=mealtime, exclude=exclude, limit=limit)

    def query_missing(self, dish_name: str, ingredients: list[str]) -> dict:
        return self.missing_executor.execute_missing(dish_name, ingredients)
