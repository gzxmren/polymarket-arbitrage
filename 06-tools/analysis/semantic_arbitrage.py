#!/usr/bin/env python3
"""
语义套利引擎 V1
使用 LLM 识别语义相关的市场，检测定价矛盾

注意: 此模块需要外部 LLM 处理，本模块只负责生成任务和处理结果
"""

import json
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "dashboard/backend/app/models"))

try:
    from database import db
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "dashboard/backend"))
    from app.models.database import db


@dataclass
class Market:
    """市场数据类"""
    id: str
    title: str
    description: str
    yes_price: float
    no_price: float
    liquidity: float
    volume: float = 0


@dataclass
class SemanticRelationship:
    """语义关系数据类"""
    market_a: str
    market_b: str
    similarity: float
    relationship_type: str  # 'implies', 'mutex', 'related', 'independent'
    confidence: float
    detected_at: datetime


@dataclass
class ArbitrageSignal:
    """套利信号数据类"""
    type: str  # 'semantic_arbitrage'
    subtype: str  # 'implies_violation', 'mutex_violation'
    market_a: str
    market_b: str
    price_a: float
    price_b: float
    violation: float
    expected_profit: float
    confidence: float
    reasoning: str
    suggested_action: str
    created_at: datetime


class SemanticArbitrageEngine:
    """
    语义套利引擎
    
    工作流程:
    1. 生成 LLM 任务 (prompt)
    2. 保存到任务队列
    3. 等待外部 LLM 处理 (OpenClaw Agent)
    4. 读取结果，处理业务逻辑
    """
    
    # 预定义的逻辑关系 (可扩展)
    IMPLICATIONS = {
        # 体育 - 使用关键词匹配
        "Chiefs": ["AFC"],
        "Eagles": ["NFC"],
        "Lakers": ["Western Conference"],
        "Celtics": ["Eastern Conference"],
        
        # 政治
        "Trump": ["Republican", "Male"],
        "Biden": ["Democrat", "Male"],
        "Haley": ["Republican", "Female"],
        
        # 地缘政治
        "Iran": ["Middle East"],
        "Russia": ["Eurasia"],
    }
    
    def __init__(self, task_dir: str = "/tmp/llm_tasks", result_dir: str = "/tmp/llm_results"):
        self.task_dir = Path(task_dir)
        self.result_dir = Path(result_dir)
        self.task_dir.mkdir(exist_ok=True)
        self.result_dir.mkdir(exist_ok=True)
        self.db = db
    
    def create_similarity_task(self, market_a: Market, market_b: Market) -> str:
        """
        创建语义相似度计算任务
        
        Returns:
            任务ID
        """
        task_id = f"sim_{uuid.uuid4().hex[:8]}"
        
        prompt = f"""分析以下两个预测市场的语义相似度：

市场A: {market_a.title}
描述: {market_a.description}

市场B: {market_b.title}
描述: {market_b.description}

请给出相似度评分（0-1）和理由。
以JSON格式返回：{{"similarity": 0.85, "reason": "..."}}"""
        
        task = {
            "id": task_id,
            "type": "similarity",
            "status": "pending",
            "market_a": market_a.id,
            "market_b": market_b.id,
            "market_a_title": market_a.title,
            "market_b_title": market_b.title,
            "prompt": prompt,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        task_file = self.task_dir / f"{task_id}.json"
        with open(task_file, 'w') as f:
            json.dump(task, f, indent=2)
        
        print(f"📝 创建相似度任务: {task_id}")
        return task_id
    
    def create_relationship_task(self, market_a: Market, market_b: Market) -> str:
        """
        创建逻辑关系检测任务
        
        Returns:
            任务ID
        """
        task_id = f"rel_{uuid.uuid4().hex[:8]}"
        
        prompt = f"""分析以下两个预测市场的逻辑关系：

市场A: {market_a.title}
市场B: {market_b.title}

可能的关系类型：
- implies: A发生则B必然发生 (A⊆B)，例如"Trump wins"→"Republican wins"
- mutex: A和B互斥 (A∩B=∅)，例如"Trump wins"和"Biden wins"
- related: 相关但无明确逻辑关系
- independent: 独立

以JSON格式返回：
{{"relationship": "implies/mutex/related/independent", "confidence": 0.9, "reason": "..."}}"""
        
        task = {
            "id": task_id,
            "type": "relationship",
            "status": "pending",
            "market_a": market_a.id,
            "market_b": market_b.id,
            "market_a_title": market_a.title,
            "market_b_title": market_b.title,
            "prompt": prompt,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        task_file = self.task_dir / f"{task_id}.json"
        with open(task_file, 'w') as f:
            json.dump(task, f, indent=2)
        
        print(f"📝 创建关系检测任务: {task_id}")
        return task_id
    
    def get_pending_tasks(self) -> List[Dict]:
        """获取待处理的 LLM 任务"""
        tasks = []
        for task_file in self.task_dir.glob("*.json"):
            try:
                with open(task_file, 'r') as f:
                    task = json.load(f)
                if task.get("status") == "pending":
                    tasks.append(task)
            except (json.JSONDecodeError, IOError):
                continue
        return tasks
    
    def process_result(self, task_id: str) -> Optional[Any]:
        """
        处理 LLM 返回的结果
        
        Args:
            task_id: 任务ID
            
        Returns:
            处理结果或 None
        """
        result_file = self.result_dir / f"{task_id}_result.json"
        task_file = self.task_dir / f"{task_id}.json"
        
        if not result_file.exists():
            return None
        
        try:
            # 读取结果
            with open(result_file, 'r') as f:
                result_data = json.load(f)
            
            # 读取任务信息
            with open(task_file, 'r') as f:
                task = json.load(f)
            
            llm_response = result_data.get("result", "")
            task_type = task.get("type")
            
            # 解析 LLM 响应
            try:
                llm_result = json.loads(llm_response)
            except json.JSONDecodeError:
                print(f"⚠️  LLM 响应解析失败: {task_id}")
                return None
            
            # 根据任务类型处理
            if task_type == "similarity":
                similarity = float(llm_result.get("similarity", 0))
                # 保存到数据库
                self._save_similarity(task, similarity)
                return similarity
            
            elif task_type == "relationship":
                relationship = SemanticRelationship(
                    market_a=task["market_a"],
                    market_b=task["market_b"],
                    similarity=0,  # 需要单独计算
                    relationship_type=llm_result.get("relationship", "independent"),
                    confidence=llm_result.get("confidence", 0),
                    detected_at=datetime.now(timezone.utc)
                )
                # 保存到数据库
                self._save_relationship(relationship)
                return relationship
            
            return None
        
        except Exception as e:
            print(f"❌ 处理结果失败 {task_id}: {e}")
            return None
    
    def _save_similarity(self, task: Dict, similarity: float):
        """保存相似度到数据库"""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        # 确保表存在
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS semantic_relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_a TEXT NOT NULL,
                market_b TEXT NOT NULL,
                similarity REAL DEFAULT 0,
                relationship_type TEXT DEFAULT 'related',
                confidence REAL DEFAULT 0,
                detected_by TEXT DEFAULT 'llm',
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(market_a, market_b)
            )
        ''')
        
        cursor.execute('''
            INSERT OR REPLACE INTO semantic_relationships 
            (market_a, market_b, similarity, relationship_type, confidence, detected_by, created_at)
            VALUES (?, ?, ?, 'related', ?, 'llm', ?)
        ''', (
            task["market_a"],
            task["market_b"],
            similarity,
            similarity,  # 用相似度作为置信度
            datetime.now(timezone.utc).isoformat()
        ))
        
        conn.commit()
        conn.close()
        print(f"💾 保存相似度: {task['market_a']} vs {task['market_b']} = {similarity:.2f}")
    
    def _save_relationship(self, relationship: SemanticRelationship):
        """保存关系到数据库"""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO semantic_relationships 
            (market_a, market_b, similarity, relationship_type, confidence, detected_by, created_at)
            VALUES (?, ?, ?, ?, ?, 'llm', ?)
        ''', (
            relationship.market_a,
            relationship.market_b,
            relationship.similarity,
            relationship.relationship_type,
            relationship.confidence,
            relationship.detected_at.isoformat()
        ))
        
        conn.commit()
        conn.close()
        print(f"💾 保存关系: {relationship.market_a} -> {relationship.market_b} ({relationship.relationship_type})")
    
    def check_pricing_contradiction(self, market_a: Market, market_b: Market, 
                                   relationship_type: str) -> Optional[Dict]:
        """
        检查定价是否违反逻辑关系
        
        Returns:
            矛盾详情或 None
        """
        price_a = market_a.yes_price
        price_b = market_b.yes_price
        tolerance = 0.05  # 5%容错
        
        if relationship_type == "implies":
            # A implies B: Price(A) <= Price(B) + tolerance
            if price_a > price_b + tolerance:
                return {
                    "type": "implies_violation",
                    "market_a": market_a.id,
                    "market_b": market_b.id,
                    "market_a_title": market_a.title,
                    "market_b_title": market_b.title,
                    "price_a": price_a,
                    "price_b": price_b,
                    "violation": price_a - price_b,
                    "suggested_action": f"SELL {market_a.title[:30]}, BUY {market_b.title[:30]}",
                    "expected_profit": price_a - price_b - tolerance
                }
        
        elif relationship_type == "mutex":
            # A mutex B: Price(A) + Price(B) <= 1.0 + tolerance
            if price_a + price_b > 1.0 + tolerance:
                return {
                    "type": "mutex_violation",
                    "market_a": market_a.id,
                    "market_b": market_b.id,
                    "market_a_title": market_a.title,
                    "market_b_title": market_b.title,
                    "combined_price": price_a + price_b,
                    "violation": price_a + price_b - 1.0,
                    "suggested_action": "SELL both YES or arbitrage",
                    "expected_profit": price_a + price_b - 1.0 - tolerance
                }
        
        return None
    
    def check_predefined_relationships(self, markets: List[Market]) -> List[ArbitrageSignal]:
        """
        检查预定义的逻辑关系中的定价矛盾
        
        注意: 此方法不需要 LLM，直接检查已知关系
        
        逻辑: 如果 conclusion implies premise，则 price(conclusion) <= price(premise)
        例如: Chiefs win → AFC win，所以 price(Chiefs) <= price(AFC)
        如果 price(Chiefs) > price(AFC)，则存在套利机会
        """
        signals = []
        
        print(f"   检查 {len(self.IMPLICATIONS)} 个预定义关系...")
        
        for conclusion, premises in self.IMPLICATIONS.items():
            # 在市场中查找 conclusion 对应的市场
            conclusion_market = None
            for market in markets:
                if conclusion.lower() in market.title.lower():
                    conclusion_market = market
                    print(f"   ✓ 找到 conclusion: {conclusion} -> {market.title}")
                    break
            
            if not conclusion_market:
                print(f"   ✗ 未找到 conclusion: {conclusion}")
                continue
            
            # 查找 premises 对应的市场
            for premise in premises:
                premise_market = None
                for market in markets:
                    if premise.lower() in market.title.lower():
                        premise_market = market
                        print(f"   ✓ 找到 premise: {premise} -> {market.title}")
                        break
                
                if not premise_market:
                    print(f"   ✗ 未找到 premise: {premise}")
                    continue
                
                # 检查蕴含关系: conclusion implies premise
                # 所以 price(conclusion) <= price(premise)
                # 如果 price(conclusion) > price(premise)，则违反
                price_conclusion = conclusion_market.yes_price
                price_premise = premise_market.yes_price
                tolerance = 0.05
                
                print(f"   检查价格: {conclusion}={price_conclusion:.2f} vs {premise}={price_premise:.2f}")
                
                if price_conclusion > price_premise + tolerance:
                    print(f"   🔴 发现矛盾: {conclusion}({price_conclusion:.2f}) > {premise}({price_premise:.2f})")
                    signals.append(ArbitrageSignal(
                        type="semantic_arbitrage",
                        subtype="implies_violation",
                        market_a=conclusion_market.id,
                        market_b=premise_market.id,
                        price_a=price_conclusion,
                        price_b=price_premise,
                        violation=price_conclusion - price_premise,
                        expected_profit=price_conclusion - price_premise - tolerance,
                        confidence=0.95,
                        reasoning=f"{conclusion} implies {premise}, but price({conclusion})={price_conclusion:.2f} > price({premise})={price_premise:.2f}",
                        created_at=datetime.now(timezone.utc),
                        suggested_action=f"SELL {conclusion[:30]}, BUY {premise[:30]}"
                    ))
        
        return signals
    
    def scan_with_predefined(self, markets: List[Market]) -> List[ArbitrageSignal]:
        """
        使用预定义关系扫描套利机会（无需 LLM）
        
        这是立即可用的功能，不需要等待 LLM 处理
        """
        print(f"\n🔍 使用预定义关系扫描 {len(markets)} 个市场...")
        signals = self.check_predefined_relationships(markets)
        print(f"   发现 {len(signals)} 个定价矛盾")
        return signals


