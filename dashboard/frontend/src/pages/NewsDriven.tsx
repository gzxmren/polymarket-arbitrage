import React, { useState, useEffect } from 'react';
import { Card, Button, Badge, Row, Col, Table, Tag, Empty, Spin, Alert, Statistic } from 'antd';
import { LineChartOutlined, ReloadOutlined, ThunderboltOutlined, RiseOutlined, FallOutlined } from '@ant-design/icons';
import api from '../services/api';

interface NewsItem {
  title: string;
  source: string;
  published_at: string;
  sentiment: 'positive' | 'negative' | 'neutral';
  relevance_score: number;
  url: string;
}

interface Position {
  market: string;
  outcome: string;
  value: number;
  news: NewsItem[];
  news_count: number;
  keywords: {
    primary: string[];
    secondary: string[];
    context: string[];
  };
}

interface NewsDrivenData {
  wallet: string;
  pseudonym: string;
  total_value: number;
  positions: Position[];
  generated_at: string;
  hours: number;
}

const NewsDriven: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<NewsDrivenData | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 获取新闻驱动数据
  const fetchNewsDriven = async () => {
    setLoading(true);
    setError(null);
    
    try {
      // 获取被关注的鲸鱼
      const whalesResponse = await api.get('/api/whales/', {
        params: { is_watched: 'true', limit: 1 }
      });
      
      if (whalesResponse.data.whales && whalesResponse.data.whales.length > 0) {
        const whale = whalesResponse.data.whales[0];
        
        // 获取该鲸鱼的新闻数据
        const newsResponse = await api.get(`/api/whales/${whale.wallet}/news?hours=24`);
        setData(newsResponse.data);
      } else {
        setError('暂无被关注的鲸鱼');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '获取数据失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchNewsDriven();
  }, []);

  // 情绪标签
  const getSentimentTag = (sentiment: string) => {
    const config = {
      positive: { color: 'green', text: '正面', icon: <RiseOutlined /> },
      negative: { color: 'red', text: '负面', icon: <FallOutlined /> },
      neutral: { color: 'gray', text: '中性', icon: null }
    };
    const conf = config[sentiment as keyof typeof config] || config.neutral;
    return <Tag color={conf.color}>{conf.icon} {conf.text}</Tag>;
  };

  // 相关性标签
  const getRelevanceTag = (score: number) => {
    let color = 'default';
    let text = '低';
    if (score >= 80) {
      color = 'red';
      text = '极高';
    } else if (score >= 60) {
      color = 'orange';
      text = '高';
    } else if (score >= 40) {
      color = 'blue';
      text = '中';
    }
    return <Tag color={color}>{text} ({score}%)</Tag>;
  };

  // 新闻列表列
  const newsColumns = [
    {
      title: '情绪',
      dataIndex: 'sentiment',
      key: 'sentiment',
      width: 80,
      render: (sentiment: string) => getSentimentTag(sentiment)
    },
    {
      title: '标题',
      dataIndex: 'title',
      key: 'title',
      render: (title: string, record: NewsItem) => (
        <div>
          <div style={{ fontWeight: 'bold' }}>{title}</div>
          <div style={{ fontSize: 12, color: '#666', marginTop: 4 }}>
            {record.source} · {new Date(record.published_at).toLocaleString()}
          </div>
        </div>
      )
    },
    {
      title: '相关性',
      dataIndex: 'relevance_score',
      key: 'relevance_score',
      width: 120,
      render: (score: number) => getRelevanceTag(score)
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_: any, record: NewsItem) => (
        <Button type="link" href={record.url} target="_blank">查看</Button>
      )
    }
  ];

  return (
    <div style={{ padding: 24 }}>
      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <ThunderboltOutlined style={{ fontSize: 32 }} />
          新闻驱动策略
        </h1>
        <p style={{ color: '#666', marginTop: 8 }}>
          分析新闻情绪与价格的背离，发现交易机会
        </p>
      </div>

      {/* Error */}
      {error && (
        <Alert
          message={error}
          type="error"
          showIcon
          style={{ marginBottom: 24 }}
          action={
            <Button size="small" onClick={fetchNewsDriven}>
              <ReloadOutlined /> 重试
            </Button>
          }
        />
      )}

      {/* Controls */}
      <div style={{ marginBottom: 24, textAlign: 'right' }}>
        <Button onClick={fetchNewsDriven} loading={loading} icon={<ReloadOutlined />}>
          刷新数据
        </Button>
      </div>

      {/* Loading */}
      {loading && (
        <div style={{ textAlign: 'center', padding: 48 }}>
          <Spin size="large" />
          <p style={{ marginTop: 16, color: '#666' }}>正在加载新闻数据...</p>
        </div>
      )}

      {/* Summary */}
      {data && !loading && (
        <>
          <Card title="鲸鱼概况" style={{ marginBottom: 24 }}>
            <Row gutter={16}>
              <Col span={6}>
                <Statistic title="鲸鱼" value={data.pseudonym || data.wallet} />
              </Col>
              <Col span={6}>
                <Statistic 
                  title="总持仓" 
                  value={`$${(data.total_value || 0).toLocaleString()}`}
                />
              </Col>
              <Col span={6}>
                <Statistic 
                  title="持仓数" 
                  value={data.positions.length}
                  suffix="个"
                />
              </Col>
              <Col span={6}>
                <Statistic 
                  title="新闻数" 
                  value={data.positions.reduce((sum, p) => sum + p.news_count, 0)}
                  suffix="条"
                />
              </Col>
            </Row>
          </Card>

          {/* Positions with News */}
          {data.positions.filter(p => p.news_count > 0).length > 0 ? (
            <Card title="持仓相关新闻">
              {data.positions
                .filter(p => p.news_count > 0)
                .map((position, index) => (
                  <Card
                    key={index}
                    type="inner"
                    title={
                      <div>
                        <LineChartOutlined /> {position.market}
                        <Tag style={{ marginLeft: 8 }}>
                          {position.outcome}
                        </Tag>
                        <Tag color="blue" style={{ marginLeft: 8 }}>
                          ${position.value.toLocaleString()}
                        </Tag>
                      </div>
                    }
                    style={{ marginBottom: 16 }}
                  >
                    {/* Keywords */}
                    <div style={{ marginBottom: 12 }}>
                      <strong>关键词：</strong>
                      {position.keywords.primary.map((kw, i) => (
                        <Tag key={i} color="blue">{kw}</Tag>
                      ))}
                      {position.keywords.secondary.map((kw, i) => (
                        <Tag key={i}>{kw}</Tag>
                      ))}
                    </div>

                    {/* News Table */}
                    <Table
                      columns={newsColumns}
                      dataSource={position.news}
                      rowKey={(record, idx) => `${idx}-${record.url}`}
                      pagination={false}
                      size="small"
                    />
                  </Card>
                ))}
            </Card>
          ) : (
            <Card>
              <Empty
                description="暂无相关新闻"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
              <div style={{ textAlign: 'center', color: '#666', marginTop: 8 }}>
                当前持仓没有匹配到相关新闻
              </div>
            </Card>
          )}
        </>
      )}

      {/* Empty State */}
      {!loading && !data && !error && (
        <Card>
          <Empty
            description="暂无数据"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
          <div style={{ textAlign: 'center', color: '#666', marginTop: 8 }}>
            点击刷新按钮加载数据
          </div>
        </Card>
      )}
    </div>
  );
};

export default NewsDriven;
