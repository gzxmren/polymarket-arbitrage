import React, { useState } from 'react';
import { Card, Button, Badge, Row, Col, Statistic, Empty, Spin, Switch, message, Checkbox, Table, Tag } from 'antd';
import { ExperimentOutlined, ReloadOutlined, AlertOutlined, LineChartOutlined, CloudOutlined, DatabaseOutlined, DownloadOutlined, CheckSquareOutlined } from '@ant-design/icons';
import { semanticAPI, marketsAPI } from '../services/api';

interface Market {
  id: string;
  title: string;
  description: string;
  yes_price: number;
  no_price: number;
  liquidity: number;
  volume: number;
  slug: string;
  end_date: string;
}

interface SemanticSignal {
  type: string;
  subtype: string;
  market_a: string;
  market_b: string;
  price_a: number;
  price_b: number;
  violation: number;
  expected_profit: number;
  confidence: number;
  reasoning: string;
  suggested_action: string;
}

interface LogicViolation {
  violation_type: string;
  nodes: string[];
  expected: string;
  actual: string;
  severity: number;
  profit_potential: number;
  description: string;
}

interface ScanResult {
  success: boolean;
  scan_time: string;
  markets_scanned: number;
  semantic_signals: SemanticSignal[];
  logic_violations: LogicViolation[];
  total_opportunities: number;
  markets?: Market[];
}

