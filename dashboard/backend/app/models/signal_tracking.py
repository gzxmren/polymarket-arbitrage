"""
信号追踪模型 - 记录信号预测准确性
"""
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from .database import db


class SignalTracking:
    """信号结果追踪管理"""
    
    @staticmethod
    def init_tables():
        """初始化信号追踪相关表"""
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # 信号结果表 - 记录每个信号的预测和实际结果
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS signal_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_type TEXT NOT NULL,
                signal_id TEXT NOT NULL,
                market_id TEXT,
                market_name TEXT,
                prediction TEXT NOT NULL,
                predicted_direction TEXT,
                confidence REAL,
                trigger_price REAL,
                target_price REAL,
                stop_loss REAL,
                actual_result TEXT,
                actual_direction TEXT,
                exit_price REAL,
                pnl_percent REAL,
                resolved_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                metadata TEXT
            )
        ''')
        
        # 质量报告表 - 每周生成的质量报告
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS quality_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_type TEXT NOT NULL,
                start_date TIMESTAMP NOT NULL,
                end_date TIMESTAMP NOT NULL,
                total_signals INTEGER DEFAULT 0,
                win_count INTEGER DEFAULT 0,
                loss_count INTEGER DEFAULT 0,
                pending_count INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                avg_pnl REAL DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                best_strategy TEXT,
                worst_strategy TEXT,
                report_data TEXT,
                generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 阈值历史表 - 记录阈值调整
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS threshold_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                threshold_type TEXT NOT NULL,
                old_value REAL NOT NULL,
                new_value REAL NOT NULL,
                change_reason TEXT,
                auto_optimized BOOLEAN DEFAULT 0,
                optimization_params TEXT,
                performance_before TEXT,
                performance_after TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 创建索引
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_signal_results_type ON signal_results(signal_type)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_signal_results_result ON signal_results(actual_result)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_signal_results_created ON signal_results(created_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_signal_results_market ON signal_results(market_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_quality_reports_date ON quality_reports(start_date, end_date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_threshold_history_type ON threshold_history(threshold_type)')
        
        conn.commit()
        conn.close()
        print("✅ 信号追踪表初始化完成")
    
    @staticmethod
    def create_signal(signal_type: str, signal_id: str, prediction: str,
                      market_id: str = None, market_name: str = None,
                      predicted_direction: str = None, confidence: float = None,
                      trigger_price: float = None, target_price: float = None,
                      stop_loss: float = None, metadata: Dict = None) -> int:
        """创建新信号记录"""
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO signal_results 
            (signal_type, signal_id, market_id, market_name, prediction,
             predicted_direction, confidence, trigger_price, target_price, stop_loss,
             actual_result, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        ''', (signal_type, signal_id, market_id, market_name, prediction,
              predicted_direction, confidence, trigger_price, target_price, stop_loss,
              json.dumps(metadata) if metadata else None))
        
        signal_db_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return signal_db_id
    
    @staticmethod
    def resolve_signal(signal_id: str, actual_result: str, exit_price: float = None,
                       pnl_percent: float = None, actual_direction: str = None,
                       metadata: Dict = None) -> bool:
        """更新信号结果"""
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT metadata FROM signal_results WHERE signal_id = ?', (signal_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return False
        
        existing_metadata = {}
        if row['metadata']:
            try:
                existing_metadata = json.loads(row['metadata'])
            except:
                pass
        
        if metadata:
            existing_metadata.update(metadata)
        
        cursor.execute('''
            UPDATE signal_results 
            SET actual_result = ?, exit_price = ?, pnl_percent = ?,
                actual_direction = ?, resolved_at = CURRENT_TIMESTAMP,
                metadata = ?
            WHERE signal_id = ?
        ''', (actual_result, exit_price, pnl_percent, actual_direction,
              json.dumps(existing_metadata), signal_id))
        
        conn.commit()
        conn.close()
        
        return cursor.rowcount > 0
    
    @staticmethod
    def get_signal_stats(signal_type: str = None, days: int = 30) -> Dict:
        """获取信号统计"""
        conn = db.get_connection()
        cursor = conn.cursor()
        
        query = '''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN actual_result = 'win' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN actual_result = 'loss' THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN actual_result = 'pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN actual_result = 'expired' THEN 1 ELSE 0 END) as expired,
                AVG(CASE WHEN actual_result IN ('win', 'loss') THEN pnl_percent END) as avg_pnl,
                SUM(CASE WHEN actual_result = 'win' THEN pnl_percent ELSE 0 END) as total_win_pnl,
                SUM(CASE WHEN actual_result = 'loss' THEN pnl_percent ELSE 0 END) as total_loss_pnl,
                AVG(confidence) as avg_confidence
            FROM signal_results
            WHERE created_at > datetime('now', '-{} days')
        '''.format(days)
        
        params = []
        if signal_type:
            query += ' AND signal_type = ?'
            params.append(signal_type)
        
        cursor.execute(query, params)
        row = cursor.fetchone()
        
        stats = dict(row) if row else {}
        
        # 计算胜率
        resolved = (stats.get('wins') or 0) + (stats.get('losses') or 0)
        stats['win_rate'] = (stats.get('wins') or 0) / resolved * 100 if resolved > 0 else 0
        stats['resolved_count'] = resolved
        
        # 计算总盈亏
        win_pnl = stats.get('total_win_pnl') or 0
        loss_pnl = stats.get('total_loss_pnl') or 0
        stats['total_pnl'] = win_pnl + loss_pnl
        
        # 按信号类型统计
        cursor.execute('''
            SELECT signal_type,
                COUNT(*) as count,
                SUM(CASE WHEN actual_result = 'win' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN actual_result = 'loss' THEN 1 ELSE 0 END) as losses,
                AVG(CASE WHEN actual_result IN ('win', 'loss') THEN pnl_percent END) as avg_pnl
            FROM signal_results
            WHERE created_at > datetime('now', '-{} days')
            GROUP BY signal_type
        '''.format(days))
        
        type_stats = [dict(row) for row in cursor.fetchall()]
        for ts in type_stats:
            resolved = (ts.get('wins') or 0) + (ts.get('losses') or 0)
            ts['win_rate'] = (ts.get('wins') or 0) / resolved * 100 if resolved > 0 else 0
        
        stats['by_type'] = type_stats
        
        conn.close()
        return stats
    
    @staticmethod
    def get_pending_signals(signal_type: str = None, limit: int = 100) -> List[Dict]:
        """获取待处理的信号"""
        conn = db.get_connection()
        cursor = conn.cursor()
        
        query = '''
            SELECT * FROM signal_results
            WHERE actual_result = 'pending'
        '''
        params = []
        
        if signal_type:
            query += ' AND signal_type = ?'
            params.append(signal_type)
        
        query += ' ORDER BY created_at DESC LIMIT ?'
        params.append(limit)
        
        cursor.execute(query, params)
        signals = [dict(row) for row in cursor.fetchall()]
        
        for signal in signals:
            if signal.get('metadata'):
                try:
                    signal['parsed_metadata'] = json.loads(signal['metadata'])
                except:
                    signal['parsed_metadata'] = {}
        
        conn.close()
        return signals
    
    @staticmethod
    def get_signals(signal_type: str = None, result: str = None, 
                    days: int = 30, limit: int = 100) -> List[Dict]:
        """获取信号列表"""
        conn = db.get_connection()
        cursor = conn.cursor()
        
        query = '''
            SELECT * FROM signal_results
            WHERE created_at > datetime('now', '-{} days')
        '''.format(days)
        params = []
        
        if signal_type:
            query += ' AND signal_type = ?'
            params.append(signal_type)
        
        if result:
            query += ' AND actual_result = ?'
            params.append(result)
        
        query += ' ORDER BY created_at DESC LIMIT ?'
        params.append(limit)
        
        cursor.execute(query, params)
        signals = [dict(row) for row in cursor.fetchall()]
        
        for signal in signals:
            if signal.get('metadata'):
                try:
                    signal['parsed_metadata'] = json.loads(signal['metadata'])
                except:
                    signal['parsed_metadata'] = {}
        
        conn.close()
        return signals
    
    @staticmethod
    def save_quality_report(report_type: str, start_date: str, end_date: str,
                           total_signals: int, win_count: int, loss_count: int,
                           pending_count: int, win_rate: float, avg_pnl: float,
                           total_pnl: float, best_strategy: str = None,
                           worst_strategy: str = None, report_data: Dict = None) -> int:
        """保存质量报告"""
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO quality_reports
            (report_type, start_date, end_date, total_signals, win_count, loss_count,
             pending_count, win_rate, avg_pnl, total_pnl, best_strategy, worst_strategy, report_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (report_type, start_date, end_date, total_signals, win_count, loss_count,
              pending_count, win_rate, avg_pnl, total_pnl, best_strategy, worst_strategy,
              json.dumps(report_data) if report_data else None))
        
        report_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return report_id
    
    @staticmethod
    def get_quality_reports(report_type: str = None, limit: int = 10) -> List[Dict]:
        """获取质量报告列表"""
        conn = db.get_connection()
        cursor = conn.cursor()
        
        query = 'SELECT * FROM quality_reports WHERE 1=1'
        params = []
        
        if report_type:
            query += ' AND report_type = ?'
            params.append(report_type)
        
        query += ' ORDER BY generated_at DESC LIMIT ?'
        params.append(limit)
        
        cursor.execute(query, params)
        reports = [dict(row) for row in cursor.fetchall()]
        
        for report in reports:
            if report.get('report_data'):
                try:
                    report['parsed_data'] = json.loads(report['report_data'])
                except:
                    report['parsed_data'] = {}
        
        conn.close()
        return reports
    
    @staticmethod
    def get_latest_quality_report(report_type: str = 'weekly') -> Optional[Dict]:
        """获取最新的质量报告"""
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT * FROM quality_reports
            WHERE report_type = ?
            ORDER BY generated_at DESC
            LIMIT 1
        ''', (report_type,))
        
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        report = dict(row)
        if report.get('report_data'):
            try:
                report['parsed_data'] = json.loads(report['report_data'])
            except:
                report['parsed_data'] = {}
        
        return report
    
    @staticmethod
    def record_threshold_change(threshold_type: str, old_value: float, new_value: float,
                                change_reason: str = None, auto_optimized: bool = False,
                                optimization_params: Dict = None,
                                performance_before: Dict = None,
                                performance_after: Dict = None) -> int:
        """记录阈值变更"""
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO threshold_history
            (threshold_type, old_value, new_value, change_reason, auto_optimized,
             optimization_params, performance_before, performance_after)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (threshold_type, old_value, new_value, change_reason, auto_optimized,
              json.dumps(optimization_params) if optimization_params else None,
              json.dumps(performance_before) if performance_before else None,
              json.dumps(performance_after) if performance_after else None))
        
        change_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return change_id
    
    @staticmethod
    def get_threshold_history(threshold_type: str = None, limit: int = 50) -> List[Dict]:
        """获取阈值变更历史"""
        conn = db.get_connection()
        cursor = conn.cursor()
        
        query = 'SELECT * FROM threshold_history WHERE 1=1'
        params = []
        
        if threshold_type:
            query += ' AND threshold_type = ?'
            params.append(threshold_type)
        
        query += ' ORDER BY created_at DESC LIMIT ?'
        params.append(limit)
        
        cursor.execute(query, params)
        history = [dict(row) for row in cursor.fetchall()]
        
        for item in history:
            for field in ['optimization_params', 'performance_before', 'performance_after']:
                if item.get(field):
                    try:
                        item[f'parsed_{field}'] = json.loads(item[field])
                    except:
                        item[f'parsed_{field}'] = {}
        
        conn.close()
        return history
    
    @staticmethod
    def get_current_thresholds() -> Dict[str, float]:
        """获取当前所有阈值设置"""
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # 获取每种类型的最新阈值
        cursor.execute('''
            SELECT threshold_type, new_value
            FROM threshold_history
            WHERE (threshold_type, created_at) IN (
                SELECT threshold_type, MAX(created_at)
                FROM threshold_history
                GROUP BY threshold_type
            )
        ''')
        
        thresholds = {row['threshold_type']: row['new_value'] for row in cursor.fetchall()}
        conn.close()
        
        return thresholds
