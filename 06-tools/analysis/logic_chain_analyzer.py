#!/usr/bin/env python3
"""
逻辑链矛盾分析器 V1
检测逻辑链中的定价矛盾，包括：
1. 蕴含关系违反 (A implies B, but Price(A) > Price(B))
2. 互斥关系违反 (A mutex B, but Price(A) + Price(B) > 1)
3. 传递性违反 (A implies B, B implies C, but Price(A) > Price(C))

注意: 此模块主要使用预定义逻辑，部分功能需要 LLM
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass
from collections import defaultdict

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "dashboard/backend/app/models"))

try:
    from database import db
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "dashboard/backend"))
    from app.models.database import db


@dataclass
class LogicNode:
    """逻辑节点"""
    market_id: str
    market_title: str
    yes_price: float
    no_price: float


@dataclass
class LogicEdge:
    """逻辑边（关系）"""
    from_node: str
    to_node: str
    relation_type: str  # 'implies', 'mutex'
    confidence: float


@dataclass
class LogicViolation:
    """逻辑违反"""
    violation_type: str  # 'implies_violation', 'mutex_violation', 'transitivity_violation'
    nodes: List[str]
    expected: str
    actual: str
    severity: float  # 0-1
    profit_potential: float
    description: str


class LogicChainAnalyzer:
    """
    逻辑链矛盾分析器
    
    功能:
    1. 构建逻辑图（节点=市场，边=逻辑关系）
    2. 检测图中的定价矛盾
    3. 生成套利信号
    """
    
    # 预定义的逻辑关系库
    # 格式: conclusion -> [premises]
    IMPLICATION_RULES = {
        # 体育
        "Chiefs": ["AFC"],
        "Eagles": ["NFC"],
        "49ers": ["NFC"],
        "Ravens": ["AFC"],
        "Lakers": ["Western Conference"],
        "Celtics": ["Eastern Conference"],
        "Warriors": ["Western Conference"],
        "Bucks": ["Eastern Conference"],
        
        # 政治
        "Trump": ["Republican", "Male"],
        "Biden": ["Democrat", "Male"],
        "Haley": ["Republican", "Female"],
        "Newsom": ["Democrat", "Male"],
        
        # 地缘政治
        "Iran regime falls": ["Middle East instability"],
        "Russia regime change": ["Eurasia instability"],
        "China invades Taiwan": ["Asia Pacific conflict"],
    }
    
    # 互斥关系（两者不能同时发生）
    MUTEX_RULES = [
        # 政治互斥
        ["Trump", "Biden"],
        ["Trump", "Haley"],
        ["Biden", "Newsom"],
        ["Republican", "Democrat"],
        
        # 体育互斥
        ["Chiefs", "Eagles"],
        ["Chiefs", "49ers"],
        ["Ravens", "49ers"],
        ["Ravens", "Eagles"],
        ["AFC", "NFC"],
        ["Lakers", "Celtics"],
        
        # 地缘互斥（简化）
        ["Iran regime falls", "Iran regime stable"],
    ]
    
    def __init__(self):
        self.db = db
        self.nodes: Dict[str, LogicNode] = {}
        self.edges: List[LogicEdge] = []
        self.implication_graph: Dict[str, Set[str]] = defaultdict(set)
        self.mutex_pairs: Set[Tuple[str, str]] = set()
    
    def build_logic_graph(self, markets: List[Dict]) -> None:
        """
        构建逻辑图
        
        Args:
            markets: 市场列表，每个市场包含 id, title, yes_price, no_price
        """
        print("🔨 构建逻辑图...")
        
        # 清空现有图
        self.nodes.clear()
        self.edges.clear()
        self.implication_graph.clear()
        self.mutex_pairs.clear()
        
        # 添加节点
        for market in markets:
            node = LogicNode(
                market_id=market['id'],
                market_title=market['title'],
                yes_price=market['yes_price'],
                no_price=market['no_price']
            )
            self.nodes[market['id']] = node
        
        # 添加蕴含关系边
        for conclusion, premises in self.IMPLICATION_RULES.items():
            # 查找包含 conclusion 的市场
            conclusion_nodes = self._find_nodes_by_keyword(conclusion)
            
            for premise in premises:
                # 查找包含 premise 的市场
                premise_nodes = self._find_nodes_by_keyword(premise)
                
                # 为每对匹配的市场添加边
                for c_node in conclusion_nodes:
                    for p_node in premise_nodes:
                        edge = LogicEdge(
                            from_node=c_node,
                            to_node=p_node,
                            relation_type='implies',
                            confidence=0.95
                        )
                        self.edges.append(edge)
                        self.implication_graph[c_node].add(p_node)
        
        # 添加互斥关系边
        for mutex_pair in self.MUTEX_RULES:
            nodes_a = self._find_nodes_by_keyword(mutex_pair[0])
            nodes_b = self._find_nodes_by_keyword(mutex_pair[1])
            
            for node_a in nodes_a:
                for node_b in nodes_b:
                    if node_a != node_b:  # 避免自互斥
                        edge = LogicEdge(
                            from_node=node_a,
                            to_node=node_b,
                            relation_type='mutex',
                            confidence=0.90
                        )
                        self.edges.append(edge)
                        self.mutex_pairs.add((min(node_a, node_b), max(node_a, node_b)))
        
        print(f"   节点数: {len(self.nodes)}")
        print(f"   边数: {len(self.edges)}")
        print(f"   蕴含关系: {sum(1 for e in self.edges if e.relation_type == 'implies')}")
        print(f"   互斥关系: {sum(1 for e in self.edges if e.relation_type == 'mutex')}")
    
    def _find_nodes_by_keyword(self, keyword: str) -> List[str]:
        """根据关键词查找节点"""
        matching_nodes = []
        keyword_lower = keyword.lower()
        
        for node_id, node in self.nodes.items():
            if keyword_lower in node.market_title.lower():
                matching_nodes.append(node_id)
        
        return matching_nodes
    
    def detect_implication_violations(self) -> List[LogicViolation]:
        """
        检测蕴含关系违反
        
        规则: 如果 A implies B，则 Price(A) <= Price(B) + tolerance
        """
        violations = []
        tolerance = 0.05
        
        print("\n🔍 检测蕴含关系违反...")
        
        for edge in self.edges:
            if edge.relation_type != 'implies':
                continue
            
            from_node = self.nodes.get(edge.from_node)
            to_node = self.nodes.get(edge.to_node)
            
            if not from_node or not to_node:
                continue
            
            price_from = from_node.yes_price
            price_to = to_node.yes_price
            
            # 检查违反: Price(A) > Price(B) + tolerance
            if price_from > price_to + tolerance:
                violation = price_from - price_to
                severity = min(violation / 0.2, 1.0)  # 最大违反程度为20%
                
                violations.append(LogicViolation(
                    violation_type='implies_violation',
                    nodes=[edge.from_node, edge.to_node],
                    expected=f"Price({from_node.market_title[:30]}) <= Price({to_node.market_title[:30]})",
                    actual=f"{price_from:.2f} > {price_to:.2f}",
                    severity=severity,
                    profit_potential=violation - tolerance,
                    description=f"{from_node.market_title[:40]} implies {to_node.market_title[:40]}, "
                               f"but price {price_from:.2f} > {price_to:.2f}"
                ))
                
                print(f"   🔴 违反: {from_node.market_title[:30]}({price_from:.2f}) > "
                      f"{to_node.market_title[:30]}({price_to:.2f})")
        
        print(f"   发现 {len(violations)} 个蕴含关系违反")
        return violations
    
    def detect_mutex_violations(self) -> List[LogicViolation]:
        """
        检测互斥关系违反
        
        规则: 如果 A mutex B，则 Price(A) + Price(B) <= 1.0 + tolerance
        """
        violations = []
        tolerance = 0.05
        
        print("\n🔍 检测互斥关系违反...")
        
        for edge in self.edges:
            if edge.relation_type != 'mutex':
                continue
            
            node_a = self.nodes.get(edge.from_node)
            node_b = self.nodes.get(edge.to_node)
            
            if not node_a or not node_b:
                continue
            
            price_a = node_a.yes_price
            price_b = node_b.yes_price
            combined = price_a + price_b
            
            # 检查违反: Price(A) + Price(B) > 1.0 + tolerance
            if combined > 1.0 + tolerance:
                violation = combined - 1.0
                severity = min(violation / 0.2, 1.0)
                
                violations.append(LogicViolation(
                    violation_type='mutex_violation',
                    nodes=[edge.from_node, edge.to_node],
                    expected=f"Price(A) + Price(B) <= 1.0",
                    actual=f"{price_a:.2f} + {price_b:.2f} = {combined:.2f}",
                    severity=severity,
                    profit_potential=violation - tolerance,
                    description=f"{node_a.market_title[:40]} and {node_b.market_title[:40]} are mutually exclusive, "
                               f"but combined price {combined:.2f} > 1.0"
                ))
                
                print(f"   🔴 违反: {node_a.market_title[:30]}({price_a:.2f}) + "
                      f"{node_b.market_title[:30]}({price_b:.2f}) = {combined:.2f}")
        
        print(f"   发现 {len(violations)} 个互斥关系违反")
        return violations
    
    def detect_transitivity_violations(self) -> List[LogicViolation]:
        """
        检测传递性违反
        
        规则: 如果 A implies B, B implies C，则 A implies C
               所以 Price(A) <= Price(C) + tolerance
        """
        violations = []
        tolerance = 0.05
        
        print("\n🔍 检测传递性违反...")
        
        # 查找传递链 A -> B -> C
        for node_a in self.nodes:
            for node_b in self.implication_graph.get(node_a, set()):
                for node_c in self.implication_graph.get(node_b, set()):
                    # 检查 A 和 C 的价格关系
                    if node_a not in self.nodes or node_c not in self.nodes:
                        continue
                    
                    price_a = self.nodes[node_a].yes_price
                    price_c = self.nodes[node_c].yes_price
                    
                    if price_a > price_c + tolerance:
                        violation = price_a - price_c
                        severity = min(violation / 0.2, 1.0)
                        
                        violations.append(LogicViolation(
                            violation_type='transitivity_violation',
                            nodes=[node_a, node_b, node_c],
                            expected=f"Price(A) <= Price(C) via transitivity",
                            actual=f"{price_a:.2f} > {price_c:.2f}",
                            severity=severity,
                            profit_potential=violation - tolerance,
                            description=f"Transitivity: {node_a} -> {node_b} -> {node_c}, "
                                       f"but price {price_a:.2f} > {price_c:.2f}"
                        ))
                        
                        print(f"   🔴 传递性违反: {node_a}({price_a:.2f}) > {node_c}({price_c:.2f})")
        
        print(f"   发现 {len(violations)} 个传递性违反")
        return violations
    
    def scan_all_violations(self, markets: List[Dict]) -> List[LogicViolation]:
        """
        扫描所有类型的逻辑违反
        
        Args:
            markets: 市场列表
            
        Returns:
            所有违反的列表
        """
        print("\n🚀 开始逻辑链矛盾扫描...")
        
        # 构建逻辑图
        self.build_logic_graph(markets)
        
        if not self.edges:
            print("   ⚪ 未发现逻辑关系")
            return []
        
        # 检测各类违反
        all_violations = []
        
        all_violations.extend(self.detect_implication_violations())
        all_violations.extend(self.detect_mutex_violations())
        all_violations.extend(self.detect_transitivity_violations())
        
        # 按严重程度排序
        all_violations.sort(key=lambda v: v.severity, reverse=True)
        
        print(f"\n📊 扫描完成: 共发现 {len(all_violations)} 个逻辑违反")
        print(f"   - 蕴含违反: {sum(1 for v in all_violations if v.violation_type == 'implies_violation')}")
        print(f"   - 互斥违反: {sum(1 for v in all_violations if v.violation_type == 'mutex_violation')}")
        print(f"   - 传递违反: {sum(1 for v in all_violations if v.violation_type == 'transitivity_violation')}")
        
        return all_violations


def format_violation_telegram(violation: LogicViolation) -> str:
    """格式化违反为 Telegram 消息"""
    emoji = {
        'implies_violation': '🔴',
        'mutex_violation': '🟡',
        'transitivity_violation': '🟠'
    }.get(violation.violation_type, '⚪')
    
    type_name = violation.violation_type.replace('_', ' ').title()
    
    return f"""
{emoji} *逻辑链矛盾: {type_name}*