def format_signal_telegram(signal: ArbitrageSignal) -> str:
    """格式化信号为 Telegram 消息"""
    emoji = "🔴" if signal.subtype == "implies_violation" else "🟡"
    
    return f"""
{emoji} *语义套利机会*

类型: {signal.subtype.replace('_', ' ').title()}
置信度: {signal.confidence*100:.0f}%

*市场A:*
📍 {signal.market_a[:50]}
💰 价格: {signal.price_a*100:.1f}%

*市场B:*
📍 {signal.market_b[:50]}
💰 价格: {signal.price_b*100:.1f}%

*矛盾:*
📊 违反程度: {signal.violation*100:.1f}%
💵 预期收益: {signal.expected_profit*100:.1f}%

*建议:*
{signal.suggested_action}

*推理:*
{signal.reasoning}

⏰ {signal.created_at.strftime('%Y-%m-%d %H:%M')}
"""


# 测试代码
if __name__ == "__main__":
    print("🧪 测试语义套利引擎...")
    
    engine = SemanticArbitrageEngine()
    
    # 测试市场数据 - 模拟定价矛盾
    # 矛盾逻辑: Chiefs(55%) > AFC(45%)，但Chiefs implies AFC
    test_markets = [
        Market(
            id="0x123",
            title="Will Chiefs win Super Bowl 2024?",
            description="Kansas City Chiefs winning the Super Bowl",
            yes_price=0.55,  # Chiefs 55% - 应该 <= AFC
            no_price=0.45,
            liquidity=100000
        ),
        Market(
            id="0x124",
            title="Will AFC win Super Bowl 2024?",
            description="AFC team winning the Super Bowl",
            yes_price=0.45,  # AFC 45% - 矛盾！Chiefs是AFC球队
            no_price=0.55,
            liquidity=100000
        ),
        Market(
            id="0x125",
            title="Will Trump win 2024?",
            description="Donald Trump winning the 2024 election",
            yes_price=0.55,  # Trump 55% - 应该 <= Republican
            no_price=0.45,
            liquidity=200000
        ),
        Market(
            id="0x126",
            title="Will Republican win 2024?",
            description="Republican party winning the 2024 election",
            yes_price=0.48,  # Republican 48% - 矛盾！Trump是共和党
            no_price=0.52,
            liquidity=150000
        )
    ]
    
    # 测试 1: 预定义关系扫描
    print("\n📋 测试 1: 预定义关系扫描")
    signals = engine.scan_with_predefined(test_markets)
    
    if signals:
        print(f"\n✅ 发现 {len(signals)} 个套利机会:")
        for signal in signals:
            print(format_signal_telegram(signal))
    else:
        print("\n⚪ 未发现套利机会")
    
    # 测试 2: 创建 LLM 任务
    print("\n📋 测试 2: 创建 LLM 任务")
    task_id = engine.create_similarity_task(test_markets[0], test_markets[1])
    print(f"   任务已创建: {task_id}")
    print(f"   任务文件: {engine.task_dir}/{task_id}.json")
    print(f"   等待 OpenClaw Agent 处理...")
    
    # 测试 3: 检查待处理任务
    print("\n📋 测试 3: 检查待处理任务")
    pending = engine.get_pending_tasks()
    print(f"   待处理任务数: {len(pending)}")
    
    print("\n✅ 测试完成!")
    print("\n💡 下一步:")
    print("   1. OpenClaw Agent 读取任务文件")
    print("   2. 调用 kimi-k2.5 处理")
    print("   3. 保存结果到 result 文件")
    print("   4. 运行 engine.process_result(task_id) 获取结果")