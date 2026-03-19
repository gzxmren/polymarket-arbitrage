import React, { useState, useEffect, useCallback } from 'react';
import { Card, Tabs, Table, Tag, Button, Empty, Statistic, Row, Col, Badge, Spin, Alert } from 'antd';
import { ArrowUpOutlined, ArrowDownOutlined, ReloadOutlined } from '@ant-design/icons';
import { getPairCostOpportunities, getCrossMarketOpportunities } from '../services/api';

const REFRESH_INTERVAL = 300000; // 300秒 = 5分钟

const Arbitrage: React.FC = () => {
  const [pairCostData, setPairCostData] = useState<any[]>([]);
  const [crossMarketData, setCrossMarketData] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchArbitrageData = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true);
    setError(null);
    
    try {
      const [pairCostRes, crossMarketRes] = await Promise.all([
        getPairCostOpportunities(),
        getCrossMarketOpportunities(),
      ]);
      
      // 处理 Pair Cost 数据
      const pairCostOpportunities = pairCostRes.data?.data || pairCostRes.data?.opportunities || [];
      setPairCostData(pairCostOpportunities);
      
      // 处理跨平台套利数据
      const crossMarketOpportunities = crossMarketRes.data?.data || crossMarketRes.data?.opportunities || [];
      setCrossMarketData(crossMarketOpportunities);
      
      setLastUpdated(new Date());
    } catch (err: any) {
      console.error('Failed to fetch arbitrage data:', err);
      setError(err.message || '获取数据失败');
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchArbitrageData();
    
    // 设置自动刷新（300秒）
    const intervalId = setInterval(() => {
      fetchArbitrageData(false);
    }, REFRESH_INTERVAL);
    
    return () => clearInterval(intervalId);
  }, [fetchArbitrageData]);

  const handleManualRefresh = () => {
    fetchArbitrageData(true);
  };

  const pairCostColumns = [
    {
      title: '市场',
      dataIndex: 'market',
      key: 'market',
      render: (text: string) => <strong>{text}</strong>,
    },
    {
      title: 'YES价格',
      dataIndex: 'yes_price',
      key: 'yes_price',
      render: (price: number) => `$${price?.toFixed(3) || 0}`,
    },
    {
      title: 'NO价格',
      dataIndex: 'no_price',
      key: 'no_price',
      render: (price: number) => `$${price?.toFixed(3) || 0}`,
    },
    {
      title: 'Pair Cost',
      dataIndex: 'pair_cost',
      key: 'pair_cost',
      render: (cost: number) => `$${cost?.toFixed(4) || 0}`,
    },
    {
      title: '利润空间',
      dataIndex: 'profit_pct',
      key: 'profit_pct',
      render: (profit: number) => (
        <Tag color={profit > 2 ? 'red' : profit > 1 ? 'orange' : 'green'}>
          <ArrowUpOutlined /> {profit?.toFixed(2) || 0}%
        </Tag>
      ),
    },
    {
      title: '流动性',
      dataIndex: 'liquidity',
      key: 'liquidity',
      render: (value: number) => `$${value?.toLocaleString() || 0}`,
    },
    {
      title: '检测时间',
      dataIndex: 'detected_at',
      key: 'detected_at',
      render: (time: string) => {
        if (!time) return '-';
        const date = new Date(time);
        return date.toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
      },
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: any) => (
        <Button 
          type="primary" 
          size="small" 
          href={`https://polymarket.com/event/${record.slug || record.market_id}`} 
          target="_blank"
        >
          前往交易
        </Button>
      ),
    },
  ];

  const crossMarketColumns = [
    {
      title: '事件',
      dataIndex: 'event_name',
      key: 'event_name',
      render: (text: string) => <strong style={{ fontSize: 13 }}>{text}</strong>,
    },
    {
      title: 'Polymarket',
      dataIndex: 'polymarket_price',
      key: 'polymarket_price',
      render: (price: number) => <Tag color="purple">{price?.toFixed(1) || 0}%</Tag>,
    },
    {
      title: 'Manifold',
      dataIndex: 'manifold_price',
      key: 'manifold_price',
      render: (price: number) => <Tag color="blue">{price?.toFixed(1) || 0}%</Tag>,
    },
    {
      title: '价差',
      dataIndex: 'gap',
      key: 'gap',
      render: (gap: number) => (
        <Tag color={gap > 15 ? 'red' : gap > 10 ? 'orange' : 'blue'}>
          <ArrowDownOutlined /> {gap?.toFixed(1) || 0}%
        </Tag>
      ),
    },
    {
      title: '预期收益',
      dataIndex: 'expected_return',
      key: 'expected_return',
      render: (ret: number) => (
        <Tag color="green">
          <ArrowUpOutlined /> +{ret?.toFixed(1) || 0}%
        </Tag>
      ),
    },
    {
      title: '匹配度',
      dataIndex: 'match_rate',
      key: 'match_rate',
      render: (rate: number, record: any) => {
        const color = rate >= 70 ? 'green' : rate >= 50 ? 'orange' : 'red';
        const warning = rate < 60 ? '⚠️ 需人工确认' : '';
        return (
          <div>
            <Tag color={color}>{rate?.toFixed(0) || 0}%</Tag>
            {warning && <div style={{ fontSize: 11, color: '#faad14', marginTop: 4 }}>{warning}</div>}
          </div>
        );
      },
    },
    {
      title: '风险',
      dataIndex: 'risk_level',
      key: 'risk_level',
      render: (level: string, record: any) => {
        const color = level === 'LOW' ? 'green' : level === 'MEDIUM' ? 'orange' : 'red';
        return <Tag color={color}>{level} ({record.risk_score?.toFixed(0) || 0}%)</Tag>;
      },
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: any) => (
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          <Button 
            type="primary" 
            size="small" 
            href={record.polymarket_url} 
            target="_blank"
          >
            Polymarket
          </Button>
          <Button 
            size="small" 
            href={record.manifold_url} 
            target="_blank"
          >
            Manifold
          </Button>
        </div>
      ),
    },
  ];

  const tabItems = [
    {
      key: 'pair-cost',
      label: (
        <span>
          Pair Cost 套利
          {pairCostData.length > 0 && (
            <Badge count={pairCostData.length} style={{ marginLeft: 8 }} color="#52c41a" />
          )}
        </span>
      ),
      children: (
        <>
          <Row gutter={16} style={{ marginBottom: 24 }}>
            <Col span={8}>
              <Statistic title="当前机会" value={pairCostData.length} suffix="个" />
            </Col>
            <Col span={8}>
              <Statistic 
                title="最高利润" 
                value={pairCostData.length > 0 ? Math.max(...pairCostData.map(d => d.profit_pct || 0)) : 0} 
                suffix="%" 
                precision={2}
              />
            </Col>
            <Col span={8}>
              <Statistic title="阈值" value={0.995} suffix="Pair Cost" />
            </Col>
          </Row>
          {pairCostData.length > 0 ? (
            <Table 
              columns={pairCostColumns} 
              dataSource={pairCostData} 
              rowKey="id"
              loading={loading}
              pagination={{ pageSize: 20 }}
            />
          ) : (
            <Empty description={loading ? <Spin /> : "当前无 Pair Cost 套利机会"} />
          )}
        </>
      ),
    },
    {
      key: 'cross-market',
      label: (
        <span>
          跨平台套利
          {crossMarketData.length > 0 && (
            <Badge count={crossMarketData.length} style={{ marginLeft: 8 }} color="#1890ff" />
          )}
        </span>
      ),
      children: (
        <>
          <Row gutter={16} style={{ marginBottom: 24 }}>
            <Col span={8}>
              <Statistic title="当前机会" value={crossMarketData.length} suffix="个" />
            </Col>
            <Col span={8}>
              <Statistic 
                title="最大价差" 
                value={crossMarketData.length > 0 ? Math.max(...crossMarketData.map(d => d.gap || 0)) : 0} 
                suffix="%" 
                precision={1}
              />
            </Col>
            <Col span={8}>
              <Statistic title="阈值" value={5} suffix="% 价差" />
            </Col>
          </Row>
          {crossMarketData.length > 0 ? (
            <Table 
              columns={crossMarketColumns} 
              dataSource={crossMarketData} 
              rowKey="id"
              loading={loading}
              pagination={{ pageSize: 20 }}
            />
          ) : (
            <Empty description={loading ? <Spin /> : "当前无跨平台套利机会"} />
          )}
        </>
      ),
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h1>🎯 套利机会</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          {lastUpdated && (
            <span style={{ color: '#999', fontSize: 14 }}>
              更新于: {lastUpdated.toLocaleTimeString('zh-CN')}
            </span>
          )}
          <Button 
            icon={<ReloadOutlined />} 
            onClick={handleManualRefresh}
            loading={loading}
          >
            刷新
          </Button>
        </div>
      </div>
      
      {error && (
        <Alert 
          message="获取数据失败" 
          description={error} 
          type="error" 
          showIcon 
          style={{ marginBottom: 16 }}
          closable
          onClose={() => setError(null)}
        />
      )}
      
      <Card>
        <Tabs items={tabItems} />
      </Card>
    </div>
  );
};

export default Arbitrage;
