#!/usr/bin/env python3
"""
语义套利扫描器
集成语义套利引擎和逻辑链分析器，扫描 Polymarket 市场

使用方式:
1. 直接运行: python3 semantic_scanner.py
2. 作为模块导入: from semantic_scanner import SemanticScanner
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional
from dataclasses import dataclass

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "dashboard/backend/app/models"))

try:
    from database import db
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "dashboard/backend"))
    from app.models.database import db

# 导入语义套利和逻辑链分析
from semantic_arbitrage import SemanticArbitrageEngine, Market as SemanticMarket, ArbitrageSignal
from logic_chain_analyzer import LogicChainAnalyzer, LogicViolation


@dataclass
class ScanResult:
    """扫描结果"""
    scan_time: datetime
    markets_scanned: int
    semantic_signals: List[ArbitrageSignal]
    logic_violations: List[LogicViolation]
    total_opportunities: int


class SemanticScanner:
    """
    语义套利扫描器
    
    集成:
    1. 语义套利引擎 (预定义关系)
    2. 逻辑链分析器 (蕴含/互斥/传递性)
    
    扫描流程:
    1. 获取活跃市场
    2. 语义套利扫描
    3. 逻辑链分析
    4. 生成报告
    """
    
    def __init__(self):
        self.semantic_engine = SemanticArbitrageEngine()
        self.logic_analyzer = LogicChainAnalyzer()
        self.db = db
    
    def fetch_active_markets(self) -> List[Dict]:
        """
        从数据库获取活跃市场
        
        Returns:
            市场列表
        """
        print("📊 获取活跃市场...")
        
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        # 从 positions 表获取市场信息
        cursor.execute('''
            SELECT DISTINCT market, MAX(cur_price) as yes_price
            FROM positions
            WHERE cur_price > 0
            GROUP BY market
        ''')
        
        markets = []
        for row in cursor.fetchall():
            markets.append({
                'id': row[0][:20],  # 截取前20字符作为ID
                'title': row[0],
                'yes_price': row[1] or 0.5,
                'no_price': 1.0 - (row[1] or 0.5)
            })
        
        conn.close()
        
        print(f"   获取 {len(markets)} 个市场")
        return markets
    
    def scan(self) -> ScanResult:
        """
        执行完整扫描
        
        Returns:
            扫描结果
        """
        print("\n" + "="*60)
        print("🚀 启动语义套利扫描")
        print("="*60)
        
        # 1. 获取市场
        markets = self.fetch_active_markets()
        
        if not markets:
            print("⚪ 没有活跃市场")
            return ScanResult(
                scan_time=datetime.now(timezone.utc),
                markets_scanned=0,
                semantic_signals=[],
                logic_violations=[],
                total_opportunities=0
            )
        
        # 2. 语义套利扫描 (预定义关系)
        print("\n📋 步骤 1: 语义套利扫描")
        semantic_markets = [
            SemanticMarket(
                id=m['id'],
                title=m['title'],
                description=m['title'],
                yes_price=m['yes_price'],
                no_price=m['no_price'],
                liquidity=1000
            )
            for m in markets
        ]
        
        semantic_signals = self.semantic_engine.scan_with_predefined(semantic_markets)
        
        # 3. 逻辑链分析
        print("\n📋 步骤 2: 逻辑链分析")
        logic_violations = self.logic_analyzer.scan_all_violations(markets)
        
        # 4. 生成结果
        total = len(semantic_signals) + len(logic_violations)
        
        result = ScanResult(
            scan_time=datetime.now(timezone.utc),
            markets_scanned=len(markets),
            semantic_signals=semantic_signals,
            logic_violations=logic_violations,
            total_opportunities=total
        )
        
        # 5. 输出报告
        self._print_report(result)
        
        return result
    
    def _print_report(self, result: ScanResult):
        """打印扫描报告"""
        print("\n" + "="*60)
        print("📊 扫描报告")
        print("="*60)
        print(f"扫描时间: {result.scan_time.strftime('%Y-%m-%d %H:%M')}")
        print(f"扫描市场: {result.markets_scanned}")
        print(f"语义套利信号: {len(result.semantic_signals)}")
        print(f"逻辑链违反: {len(result.logic_violations)}")
        print(f"总计机会: {result.total_opportunities}")
        
        if result.semantic_signals:
            print("\n🔴 语义套利机会:")
            for i, signal in enumerate(result.semantic_signals, 1):
                print(f"\n   {i}. {signal.market_a[:40]}")
                print(f"      价格: {signal.price_a*100:.1f}% vs {signal.price_b*100:.1f}%")
                print(f"      预期收益: {signal.expected_profit*100:.1f}%")
        
        if result.logic_violations:
            print("\n🔴 逻辑链违反:")
            for i, violation in enumerate(result.logic_violations, 1):
                print(f"\n   {i}. {violation.violation_type}")
                print(f"      严重程度: {violation.severity*100:.0f}%")
                print(f"      预期收益: {violation.profit_potential*100:.1f}%")
        
        if result.total_opportunities == 0:
            print("\n⚪ 未发现套利机会")
        
        print("\n" + "="*60)
    
    def save_results(self, result: ScanResult):
        """保存扫描结果到数据库"""
        print("\n💾 保存结果...")
        
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        # 创建信号表（如果不存在）
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS semantic_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                subtype TEXT,
                market_a TEXT,
                market_b TEXT,
                price_a REAL,
                price_b REAL,
                violation REAL,
                expected_profit REAL,
                confidence REAL,
                reasoning TEXT,
                suggested_action TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 保存语义套利信号
        for signal in result.semantic_signals:
            cursor.execute('''
                INSERT INTO semantic_signals 
                (type, subtype, market_a, market_b, price_a, price_b, violation, 
                 expected_profit, confidence, reasoning, suggested_action)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                signal.type,
                signal.subtype,
                signal.market_a,
                signal.market_b,
                signal.price_a,
                signal.price_b,
                signal.violation,
                signal.expected_profit,
                signal.confidence,
                signal.reasoning,
                signal.suggested_action
            ))
        
        conn.commit()
        conn.close()
        
        print(f"   保存 {len(result.semantic_signals)} 个信号")
    
    def send_telegram_notification(self, result: ScanResult):
        """发送 Telegram 通知"""
        if result.total_opportunities == 0:
            return
        
        print("\n📤 发送 Telegram 通知...")
        
        # 构建消息
        message = f"""
