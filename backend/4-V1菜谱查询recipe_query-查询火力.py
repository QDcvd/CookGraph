#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
菜谱知识图谱查询系统 V1.1 (Bugfix)
修复：reverse_keywords变量未定义错误


"""

import os
import sys
import json
import pickle
import argparse
import re
import csv
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
from difflib import SequenceMatcher
from collections import defaultdict

# ========== 【配置区域 - 修改这里】 ==========
# 配置文件路径
ENTITY_CONFIG_PATH = 'config/recepi/entity_config.csv'
RELATION_CONFIG_PATH = 'config/recepi/relation_config.csv'
ATTRIBUTE_CONFIG_PATH = 'config/recepi/attribute_config.csv'
KEYWORD_CONFIG_PATH = 'config/recepi/keyword_config.json'

# 知识图谱数据路径
DEFAULT_KG_PATH = 'config/2kg_chem+recipe_fire_12K.pkl'

# 模糊匹配阈值 (0-1，越高越严格)
FUZZY_THRESHOLD = 0.6
# =============================================



class CypherParser:
    """解析简化Cypher语句，转换为内部查询参数"""

    # 支持的Cypher模式正则
    PATTERNS = {
        # 模式1: MATCH (d:Dish {name:'xxx'}) RETURN d.attr_name  -> 正向属性
        'forward_attr': re.compile(
            r"MATCH\s+\((\w+):Dish\s+\{name:\s*['\"](.+?)['\"]\}\)\s+RETURN\s+\1\.(\w+)",
            re.IGNORECASE
        ),
        # 模式2: MATCH (d:Dish {name:'xxx'})-[:REL]->(t) RETURN t.name  -> 正向关系
        'forward_rel': re.compile(
            r"MATCH\s+\((\w+):Dish\s+\{name:\s*['\"](.+?)['\"]\}\)-\[:\s*(\w+)\s*\]->\(\w+\)\s+RETURN\s+\w+\.name",
            re.IGNORECASE
        ),
        # 模式3: MATCH (d:Dish)-[:REL]->(t:Type {name:'xxx'}) RETURN d.name  -> 反向查询
        'reverse': re.compile(
            r"MATCH\s+\((\w+):Dish\)-\[:\s*(\w+)\s*\]->\((\w+):\w+\s+\{name:\s*['\"](.+?)['\"]\}\)\s+RETURN\s+\1\.name",
            re.IGNORECASE
        ),
        # 模式4: MATCH (d:Dish {name:'xxx'})-[:REL]->(t:Type {name:'yyy'}) RETURN ...  -> 带值正向关系
        'forward_rel_with_value': re.compile(
            r"MATCH\s+\((\w+):Dish\s+\{name:\s*['\"](.+?)['\"]\}\)-\[:\s*(\w+)\s*\]->\(\w+:\w+\s+\{name:\s*['\"](.+?)['\"]\}\)",
            re.IGNORECASE
        ),
        # 模式5: MATCH (d:Dish {name:'xxx'}) RETURN d  -> 完整档案
        'forward_summary': re.compile(
            r"MATCH\s+\((\w+):Dish\s+\{name:\s*['\"](.+?)['\"]\}\)\s+RETURN\s+\1\b(?!\.)",
            re.IGNORECASE
        ),
        # 模式6: 简写: (d:Dish)-[:REL]->(t {name:'xxx'}) RETURN d.name
        'reverse_short': re.compile(
            r"\((\w+):Dish\)-\[:\s*(\w+)\s*\]->\(\w+\s+\{name:\s*['\"](.+?)['\"]\}\)\s+RETURN\s+\1\.name",
            re.IGNORECASE
        ),
    }

    @classmethod
    def is_cypher(cls, query: str) -> bool:
        """判断是否为Cypher语句"""
        q = query.strip().upper()
        return q.startswith('MATCH') or 'RETURN' in q

    @classmethod
    def parse(cls, query: str) -> Dict[str, Any]:
        """解析Cypher语句，返回与QueryParser.parse兼容的参数字典"""
        query = query.strip()
        result = {
            'type': 'unknown',
            'dish': None,
            'target_type': None,
            'target_name': None,
            'reverse_value': None,
            'original': query,
            'cypher': True
        }

        print(f"   [Cypher] 解析Cypher语句: {query}")

        # 尝试各模式匹配
        for mode, pattern in cls.PATTERNS.items():
            match = pattern.search(query)
            if match:
                print(f"   [Cypher] 匹配模式: {mode}")

                if mode == 'forward_attr':
                    result['type'] = 'forward_attr'
                    result['dish'] = match.group(2)
                    result['target_type'] = 'attribute'
                    result['target_name'] = match.group(3)
                    return result

                elif mode == 'forward_rel':
                    result['type'] = 'forward_rel'
                    result['dish'] = match.group(2)
                    result['target_type'] = 'relation'
                    result['target_name'] = match.group(3).upper()
                    return result

                elif mode == 'reverse':
                    result['type'] = 'reverse'
                    result['target_name'] = match.group(2).upper()
                    result['reverse_value'] = match.group(4)
                    return result

                elif mode == 'reverse_short':
                    result['type'] = 'reverse'
                    result['target_name'] = match.group(2).upper()
                    result['reverse_value'] = match.group(3)
                    return result

                elif mode == 'forward_rel_with_value':
                    result['type'] = 'forward_rel'
                    result['dish'] = match.group(2)
                    result['target_type'] = 'relation'
                    result['target_name'] = match.group(3).upper()
                    result['filter_value'] = match.group(4)
                    return result

                elif mode == 'forward_summary':
                    result['type'] = 'forward_summary'
                    result['dish'] = match.group(2)
                    return result

        # 兜底：尝试更宽松的解析
        return cls._loose_parse(query, result)

    @classmethod
    def _loose_parse(cls, query: str, result: Dict) -> Dict:
        """宽松解析：提取关键元素"""
        query_upper = query.upper()

        # 提取 Dish name
        dish_match = re.search(r"\{name:\s*['\"](.+?)['\"]\}", query, re.IGNORECASE)
        if dish_match:
            result['dish'] = dish_match.group(1)

        # 提取关系类型
        rel_match = re.search(r"-\[:\s*(\w+)\s*\]->", query, re.IGNORECASE)
        if rel_match:
            result['target_name'] = rel_match.group(1).upper()
            result['target_type'] = 'relation'

        # 提取 RETURN 的字段
        return_match = re.search(r"RETURN\s+(\w+)\.(\w+)", query, re.IGNORECASE)
        if return_match:
            returned = return_match.group(2).lower()
            if returned == 'name':
                if 'Dish' in query_upper and result.get('target_name'):
                    result['type'] = 'reverse'
                    val_match = re.search(r"\{name:\s*['\"](.+?)['\"]\}.*RETURN", query, re.IGNORECASE)
                    if val_match and val_match.group(1) != result.get('dish'):
                        result['reverse_value'] = val_match.group(1)
            else:
                result['type'] = 'forward_attr'
                result['target_name'] = returned
                result['target_type'] = 'attribute'

        # 如果 RETURN 是整个节点
        if re.search(r"RETURN\s+\w+\b(?!\.)", query, re.IGNORECASE) and not return_match:
            result['type'] = 'forward_summary'

        print(f"   [Cypher] 宽松解析结果: type={result['type']}, dish={result.get('dish')}, target={result.get('target_name')}, reverse_value={result.get('reverse_value')}")
        return result

    @classmethod
    def get_supported_syntax(cls) -> str:
        """返回支持的Cypher语法说明"""
        return """