*严重程度:* {violation.severity*100:.0f}%
*预期收益:* {violation.profit_potential*100:.1f}%

*违反详情:*
📍 {violation.description}

*预期:*
{violation.expected}

*实际:*
{violation.actual}

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}
"""


# 测试代码
if __name__ == "__main__":
    print("🧪 测试逻辑链分析器...")
    
    analyzer = LogicChainAnalyzer()
    
    # 测试市场数据 - 包含多种矛盾
    test_markets = [
        {
            "id": "0x100",
            "title": "Will Chiefs win Super Bowl 2024?",
            "yes_price": 0.55,  # Chiefs 55%
            "no_price": 0.45
        },
        {
            "id": "0x101",
            "title": "Will AFC win Super Bowl 2024?",
            "yes_price": 0.45,  # AFC 45% - 蕴含违反
            "no_price": 0.55
        },
        {
            "id": "0x102",
            "title": "Will Eagles win Super Bowl 2024?",
            "yes_price": 0.30,  # Eagles 30%
            "no_price": 0.70
        },
        {
            "id": "0x103",
            "title": "Will Trump win 2024?",
            "yes_price": 0.55,  # Trump 55%
            "no_price": 0.45
        },
        {
            "id": "0x104",
            "title": "Will Republican win 2024?",
            "yes_price": 0.48,  # Republican 48% - 蕴含违反
            "no_price": 0.52
        },
        {
            "id": "0x105",
            "title": "Will Biden win 2024?",
            "yes_price": 0.45,
            "no_price": 0.55
        }
    ]
    
    # 扫描所有违反
    violations = analyzer.scan_all_violations(test_markets)
    
    if violations:
        print(f"\n✅ 发现 {len(violations)} 个逻辑违反:\n")
        for i, v in enumerate(violations[:3], 1):  # 只显示前3个
            print(f"--- 违反 {i} ---")
            print(format_violation_telegram(v))
    else:
        print("\n⚪ 未发现逻辑违反")
    
    print("\n✅ 测试完成!")