const SemanticArbitrage: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [useRealData, setUseRealData] = useState(true);
  
  // 市场列表相关
  const [markets, setMarkets] = useState<Market[]>([]);
  const [selectedMarkets, setSelectedMarkets] = useState<string[]>([]);
  const [marketsLoaded, setMarketsLoaded] = useState(false);
  const [loadingMarkets, setLoadingMarkets] = useState(false);

  // 测试数据
  const testMarkets: Market[] = [
    { id: '0x100', title: 'Will Chiefs win Super Bowl 2024?', description: '', yes_price: 0.55, no_price: 0.45, liquidity: 100000, volume: 500000, slug: 'chiefs', end_date: '2024-02-12' },
    { id: '0x101', title: 'Will AFC win Super Bowl 2024?', description: '', yes_price: 0.45, no_price: 0.55, liquidity: 100000, volume: 400000, slug: 'afc', end_date: '2024-02-12' },
    { id: '0x102', title: 'Will Trump win 2024?', description: '', yes_price: 0.55, no_price: 0.45, liquidity: 200000, volume: 1000000, slug: 'trump', end_date: '2024-11-05' },
    { id: '0x103', title: 'Will Republican win 2024?', description: '', yes_price: 0.48, no_price: 0.52, liquidity: 150000, volume: 800000, slug: 'gop', end_date: '2024-11-05' }
  ];

  // 加载市场列表
  const loadMarkets = async () => {
    setLoadingMarkets(true);
    setError(null);
    
    try {
      if (useRealData) {
        const response = await marketsAPI.getActive(100);
        if (response.data.success) {
          setMarkets(response.data.markets);
          setMarketsLoaded(true);
          message.success(`已加载 ${response.data.markets.length} 个市场`);
        } else {
          throw new Error(response.data.error || '加载失败');
        }
      } else {
        setMarkets(testMarkets);
        setMarketsLoaded(true);
        message.success(`已加载 ${testMarkets.length} 个测试市场`);
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : '加载失败';
      setError(errMsg);
      message.error(errMsg);
    } finally {
      setLoadingMarkets(false);
    }
  };

  // 选择/取消选择市场
  const toggleMarketSelection = (marketId: string) => {
    setSelectedMarkets(prev => 
      prev.includes(marketId) ? prev.filter(id => id !== marketId) : [...prev, marketId]
    );
  };

  // 全选/取消全选
  const toggleSelectAll = () => {
    if (selectedMarkets.length === markets.length) {
      setSelectedMarkets([]);
    } else {
      setSelectedMarkets(markets.map(m => m.id));
    }
  };

  // 扫描选中的市场
  const scanSelectedMarkets = async () => {
    if (selectedMarkets.length === 0) {
      message.warning('请先选择要扫描的市场');
      return;
    }

    setScanning(true);
    setError(null);
    message.loading({ content: `正在扫描 ${selectedMarkets.length} 个市场...`, key: 'scan' });
    
    try {
      const selectedData = markets.filter(m => selectedMarkets.includes(m.id));
      const response = await marketsAPI.scan(selectedData, 'all');
      
      if (response.data.success) {
        setResult(response.data);
        message.success({ content: `扫描完成！发现 ${response.data.total_opportunities} 个套利机会`, key: 'scan' });
      } else {
        throw new Error(response.data.error || '扫描失败');
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : '扫描失败';
      setError(errMsg);
      message.error({ content: errMsg, key: 'scan' });
    } finally {
      setScanning(false);
    }
  };

  // 测试连接
  const runTest = async () => {
    setLoading(true);
    try {
      const response = await semanticAPI.test();
      message.success('API 连接正常');
      console.log('Test response:', response.data);
    } catch (err) {
      message.error(err instanceof Error ? err.message : '测试失败');
    } finally {
      setLoading(false);
    }
  };

  // 快速扫描
  const runQuickScan = async () => {
    setScanning(true);
    setError(null);
    message.loading({ content: '快速扫描中...', key: 'scan' });
    
    try {
      const response = await marketsAPI.quickScan(50);
      if (response.data.success) {
        setResult(response.data);
        if (response.data.markets) {
          setMarkets(response.data.markets);
          setMarketsLoaded(true);
        }
        message.success({ content: `扫描完成！发现 ${response.data.total_opportunities} 个套利机会`, key: 'scan' });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '扫描失败');
    } finally {
      setScanning(false);
    }
  };

  // 表格列
  const columns = [
    { title: '选择', dataIndex: 'id', key: 'select', width: 60, render: (id: string) => <Checkbox checked={selectedMarkets.includes(id)} onChange={() => toggleMarketSelection(id)} /> },
    { title: '市场', dataIndex: 'title', key: 'title', render: (title: string) => <div style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</div> },
    { title: 'YES', dataIndex: 'yes_price', key: 'yes', width: 80, render: (p: number) => <Tag color="green">{(p * 100).toFixed(1)}%</Tag> },
    { title: 'NO', dataIndex: 'no_price', key: 'no', width: 80, render: (p: number) => <Tag color="red">{(p * 100).toFixed(1)}%</Tag> },
    { title: '流动性', dataIndex: 'liquidity', key: 'liq', width: 100, render: (l: number) => `$${((l || 0) / 1000).toFixed(0)}k` }
  ];

  return (
    <div style={{ padding: 24 }}>
      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <ExperimentOutlined style={{ fontSize: 32 }} /> 语义套利扫描
        </h1>
        <p style={{ color: '#666', marginTop: 8 }}>使用 LLM 识别语义相关市场的定价矛盾</p>
      </div>

      {/* Error */}
      {error && (
        <Card style={{ marginBottom: 24, borderColor: '#ff4d4f' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#ff4d4f' }}>
            <AlertOutlined /> <span>{error}</span>
          </div>
        </Card>
      )}

      {/* Controls */}
      <Card style={{ marginBottom: 24 }}>
        <Row gutter={16} align="middle">
          <Col><DatabaseOutlined /> <span>数据源：</span></Col>
          <Col>
            <Switch checked={useRealData} onChange={setUseRealData} checkedChildren="真实" unCheckedChildren="测试" />
          </Col>
          <Col flex={1} style={{ textAlign: 'right' }}>
            <Button onClick={runTest} loading={loading} style={{ marginRight: 8 }}>测试连接</Button>
            <Button onClick={runQuickScan} loading={scanning} type="primary">快速扫描</Button>
          </Col>
        </Row>
      </Card>

      {/* Load Markets Button */}
      {!marketsLoaded && (
        <Card style={{ marginBottom: 24, textAlign: 'center' }}>
          <Button type="primary" size="large" onClick={loadMarkets} loading={loadingMarkets} icon={<DownloadOutlined />}>
            加载市场列表
          </Button>
          <p style={{ color: '#666', marginTop: 12 }}>点击加载 Polymarket 活跃市场</p>
        </Card>
      )}

      {/* Markets Table */}
      {marketsLoaded && markets.length > 0 && (
        <Card title={`市场列表 (${selectedMarkets.length}/${markets.length} 已选择)`} style={{ marginBottom: 24 }}
          extra={
            <span>
              <Button size="small" onClick={toggleSelectAll} style={{ marginRight: 8 }}>
                {selectedMarkets.length === markets.length ? '取消全选' : '全选'}
              </Button>
              <Button size="small" onClick={loadMarkets} loading={loadingMarkets}>刷新</Button>
            </span>
          }
        >
          <Table dataSource={markets} columns={columns} rowKey="id" size="small" pagination={{ pageSize: 10 }} loading={loadingMarkets} />
          <div style={{ marginTop: 16, textAlign: 'right' }}>
            <Button type="primary" onClick={scanSelectedMarkets} loading={scanning} disabled={selectedMarkets.length === 0} icon={<LineChartOutlined />}>
              扫描选中市场 ({selectedMarkets.length})
            </Button>
          </div>
        </Card>
      )}

      {/* Results */}
      {result && (
        <Card title="扫描结果" style={{ marginBottom: 24 }}>
          <Row gutter={16}>
            <Col span={6}><Statistic title="扫描市场" value={result.markets_scanned} /></Col>
            <Col span={6}><Statistic title="语义套利" value={result.semantic_signals?.length || 0} valueStyle={{ color: '#ff4d4f' }} /></Col>
            <Col span={6}><Statistic title="逻辑违反" value={result.logic_violations?.length || 0} valueStyle={{ color: '#faad14' }} /></Col>
            <Col span={6}><Statistic title="总计机会" value={result.total_opportunities || 0} valueStyle={{ color: '#52c41a' }} /></Col>
          </Row>
        </Card>
      )}

      {/* Semantic Signals */}
      {result && result.semantic_signals && result.semantic_signals.length > 0 && (
        <Card title="语义套利信号" style={{ marginBottom: 24 }}>
          {result.semantic_signals.map((s, i) => (
            <Card key={i} type="inner" style={{ marginBottom: 16, backgroundColor: '#fff2f0' }}>
              <Row justify="space-between">
                <Col><Badge color="red" text={s.subtype === 'implies_violation' ? '蕴含违反' : '其他'} /></Col>
                <Col style={{ color: '#ff4d4f', fontWeight: 'bold' }}>+{(s.expected_profit * 100).toFixed(1)}%</Col>
              </Row>
              <Row gutter={16} style={{ marginTop: 12 }}>
                <Col span={12}>
                  <Card size="small">
                    <div style={{ fontSize: 12, color: '#666' }}>市场 A</div>
                    <div style={{ fontWeight: 'bold' }}>{s.market_a}</div>
                    <div style={{ fontSize: 18 }}>{(s.price_a * 100).toFixed(1)}%</div>
                  </Card>
                </Col>
                <Col span={12}>
                  <Card size="small">
                    <div style={{ fontSize: 12, color: '#666' }}>市场 B</div>
                    <div style={{ fontWeight: 'bold' }}>{s.market_b}</div>
                    <div style={{ fontSize: 18 }}>{(s.price_b * 100).toFixed(1)}%</div>
                  </Card>
                </Col>
              </Row>
              <Card size="small" style={{ marginTop: 12 }}>
                <div style={{ fontSize: 12, color: '#666' }}>建议操作</div>
                <div>{s.suggested_action}</div>
              </Card>
            </Card>
          ))}
        </Card>
      )}

      {/* Empty */}
      {result && result.total_opportunities === 0 && (
        <Card>
          <Empty description="未发现套利机会" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        </Card>
      )}
    </div>
  );
};

export default SemanticArbitrage;