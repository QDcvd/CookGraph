"""菜谱查询测试数据集 — 供 run_recall_test.py 使用。

共 100 条用例，分 8 个维度：
  第一阶段（core）：50 条
  第二阶段（ext）：50 条
"""

from typing import Any

# 每个测试用例的字段说明：
#   id:            编号
#   input:         用户输入文本
#   category:      所属维度
#   phase:         1=第一阶段核心, 2=第二阶段扩展
#   query_type:    期望的 query_type（forward_attr/forward_rel/forward_summary/reverse）
#   expected:      期望命中的菜名（列表，任一匹配即可）
#   strict_ok:     是否期望严格命中（success=True）
#   eval_type:     可选。positive=菜名严格命中；negative=负样本/边界查询；web_fallback=图谱未命中后联网兜底
#   note:          备注/说明

TEST_CASES: list[dict[str, Any]] = [
    # ═══════════════════════════════════════════
    # 一、正向查询 — 精确菜名 + 属性 (1-20)
    # ═══════════════════════════════════════════
    dict(id=1,  input="小炒黄牛肉怎么做",        category="正向-属性", phase=1, query_type="forward_attr", expected=["小炒黄牛肉"], strict_ok=True, note="做法查询"),
    dict(id=2,  input="小炒黄牛肉的做法",        category="正向-属性", phase=1, query_type="forward_attr", expected=["小炒黄牛肉"], strict_ok=True, note="做法同义"),
    dict(id=3,  input="西红柿炒鸡蛋的烹饪方法",   category="正向-属性", phase=1, query_type="forward_attr", expected=["番茄炒蛋"], strict_ok=True, note="别名"),
    dict(id=4,  input="清蒸鲈鱼怎么做",          category="正向-属性", phase=1, query_type="forward_attr", expected=["清蒸鲈鱼"], strict_ok=True, note="做法"),
    dict(id=5,  input="糖醋里脊的做法步骤",      category="正向-属性", phase=1, query_type="forward_attr", expected=["糖醋里脊"], strict_ok=True, note="做法"),
    dict(id=6,  input="鱼香肉丝的家常做法",      category="正向-属性", phase=1, query_type="forward_attr", expected=["鱼香肉丝"], strict_ok=True, note="家常做法"),
    dict(id=7,  input="可乐鸡翅怎么做好吃",      category="正向-属性", phase=1, query_type="forward_attr", expected=["可乐鸡翅"], strict_ok=True, note="好吃"),
    dict(id=8,  input="锅包肉的做法步骤",        category="联网兜底", phase=1, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱无此菜，期望联网搜索"),
    dict(id=9,  input="小炒黄牛肉的配料",        category="正向-属性", phase=1, query_type="forward_rel",  expected=["小炒黄牛肉"], strict_ok=True, note="配料查询"),
    dict(id=10, input="小炒黄牛肉用什么调料",    category="正向-属性", phase=1, query_type="forward_rel",  expected=["小炒黄牛肉"], strict_ok=True, note="调料查询"),
    dict(id=11, input="蒜蓉粉丝虾需要什么材料",  category="正向-属性", phase=1, query_type="forward_summary", expected=["蒜蓉粉丝虾"], strict_ok=True, note="材料查询"),
    dict(id=12, input="干锅肥肠的烹饪方法",      category="正向-属性", phase=1, query_type="forward_attr", expected=["干锅肥肠"], strict_ok=True, note="做法"),
    dict(id=13, input="小炒黄牛肉的火力调节过程", category="正向-属性", phase=1, query_type="forward_attr", expected=["小炒黄牛肉"], strict_ok=True, note="火力特色列"),
    dict(id=14, input="小炒黄牛肉的备菜过程",    category="正向-属性", phase=1, query_type="forward_attr", expected=["小炒黄牛肉"], strict_ok=True, note="备菜特色列"),
    dict(id=15, input="清蒸鲈鱼的火候怎么控制",  category="正向-属性", phase=1, query_type="forward_attr", expected=["清蒸鲈鱼"], strict_ok=True, note="火候"),
    dict(id=16, input="糖醋里脊的调味汁怎么配",  category="正向-属性", phase=1, query_type="forward_attr", expected=["糖醋里脊"], strict_ok=True, note="调味"),
    dict(id=17, input="番茄炒蛋先炒蛋还是先炒番茄", category="正向-属性", phase=1, query_type="forward_attr", expected=["番茄炒蛋"], strict_ok=True, note="顺序问题"),
    dict(id=18, input="可乐鸡翅需要焯水吗",      category="正向-属性", phase=1, query_type="forward_attr", expected=["可乐鸡翅"], strict_ok=True, note="小提示"),
    dict(id=19, input="手撕包菜怎么做才脆",      category="正向-属性", phase=1, query_type="forward_attr", expected=["手撕包菜"], strict_ok=True, note="口感"),
    dict(id=20, input="清蒸鲈鱼蒸几分钟",        category="正向-属性", phase=1, query_type="forward_attr", expected=["清蒸鲈鱼"], strict_ok=True, note="时间"),

    # ═══════════════════════════════════════════
    # 二、正向查询 — 完整档案 (21-26)
    # ═══════════════════════════════════════════
    dict(id=21, input="给我讲讲小炒黄牛肉",      category="正向-档案", phase=1, query_type="forward_summary", expected=["小炒黄牛肉"], strict_ok=True, note="完整档案"),
    dict(id=22, input="介绍一下鱼香肉丝",        category="正向-档案", phase=1, query_type="forward_summary", expected=["鱼香肉丝"], strict_ok=True, note="完整档案"),
    dict(id=23, input="小炒黄牛肉",              category="正向-档案", phase=1, query_type="forward_summary", expected=["小炒黄牛肉"], strict_ok=True, note="纯菜名"),
    dict(id=24, input="我想学做糖醋里脊",        category="正向-档案", phase=1, query_type="forward_summary", expected=["糖醋里脊"], strict_ok=True, note="学习意图"),
    dict(id=25, input="锅包肉是什么菜",          category="联网兜底", phase=1, query_type="forward_summary", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱无此菜，期望联网搜索"),
    dict(id=26, input="可乐鸡翅是哪个菜系的",    category="正向-属性", phase=1, query_type="forward_attr", expected=["可乐鸡翅"], strict_ok=True, note="菜系属性"),

    # ═══════════════════════════════════════════
    # 三、反向查询 (27-40)
    # ═══════════════════════════════════════════
    dict(id=27, input="哪些菜用了爆炒技法",      category="反向-技法", phase=1, query_type="reverse", expected=["小炒黄牛肉", "香辣牛蛙", "姜葱炒鱿鱼"], strict_ok=True, note="技法反向"),
    dict(id=28, input="哪些菜用了炝炒技法",      category="反向-技法", phase=1, query_type="reverse", expected=["炝炒藕片"], strict_ok=True, note="炝炒"),
    dict(id=29, input="有哪些菜是蒸制的",        category="反向-技法", phase=1, query_type="reverse", expected=["蒜蓉粉丝虾", "清蒸鲈鱼"], strict_ok=True, note="蒸制"),
    dict(id=30, input="哪些菜是香辣味的",        category="反向-味道", phase=1, query_type="reverse", expected=["小炒黄牛肉", "干锅肥肠", "香辣牛蛙"], strict_ok=True, note="香辣"),
    dict(id=31, input="有什么酸甜味的菜",        category="反向-味道", phase=1, query_type="reverse", expected=["糖醋里脊"], strict_ok=True, note="酸甜"),
    dict(id=32, input="哪些菜属于湘菜",          category="反向-菜系", phase=1, query_type="reverse", expected=["小炒黄牛肉"], strict_ok=True, note="湘菜"),
    dict(id=33, input="有哪些粤菜推荐",          category="反向-菜系", phase=1, query_type="reverse", expected=["蒜蓉粉丝虾", "白灼菜心", "清蒸鲈鱼"], strict_ok=True, note="粤菜"),
    dict(id=34, input="用了黄牛肉的菜有哪些",    category="反向-食材", phase=1, query_type="reverse", expected=["小炒黄牛肉"], strict_ok=True, note="牛肉"),
    dict(id=35, input="哪些菜用了牛肉",          category="反向-食材", phase=1, query_type="reverse", expected=["小炒黄牛肉", "黑椒牛柳", "丝瓜牛肉"], strict_ok=True, note="牛肉泛称"),
    dict(id=36, input="哪些菜主要食材是鸡胸肉",  category="反向-食材", phase=1, query_type="reverse", expected=["彩椒炒鸡丁", "西蓝花炒鸡胸肉"], strict_ok=True, note="鸡胸肉"),
    dict(id=37, input="哪些菜用了蒜蓉这种做法",  category="反向-技法", phase=1, query_type="reverse", expected=["蒜蓉粉丝虾", "蒜蓉西兰花", "蒜蓉生菜"], strict_ok=True, note="蒜蓉"),
    dict(id=38, input="有什么川菜推荐",          category="反向-菜系", phase=1, query_type="reverse", expected=["干锅肥肠", "香辣牛蛙", "鱼香肉丝"], strict_ok=True, note="川菜"),
    dict(id=39, input="哪些菜用了莲藕",          category="反向-食材", phase=1, query_type="reverse", expected=["荷塘月色", "香辣藕丁", "炝炒藕片"], strict_ok=True, note="莲藕"),

    # ═══════════════════════════════════════════
    # 四、模糊/口语化 (41-55) — 第二阶段
    # ═══════════════════════════════════════════
    dict(id=41, input="番茄炒鸡蛋咋做",          category="模糊-口语", phase=2, query_type="forward_attr", expected=["番茄炒蛋"], strict_ok=True, note="同义表达"),
    dict(id=42, input="西红柿炒鸡蛋",            category="模糊-口语", phase=2, query_type="forward_summary", expected=["番茄炒蛋"], strict_ok=True, note="食材别名"),
    dict(id=43, input="番茄蛋怎么做",            category="模糊-口语", phase=2, query_type="forward_attr", expected=["番茄炒蛋"], strict_ok=True, note="缩写"),
    dict(id=44, input="韭菜蛋",                  category="模糊-口语", phase=2, query_type="forward_summary", expected=["韭菜炒鸡蛋"], strict_ok=True, note="缩写"),
    dict(id=45, input="辣椒炒肉的做法",          category="模糊-口语", phase=2, query_type="forward_attr", expected=["辣椒炒肉"], strict_ok=True, note="精确"),
    dict(id=46, input="青椒肉丝家常做法",        category="模糊-口语", phase=2, query_type="forward_attr", expected=["青椒肉丝"], strict_ok=True, note="精确"),
    dict(id=47, input="牛肉炒河粉怎么做",        category="模糊-口语", phase=2, query_type="forward_attr", expected=["干炒牛河"], strict_ok=True, note="同义"),
    dict(id=48, input="蒜蓉虾的做法",            category="模糊-口语", phase=2, query_type="forward_attr", expected=["蒜蓉粉丝虾"], strict_ok=True, note="缺词"),
    dict(id=49, input="花甲怎么做",              category="模糊-口语", phase=2, query_type="forward_attr", expected=["爆炒花甲", "葱香花蛤"], strict_ok=True, note="泛称"),
    dict(id=50, input="肥牛怎么做",              category="模糊-口语", phase=2, query_type="forward_attr", expected=["洋葱炒肥牛", "番茄金针菇肥牛"], strict_ok=True, note="泛称"),
    dict(id=51, input="莲藕怎么做才好吃",        category="模糊-口语", phase=2, query_type="forward_attr", expected=["香辣藕丁", "炝炒藕片"], strict_ok=True, note="泛称"),
    dict(id=52, input="包菜怎么炒",              category="模糊-口语", phase=2, query_type="forward_attr", expected=["手撕包菜"], strict_ok=True, note="口语"),
    dict(id=53, input="黄牛肉怎么做",            category="模糊-口语", phase=2, query_type="forward_attr", expected=["小炒黄牛肉"], strict_ok=True, note="泛称"),
    dict(id=54, input="金针菇肥牛",              category="模糊-口语", phase=2, query_type="forward_summary", expected=["番茄金针菇肥牛"], strict_ok=True, note="缺词"),
    # ═══════════════════════════════════════════
    # 五、边界/否定 (71-84) — 第一阶段
    # ═══════════════════════════════════════════
    dict(id=71, input="满汉全席怎么做",          category="联网兜底", phase=1, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱无此菜，期望联网搜索"),
    dict(id=72, input="佛跳墙的做法",            category="联网兜底", phase=1, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱无此菜，期望联网搜索"),
    dict(id=73, input="红烧排骨怎么做",          category="联网兜底", phase=1, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱无此菜，期望联网搜索"),
    dict(id=74, input="麻婆豆腐",                category="联网兜底", phase=1, query_type="forward_summary", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱无此菜，期望联网搜索"),
    dict(id=75, input="水煮鱼怎么做",            category="联网兜底", phase=1, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱无此菜，期望联网搜索"),
    dict(id=76, input="空气炸锅能做哪些菜",      category="边界-无此维度", phase=1, query_type="reverse", expected=[], strict_ok=False, eval_type="negative", note="图谱无此维度"),
    dict(id=77, input="",                        category="边界-空输入", phase=1, query_type="none", expected=[], strict_ok=False, eval_type="negative", note="空输入，期望友好提示"),
    dict(id=78, input="你好",                    category="边界-非菜谱", phase=1, query_type="none", expected=[], strict_ok=False, eval_type="negative", note="闲聊不触发"),
    dict(id=79, input="今天天气怎么样",          category="边界-非菜谱", phase=1, query_type="none", expected=[], strict_ok=False, eval_type="negative", note="天气问题"),
    dict(id=80, input="你是什么模型",            category="边界-非菜谱", phase=1, query_type="none", expected=[], strict_ok=False, eval_type="negative", note="身份问题"),
    dict(id=84, input="介绍一下你们的知识图谱里有什么菜", category="边界-元问题", phase=1, query_type="reverse", expected=[], strict_ok=False, eval_type="negative", note="元问题"),

    # ═══════════════════════════════════════════
    # 六、特色数据专项 (85-92) — 第二阶段
    # ═══════════════════════════════════════════
    dict(id=85, input="小炒黄牛肉的火候",        category="特色-火力", phase=2, query_type="forward_attr", expected=["小炒黄牛肉"], strict_ok=True, note="火力"),
    dict(id=86, input="炒牛肉怎么控制火候",      category="特色-火力", phase=2, query_type="forward_attr", expected=["小炒黄牛肉"], strict_ok=True, note="火力泛称"),
    dict(id=87, input="清蒸鲈鱼的火力调节",      category="特色-火力", phase=2, query_type="forward_attr", expected=["清蒸鲈鱼"], strict_ok=True, note="火力"),
    dict(id=88, input="鱼香肉丝的下锅顺序",      category="特色-下锅", phase=2, query_type="forward_attr", expected=["鱼香肉丝"], strict_ok=True, note="下锅顺序"),
    dict(id=89, input="小炒黄牛肉的备菜",        category="特色-备菜", phase=2, query_type="forward_attr", expected=["小炒黄牛肉"], strict_ok=True, note="备菜"),
    dict(id=90, input="糖醋里脊备菜要多久",      category="特色-备菜", phase=2, query_type="forward_attr", expected=["糖醋里脊"], strict_ok=True, note="备菜时间"),
    dict(id=91, input="干锅肥肠的下锅步骤",      category="特色-下锅", phase=2, query_type="forward_attr", expected=["干锅肥肠"], strict_ok=True, note="下锅步骤"),
    dict(id=92, input="蒜蓉粉丝虾要提前准备什么", category="特色-备菜", phase=2, query_type="forward_attr", expected=["蒜蓉粉丝虾"], strict_ok=True, note="提前准备"),

    # ═══════════════════════════════════════════
    # 七、技法/菜系/食材交叉 (93-100) — 第二阶段
    # ═══════════════════════════════════════════
    dict(id=93, input="湘菜里有什么爆炒的菜",    category="交叉", phase=2, query_type="reverse", expected=["小炒黄牛肉"], strict_ok=True, note="菜系+技法"),
    dict(id=94, input="粤菜里有哪些蒸菜",        category="交叉", phase=2, query_type="reverse", expected=["蒜蓉粉丝虾", "清蒸鲈鱼"], strict_ok=True, note="菜系+技法"),
    dict(id=95, input="川菜中哪些菜是香辣味的",  category="交叉", phase=2, query_type="reverse", expected=["干锅肥肠", "香辣牛蛙"], strict_ok=True, note="菜系+味道"),
    dict(id=96, input="用牛肉做的家常菜推荐",    category="交叉", phase=2, query_type="reverse", expected=["小炒黄牛肉", "黑椒牛柳"], strict_ok=True, note="食材+菜系"),
    dict(id=97, input="有虾的菜有哪些",          category="交叉", phase=2, query_type="reverse", expected=["蒜蓉粉丝虾", "避风塘炒虾"], strict_ok=True, note="食材"),
    dict(id=98, input="用鸡蛋做的菜",            category="交叉", phase=2, query_type="reverse", expected=["番茄炒蛋", "韭菜炒鸡蛋", "西葫芦炒鸡蛋"], strict_ok=True, note="食材"),

    # ═══════════════════════════════════════════
    # 八、图谱未命中联网兜底 (99-120) — 第二阶段
    # ═══════════════════════════════════════════
    dict(id=99,  input="北京烤鸭怎么做",          category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=100, input="东坡肉怎么做",            category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=101, input="回锅肉怎么做",            category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=102, input="酸菜鱼怎么做",            category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=103, input="梅菜扣肉怎么做",          category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=104, input="盐焗鸡怎么做",            category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=105, input="白切鸡怎么做",            category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=106, input="担担面怎么做",            category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=107, input="热干面怎么做",            category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=108, input="肉夹馍怎么做",            category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=109, input="沙县拌面怎么做",          category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=110, input="蚂蚁上树怎么做",          category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=111, input="拍黄瓜怎么做",            category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=112, input="凉拌木耳怎么做",          category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=113, input="口水鸡怎么做",            category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=114, input="烤冷面怎么做",            category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=115, input="新疆大盘鸡怎么做",        category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=116, input="赛螃蟹怎么做",            category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=117, input="毛血旺怎么做",            category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=118, input="白灼虾怎么做",            category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=119, input="羊肉泡馍怎么做",          category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
    dict(id=120, input="小笼包怎么做",            category="联网兜底", phase=2, query_type="forward_attr", expected=[], strict_ok=False, eval_type="web_fallback", note="图谱未命中，期望联网搜索"),
]