╔══════════════════════════════════════════════════════════════════════════════╗
║                        支持的简化Cypher语法                                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ 1. 正向属性查询 - 查菜品属性                                                  ║
║    MATCH (d:Dish {name:'菜名'}) RETURN d.属性名                               ║
║    例: MATCH (d:Dish {name:'小炒黄牛肉'}) RETURN d.prep_process              ║
║    例: MATCH (d:Dish {name:'小炒黄牛肉'}) RETURN d.cooking_tips              ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ 2. 正向关系查询 - 查菜品关联实体                                              ║
║    MATCH (d:Dish {name:'菜名'})-[:关系类型]->(t) RETURN t.name                ║
║    例: MATCH (d:Dish {name:'小炒黄牛肉'})-[:USES_TECHNIQUE]->(t) RETURN t.name║
║    例: MATCH (d:Dish {name:'小炒黄牛肉'})-[:USES_MAIN_INGREDIENT]->(t) RETURN t.name
╠══════════════════════════════════════════════════════════════════════════════╣
║ 3. 反向查询 - 查哪些菜用了某实体                                              ║
║    MATCH (d:Dish)-[:关系类型]->(t:实体类型 {name:'值'}) RETURN d.name          ║
║    例: MATCH (d:Dish)-[:USES_TECHNIQUE]->(t:Technique {name:'炝炒'}) RETURN d.name
║    例: MATCH (d:Dish)-[:USES_MAIN_INGREDIENT]->(t:Ingredient {name:'黄牛肉'}) RETURN d.name
║    简写: (d:Dish)-[:USES_TECHNIQUE]->(t {name:'炝炒'}) RETURN d.name           ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ 4. 完整档案查询                                                               ║
║    MATCH (d:Dish {name:'菜名'}) RETURN d                                      ║
║    例: MATCH (d:Dish {name:'小炒黄牛肉'}) RETURN d                           ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ 5. 带值正向关系（过滤特定值）                                                 ║
║    MATCH (d:Dish {name:'菜名'})-[:关系]->(t:Type {name:'值'})                 ║
║    例: MATCH (d:Dish {name:'小炒黄牛肉'})-[:USES_MAIN_INGREDIENT]->(t:Ingredient {name:'黄牛肉'})
╠══════════════════════════════════════════════════════════════════════════════╣
║ 可用的关系类型（RELATION）：                                                  ║
║    USES_TECHNIQUE, USES_MAIN_INGREDIENT, USES_SEASONING,                    ║
║    HAS_TASTE, BELONGS_TO_CUISINE, SUITABLE_FOR, USES_METHOD                  ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ 可用的属性名（ATTRIBUTE）：                                                   ║
║    prep_process, cooking_process, cooking_tips, 以及配置文件中定义的其他属性   ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

