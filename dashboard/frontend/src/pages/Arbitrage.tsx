import React, { useState, useEffect, useCallback } from 'react';
import { Card, Tabs, Table, Tag, Button, Empty, Statistic, Row, Col, Badge, Spin, Alert, Modal, Radio, Input, message } from 'antd';
import { ArrowUpOutlined, ArrowDownOutlined, ReloadOutlined, CheckCircleOutlined, CloseCircleOutlined, SwapOutlined } from '@ant-design/icons';
import { getPairCostOpportunities, getCrossMarketOpportunities } from '../services/api';
import axios from 'axios';

const API_BASE_URL = process.env.REACT_APP_API_URL || 'http://localhost:5000';

const REFRESH_INTERVAL = 300000; // 300秒 = 5分钟

const Arbitrage: React.FC = () => {
  const [pairCostData, setPairCostData] = useState<any[]>([]);
  const [crossMarketData, setCrossMarketData] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  
  // 反馈模态框状态
  const [feedbackModalVisible, setFeedbackModalVisible] = useState(false);
  const [feedbackType, setFeedbackType] = useState<'confirmed' | 'error'>('confirmed');
  const [feedbackText, setFeedbackText] = useState('');
  const [userNotes, setUserNotes] = useState('');
  const [currentRecord, setCurrentRecord] = useState<any>(null);
  const [currentArbitrageType, setCurrentArbitrageType] = useState<'pair_cost' | 'cross_market'>('pair_cost');
  const [submittingFeedback, setSubmittingFeedback] = useState(false);
  
  // 对比模态框状态
  const [compareModalVisible, setCompareModalVisible] = useState(false);
  const [compareData, setCompareData] = useState<any>(null);
  const [comparing, setComparing] = useState(false);

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

  // 打开反馈模态框
  const openFeedbackModal = (record: any, type: 'pair_cost' | 'cross_market') => {
    setCurrentRecord(record);
    setCurrentArbitrageType(type);
    setFeedbackType('confirmed');
    setFeedbackText('');
    setUserNotes('');
    setFeedbackModalVisible(true);
  };

  // 提交反馈
  const submitFeedback = async () => {
    if (!currentRecord) return;
    
    setSubmittingFeedback(true);
    try {
      const response = await axios.post(`${API_BASE_URL}/api/arbitrage/feedback`, {
        arbitrage_id: currentRecord.id,
        arbitrage_type: currentArbitrageType,
        feedback_type: feedbackType,
        feedback_text: feedbackText,
        user_notes: userNotes
      });
      
      if (response.data.success) {
        message.success(`反馈提交成功: ${feedbackType === 'confirmed' ? '确认有效' : '标记错误'}`);
        setFeedbackModalVisible(false);
        // 刷新数据
        fetchArbitrageData(false);
      } else {
        message.error(response.data.error || '提交失败');
      }
    } catch (err: any) {
      message.error(err.response?.data?.error || '提交反馈失败');
    } finally {
      setSubmittingFeedback(false);
    }
  };

  // 打开对比模态框
  const openCompareModal = async (record: any, type: 'pair_cost' | 'cross_market') => {
    setCurrentRecord(record);
    setCurrentArbitrageType(type);
    setCompareModalVisible(true);
    setComparing(true);
    
    try {
      const response = await axios.post(`${API_BASE_URL}/api/arbitrage/compare`, {
        arbitrage_type: type,
        market_id: record.market_id || record.slug,
        event_name: record.event_name || record.market
      });
      
      if (response.data.success) {
        setCompareData(response.data.data);
      } else {
        message.error(response.data.error || '对比失败');
      }
    } catch (err: any) {
      message.error(err.response?.data?.error || '获取对比数据失败');
    } finally {
      setComparing(false);
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
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          <Button 
            type="primary" 
            size="small" 
            href={`https://polymarket.com/event/${record.slug || record.market_id}`} 
            target="_blank"
          >
            交易
          </Button>
          <Button 
            size="small" 
            icon={<SwapOutlined />}
            onClick={() => openCompareModal(record, 'pair_cost')}
          >
            对比
          </Button>
          <Button 
            size="small"
            icon={<CheckCircleOutlined />}
            style={{ color: '#52c41a' }}
            onClick={() => openFeedbackModal(record, 'pair_cost')}
          >
            确认
          </Button>
        </div>
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
          <Button 
            size="small" 
            icon={<SwapOutlined />}
            onClick={() => openCompareModal(record, 'cross_market')}
          >
            对比
          </Button>
          <Button 
            size="small"
            icon={<CheckCircleOutlined />}
            style={{ color: '#52c41a' }}
            onClick={() => openFeedbackModal(record, 'cross_market')}
          >
            确认
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
      
      {/* 反馈模态框 */}
      <Modal
        title="套利确认反馈"
        open={feedbackModalVisible}
        onOk={submitFeedback}
        onCancel={() => setFeedbackModalVisible(false)}
        confirmLoading={submittingFeedback}
        okText="提交"
        cancelText="取消"
      >
        <div style={{ marginBottom: 16 }}>
          <p><strong>事件:</strong> {currentRecord?.event_name || currentRecord?.market || currentRecord?.market_name}</p>
          <p><strong>类型:</strong> {currentArbitrageType === 'pair_cost' ? 'Pair Cost 套利' : '跨平台套利'}</p>
        </div>
        
        <div style={{ marginBottom: 16 }}>
          <label style={{ display: 'block', marginBottom: 8 }}>反馈类型:</label>
          <Radio.Group value={feedbackType} onChange={(e) => setFeedbackType(e.target.value)}>
            <Radio.Button value="confirmed">
              <CheckCircleOutlined style={{ color: '#52c41a' }} /> 确认有效
            </Radio.Button>
            <Radio.Button value="error">
              <CloseCircleOutlined style={{ color: '#ff4d4f' }} /> 标记错误
            </Radio.Button>
          </Radio.Group>
        </div>
        
        <div style={{ marginBottom: 16 }}>
          <label style={{ display: 'block', marginBottom: 8 }}>反馈说明:</label>
          <Input.TextArea
            value={feedbackText}
            onChange={(e) => setFeedbackText(e.target.value)}
            placeholder="请输入反馈说明（可选）"
            rows={3}
          />
        </div>
        
        <div>
          <label style={{ display: 'block', marginBottom: 8 }}>备注:</label>
          <Input.TextArea
            value={userNotes}
            onChange={(e) => setUserNotes(e.target.value)}
            placeholder="个人备注（可选）"
            rows={2}
          />
        </div>
      </Modal>
      
      {/* 对比模态框 */}
      <Modal
        title="价格对比"
        open={compareModalVisible}
        onCancel={() => setCompareModalVisible(false)}
        footer={[
          <Button key="close" onClick={() => setCompareModalVisible(false)}>
            关闭
          </Button>
        ]}
      >
        {comparing ? (
          <Spin tip="正在获取最新价格..." />
        ) : compareData ? (
          <div>
            <p><strong>对比时间:</strong> {new Date(compareData.comparison_time).toLocaleString('zh-CN')}</p>
            <p><strong>套利类型:</strong> {compareData.arbitrage_type === 'pair_cost' ? 'Pair Cost 套利' : '跨平台套利'}</p>
            
            {compareData.arbitrage_type === 'pair_cost' && (
              <div style={{ marginTop: 16 }}>
                <Alert
                  message="Pair Cost 对比"
                  description="实时价格对比功能已触发。实际价格数据将从CLOB API获取。"
                  type="info"
                />
              </div>
            )}
            
            {compareData.arbitrage_type === 'cross_market' && (
              <div style={{ marginTop: 16 }}>
                <Alert
                  message="跨平台对比"
                  description="实时价格对比功能已触发。实际价格数据将从Polymarket和Manifold API获取。"
                  type="info"
                />
              </div>
            )}
            
            <div style={{ marginTop: 16, padding: 12, background: '#f5f5f5', borderRadius: 4 }}>
              <pre style={{ margin: 0, fontSize: 12 }}>
                {JSON.stringify(compareData.prices, null, 2)}
              </pre>
            </div>
          </div>
        ) : (
          <Empty description="暂无对比数据" />
        )}
      </Modal>
    </div>
  );
};

export default Arbitrage;