🔴 *语义套利扫描报告*

扫描时间: {result.scan_time.strftime('%Y-%m-%d %H:%M')}
扫描市场: {result.markets_scanned}
发现机会: {result.total_opportunities}

"""
        
        if result.semantic_signals:
            message += "*语义套利:*\n"
            for signal in result.semantic_signals:
                message += f"• {signal.market_a[:30]}: {signal.expected_profit*100:.1f}%\n"
        
        if result.logic_violations:
            message += "\n*逻辑链违反:*\n"
            for violation in result.logic_violations[:3]:  # 最多显示3个
                message += f"• {violation.violation_type}: {violation.profit_potential*100:.1f}%\n"
        
        message += f"\n⏰ {datetime.now(timezone.utc).strftime('%H:%M')}"
        
        # 这里应该调用 Telegram API 发送消息
        # 简化版: 只打印消息
        print(message)
        print("   ✅ 消息已构建 (待发送)")


# 测试代码
if __name__ == "__main__":
    print("🧪 测试语义套利扫描器...")
    
    scanner = SemanticScanner()
    
    # 执行扫描
    result = scanner.scan()
    
    # 保存结果
    if result.total_opportunities > 0:
        scanner.save_results(result)
        scanner.send_telegram_notification(result)
    
    print("\n✅ 扫描完成!")