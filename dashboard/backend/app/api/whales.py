from flask import Blueprint, jsonify, request
from datetime import datetime, timezone
import sys
from pathlib import Path

# 添加新闻模块路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "06-tools/analysis"))

from ..models.database import db
from ..services.whale_analyzer import analyzer
from ..services.whale_deep_analyzer import WhaleDeepAnalyzer

# 尝试导入新闻模块
try:
    from whale_news_connector import WhaleNewsConnector
    from news_fetcher import NewsFetcher
    NEWS_MODULE_AVAILABLE = True
except Exception as e:
    print(f"⚠️ 新闻模块加载失败: {e}")
    NEWS_MODULE_AVAILABLE = False

whales_bp = Blueprint('whales', __name__)

@whales_bp.route('/', methods=['GET'])
def get_whales():
    """获取鲸鱼列表（支持多维度排序）"""
    conn = db.get_connection()
    cursor = conn.cursor()
    
    # 查询参数
    is_watched = request.args.get('is_watched', None)
    sort_by = request.args.get('sort_by', 'total_value')
    sort_order = request.args.get('sort_order', 'desc')
    limit = request.args.get('limit', 100, type=int)  # 默认返回100个，支持显示全部关注鲸鱼
    
    query = 'SELECT * FROM whales WHERE 1=1'
    params = []
    
    if is_watched is not None:
        query += ' AND is_watched = ?'
        params.append(is_watched == 'true')
    
    # 排序
    valid_sort_fields = ['total_value', 'position_count', 'top5_ratio', 'last_updated', 'total_pnl', 'changes_count']
    if sort_by in valid_sort_fields:
        order = 'DESC' if sort_order == 'desc' else 'ASC'
        query += f' ORDER BY {sort_by} {order}'
    
    query += ' LIMIT ?'
    params.append(limit)
    
    cursor.execute(query, params)
    whales = [dict(row) for row in cursor.fetchall()]
    
    # 为每个鲸鱼添加变动次数（从changes表统计）
    for whale in whales:
        cursor.execute('''
            SELECT COUNT(*) as count FROM changes 
            WHERE wallet = ? AND timestamp > datetime('now', '-1 day')
        ''', (whale['wallet'],))
        result = cursor.fetchone()
        whale['changes_count'] = result['count'] if result else 0
    
    conn.close()
    
    return jsonify({
        'count': len(whales),
        'whales': whales
    })

@whales_bp.route('/<wallet>', methods=['GET'])
def get_whale_detail(wallet):
    """获取鲸鱼详情"""
    conn = db.get_connection()
    cursor = conn.cursor()
    
    # 鲸鱼基本信息
    cursor.execute('SELECT * FROM whales WHERE wallet = ?', (wallet,))
    whale = cursor.fetchone()
    
    if not whale:
        conn.close()
        return jsonify({'error': 'Whale not found'}), 404
    
    whale_dict = dict(whale)
    
    # 持仓明细
    cursor.execute('SELECT * FROM positions WHERE wallet = ? ORDER BY value DESC', (wallet,))
    whale_dict['positions'] = [dict(row) for row in cursor.fetchall()]
    
    # 最近变动
    cursor.execute('SELECT * FROM changes WHERE wallet = ? ORDER BY timestamp DESC LIMIT 20', (wallet,))
    whale_dict['changes'] = [dict(row) for row in cursor.fetchall()]
    
    # 集中度历史
    cursor.execute('SELECT * FROM concentration_history WHERE wallet = ? ORDER BY timestamp DESC LIMIT 24', (wallet,))
    whale_dict['concentration_history'] = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    return jsonify(whale_dict)