class ConfigLoader:
    """加载配置文件"""
    
    def __init__(self, entity_path: str, relation_path: str, 
                 attr_path: str, keyword_path: str):
        self.entity_config = self._load_csv(entity_path)
        self.relation_config = self._load_csv(relation_path)
        self.attr_config = self._load_csv(attr_path)
        self.keyword_config = self._load_json(keyword_path)
        
        # 构建快速查找索引
        self._build_indices()
    
    def _load_csv(self, path: str) -> List[Dict]:
        """加载CSV文件"""
        if not os.path.exists(path):
            print(f"❌ 配置文件不存在: {path}")
            return []
        
        with open(path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            return list(reader)
    
    def _load_json(self, path: str) -> Dict:
        """加载JSON文件"""
        if not os.path.exists(path):
            print(f"❌ 配置文件不存在: {path}")
            return {}
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _build_indices(self):
        """构建查询索引"""
        # 实体类型索引：关键词 -> 实体类型
        self.entity_keyword_map = {}
        for row in self.entity_config:
            entity_type = row.get('entity_type', '')
            keywords = row.get('query_keywords_zh', '') + ';' + row.get('query_keywords_en', '')
            for kw in keywords.split(';'):
                if kw.strip():
                    self.entity_keyword_map[kw.strip().lower()] = entity_type
        
        # 属性索引：关键词 -> (实体类型, 属性名, 数据类型)
        self.attr_keyword_map = {}
        for row in self.attr_config:
            entity_type = row.get('entity_type', '')
            attr_name = row.get('attr_name', '')
            data_type = row.get('data_type', 'string')
            keywords = row.get('query_keywords_zh', '') + ';' + row.get('query_keywords_en', '')
            for kw in keywords.split(';'):
                if kw.strip():
                    self.attr_keyword_map[kw.strip().lower()] = (entity_type, attr_name, data_type)
        
        # 关系索引：关键词 -> 关系类型
        self.relation_keyword_map = {}
        self.reverse_relation_map = {}
        for row in self.relation_config:
            rel_type = row.get('relation_type', '')
            fwd_keywords = row.get('query_keywords_forward', '')
            rev_keywords = row.get('query_keywords_reverse', '')
            
            for kw in fwd_keywords.split(';'):
                if kw.strip():
                    self.relation_keyword_map[kw.strip().lower()] = rel_type
            
            for kw in rev_keywords.split(';'):
                if kw.strip():
                    self.reverse_relation_map[kw.strip().lower()] = rel_type


class QueryParser:
    """解析查询输入"""
    
    def __init__(self, config: ConfigLoader):
        self.config = config
    
    def parse(self, query: str) -> Dict[str, Any]:
        """
        解析查询语句，支持自然语言和Cypher语法
        """
        query_original = query.strip()

        # 优先检测Cypher语句
        if CypherParser.is_cypher(query_original):
            return CypherParser.parse(query_original)

        query_lower = query_original.lower()
        result = {
            'type': 'unknown',
            'dish': None,
            'target_type': None,
            'target_name': None,
            'reverse_value': None,
            'original': query_original
        }

        query_lower = query_original.lower()
        result = {
            'type': 'unknown',
            'dish': None,
            'target_type': None,
            'target_name': None,
            'reverse_value': None,
            'original': query_original
        }
        
        # 调试输出
        print(f"   [调试] 开始解析: {query_original}")
        print(f"   [调试] reverse_relation_map 数量: {len(self.config.reverse_relation_map)}")
        
        # 1. 反向查询检测 - 优先处理
        reverse_indicators = ['哪些', '什么', '有哪些', '有什么', 'list', 'show']
        is_reverse = any(ind in query_lower for ind in reverse_indicators)
        
        if is_reverse:
            print(f"   [调试] 检测到反向查询意图")
            
            # ===== 模式A: 提取"动词+值+关系词"（原有成功逻辑保留）=====
            action_match = re.search(r'(?:用了|使用|采用|是|为|有|包含)\s*(.+?)(?:的|之|$)', query_lower)
            if action_match:
                candidate = action_match.group(1).strip()
                print(f"   [调试] 模式A提取候选值: {candidate}")
                
                # 检查后缀关系词（如"炝炒技法"）
                for rel_kw, rel_type in sorted(self.config.reverse_relation_map.items(), 
                                              key=lambda x: len(x[0]), reverse=True):
                    if len(rel_kw) < 2:
                        continue
                    if candidate.endswith(rel_kw):
                        value = candidate[:-len(rel_kw)].strip()
                        if value:
                            print(f"   [调试] 模式A后缀匹配成功: 关系={rel_type}, 值={value}")
                            result['type'] = 'reverse'
                            result['target_name'] = rel_type
                            result['reverse_value'] = value
                            return result
                
                # 检查前缀关系词（如"技法炝炒"）
                for rel_kw, rel_type in sorted(self.config.reverse_relation_map.items(), 
                                              key=lambda x: len(x[0]), reverse=True):
                    if len(rel_kw) < 2:
                        continue
                    if candidate.startswith(rel_kw):
                        value = candidate[len(rel_kw):].strip()
                        if value:
                            print(f"   [调试] 模式A前缀匹配成功: 关系={rel_type}, 值={value}")
                            result['type'] = 'reverse'
                            result['target_name'] = rel_type
                            result['reverse_value'] = value
                            return result
                
                # ===== 新增模式C: 未找到关系词，尝试匹配实体类型关键词 =====
                # 例如："哪些菜用了包菜" -> candidate="包菜", 需要推断实体类型
                print(f"   [调试] 模式A未匹配到关系词，进入实体类型推断")
                self._try_infer_entity_type(candidate, result)
                if result['type'] == 'reverse':
                    return result
            
            # ===== 模式B: 关系词+值结构（原有逻辑保留）=====
            for rel_kw, rel_type in sorted(self.config.reverse_relation_map.items(), 
                                          key=lambda x: len(x[0]), reverse=True):
                if len(rel_kw) < 2:
                    continue
                pattern = rf'{rel_kw}(?:为|是|有)?\s*(.+?)(?:的|之|则|$)'
                match = re.search(pattern, query_lower)
                if match:
                    value = match.group(1).strip()
                    print(f"   [调试] 模式B匹配成功: 关系={rel_type}, 值={value}")
                    result['type'] = 'reverse'
                    result['target_name'] = rel_type
                    result['reverse_value'] = value
                    return result
            
            # ===== 新增模式D: 包含实体类型关键词的复杂结构 =====
            # 例如："哪些菜式的食材用了包菜"、"哪些菜式用了包菜这种食材"
            print(f"   [调试] 进入模式D: 实体类型关键词匹配")
            
            # 从entity_config构建实体类型关键词映射
            entity_type_map = {}  # 关键词 -> (实体类型, 关系类型)
            for row in self.config.entity_config:
                entity_type = row.get('entity_type', '')
                # 根据实体类型推断关系类型
                inferred_rel = self._infer_relation_by_entity(entity_type)
                if inferred_rel:
                    keywords = row.get('query_keywords_zh', '').split(';')
                    for kw in keywords:
                        if kw.strip() and len(kw.strip()) >= 2:
                            entity_type_map[kw.strip()] = (entity_type, inferred_rel)
            
            # 按关键词长度降序，避免短词覆盖长词（如"食材"优先于"材"）
            sorted_keywords = sorted(entity_type_map.items(), key=lambda x: len(x[0]), reverse=True)
            
            for kw, (entity_type, rel_type) in sorted_keywords:
                if kw not in query_lower:
                    continue
                
                print(f"   [调试] 匹配到实体类型关键词: {kw} -> {entity_type} -> {rel_type}")
                
                # 模式D1: "实体类型+动词+值" 如 "食材用了包菜"
                pattern_d1 = rf'{kw}(?:用了|使用|采用|包含|有)?\s*(.+)'
                match_d1 = re.search(pattern_d1, query_lower)
                if match_d1:
                    value = match_d1.group(1).strip()
                    # 清理值（去掉"的"、"有哪些"等后缀）
                    value = re.sub(r'(?:的|之|有哪些|有什么).*', '', value).strip()
                    if value and len(value) < 15:  # 合理长度检查
                        print(f"   [调试] 模式D1匹配: 值={value}")
                        result['type'] = 'reverse'
                        result['target_name'] = rel_type
                        result['reverse_value'] = value
                        return result
                
                # 模式D2: "值+这种+实体类型" 如 "包菜这种食材"
                pattern_d2 = rf'(.+?)这种\s*{kw}'
                match_d2 = re.search(pattern_d2, query_lower)
                if match_d2:
                    value = match_d2.group(1).strip()
                    # 清理值（去掉动词前缀）
                    value = re.sub(r'(?:用了|使用|采用|包含|有)\s*', '', value).strip()
                    if value and len(value) < 15:
                        print(f"   [调试] 模式D2匹配: 值={value}")
                        result['type'] = 'reverse'
                        result['target_name'] = rel_type
                        result['reverse_value'] = value
                        return result
        
        # 2. 正向查询解析（保持不变）
        print(f"   [调试] 进入正向查询分支")
        dish_candidates = []
        
        if '的' in query_original:
            parts = query_original.split('的')
            for i in range(len(parts)-1, 0, -1):
                candidate = '的'.join(parts[:i])
                dish_candidates.append(candidate)
        
        if not dish_candidates:
            words = query_original.split()
            for i in range(len(words), 0, -1):
                candidate = ' '.join(words[:i])
                dish_candidates.append(candidate)
        
        for dish_candidate in dish_candidates:
            remainder = query_original.replace(dish_candidate, '').replace('的', '').strip()
            if not remainder:
                continue
                
            for attr_kw, (entity, attr_name, dtype) in self.config.attr_keyword_map.items():
                if attr_kw.lower() in remainder.lower():
                    result['type'] = 'forward_attr'
                    result['dish'] = dish_candidate
                    result['target_type'] = 'attribute'
                    result['target_name'] = attr_name
                    print(f"   [调试] 正向属性匹配: {attr_name}")
                    return result
            
            for rel_kw, rel_type in self.config.relation_keyword_map.items():
                if rel_kw.lower() in remainder.lower():
                    result['type'] = 'forward_rel'
                    result['dish'] = dish_candidate
                    result['target_type'] = 'relation'
                    result['target_name'] = rel_type
                    print(f"   [调试] 正向关系匹配: {rel_type}")
                    return result
        
        # 3. 兜底：完整档案查询
        if len(query_original) < 20 and '的' not in query_original and not is_reverse:
            result['type'] = 'forward_summary'
            result['dish'] = query_original
            print(f"   [调试] 兜底为完整档案查询: {query_original}")
        
        print(f"   [调试] 最终解析结果: type={result['type']}")
        return result
    
    def _try_infer_entity_type(self, candidate: str, result: Dict) -> bool:
        """尝试根据值推断实体类型（模糊匹配）"""
        # 在所有实体中搜索这个值，找到最可能的类型
        best_match = None
        best_score = 0
        best_label = None
        
        for label, nodes in self.config.entity_keyword_map.items():
            # 这里需要通过executor访问，但parser没有executor...
            # 简化：根据常见食材、技法等做简单判断
            pass
        
        # 如果无法推断，返回False让上层继续处理
        return False
    
    def _infer_relation_by_entity(self, entity_type: str) -> Optional[str]:
        """根据实体类型推断关系类型"""
        mapping = {
            'Technique': 'USES_TECHNIQUE',
            'Taste': 'HAS_TASTE',
            'Cuisine': 'BELONGS_TO_CUISINE',
            'Ingredient': 'USES_MAIN_INGREDIENT',  # 食材默认查主料
            'Seasoning': 'USES_SEASONING',
            'MealTime': 'SUITABLE_FOR',
            'CookingMethod': 'USES_METHOD'
        }
        return mapping.get(entity_type)
    
    def parse_args(self, args) -> Dict[str, Any]:
        """解析命令行参数"""
        if args.reverse:
            return {
                'type': 'reverse',
                'target_name': args.relation_type or self._infer_relation_from_args(args),
                'reverse_value': args.value or args.ingredient or args.technique or args.taste or args.cuisine,
                'original': f"reverse query: {args}"
            }
        else:
            target_name = args.field or args.relation
            target_type = 'attribute' if args.field else ('relation' if args.relation else 'unknown')
            
            return {
                'type': 'forward_attr' if args.field else ('forward_rel' if args.relation else 'forward_summary'),
                'dish': args.dish,
                'target_type': target_type,
                'target_name': target_name,
                'original': f"forward query: {args.dish}"
            }
    
    def _infer_relation_from_args(self, args) -> str:
        if args.technique:
            return 'USES_TECHNIQUE'
        elif args.ingredient:
            return 'USES_MAIN_INGREDIENT'
        elif args.taste:
            return 'HAS_TASTE'
        elif args.cuisine:
            return 'BELONGS_TO_CUISINE'
        return ''


class QueryExecutor:
    """执行图谱查询"""
    
    def __init__(self, graph, config: ConfigLoader):
        self.graph = graph
        self.config = config
        self._build_node_indices()
    
    def _build_node_indices(self):
        """构建节点索引"""
        self.dish_nodes = {}  # name -> node_id
        self.all_nodes_by_label = defaultdict(dict)  # label -> {name: id}
        
        for node_id, attrs in self.graph.nodes(data=True):
            label = attrs.get('label', '')
            name = attrs.get('name', '')
            if not label or not name:
                continue
            
            self.all_nodes_by_label[label][name] = node_id
            
            if label == 'Dish':
                self.dish_nodes[name] = node_id
    
    def fuzzy_match(self, query: str, candidates: List[str], threshold: float = FUZZY_THRESHOLD) -> Tuple[Optional[str], float]:
        """模糊匹配"""
        best_match = None
        best_score = 0
        
        query_lower = query.lower()
        
        for candidate in candidates:
            # 精确匹配优先
            if candidate.lower() == query_lower:
                return candidate, 1.0
            
            # 包含匹配（部分匹配给较高分）
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
        
        # SequenceMatcher模糊匹配
        if best_score < threshold:
            for candidate in candidates:
                score = SequenceMatcher(None, query_lower, candidate.lower()).ratio()
                if score > best_score and score >= threshold:
                    best_score = score
                    best_match = candidate
        
        return best_match, best_score
    
    def execute(self, parsed: Dict) -> Dict[str, Any]:
        """执行查询"""
        query_type = parsed['type']
        
        if query_type == 'forward_attr':
            return self._query_forward_attribute(parsed)
        elif query_type == 'forward_rel':
            return self._query_forward_relation(parsed)
        elif query_type == 'reverse':
            return self._query_reverse(parsed)
        elif query_type == 'forward_summary':
            return self._query_summary(parsed)
        else:
            return {
                'success': False,
                'error': '无法解析查询意图',
                'match_mode': 'none',
                'structured': {},
                'human_readable': '无法理解的查询格式，请使用："菜名的属性是什么" 或 "哪些菜用了XXX"'
            }
    
    def _find_dish(self, dish_name: str) -> Tuple[Optional[str], str, float]:
        """查找菜品节点，支持精确/模糊匹配"""
        # 精确匹配
        if dish_name in self.dish_nodes:
            return self.dish_nodes[dish_name], dish_name, 1.0
        
        # 模糊匹配
        matched_name, score = self.fuzzy_match(dish_name, list(self.dish_nodes.keys()))
        if matched_name:
            return self.dish_nodes[matched_name], matched_name, score
        
        return None, dish_name, 0.0
    
    def _query_forward_attribute(self, parsed: Dict) -> Dict:
        """正向属性查询：菜名 -> 属性值"""
        dish_name = parsed['dish']
        attr_name = parsed['target_name']
        
        dish_id, matched_name, score = self._find_dish(dish_name)
        
        if not dish_id:
            return {
                'success': False,
                'error': f'未找到菜品: {dish_name}',
                'match_mode': 'none',
                'structured': {},
                'human_readable': f'❌ 未找到菜品"{dish_name}"，请检查菜名是否正确。'
            }
        
        # 获取节点属性
        node_data = self.graph.nodes[dish_id]
        attr_value = node_data.get(attr_name, '无数据')
        
        is_fuzzy = score < 1.0
        match_mode = 'fuzzy' if is_fuzzy else 'exact'
        
        result = {
            'success': True,
            'query_type': 'forward_attribute',
            'dish': {
                'original': dish_name,
                'matched': matched_name,
                'similarity': score
            },
            'attribute': attr_name,
            'value': attr_value,
            'match_mode': match_mode,
            'structured': {
                'dish_name': matched_name,
                'attribute_name': attr_name,
                'attribute_value': attr_value,
                'match_mode': match_mode,
                'confidence': score
            }
        }
        
        # 生成人类可读文本
        fuzzy_warning = f"⚠️ 【模糊检索结果】未找到精确匹配\"{dish_name}\"，为您找到相似菜品：\"{matched_name}\"\n\n" if is_fuzzy else ""
        
        human_text = f"{fuzzy_warning}【查询结果】\n"
        human_text += f"菜品：{matched_name}\n"
        
        # 根据属性类型格式化输出
        if attr_name in ['prep_process', 'cooking_process']:
            human_text += f"\n{'='*40}\n"
            human_text += f"{attr_name}：\n{'='*40}\n"
            human_text += str(attr_value)
        elif attr_name == 'cooking_tips':
            human_text += f"烹饪提示：{attr_value}"
        else:
            human_text += f"{attr_name}：{attr_value}"
        
        result['human_readable'] = human_text
        return result
    
    def _query_forward_relation(self, parsed: Dict) -> Dict:
        """正向关系查询：菜名 -> 关联实体"""
        dish_name = parsed['dish']
        rel_type = parsed['target_name']
        
        dish_id, matched_name, score = self._find_dish(dish_name)
        
        if not dish_id:
            return {
                'success': False,
                'error': f'未找到菜品: {dish_name}',
                'match_mode': 'none',
                'structured': {},
                'human_readable': f'❌ 未找到菜品"{dish_name}"。'
            }
        
        # 查找出边
        targets = []
        for _, target_id, edge_data in self.graph.edges(dish_id, data=True):
            edge_rel = edge_data.get('relation') or edge_data.get('type')
            if edge_rel == rel_type:
                target_node = self.graph.nodes[target_id]
                target_name = target_node.get('name', '未知')
                amount = edge_data.get('amount', '')
                targets.append({
                    'name': target_name,
                    'amount': amount,
                    'properties': {k: v for k, v in edge_data.items() if k not in ['relation', 'type']}
                })
        
        is_fuzzy = score < 1.0
        match_mode = 'fuzzy' if is_fuzzy else 'exact'
        
        # 获取关系中文名
        rel_label = rel_type
        for row in self.config.relation_config:
            if row.get('relation_type') == rel_type:
                rel_label = row.get('query_keywords_forward', rel_type).split(';')[0]
                break
        
        result = {
            'success': len(targets) > 0,
            'query_type': 'forward_relation',
            'dish': {
                'original': dish_name,
                'matched': matched_name,
                'similarity': score
            },
            'relation': rel_type,
            'relation_label': rel_label,
            'targets': targets,
            'count': len(targets),
            'match_mode': match_mode,
            'structured': {
                'dish_name': matched_name,
                'relation_type': rel_type,
                'related_entities': targets,
                'count': len(targets),
                'match_mode': match_mode
            }
        }
        
        # 人类可读
        fuzzy_warning = f"⚠️ 【模糊检索结果】未找到精确匹配\"{dish_name}\"，为您找到相似菜品：\"{matched_name}\"\n\n" if is_fuzzy else ""
        
        if not targets:
            human_text = f"{fuzzy_warning}【查询结果】\n菜品：{matched_name}\n{rel_label}：无数据或未记录"
        else:
            human_text = f"{fuzzy_warning}【查询结果】\n菜品：{matched_name}\n{rel_label}：\n"
            for i, t in enumerate(targets, 1):
                if t['amount']:
                    human_text += f"  {i}. {t['name']}（用量：{t['amount']}）\n"
                else:
                    human_text += f"  {i}. {t['name']}\n"
        
        result['human_readable'] = human_text
        return result
    
    def _query_reverse(self, parsed: Dict) -> Dict:
        """反向查询：属性值 -> 菜品列表"""
        rel_type = parsed['target_name']
        value = parsed['reverse_value']
        
        if not rel_type or not value:
            return {
                'success': False,
                'error': '反向查询缺少关系类型或查询值',
                'match_mode': 'none',
                'structured': {},
                'human_readable': '❌ 反向查询参数不完整'
            }
        
        # 确定要搜索的实体类型
        target_label = None
        for row in self.config.relation_config:
            if row.get('relation_type') == rel_type:
                target_label = row.get('target_entity')
                break
        
        if not target_label:
            return {
                'success': False,
                'error': f'未知关系类型: {rel_type}',
                'match_mode': 'none',
                'structured': {},
                'human_readable': f'❌ 未知查询类型: {rel_type}'
            }
        
        # 查找匹配的目标实体
        candidates = self.all_nodes_by_label.get(target_label, {})
        
        # 精确匹配
        matched_targets = []
        if value in candidates:
            matched_targets.append((value, candidates[value], 1.0))
        
        # 如果没有精确匹配，模糊匹配
        if not matched_targets:
            matched_name, score = self.fuzzy_match(value, list(candidates.keys()))
            if matched_name:
                matched_targets.append((matched_name, candidates[matched_name], score))
        
        if not matched_targets:
            return {
                'success': False,
                'error': f'未找到{target_label}: {value}',
                'match_mode': 'none',
                'structured': {},
                'human_readable': f'❌ 未找到"{value}"（类型：{target_label}）。'
            }
        
        # 查找入边（反向）
        dishes = []
        for target_name, target_id, score in matched_targets:
            for source_id, _, edge_data in self.graph.in_edges(target_id, data=True):
                edge_rel = edge_data.get('relation') or edge_data.get('type')
                if edge_rel == rel_type:
                    source_node = self.graph.nodes[source_id]
                    dish_name = source_node.get('name', '未知')
                    amount = edge_data.get('amount', '')
                    dishes.append({
                        'dish_name': dish_name,
                        'matched_value': target_name,
                        'amount': amount,
                        'similarity': score
                    })
        
        is_fuzzy = matched_targets[0][2] < 1.0 if matched_targets else False
        
        result = {
            'success': len(dishes) > 0,
            'query_type': 'reverse',
            'relation': rel_type,
            'search_value': value,
            'matched_value': matched_targets[0][0] if matched_targets else None,
            'dishes': dishes,
            'count': len(dishes),
            'match_mode': 'fuzzy' if is_fuzzy else 'exact',
            'structured': {
                'search_value': value,
                'matched_value': matched_targets[0][0] if matched_targets else None,
                'relation_type': rel_type,
                'dishes': dishes,
                'count': len(dishes),
                'match_mode': 'fuzzy' if is_fuzzy else 'exact'
            }
        }
        
        # 人类可读
        fuzzy_warning = ""
        if is_fuzzy:
            fuzzy_warning = f"⚠️ 【模糊检索结果】未找到精确匹配\"{value}\"，为您找到相似结果：\"{matched_targets[0][0]}\"\n\n"
        
        if not dishes:
            human_text = f"{fuzzy_warning}【查询结果】\n未找到使用\"{matched_targets[0][0]}\"的菜品。"
        else:
            human_text = f"{fuzzy_warning}【查询结果】\n使用 \"{matched_targets[0][0]}\" 的菜品有（共{len(dishes)}道）：\n"
            for i, d in enumerate(dishes, 1):
                if d['amount']:
                    human_text += f"  {i}. {d['dish_name']}（用量：{d['amount']}）\n"
                else:
                    human_text += f"  {i}. {d['dish_name']}\n"
        
        result['human_readable'] = human_text
        return result
    
    def _query_summary(self, parsed: Dict) -> Dict:
        """查询完整档案"""
        dish_name = parsed['dish']
        dish_id, matched_name, score = self._find_dish(dish_name)
        
        if not dish_id:
            return {
                'success': False,
                'error': f'未找到菜品: {dish_name}',
                'match_mode': 'none',
                'structured': {},
                'human_readable': f'❌ 未找到菜品"{dish_name}"。'
            }
        
        node_data = self.graph.nodes[dish_id]
        
        # 收集所有关系
        relations = defaultdict(list)
        for _, target_id, edge_data in self.graph.edges(dish_id, data=True):
            rel_type = edge_data.get('relation') or edge_data.get('type')
            target_node = self.graph.nodes[target_id]
            target_name = target_node.get('name', '未知')
            amount = edge_data.get('amount', '')
            
            rel_label = rel_type
            for row in self.config.relation_config:
                if row.get('relation_type') == rel_type:
                    rel_label = row.get('query_keywords_forward', rel_type).split(';')[0]
                    break
            
            relations[rel_label].append({
                'name': target_name,
                'amount': amount,
                'type': rel_type
            })
        
        is_fuzzy = score < 1.0
        
        # 过滤有效属性
        valid_attrs = {}
        for k, v in node_data.items():
            if k not in ['label', 'name', 'created_at'] and not k.startswith('_'):
                if v and str(v).lower() not in ['nan', 'none', 'null', '']:
                    valid_attrs[k] = v
        
        result = {
            'success': True,
            'query_type': 'summary',
            'dish': {
                'original': dish_name,
                'matched': matched_name,
                'similarity': score
            },
            'attributes': valid_attrs,
            'relations': dict(relations),
            'match_mode': 'fuzzy' if is_fuzzy else 'exact',
            'structured': {
                'dish_name': matched_name,
                'attributes': valid_attrs,
                'relations': dict(relations),
                'match_mode': 'fuzzy' if is_fuzzy else 'exact'
            }
        }
        
        # 人类可读格式化
        fuzzy_warning = f"⚠️ 【模糊检索结果】未找到精确匹配\"{dish_name}\"，为您找到相似菜品：\"{matched_name}\"\n\n" if is_fuzzy else ""
        
        human_text = f"{fuzzy_warning}【{matched_name} 完整档案】\n"
        human_text += f"{'='*50}\n"
        
        # 属性部分
        human_text += "\n【基本信息】\n"
        for attr, val in valid_attrs.items():
            if attr in ['prep_process', 'cooking_process', 'cooking_tips']:
                human_text += f"\n【{attr}】\n{val}\n"
            else:
                human_text += f"{attr}: {val}\n"
        
        # 关系部分
        if relations:
            human_text += f"\n{'='*50}\n【关联信息】\n"
            for rel_label, items in relations.items():
                human_text += f"\n{rel_label}：\n"
                for item in items:
                    if item['amount']:
                        human_text += f"  • {item['name']}（{item['amount']}）\n"
                    else:
                        human_text += f"  • {item['name']}\n"
        
        result['human_readable'] = human_text
        return result


class RecipeQuerySystem:
    """菜谱查询系统主控"""
    
    def __init__(self, kg_path: str = DEFAULT_KG_PATH):
        self.kg_path = kg_path
        self.config = ConfigLoader(
            ENTITY_CONFIG_PATH,
            RELATION_CONFIG_PATH,
            ATTRIBUTE_CONFIG_PATH,
            KEYWORD_CONFIG_PATH
        )
        self.graph = self._load_graph()
        self.parser = QueryParser(self.config)
        self.executor = QueryExecutor(self.graph, self.config)
    
    def _load_graph(self):
        """加载知识图谱"""
        if not os.path.exists(self.kg_path):
            print(f"❌ 知识图谱文件不存在: {self.kg_path}")
            print(f"   请检查路径或先构建图谱")
            sys.exit(1)
        
        with open(self.kg_path, 'rb') as f:
            graph = pickle.load(f)
        
        print(f"📂 成功加载知识图谱: {self.kg_path}")
        print(f"   节点数: {len(graph)} | 关系数: {len(graph.edges)}")
        return graph
    
    def query(self, query_str: str) -> Dict:
        """执行查询"""
        print(f"\n🔍 查询: {query_str}")
        print("-" * 50)
        
        parsed = self.parser.parse(query_str)
        result = self.executor.execute(parsed)
        
        # 输出结果
        self._print_result(result)
        return result
    
    def _print_result(self, result: Dict):
        """打印结果（双格式）"""
        print("\n📊 【结构化数据】")
        print(json.dumps(result.get('structured', {}), ensure_ascii=False, indent=2))
        
        print("\n📝 【查询结果】")
        print(result.get('human_readable', '无结果'))
        print("-" * 50)
    
    def interactive(self):
        """交互模式"""
        print("\n🍳 菜谱知识图谱查询系统 - 交互模式")
        print("支持查询示例：")
        print('  自然语言: "小炒黄牛肉的备菜过程"')
        print('  自然语言: "小炒黄牛肉的配料有哪些"')
        print('  自然语言: "哪些菜用了炝炒技法"')
        print('  自然语言: "包含黄牛肉的菜式"')
        print('  Cypher:   MATCH (d:Dish {name:"小炒黄牛肉"}) RETURN d.prep_process')
        print('  Cypher:   MATCH (d:Dish)-[:USES_TECHNIQUE]->(t:Technique {name:"炝炒"}) RETURN d.name')
        print('  输入 help_cypher 查看完整Cypher语法说明')
        print('  输入 exit 退出\n')

        while True:
            try:
                query = input("> ").strip()
                if query.lower() in ['exit', 'quit', '退出', 'q']:
                    print("再见！")
                    break
                if query.lower() in ['help_cypher', 'cypher_help', 'cypher']:
                    print(CypherParser.get_supported_syntax())
                    continue
                if query:
                    self.query(query)
            except KeyboardInterrupt:
                print("\n再见！")
                break
            except Exception as e:
                print(f"❌ 查询出错: {e}")


def main():
    parser = argparse.ArgumentParser(
        description='菜谱知识图谱查询系统 V1.1',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 自然语言查询
  python 4-V1菜谱查询recipe_query-查询火力.py "小炒黄牛肉的备菜过程"
  python 4-V1菜谱查询recipe_query-查询火力.py "小炒黄牛肉的配料是什么"
  python 4-V1菜谱查询recipe_query-查询火力.py "小炒黄牛肉的cuisine"
  python 4-V1菜谱查询recipe_query-查询火力.py "哪些菜用了炝炒技法"
  python 4-V1菜谱查询recipe_query-查询火力.py "主要食材包含黄牛肉的菜"
  python 4-V1菜谱查询recipe_query-查询火力.py "小炒黄牛肉的火力调节过程"  

  # Cypher查询
  python 4-V1菜谱查询recipe_query-查询火力.py "MATCH (d:Dish {name:'小炒黄牛肉'}) RETURN d.prep_process"
  python 4-V1菜谱查询recipe_query-查询火力.py "MATCH (d:Dish {name:'小炒黄牛肉'})-[:USES_TECHNIQUE]->(t) RETURN t.name"
  python 4-V1菜谱查询recipe_query-查询火力.py "MATCH (d:Dish)-[:USES_TECHNIQUE]->(t:Technique {name:'炝炒'}) RETURN d.name"
  python 4-V1菜谱查询recipe_query-查询火力.py "MATCH (d:Dish {name:'小炒黄牛肉'}) RETURN d"

  # 参数式查询
  python 4-V1菜谱查询recipe_query-查询火力.py -d "小炒黄牛肉" -f "备菜过程"
  python 4-V1菜谱查询recipe_query-查询火力.py -d "小炒黄牛肉" -r "USES_MAIN_INGREDIENT"
  python 4-V1菜谱查询recipe_query-查询火力.py --reverse --technique "炝炒"
  python 4-V1菜谱查询recipe_query-查询火力.py --reverse --ingredient "牛肉"

  # 交互模式
  python 4-V1菜谱查询recipe_query-查询火力.py -i

  # Cypher语法帮助
  python 4-V1菜谱查询recipe_query-查询火力.py -c
        """

    )
    
    parser.add_argument('query', nargs='?', help='自然语言或Cypher查询语句')
    parser.add_argument('-i', '--interactive', action='store_true', help='交互模式')
    parser.add_argument('-c', '--cypher-help', action='store_true', help='显示Cypher语法帮助')
    parser.add_argument('-d', '--dish', help='指定菜名（正向查询）')
    parser.add_argument('-f', '--field', help='查询字段/属性名')
    parser.add_argument('-r', '--relation', dest='relation_type', help='查询关系类型')
    parser.add_argument('--reverse', action='store_true', help='反向查询模式')
    parser.add_argument('--technique', help='按技法反向查询')
    parser.add_argument('--ingredient', help='按食材反向查询')
    parser.add_argument('--taste', help='按味道反向查询')
    parser.add_argument('--cuisine', help='按菜系反向查询')
    parser.add_argument('--value', help='反向查询的值')
    parser.add_argument('-k', '--kg-path', default=DEFAULT_KG_PATH, help=f'知识图谱路径 (默认: {DEFAULT_KG_PATH})')
    args = parser.parse_args()

    # 显示Cypher帮助
    if args.cypher_help:
        print(CypherParser.get_supported_syntax())
        sys.exit(0)

    # 初始化系统
    system = RecipeQuerySystem(args.kg_path)

    if args.interactive:
        system.interactive()
    elif args.query:
        system.query(args.query)
    elif args.dish or args.reverse or args.technique or args.ingredient or args.taste or args.cuisine:
        # 命令行参数模式
        parsed = system.parser.parse_args(args)
        result = system.executor.execute(parsed)
        system._print_result(result)
    else:
        parser.print_help()
if __name__ == '__main__':
    main()
