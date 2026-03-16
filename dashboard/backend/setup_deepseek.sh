#!/bin/bash
# 配置 DeepSeek API Key

echo "🔧 配置 DeepSeek API Key"
echo "========================"
echo ""

# 切换到正确目录
cd "$(dirname "$0")"

# 检查是否已有配置
if [ -f .env ]; then
    echo "发现已有的 .env 文件"
    if grep -q "DEEPSEEK_API_KEY" .env; then
        echo "✅ DeepSeek API Key 已配置"
        grep "DEEPSEEK_API_KEY" .env
        echo ""
        read -p "是否更新? (y/n): " update
        if [ "$update" != "y" ]; then
            echo "保持现有配置"
            exit 0
        fi
    fi
fi

# 输入 API Key
read -sp "请输入 DeepSeek API Key: " api_key
echo ""

if [ -z "$api_key" ]; then
    echo "❌ API Key 不能为空"
    exit 1
fi

# 保存到 .env 文件
if [ -f .env ]; then
    # 删除旧的配置
    sed -i '/DEEPSEEK_API_KEY/d' .env
    sed -i '/LLM_MODEL/d' .env
fi

# 添加新配置
echo "" >> .env
echo "# DeepSeek 配置" >> .env
echo "DEEPSEEK_API_KEY=$api_key" >> .env
echo "LLM_MODEL=deepseek-chat" >> .env

echo ""
echo "✅ 配置已保存到 .env 文件"
echo ""
echo "配置内容:"
grep "DEEPSEEK_API_KEY" .env
grep "LLM_MODEL" .env
echo ""
echo "📝 重启后端服务:"
echo "  cd $(pwd)"
echo "  pkill -f 'python3 run.py'"
echo "  . ../venv/bin/activate"
echo "  python3 run.py"
echo ""
echo "💰 DeepSeek 价格: 约 $0.0002/1K tokens (比 GPT-3.5 便宜 10 倍)"
