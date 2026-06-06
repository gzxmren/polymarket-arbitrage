#!/usr/bin/env python3
"""
鲸鱼深度分析服务（LLM）
用户手动触发，结果缓存24小时
"""

import json
import hashlib
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

# 导入实时分析器获取数据
from .whale_analyzer import WhaleAnalyzer


class WhaleDeepAnalyzer:
    """鲸鱼深度分析服务（LLM）"""
    
    def __init__(self):
        self.analyzer = WhaleAnalyzer()
        self.db = self.analyzer.db
        
        # LLM API 配置（从环境变量读取）
        # 支持 OpenAI 和 DeepSeek
        self._load_config()
    
    def _load_config(self):
        """加载配置（支持运行时重新加载）"""
        import os
        
        self.deepseek_api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.openai_api_key = os.getenv("OPENAI_API_KEY", "")
        
        # 默认模型
        if self.deepseek_api_key:
            self.default_model = os.getenv("LLM_MODEL", "deepseek-chat")
            self.api_base = "https://api.deepseek.com/v1"
            self.api_key = self.deepseek_api_key
        elif self.openai_api_key:
            self.default_model = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
            self.api_base = None  # 使用 OpenAI 默认
            self.api_key = self.openai_api_key
        else:
            self.default_model = "mock-llm"
            self.api_base = None
            self.api_key = None
    
    def get_deep_analysis(self, wallet: str, force_refresh: bool = False) -> Dict:
        """
        获取深度分析
        
        Args:
            wallet: 鲸鱼钱包地址
            force_refresh: 是否强制刷新缓存
            
        Returns:
            {
                "wallet": str,
                "content": str,
                "model": str,
                "generated_at": str,
                "cost": float,
                "from_cache": bool,
                "error": str (可选)
            }
        """
        # 1. 获取实时数据
        realtime_data = self.analyzer.analyze_whale(wallet)
        if "error" in realtime_data:
            return {"error": realtime_data["error"]}
        
        # 2. 计算数据哈希
        data_hash = self._calculate_data_hash(realtime_data)
        
        # 3. 检查缓存（除非强制刷新）
        if not force_refresh:
            cached = self._get_cached_analysis(wallet, data_hash)
            if cached:
                cached["from_cache"] = True
                return cached
        
        # 4. 没有缓存或强制刷新，调用 LLM
        try:
            # 重新加载配置（确保环境变量已加载）
            self._load_config()
            result = self._call_llm(wallet, realtime_data)
            
            # 5. 保存缓存
            self._save_analysis(wallet, result, data_hash)
            
            result["from_cache"] = False
            return result
            
        except Exception as e:
            return {
                "error": f"LLM调用失败: {str(e)}",
                "wallet": wallet
            }
    
    def _calculate_data_hash(self, data: Dict) -> str:
        """计算数据哈希，用于缓存判断"""
        # 提取关键字段
        key_fields = {
            "total_value": data.get("pnl_status", {}).get("value", 0),
            "position_count": len(data.get("dimensions", {}).get("fund_strength", {}).get("desc", "")),
            "top5_ratio": data.get("strategy_assessment", {}).get("top5_ratio", 0),
            "changes_count": data.get("signal_strength", {}).get("changes_count", 0),
            "composite_score": data.get("composite_score", 0)
        }
        
        # 计算哈希
        hash_str = json.dumps(key_fields, sort_keys=True)
        return hashlib.md5(hash_str.encode()).hexdigest()[:16]
    
    def _get_cached_analysis(self, wallet: str, data_hash: str) -> Optional[Dict]:
        """获取缓存的深度分析"""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT wallet, content, model, generated_at, cost, data_hash
            FROM whale_deep_analysis
            WHERE wallet = ? AND data_hash = ? AND expires_at > ?
        ''', (wallet, data_hash, datetime.now(timezone.utc).isoformat()))
        
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return {
                "wallet": row["wallet"],
                "content": row["content"],
                "model": row["model"],
                "generated_at": row["generated_at"],
                "cost": row["cost"],
                "data_hash": row["data_hash"]
            }
        
        return None
    
    def _save_analysis(self, wallet: str, analysis: Dict, data_hash: str):
        """保存深度分析到缓存"""
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        
        cursor.execute('''
            INSERT OR REPLACE INTO whale_deep_analysis
            (wallet, content, model, generated_at, data_hash, cost, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            wallet,
            analysis["content"],
            analysis["model"],
            analysis["generated_at"],
            data_hash,
            analysis.get("cost", 0),
            expires_at.isoformat()
        ))
        
        conn.commit()
        conn.close()
    
    def _call_llm(self, wallet: str, data: Dict) -> Dict:
        """调用 LLM 生成深度分析"""
        # 构建 prompt
        prompt = self._build_prompt(wallet, data)
        
        print(f"[DEBUG] _call_llm: deepseek_api_key={self.deepseek_api_key[:20]}..." if self.deepseek_api_key else "[DEBUG] _call_llm: deepseek_api_key=NOT SET")
        print(f"[DEBUG] _call_llm: openai_api_key={self.openai_api_key[:20]}..." if self.openai_api_key else "[DEBUG] _call_llm: openai_api_key=NOT SET")
        
        # 根据配置的 API 调用
        if self.deepseek_api_key:
            print("[DEBUG] Calling DeepSeek API...")
            return self._call_deepseek(prompt)
        elif self.openai_api_key:
            print("[DEBUG] Calling OpenAI API...")
            return self._call_openai(prompt)
        else:
            print("[DEBUG] No API key, using mock response")
            # 没有 API Key，返回模拟数据（开发测试用）
            return self._mock_llm_response(wallet, data)
    
    def _build_prompt(self, wallet: str, data: Dict) -> str:
        """构建 LLM prompt"""
        wallet_short = wallet[:10] + "..." if len(wallet) > 10 else wallet
        
        return f"""你是一位专业的预测市场分析师。请分析以下鲸鱼交易者的行为和策略：

【基本信息】
- 钱包: {wallet_short}
- 持仓价值: ${data.get('pnl_status', {}).get('value', 0):,}
- 信号强度: {data.get('signal_strength', {}).get('desc', '未知')} ({data.get('signal_strength', {}).get('changes_count', 0)}次变动)
- 策略判断: {data.get('strategy_assessment', {}).get('concentration_level', '未知')} (Top5占{data.get('strategy_assessment', {}).get('top5_ratio', 0):.1%})
- 盈亏状态: {data.get('pnl_status', {}).get('emoji', '')} ${data.get('pnl_status', {}).get('value', 0):+,.0f} ({data.get('pnl_status', {}).get('percent', 0):.1f}%)
- 综合评分: {data.get('composite_score', 0)}/100
- 跟单评分: {data.get('copy_score', 0)}/100

【维度评分】
- 资金实力: {data.get('dimensions', {}).get('fund_strength', {}).get('score', 0)}分 - {data.get('dimensions', {}).get('fund_strength', {}).get('desc', '')}
- 活跃度: {data.get('dimensions', {}).get('activity', {}).get('score', 0)}分 - {data.get('dimensions', {}).get('activity', {}).get('desc', '')}
- 策略明确: {data.get('dimensions', {}).get('concentration', {}).get('score', 0)}分 - {data.get('dimensions', {}).get('concentration', {}).get('desc', '')}
- 盈利能力: {data.get('dimensions', {}).get('profitability', {}).get('score', 0)}分 - {data.get('dimensions', {}).get('profitability', {}).get('desc', '')}

【程序解读】
{data.get('interpretation', '暂无')}

【风险提示】
{data.get('risk_warning', '暂无')}

请提供以下深度分析：

## 1. 策略深度解读
这位鲸鱼的交易策略是什么？集中在哪些主题/事件？看多还是看空？

## 2. 信号可靠性评估
基于活跃度和集中度，这个信号的可靠度如何？为什么？

## 3. 具体操作建议
如果我要跟随这位鲸鱼，具体应该怎么操作？仓位建议？

## 4. 关键风险提示
除了程序识别的风险，还有什么潜在风险需要注意？

## 5. 对比同类鲸鱼
与一般的鲸鱼相比，这位有什么独特之处？

要求：
- 分析要具体，基于提供的数据
- 给出明确的操作建议
- 指出关键风险点
- 语言简洁专业
- 总字数控制在400字以内"""
    
    def _call_openai(self, prompt: str) -> Dict:
        """调用 OpenAI API"""
        import openai
        
        openai.api_key = self.openai_api_key
        
        response = openai.ChatCompletion.create(
            model=self.default_model,
            messages=[
                {"role": "system", "content": "你是一位专业的预测市场分析师，擅长分析鲸鱼交易行为。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=800
        )
        
        content = response.choices[0].message.content
        
        # 估算成本
        tokens = response.usage.total_tokens
        cost = tokens * 0.002 / 1000  # GPT-3.5 价格
        
        return {
            "content": content,
            "model": self.default_model,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cost": round(cost, 4)
        }
    
    def _call_deepseek(self, prompt: str) -> Dict:
        """调用 DeepSeek API"""
        import openai
        
        # 禁用代理
        import os
        os.environ['HTTP_PROXY'] = ''
        os.environ['HTTPS_PROXY'] = ''
        os.environ['ALL_PROXY'] = ''
        os.environ['http_proxy'] = ''
        os.environ['https_proxy'] = ''
        os.environ['all_proxy'] = ''
        
        # DeepSeek API 与 OpenAI 兼容
        client = openai.OpenAI(
            api_key=self.deepseek_api_key,
            base_url="https://api.deepseek.com"
        )
        
        response = client.chat.completions.create(
            model=self.default_model,  # deepseek-chat 或 deepseek-coder
            messages=[
                {"role": "system", "content": "你是一位专业的预测市场分析师，擅长分析鲸鱼交易行为。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=800
        )
        
        content = response.choices[0].message.content
        
        # 估算成本（DeepSeek 价格约为 GPT-3.5 的 1/10）
        tokens = response.usage.total_tokens
        cost = tokens * 0.0002 / 1000  # DeepSeek 约 $0.0002/1K tokens
        
        return {
            "content": content,
            "model": f"deepseek-{self.default_model}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cost": round(cost, 4)
        }
    
    def _mock_llm_response(self, wallet: str, data: Dict) -> Dict:
        """模拟 LLM 响应（开发测试用）"""
        wallet_short = wallet[:10] + "..." if len(wallet) > 10 else wallet        
        # 根据数据生成模拟分析
        signal = data.get('signal_strength', {}).get('desc', '中等')
        concentration = data.get('strategy_assessment', {}).get('concentration_level', '分散')
        pnl = data.get('pnl_status', {}).get('value', 0)
        
        content = f"""## 1. 策略深度解读

该鲸鱼持仓{'高度' if concentration == '高度集中' else '较为'}集中，显示其对{'特定市场' if concentration == '高度集中' else '多个方向'}有明确判断。

## 2. 信号可靠性评估

信号强度为{signal}，可靠度{'较高' if signal in ['极强', '强'] else '中等'}。基于近期{data.get('signal_strength', {}).get('changes_count', 0)}次变动，{'活跃度高，值得重点关注' if signal in ['极强', '强'] else '活跃度一般，建议观察'}。

## 3. 具体操作建议

- 建议仓位：小仓位试探（总资金5-10%）
- 跟随策略：等待其进一步收敛后再加仓
- 止损设置：-15%

## 4. 关键风险提示

- 持仓过度集中风险
- 单一事件黑天鹅风险
- 市场流动性风险

## 5. 对比同类鲸鱼

与同类鲸鱼相比，该交易者{'活跃度更高' if signal in ['极强', '强'] else '较为稳健'}，{'策略更为明确' if concentration == '高度集中' else '仍在探索方向'}。

---
*注：此为模拟分析，用于开发测试。配置 OPENAI_API_KEY 后可获得真实 AI 分析。*"""
        
        return {
            "content": content,
            "model": "mock-llm",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cost": 0.0
        }


# 全局深度分析器实例
deep_analyzer = WhaleDeepAnalyzer()
