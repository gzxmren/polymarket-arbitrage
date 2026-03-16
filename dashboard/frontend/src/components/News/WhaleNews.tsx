import React, { useState, useEffect } from 'react';
import { Card, List, Tag, Spin, Empty, Button, Badge, Tooltip } from 'antd';
import { 
  ClockCircleOutlined, 
  LinkOutlined, 
  FireOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined,
  MinusOutlined
} from '@ant-design/icons';
import api from '../../services/api';

interface NewsItem {
  source: string;
  title: string;
  url: string;
  published_at: string;
  sentiment: 'positive' | 'negative' | 'neutral';
  relevance_score: number;
  matched_keywords: string[];
}

interface PositionWithNews {
  market: string;
  outcome: string;
  value: number;
  keywords: {
    primary: string[];
    secondary: string[];
    context: string[];
  };
  news_count: number;
  news: NewsItem[];
}

interface WhaleNewsData {
  wallet: string;
  pseudonym: string;
  total_value: number;
  hours: number;
  positions: PositionWithNews[];
  generated_at: string;
}

interface WhaleNewsProps {
  wallet: string;
}

const WhaleNews: React.FC<WhaleNewsProps> = ({ wallet }) => {
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<WhaleNewsData | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchNews = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get(`/api/whales/${wallet}/news?hours=6`);
      setData(res.data);
    } catch (err: any) {
      setError(err.response?.data?.error || '获取新闻失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (wallet) {
      fetchNews();
    }
  }, [wallet]);

  const formatTime = (isoTime: string) => {
    const date = new Date(isoTime);
    const now = new Date();
    const diff = (now.getTime() - date.getTime()) / 1000 / 60; // minutes
    
    if (diff < 60) {
      return `${Math.floor(diff)}分钟前`;
    } else if (diff < 1440) {
      return `${Math.floor(diff / 60)}小时前`;
    } else {
      return date.toLocaleDateString('zh-CN');
    }
  };

  const getSentimentIcon = (sentiment: string) => {
    switch (sentiment) {
      case 'positive':
        return <ArrowUpOutlined style={{ color: '#52c41a' }} />;
      case 'negative':
        return <ArrowDownOutlined style={{ color: '#f5222d' }} />;
      default:
        return <MinusOutlined style={{ color: '#8c8c8c' }} />;
    }
  };

  const getSentimentTag = (sentiment: string) => {
    const config = {
      positive: { color: 'success', text: '正面' },
      negative: { color: 'error', text: '负面' },
      neutral: { color: 'default', text: '中性' }
    };
    const { color, text } = config[sentiment as keyof typeof config] || config.neutral;
    return <Tag color={color}>{text}</Tag>;
  };

  const getRelevanceColor = (score: number) => {
    if (score >= 80) return '#f5222d';  // 高关联 - 红色
    if (score >= 60) return '#faad14';  // 中关联 - 橙色
    return '#8c8c8c';  // 低关联 - 灰色
  };

  if (loading) {
    return (
      <Card title="📰 持仓相关新闻" extra={<Spin size="small" />}>
        <div style={{ textAlign: 'center', padding: '40px' }}>
          <Spin size="large" />
          <p style={{ marginTop: 16, color: '#8c8c8c' }}>正在抓取相关新闻...</p>
        </div>
      </Card>
    );
  }

  if (error) {
    return (
      <Card title="📰 持仓相关新闻">
        <Empty
          description={error}
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
        <div style={{ textAlign: 'center', marginTop: 16 }}>
          <Button onClick={fetchNews}>重试</Button>
        </div>
      </Card>
    );
  }

  if (!data || data.positions.length === 0) {
    return (
      <Card title="📰 持仓相关新闻">
        <Empty description="暂无相关新闻" />
      </Card>
    );
  }

  const totalNews = data.positions.reduce((sum, p) => sum + p.news_count, 0);

  return (
    <Card 
      title={
        <span>
          📰 持仓相关新闻
          <Badge 
            count={totalNews} 
            style={{ marginLeft: 8, backgroundColor: '#1890ff' }}
          />
        </span>
      }
      extra={
        <span style={{ color: '#8c8c8c', fontSize: 12 }}>
          <ClockCircleOutlined /> 最近{data.hours}小时
          <Button 
            size="small" 
            style={{ marginLeft: 8 }}
            onClick={fetchNews}
          >
            刷新
          </Button>
        </span>
      }
    >
      {data.positions.map((position, index) => (
        <div key={index} style={{ marginBottom: 24 }}>
          {/* 市场标题 */}
          <div style={{ 
            padding: '12px 16px', 
            background: '#f6ffed', 
            borderRadius: 8,
            marginBottom: 12
          }}>
            <div style={{ fontWeight: 'bold', marginBottom: 4 }}>
              {position.market}
            </div>
            <div style={{ fontSize: 12, color: '#8c8c8c' }}>
              <span style={{ marginRight: 16 }}>
                💰 持仓: ${position.value?.toLocaleString() || 0}
              </span>
              <span>
                🔍 关键词: {position.keywords.primary.join(', ')}
              </span>
              {position.news_count > 0 && (
                <Badge 
                  count={position.news_count} 
                  style={{ marginLeft: 8 }}
                />
              )}
            </div>
          </div>

          {/* 新闻列表 */}
          {position.news.length > 0 ? (
            <List
              size="small"
              dataSource={position.news}
              renderItem={(news) => (
                <List.Item
                  style={{ 
                    padding: '8px 0',
                    borderBottom: '1px solid #f0f0f0'
                  }}
                >
                  <div style={{ width: '100%' }}>
                    {/* 标题行 */}
                    <div style={{ marginBottom: 4 }}>
                      <a 
                        href={news.url} 
                        target="_blank" 
                        rel="noopener noreferrer"
                        style={{ 
                          color: '#1890ff',
                          fontSize: 14
                        }}
                      >
                        {news.title}
                      </a>
                    </div>
                    
                    {/* 元信息行 */}
                    <div style={{ 
                      fontSize: 12, 
                      color: '#8c8c8c',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 12
                    }}>
                      {/* 来源 */}
                      <Tag>{news.source}</Tag>
                      
                      {/* 时间 */}
                      <Tooltip title={new Date(news.published_at).toLocaleString('zh-CN')}>
                        <span>
                          <ClockCircleOutlined style={{ marginRight: 4 }} />
                          {formatTime(news.published_at)}
                        </span>
                      </Tooltip>
                      
                      {/* 情绪 */}
                      <span>
                        {getSentimentIcon(news.sentiment)}
                        {getSentimentTag(news.sentiment)}
                      </span>
                      
                      {/* 关联度 */}
                      <Tooltip title="关联度评分">
                        <span style={{ 
                          color: getRelevanceColor(news.relevance_score),
                          fontWeight: 'bold'
                        }}>
                          <FireOutlined style={{ marginRight: 4 }} />
                          {news.relevance_score}%
                        </span>
                      </Tooltip>
                      
                      {/* 匹配关键词 */}
                      {news.matched_keywords && news.matched_keywords.length > 0 && (
                        <span>
                          🔑 {news.matched_keywords.join(', ')}
                        </span>
                      )}
                    </div>
                  </div>
                </List.Item>
              )}
            />
          ) : (
            <Empty 
              description="暂无相关新闻" 
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              style={{ margin: '16px 0' }}
            />
          )}
        </div>
      ))}
    </Card>
  );
};

export default WhaleNews;