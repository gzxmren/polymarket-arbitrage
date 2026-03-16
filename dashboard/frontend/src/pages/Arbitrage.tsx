import React, { useState, useEffect } from 'react';
import { Card, Tabs, Table, Tag, Button, Empty, Statistic, Row, Col } from 'antd';
import { ArrowUpOutlined, ArrowDownOutlined } from '@ant-design/icons';
import { getPairCostOpportunities, getCrossMarketOpportunities } from '../services/api';

const Arbitrage: React.FC = () => {
  const [pairCostData, setPairCostData] = useState<any[]>([]);
  const [crossMarketData, setCrossMarketData] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetchArbitrageData();
  }, []);

  const fetchArbitrageData = async () => {
    setLoading(true);
    try {
      const [pairCostRes, crossMarketRes] = await Promise.all([
        getPairCostOpportunities(),
        getCrossMarketOpportunities(),
      ]);
      setPairCostData(pairCostRes.data.opportunities || []);
      setCrossMarketData(crossMarketRes.data.opportunities || []);
    } catch (error) {
      console.error('Failed to fetch arbitrage data:', error);
    } finally {
      setLoading(false);
    }
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
        <Tag color="green">
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
      title: '操作',
      key: 'action',
      render: (_: any, record: any) => (
        <Button type="primary" size="small" href={`https://polymarket.com/event/${record.slug}`} target="_blank">
          前往交易
        </Button>
      ),
    },
  ];

  const crossMarketColumns = [
    {
      title: '事件',
      dataIndex: 'question',
      key: 'question',
      render: (text: string) => <strong>{text}</strong>,
    },
    {
      title: 'Polymarket',
      dataIndex: 'polymarket',
      key: 'polymarket',
      render: (price: number) => `${(price * 100)?.toFixed(1) || 0}%`,
    },
    {
      title: 'Manifold',
      dataIndex: 'manifold',
      key: 'manifold',
      render: (price: number) => `${(price * 100)?.toFixed(1) || 0}%`,
    },
    {
      title: '价差',
      dataIndex: 'gap',
      key: 'gap',
      render: (gap: number) => (
        <Tag color={gap > 0.1 ? 'red' : 'orange'}>
          <ArrowDownOutlined /> {(gap * 100)?.toFixed(1) || 0}%
        </Tag>
      ),
    },
    {
      title: '匹配度',
      dataIndex: 'similarity',
      key: 'similarity',
      render: (sim: number) => (
        <Tag color={sim >= 0.7 ? 'green' : sim >= 0.5 ? 'orange' : 'red'}>
          {(sim * 100)?.toFixed(0) || 0}%
        </Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: any) => (
        <Button type="primary" size="small" href={record.polymarket_url} target="_blank">
          Polymarket
        </Button>
      ),
    },
  ];

  const tabItems = [
    {
      key: 'pair-cost',
      label: 'Pair Cost 套利',
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
              rowKey="market"
              loading={loading}
            />
          ) : (
            <Empty description="当前无 Pair Cost 套利机会" />
          )}
        </>
      ),
    },
    {
      key: 'cross-market',
      label: '跨平台套利',
      children: (
        <>
          <Row gutter={16} style={{ marginBottom: 24 }}>
            <Col span={8}>
              <Statistic title="当前机会" value={crossMarketData.length} suffix="个" />
            </Col>
            <Col span={8}>
              <Statistic 
                title="最大价差" 
                value={crossMarketData.length > 0 ? Math.max(...crossMarketData.map(d => d.gap || 0)) * 100 : 0} 
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
              rowKey="question"
              loading={loading}
            />
          ) : (
            <Empty description="当前无跨平台套利机会" />
          )}
        </>
      ),
    },
  ];

  return (
    <div>
      <h1 style={{ marginBottom: 24 }}>🎯 套利机会</h1>
      <Card>
        <Tabs items={tabItems} />
      </Card>
    </div>
  );
};

export default Arbitrage;
