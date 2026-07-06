"""菜谱查询测试数据集 — 供 run_recall_test.py 使用。

共 100 条用例，分 8 个维度，按 id 范围划分阶段：
  第一阶段（core）：id=1-40 + 71-84 = 55 条
  第二阶段（ext）：id=41-70 + 85-100 = 45 条
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
    dict(id=8,  input="锅包肉的做法步骤",        category="正向-属性", phase=1, query_type="forward_attr", expected=["锅包肉"], strict_ok=True, note="做法"),
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
    dict(id=25, input="锅包肉是什么菜",          category="正向-档案", phase=1, query_type="forward_summary", expected=["锅包肉"], strict_ok=True, note="介绍"),
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
    dict(id=40, input="有什么适合冬天吃的菜",    category="反向-场景", phase=1, query_type="reverse", expected=[], strict_ok=False, note="弱查询，宽松匹配即可"),

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
    dict(id=55, input="丝瓜炒什么好",            category="模糊-口语", phase=2, query_type="forward_summary", expected=["丝瓜牛肉", "清炒丝瓜"], strict_ok=False, note="开放推荐"),

    # ═══════════════════════════════════════════
    # 五、场景化对话 (56-70) — 第二阶段
    # ═══════════════════════════════════════════
    dict(id=56, input="我想做个香辣的菜，有什么推荐", category="场景-口味", phase=2, query_type="reverse", expected=["小炒黄牛肉", "干锅肥肠", "香辣牛蛙"], strict_ok=True, note="按口味推荐"),
    dict(id=57, input="今天买了牛肉，不知道怎么做",   category="场景-食材", phase=2, query_type="forward_summary", expected=["小炒黄牛肉", "黑椒牛柳", "丝瓜牛肉"], strict_ok=False, note="食材推荐"),
    dict(id=58, input="家里有莲藕、荷兰豆、胡萝卜，能做啥", category="场景-组合", phase=2, query_type="forward_summary", expected=["荷塘月色"], strict_ok=False, note="多食材匹配"),
    dict(id=59, input="有没有不用油炸的菜",      category="场景-健康", phase=2, query_type="reverse", expected=["清蒸鲈鱼", "白灼菜心", "清炒丝瓜"], strict_ok=False, note="技法排除"),
    dict(id=60, input="推荐几个快手菜，10分钟搞定的", category="场景-时间", phase=2, query_type="reverse", expected=[], strict_ok=False, note="时间维度"),
    dict(id=61, input="来点清淡的菜吧，最近上火", category="场景-口味", phase=2, query_type="reverse", expected=["清炒丝瓜", "清蒸鲈鱼", "白灼菜心"], strict_ok=False, note="清淡"),
    dict(id=62, input="年夜饭做什么菜好",        category="场景-节日", phase=2, query_type="reverse", expected=[], strict_ok=False, note="开放推荐"),
    dict(id=63, input="有没有适合带便当的菜",    category="场景-便当", phase=2, query_type="reverse", expected=[], strict_ok=False, note="开放推荐"),
    dict(id=64, input="孩子不爱吃蔬菜，有什么推荐", category="场景-儿童", phase=2, query_type="reverse", expected=[], strict_ok=False, note="蔬菜推荐"),
    dict(id=65, input="减脂期吃什么肉好",        category="场景-减脂", phase=2, query_type="reverse", expected=["西蓝花炒鸡胸肉", "彩椒炒鸡丁"], strict_ok=False, note="减脂"),
    dict(id=66, input="能帮我设计个三菜一汤的菜单吗", category="场景-组合", phase=2, query_type="reverse", expected=[], strict_ok=False, note="菜单设计"),
    dict(id=67, input="朋友来家里吃饭，6个人，推荐什么菜", category="场景-宴客", phase=2, query_type="reverse", expected=[], strict_ok=False, note="宴客"),
    dict(id=68, input="有什么适合宿舍小电锅做的菜", category="场景-厨具", phase=2, query_type="reverse", expected=[], strict_ok=False, note="厨具限制"),
    dict(id=69, input="冰箱里只剩鸡蛋和番茄了，能做什么", category="场景-剩菜", phase=2, query_type="forward_summary", expected=["番茄炒蛋"], strict_ok=False, note="食材组合"),
    dict(id=70, input="我想学做菜，从什么开始比较好", category="场景-新手", phase=2, query_type="reverse", expected=[], strict_ok=False, note="新手推荐"),

    # ═══════════════════════════════════════════
    # 六、边界/否定 (71-84) — 第一阶段
    # ═══════════════════════════════════════════
    dict(id=71, input="满汉全席怎么做",          category="边界-不存在", phase=1, query_type="forward_attr", expected=[], strict_ok=False, note="图谱不存在"),
    dict(id=72, input="佛跳墙的做法",            category="边界-不存在", phase=1, query_type="forward_attr", expected=[], strict_ok=False, note="图谱不存在"),
    dict(id=73, input="红烧排骨怎么做",          category="边界-不存在", phase=1, query_type="forward_attr", expected=[], strict_ok=False, note="图谱没有（有玉米排骨汤）"),
    dict(id=74, input="麻婆豆腐",                category="边界-不存在", phase=1, query_type="forward_summary", expected=[], strict_ok=False, note="图谱无此菜"),
    dict(id=75, input="水煮鱼怎么做",            category="边界-不存在", phase=1, query_type="forward_attr", expected=[], strict_ok=False, note="图谱无此菜"),
    dict(id=76, input="空气炸锅能做哪些菜",      category="边界-无此维度", phase=1, query_type="reverse", expected=[], strict_ok=False, note="图谱无此维度"),
    dict(id=77, input="",                        category="边界-空输入", phase=1, query_type="none", expected=[], strict_ok=False, note="空输入，期望友好提示"),
    dict(id=78, input="你好",                    category="边界-非菜谱", phase=1, query_type="none", expected=[], strict_ok=False, note="闲聊不触发"),
    dict(id=79, input="今天天气怎么样",          category="边界-非菜谱", phase=1, query_type="none", expected=[], strict_ok=False, note="天气问题"),
    dict(id=80, input="你是什么模型",            category="边界-非菜谱", phase=1, query_type="none", expected=[], strict_ok=False, note="身份问题"),
    dict(id=81, input="用番茄、鸡蛋、牛肉能做几道菜", category="边界-多食材", phase=1, query_type="forward_summary", expected=["番茄炒蛋"], strict_ok=False, note="多食材交叉"),
    dict(id=82, input="哪些菜又辣又酸",          category="反向-多重", phase=1, query_type="reverse", expected=["酸辣味"], strict_ok=False, note="多条件"),
    dict(id=83, input="不用辣椒的菜有哪些",      category="反向-排除", phase=1, query_type="reverse", expected=[], strict_ok=False, note="排除逻辑"),
    dict(id=84, input="介绍一下你们的知识图谱里有什么菜", category="边界-元问题", phase=1, query_type="reverse", expected=[], strict_ok=False, note="元问题"),

    # ═══════════════════════════════════════════
    # 七、特色数据专项 (85-92) — 第二阶段
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
    # 八、技法/菜系/食材交叉 (93-100) — 第二阶段
    # ═══════════════════════════════════════════
    dict(id=93, input="湘菜里有什么爆炒的菜",    category="交叉", phase=2, query_type="reverse", expected=["小炒黄牛肉"], strict_ok=True, note="菜系+技法"),
    dict(id=94, input="粤菜里有哪些蒸菜",        category="交叉", phase=2, query_type="reverse", expected=["蒜蓉粉丝虾", "清蒸鲈鱼"], strict_ok=True, note="菜系+技法"),
    dict(id=95, input="川菜中哪些菜是香辣味的",  category="交叉", phase=2, query_type="reverse", expected=["干锅肥肠", "香辣牛蛙"], strict_ok=True, note="菜系+味道"),
    dict(id=96, input="用牛肉做的家常菜推荐",    category="交叉", phase=2, query_type="reverse", expected=["小炒黄牛肉", "黑椒牛柳"], strict_ok=True, note="食材+菜系"),
    dict(id=97, input="有虾的菜有哪些",          category="交叉", phase=2, query_type="reverse", expected=["蒜蓉粉丝虾", "避风塘炒虾"], strict_ok=True, note="食材"),
    dict(id=98, input="用鸡蛋做的菜",            category="交叉", phase=2, query_type="reverse", expected=["番茄炒蛋", "韭菜炒鸡蛋", "西葫芦炒鸡蛋"], strict_ok=True, note="食材"),
    dict(id=99, input="不放油能做的菜",          category="交叉", phase=2, query_type="reverse", expected=[], strict_ok=False, note="排除技法"),
    dict(id=100, input="适合夏天吃的清淡菜",     category="交叉", phase=2, query_type="reverse", expected=["清炒丝瓜", "白灼菜心", "清蒸鲈鱼"], strict_ok=False, note="场景+口味"),
]