@whales_bp.route('/<wallet>/history', methods=['GET'])
def get_whale_history(wallet):
    """获取鲸鱼历史数据"""
    conn = db.get_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT * FROM concentration_history 
        WHERE wallet = ? 
        ORDER BY timestamp DESC 
        LIMIT 100
    ''', (wallet,))
    
    history = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    return jsonify({
        'wallet': wallet,
        'history': history
    })


@whales_bp.route('/<wallet>/pnl-history', methods=['GET'])
def get_whale_pnl_history(wallet):
    """获取鲸鱼盈亏历史数据"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # 获取盈亏历史（从changes表计算）
        cursor.execute('''
            SELECT 
                date(timestamp) as date,
                SUM(CASE WHEN type = 'increase' THEN change_amount ELSE 0 END) as realized_pnl,
                COUNT(*) as trade_count
            FROM changes 
            WHERE wallet = ? 
              AND timestamp > datetime('now', '-30 days')
            GROUP BY date(timestamp)
            ORDER BY date(timestamp) ASC
        ''', (wallet,))
        
        rows = cursor.fetchall()
        
        # 获取当前总盈亏
        cursor.execute('SELECT total_pnl FROM whales WHERE wallet = ?', (wallet,))
        whale_row = cursor.fetchone()
        current_pnl = whale_row['total_pnl'] if whale_row else 0
        
        conn.close()
        
        # 构建历史数据
        pnl_history = []
        cumulative_pnl = 0
        
        for row in rows:
            cumulative_pnl += row['realized_pnl'] or 0
            pnl_history.append({
                'date': row['date'],
                'daily_pnl': row['realized_pnl'] or 0,
                'cumulative_pnl': cumulative_pnl,
                'trade_count': row['trade_count']
            })
        
        return jsonify({
            'success': True,
            'wallet': wallet,
            'current_pnl': current_pnl,
            'history': pnl_history
        })
        
    except Exception as e:
        print(f"[ERROR] 获取盈亏历史失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@whales_bp.route('/concentration-trend', methods=['GET'])
def get_concentration_trend():
    """获取所有关注鲸鱼的集中度趋势"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # 获取关注的鲸鱼列表
        cursor.execute('SELECT wallet, pseudonym FROM whales WHERE is_watched = 1')
        watched_whales = cursor.fetchall()
        
        # 获取每个关注鲸鱼的集中度历史
        trend_data = []
        for whale in watched_whales:
            cursor.execute('''
                SELECT 
                    date(timestamp) as date,
                    AVG(top5_ratio) as avg_top5_ratio,
                    AVG(hhi) as avg_hhi
                FROM concentration_history 
                WHERE wallet = ? 
                  AND timestamp > datetime('now', '-7 days')
                GROUP BY date(timestamp)
                ORDER BY date(timestamp) ASC
            ''', (whale['wallet'],))
            
            history = cursor.fetchall()
            
            if history:
                trend_data.append({
                    'wallet': whale['wallet'],
                    'pseudonym': whale['pseudonym'],
                    'history': [
                        {
                            'date': row['date'],
                            'top5_ratio': row['avg_top5_ratio'],
                            'hhi': row['avg_hhi']
                        }
                        for row in history
                    ]
                })
        
        conn.close()
        
        return jsonify({
            'success': True,
            'count': len(trend_data),
            'data': trend_data
        })
        
    except Exception as e:
        print(f"[ERROR] 获取集中度趋势失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@whales_bp.route('/pnl-ranking', methods=['GET'])
def get_pnl_ranking():
    """获取鲸鱼盈亏排名"""
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        
        # 获取盈亏排名
        cursor.execute('''
            SELECT 
                wallet,
                pseudonym,
                total_pnl,
                total_value,
                position_count,
                is_watched
            FROM whales
            WHERE total_pnl != 0
            ORDER BY total_pnl DESC
            LIMIT 50
        ''')
        
        rows = cursor.fetchall()
        
        ranking = []
        for i, row in enumerate(rows, 1):
            ranking.append({
                'rank': i,
                'wallet': row['wallet'],
                'pseudonym': row['pseudonym'],
                'total_pnl': row['total_pnl'],
                'total_value': row['total_value'],
                'position_count': row['position_count'],
                'is_watched': row['is_watched'],
                'pnl_ratio': (row['total_pnl'] / row['total_value'] * 100) if row['total_value'] > 0 else 0
            })
        
        conn.close()
        
        return jsonify({
            'success': True,
            'count': len(ranking),
            'data': ranking
        })
        
    except Exception as e:
        print(f"[ERROR] 获取盈亏排名失败: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@whales_bp.route('/<wallet>/analysis', methods=['GET'])
def get_whale_analysis(wallet):
    """获取鲸鱼 AI 分析（实时）"""
    try:
        analysis = analyzer.analyze_whale(wallet)
        if 'error' in analysis:
            return jsonify(analysis), 404
        return jsonify(analysis)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@whales_bp.route('/<wallet>/deep-analysis', methods=['GET'])
def get_whale_deep_analysis_cached(wallet):
    """获取深度分析（仅缓存，不触发新调用）"""
    try:
        # 只返回缓存的数据
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT wallet, content, model, generated_at, cost
            FROM whale_deep_analysis
            WHERE wallet = ? AND expires_at > ?
        ''', (wallet, datetime.now(timezone.utc).isoformat()))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return jsonify({
                'wallet': row['wallet'],
                'content': row['content'],
                'model': row['model'],
                'generated_at': row['generated_at'],
                'cost': row['cost'],
                'from_cache': True
            })
        else:
            return jsonify({'exists': False})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@whales_bp.route('/<wallet>/deep-analysis', methods=['POST'])
def generate_whale_deep_analysis(wallet):
    """生成深度分析（触发 LLM 调用）"""
    try:
        import os
        print(f"[DEBUG] DEEPSEEK_API_KEY: {os.getenv('DEEPSEEK_API_KEY', 'NOT SET')[:20]}...")
        
        data = request.get_json() or {}
        force_refresh = data.get('force_refresh', False)
        
        # 每次创建新实例，确保环境变量已加载
        deep_analyzer = WhaleDeepAnalyzer()
        print(f"[DEBUG] deep_analyzer.deepseek_api_key: {deep_analyzer.deepseek_api_key[:20]}..." if deep_analyzer.deepseek_api_key else "[DEBUG] deep_analyzer.deepseek_api_key: NOT SET")
        
        result = deep_analyzer.get_deep_analysis(wallet, force_refresh)
        print(f"[DEBUG] result model: {result.get('model', 'unknown')}")
        
        if 'error' in result:
            return jsonify(result), 500
            
        return jsonify(result)
    except Exception as e:
        import traceback
        print(f"[ERROR] {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


# 新闻缓存（简单内存缓存）
_news_cache = {}
_news_cache_time = {}
NEWS_CACHE_TTL = 60  # 1分钟缓存（缩短以便测试）

@whales_bp.route('/<wallet>/news', methods=['GET'])
def get_whale_news(wallet):
    """获取鲸鱼持仓相关新闻（带缓存）"""
    try:
        if not NEWS_MODULE_AVAILABLE:
            return jsonify({'error': '新闻模块不可用'}), 503
        
        # 获取时间窗口参数
        hours = request.args.get('hours', 6, type=int)
        
        # 检查是否强制刷新
        force_refresh = request.args.get('force', 'false').lower() == 'true'
        
        # 检查缓存
        cache_key = f"{wallet}_{hours}"
        now = datetime.now(timezone.utc)
        if not force_refresh and cache_key in _news_cache:
            cache_time = _news_cache_time.get(cache_key, now)
            if (now - cache_time).total_seconds() < NEWS_CACHE_TTL:
                print(f"[INFO] 返回缓存的新闻数据: {wallet}")
                return jsonify(_news_cache[cache_key])
        
        # 获取鲸鱼数据
        conn = db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT * FROM whales WHERE wallet = ?', (wallet,))
        whale = cursor.fetchone()
        
        if not whale:
            conn.close()
            return jsonify({'error': 'Whale not found'}), 404
        
        whale_dict = dict(whale)
        
        # 获取持仓
        cursor.execute('SELECT * FROM positions WHERE wallet = ? ORDER BY value DESC', (wallet,))
        positions = [dict(row) for row in cursor.fetchall()]
        conn.close()
        
        # 初始化新闻连接器
        connector = WhaleNewsConnector()
        
        # 为每个持仓抓取新闻（限制最多3个市场，避免超时）
        positions_with_news = []
        for position in positions[:3]:  # 只处理前3个持仓
            market = position.get('market', '')
            if not market:
                continue
            
            try:
                # 提取关键词
                print(f"[DEBUG] 提取关键词: {market}")
                keywords = connector.extract_keywords(market)
                print(f"[DEBUG] 关键词结果: {keywords}")
                
                # 检查关键词是否为空
                if not any(keywords.values()):
                    print(f"[WARN] 关键词为空，尝试备用提取...")
                    # 备用关键词提取
                    import re
                    text_lower = market.lower()
                    backup_keywords = {'primary': [], 'secondary': [], 'context': []}
                    
                    # 手动匹配关键实体
                    entities = ['iran', 'israel', 'trump', 'biden', 'btc', 'bitcoin', 'netanyahu']
                    for entity in entities:
                        if entity in text_lower:
                            backup_keywords['primary'].append(entity.capitalize())
                    
                    if backup_keywords['primary']:
                        keywords = backup_keywords
                        print(f"[DEBUG] 备用关键词: {keywords}")
                
                # 抓取新闻（限制时间）
                # 注意：不使用signal，因为Flask多线程不支持
                try:
                    news_list = connector.fetch_news(keywords, hours=hours)
                except Exception as e:
                    print(f"[WARN] 新闻抓取失败: {market}: {e}")
                    news_list = []
                
                # 计算关联度
                relevant_news = []
                for news in news_list:
                    relevance = connector.calculate_relevance(
                        news, 
                        {'market': market}, 
                        datetime.now(timezone.utc)
                    )
                    if relevance['score'] >= 40:
                        relevant_news.append({
                            'source': news.get('source', 'Unknown'),
                            'title': news.get('title', ''),
                            'url': news.get('url', ''),
                            'published_at': news.get('published_at', ''),
                            'sentiment': news.get('sentiment', 'neutral'),
                            'relevance_score': relevance['score'],
                            'matched_keywords': news.get('matched_keywords', [])
                        })
                
                # 按关联度排序
                relevant_news.sort(key=lambda x: x['relevance_score'], reverse=True)
                
                positions_with_news.append({
                    'market': market,
                    'outcome': position.get('outcome', '?'),
                    'value': position.get('value', 0),
                    'keywords': keywords,
                    'news_count': len(relevant_news),
                    'news': relevant_news[:5]
                })
            
            except Exception as e:
                import traceback
                print(f"[WARN] 处理持仓新闻失败 {market}: {e}")
                print(traceback.format_exc())
                # 返回空新闻但不中断
                positions_with_news.append({
                    'market': market,
                    'outcome': position.get('outcome', '?'),
                    'value': position.get('value', 0),
                    'keywords': {'primary': [], 'secondary': [], 'context': []},
                    'news_count': 0,
                    'news': []
                })
        
        result = {
            'wallet': wallet,
            'pseudonym': whale_dict.get('pseudonym', wallet[:10] + '...'),
            'total_value': whale_dict.get('total_value', 0),
            'hours': hours,
            'positions': positions_with_news,
            'generated_at': datetime.now(timezone.utc).isoformat()
        }
        
        # 更新缓存
        _news_cache[cache_key] = result
        _news_cache_time[cache_key] = now
        
        return jsonify(result)
        
    except Exception as e:
        import traceback
        print(f"[ERROR] 获取鲸鱼新闻失败: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


# ========== 鲸鱼跟随策略 API ==========

@whales_bp.route('/<wallet>/signal', methods=['POST'])
def generate_whale_signal(wallet):
    """
    为指定鲸鱼生成跟随信号
    
    POST /api/whales/<wallet>/signal
    
    Returns:
        跟随信号
    """
    try:
        # 导入鲸鱼跟随策略
        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "06-tools/analysis"))
        from whale_following import WhaleFollowingStrategy
        
        strategy = WhaleFollowingStrategy()
        
        # 获取鲸鱼信息
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM whales WHERE wallet = ?', (wallet,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return jsonify({'error': 'Whale not found'}), 404
        
        # 构建 Whale 对象
        from whale_following import Whale
        whale = Whale(
            wallet=row[1],
            pseudonym=row[2] or row[1][:10] + "...",
            total_value=row[3] or 0,
            win_rate=0.6 if (row[6] or 0) > 0 else 0.4,  # 基于盈亏估算
            sharpe_ratio=min((row[6] or 0) / (row[3] or 1), 2.0),
            strategy_consistency=0.7
        )
        
        # 生成信号
        signal = strategy.detect_large_trade(whale)
        
        if signal:
            return jsonify({
                'success': True,
                'has_signal': True,
                'signal': {
                    'type': signal.type,
                    'market': signal.market,
                    'direction': signal.direction,
                    'confidence': signal.confidence,
                    'suggested_position': signal.suggested_position,
                    'expected_return': signal.expected_return,
                    'risk_level': signal.risk_level,
                    'reasoning': signal.reasoning,
                    'created_at': signal.created_at.isoformat()
                }
            })
        else:
            return jsonify({
                'success': True,
                'has_signal': False,
                'message': 'No significant trade detected in recent hours'
            })
    
    except Exception as e:
        import traceback
        print(f"[ERROR] 生成鲸鱼信号失败: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@whales_bp.route('/signals', methods=['GET'])
def get_whale_signals():
    """
    获取所有鲸鱼跟随信号
    
    GET /api/whales/signals?hours=24&min_confidence=0.7
    
    Returns:
        信号列表
    """
    try:
        hours = request.args.get('hours', 24, type=int)
        min_confidence = request.args.get('min_confidence', 0.7, type=float)
        
        # 导入策略
        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "06-tools/analysis"))
        from whale_following import WhaleFollowingStrategy
        
        strategy = WhaleFollowingStrategy()
        
        # 扫描所有聪明钱鲸鱼
        signals = strategy.scan()
        
        # 过滤低置信度
        filtered_signals = [s for s in signals if s.confidence >= min_confidence]
        
        return jsonify({
            'success': True,
            'count': len(filtered_signals),
            'hours': hours,
            'signals': [
                {
                    'type': s.type,
                    'whallet': s.whale.wallet,
                    'whale_pseudonym': s.whale.pseudonym,
                    'market': s.market,
                    'direction': s.direction,
                    'confidence': s.confidence,
                    'suggested_position': s.suggested_position,
                    'expected_return': s.expected_return,
                    'risk_level': s.risk_level,
                    'reasoning': s.reasoning,
                    'created_at': s.created_at.isoformat()
                }
                for s in filtered_signals
            ]
        })
    
    except Exception as e:
        import traceback
        print(f"[ERROR] 获取鲸鱼信号失败: {str(e)}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
