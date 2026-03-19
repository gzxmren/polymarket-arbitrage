import React, { useEffect, useState } from 'react';
import { Card, Table, Tag, Button, Tabs, Statistic, Row, Col, Select, message, Spin, Empty, Progress } from 'antd';
import { getWhales } from '../services/api';
import api from '../services/api';
import { TrophyOutlined, FireOutlined, CrownOutlined, LineChartOutlined, ReloadOutlined, ArrowUpOutlined, ArrowDownOutlined } from '@ant-design/icons';

const { TabPane } = Tabs;
const { Option } = Select;

const SORT_OPTIONS = [
  { value: 'recommended', label: '⭐ 智能推荐', desc: '基于胜率+活跃度+收益综合推荐' },
  { value: 'total_value', label: '💰 持仓价值', desc: '按持仓总价值排序' },
  { value: 'activity', label: '🔥 活跃度', desc: '按24h变动次数排序' },
  { value: 'concentration', label: '🎯 集中度', desc: '按Top5占比排序' },
  { value: 'pnl', label: '📈 收益率', desc: '按总盈亏排序' },
  { value: 'composite', label: '⭐ 综合评分', desc: '多维度加权评分' },
  { value: 'copy', label: '🤖 智能跟单', desc: '最适合跟随的鲸鱼' },
];

const Whales: React.FC = () => {
  const [whales, setWhales] = useState<any[]>([]);
  const [topWhales, setTopWhales] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('watched');
  const [sortBy, setSortBy] = useState('total_value');
  
  // 跟随信号相关
  const [signals, setSignals] = useState<any[]>([]);
  const [loadingSignals, setLoadingSignals] = useState(false);
  const [generatingSignal, setGeneratingSignal] = useState<string | null>(null);

  useEffect(() => {
    fetchWatchedWhales();
    fetchTopWhales();
  }, []);

  useEffect(() => {
    if (activeTab === 'top10') {
      fetchTopWhales();
    } else if (activeTab === 'signals') {
      fetchSignals();
    }
  }, [activeTab, sortBy]);

  const fetchWatchedWhales = async () => {
    setLoading(true);
    try {
      const response = await getWhales({ is_watched: 'true', sort_by: 'top5_ratio' });
      setWhales(response.data.whales || []);
    } catch (error) {
      console.error('Failed to fetch watched whales:', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchTopWhales = async () => {
    setLoading(true);
    try {
      const params: any = { limit: 10 };
      
      switch (sortBy) {
        case 'activity':
          params.sort_by = 'changes_count';
          break;
        case 'concentration':
          params.sort_by = 'top5_ratio';
          break;
        case 'pnl':
          params.sort_by = 'total_pnl';
          break;
        case 'composite':
        case 'copy':
          params.limit = 50;
          params.sort_by = 'total_value';
          break;
        default:
          params.sort_by = 'total_value';
      }
      
      const response = await getWhales(params);
      let whales = response.data.whales || [];
      
      if (sortBy === 'composite') {
        whales = calculateCompositeRanking(whales);
      } else if (sortBy === 'copy') {
        whales = calculateCopyRanking(whales);
      }
      
      setTopWhales(whales.slice(0, 10));
    } catch (error) {
      console.error('Failed to fetch top whales:', error);
    } finally {
      setLoading(false);
    }
  };

  // 获取跟随信号
  const fetchSignals = async () => {
    setLoadingSignals(true);
    try {
      const response = await api.get('/api/whales/signals', { params: { hours: 24, min_confidence: 0.5 } });
      if (response.data.success) {
        setSignals(response.data.signals || []);
      }
    } catch (error) {
      console.error('Failed to fetch signals:', error);
      message.error('获取信号失败');
    } finally {
      setLoadingSignals(false);
    }
  };

  // 生成单个鲸鱼的跟随信号
  const generateSignal = async (wallet: string) => {
    setGeneratingSignal(wallet);
    try {
      const response = await api.post(`/api/whales/${wallet}/signal`);
      if (response.data.success && response.data.has_signal) {
        message.success('发现新的跟随信号！');
        fetchSignals(); // 刷新信号列表
      } else {
        message.info(response.data.message || '近期无大额交易');
      }
    } catch (error) {
      console.error('Failed to generate signal:', error);
      message.error('生成信号失败');
    } finally {
      setGeneratingSignal(null);
    }
  };

  const calculateCompositeRanking = (whales: any[]) => {
    return whales.map(whale => {
      let score = 0;
      const valueScore = Math.min((whale.total_value || 0) / 100000, 1.0) * 40;
      const changesCount = whale.changes_count || 0;
      const activityScore = Math.min(changesCount / 5, 1.0) * 25;
      const top5 = whale.top5_ratio || 0;
      let concentrationScore = 0;
      if (top5 >= 0.5 && top5 <= 0.7) {
        concentrationScore = 20;
      } else if (top5 > 0.7) {
        concentrationScore = 15;
      } else {
        concentrationScore = top5 * 40;
      }
      const pnl = whale.total_pnl || 0;
      const value = whale.total_value || 1;
      const pnlRatio = pnl / value;
      const pnlScore = Math.min(Math.max(pnlRatio * 100, -15), 15) + 15;
      score = valueScore + activityScore + concentrationScore + pnlScore;
      return { ...whale, composite_score: Math.round(score) };
    }).sort((a, b) => b.composite_score - a.composite_score);
  };

  const calculateCopyRanking = (whales: any[]) => {
    return whales.map(whale => {
      let score = 0;
      const top5 = whale.top5_ratio || 0;
      if (top5 >= 0.4 && top5 <= 0.8) {
        score += 30;
      } else if (top5 > 0.8) {
        score += 20;
      } else {
        score += top5 * 50;
      }
      if ((whale.total_pnl || 0) > 0) {
        score += 25;
      }
      const changes = whale.changes_count || 0;
      if (changes >= 1 && changes <= 5) {
        score += 25;
      } else if (changes > 5) {
        score += 15;
      } else {
        score += changes * 5;
      }
      const value = whale.total_value || 0;
      if (value >= 50000 && value <= 500000) {
        score += 20;
      } else if (value > 500000) {
        score += 15;
      } else {
        score += (value / 50000) * 20;
      }
      return { ...whale, copy_score: Math.round(score) };
    }).sort((a, b) => b.copy_score - a.copy_score);
  };

  // 智能推荐评分
  const calculateRecommendedScore = (whale: any) => {
    let score = 0;
    let reasons: string[] = [];
    
    // 1. 活跃度评分 (30分)
    const changes = whale.changes_count || 0;
    if (changes >= 3 && changes <= 10) {
      score += 30;
      reasons.push('近期活跃');
    } else if (changes > 0) {
      score += changes * 3;
    }
    
    // 2. 收益评分 (25分)
    const pnl = whale.total_pnl || 0;
    if (pnl > 10000) {
      score += 25;
      reasons.push('高收益');
    } else if (pnl > 0) {
      score += 15;
      reasons.push('盈利中');
    }
    
    // 3. 持仓价值评分 (20分)
    const value = whale.total_value || 0;
    if (value >= 100000 && value <= 1000000) {
      score += 20;
      reasons.push('持仓适中');
    } else if (value > 0) {
      score += Math.min((value / 100000) * 20, 20);
    }
    
    // 4. 有信号加分 (15分)
    const hasSignal = signals.some((s: any) => s.whallet === whale.wallet);
    if (hasSignal) {
      score += 15;
      reasons.push('有信号');
    }
    
    // 5. 被关注加分 (10分)
    if (whale.is_watched) {
      score += 10;
    }
    
    return { score: Math.min(score, 100), reasons };
  };

  const watchedColumns = [
    { 
      title: '鲸鱼', 
      dataIndex: 'pseudonym', 
      key: 'pseudonym', 
      render: (text: string, record: any) => (
        <div>
          <strong>{text}</strong>
          {record.has_activity && <Tag color="red" style={{ marginLeft: 8 }}><FireOutlined /> 活跃</Tag>}
        </div>
      ) 
    },
    { 
      title: '持仓/盈亏', 
      key: 'value_pnl',
      render: (_: any, record: any) => (
        <div>
          <div style={{ fontWeight: 'bold', color: '#1890ff' }}>
            ${(record.total_value || 0).toLocaleString()}
          </div>
          <div style={{ 
            fontSize: '12px', 
            color: (record.total_pnl || 0) > 0 ? '#52c41a' : (record.total_pnl || 0) < 0 ? '#f5222d' : '#999'
          }}>
            {(record.total_pnl || 0) > 0 ? '+' : ''}${(record.total_pnl || 0).toLocaleString()}
          </div>
        </div>
      )
    },
    { 
      title: '24h变动', 
      dataIndex: 'changes_count', 
      key: 'changes_count',
      render: (value: number) => {
        const count = value || 0;
        if (count >= 5) return <Tag color="red">{count} 次 🔥</Tag>;
        if (count >= 3) return <Tag color="orange">{count} 次</Tag>;
        return <Tag>{count} 次</Tag>;
      }
    },
    { 
      title: '最新信号', 
      key: 'latest_signal',
      render: (_: any, record: any) => {
        // 查找该鲸鱼的最新信号
        const whaleSignal = signals.find((s: any) => s.whallet === record.wallet);
        if (whaleSignal) {
          return (
            <div>
              <Tag color={whaleSignal.direction === 'BUY' ? 'green' : 'red'}>
                {whaleSignal.direction === 'BUY' ? '📈' : '📉'} {whaleSignal.direction}
              </Tag>
              <div style={{ fontSize: '11px', color: '#666', marginTop: 4 }}>
                {(whaleSignal.confidence * 100).toFixed(0)}% 置信度
              </div>
            </div>
          );
        }
        return <Tag color="default">无信号</Tag>;
      }
    },
    { 
      title: '操作', 
      key: 'action', 
      render: (_: any, record: any) => (
        <span>
          <Button type="link" href={`/whales/${record.wallet}`}>详情</Button>
          <Button 
            type="link" 
            onClick={() => generateSignal(record.wallet)}
            loading={generatingSignal === record.wallet}
          >
            生成信号
          </Button>
        </span>
      ) 
    },
  ];

  const getTopColumns = () => {
    const baseColumns = [
      { title: '排名', key: 'rank', width: 80, render: (_: any, __: any, index: number) => {
        const rank = index + 1;
        if (rank === 1) return <span style={{ fontSize: '20px' }}>🥇</span>;
        if (rank === 2) return <span style={{ fontSize: '20px' }}>🥈</span>;
        if (rank === 3) return <span style={{ fontSize: '20px' }}>🥉</span>;
        return <span style={{ fontSize: '16px', fontWeight: 'bold' }}>{rank}.</span>;
      }},
      { title: '鲸鱼', dataIndex: 'pseudonym', key: 'pseudonym', render: (text: string, record: any) => (
        <div><strong>{text}</strong>
          {record.is_watched && <Tag color="gold" style={{ marginLeft: 8 }}><CrownOutlined /> 重点</Tag>}
          {record.has_activity && <Tag color="red" style={{ marginLeft: 8 }}><FireOutlined /> 活跃</Tag>}
        </div>
      )},
      { title: '持仓价值', dataIndex: 'total_value', key: 'total_value', render: (value: number) => (
        <span style={{ fontWeight: 'bold', color: '#1890ff' }}>${value?.toLocaleString() || 0}</span>
      )},
      { title: '市场数', dataIndex: 'position_count', key: 'position_count', render: (value: number) => `${value || 0} 个` },
      { title: '24h变动', dataIndex: 'changes_count', key: 'changes_count', render: (value: number) => {
        const count = value || 0;
        if (count >= 5) return <Tag color="red">{count} 次 🔥</Tag>;
        if (count >= 3) return <Tag color="orange">{count} 次</Tag>;
        return <Tag>{count} 次</Tag>;
      }},
      { title: '总盈亏', dataIndex: 'total_pnl', key: 'total_pnl', render: (value: number) => {
        const isPositive = (value || 0) > 0;
        const isNegative = (value || 0) < 0;
        return <span style={{ color: isPositive ? '#52c41a' : isNegative ? '#f5222d' : '#999', fontWeight: 'bold' }}>
          {isPositive ? '+' : ''}${value?.toLocaleString() || 0}
        </span>;
      }},
      { title: '集中度', dataIndex: 'top5_ratio', key: 'top5_ratio', render: (ratio: number) => {
        const percent = Math.round((ratio || 0) * 100);
        let color = 'default';
        if (percent >= 70) color = 'success';
        else if (percent >= 50) color = 'warning';
        return <Tag color={color}>{percent}%</Tag>;
      }},
      { title: '操作', key: 'action', render: (_: any, record: any) => <Button type="link" href={`/whales/${record.wallet}`}>查看详情</Button> },
    ];

    if (sortBy === 'composite') {
      baseColumns.splice(2, 0, {
        title: '综合评分',
        key: 'composite_score',
        render: (_: any, record: any) => <Tag color="purple">⭐ {record.composite_score || 0}</Tag>
      });
    } else if (sortBy === 'copy') {
      baseColumns.splice(2, 0, {
        title: '跟单评分',
        key: 'copy_score',
        render: (_: any, record: any) => <Tag color="blue">🤖 {record.copy_score || 0}</Tag>
      });
    }

    return baseColumns;
  };

  const totalValue = topWhales.reduce((sum, w) => sum + (w?.total_value || 0), 0);
  const activeCount = topWhales.filter(w => w?.has_activity).length;
  const watchedCount = topWhales.filter(w => w?.is_watched).length;
  const currentSortOption = SORT_OPTIONS.find(opt => opt.value === sortBy);

  // 获取推荐鲸鱼
  const getRecommendedWhalesList = () => {
    const scoredWhales = whales.map((whale: any) => {
      const { score, reasons } = calculateRecommendedScore(whale);
      return { ...whale, recommend_score: score, recommend_reasons: reasons };
    });
    return scoredWhales.sort((a: any, b: any) => b.recommend_score - a.recommend_score).slice(0, 5);
  };

  const recommendedWhales = getRecommendedWhalesList();

  return (
    <div>
      <h1 style={{ marginBottom: 24 }}>🐋 鲸鱼跟踪</h1>
      
      {/* 智能推荐区域 */}
      {recommendedWhales.length > 0 && (
        <Card title="⭐ 智能推荐" style={{ marginBottom: 24 }}>
          <Row gutter={16}>
            {recommendedWhales.map((whale: any, index: number) => (
              <Col span={12} key={whale.wallet} style={{ marginBottom: 16 }}>
                <Card type="inner" style={{ borderLeft: '4px solid #1890ff' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                    <div>
                      <span style={{ fontSize: 20, marginRight: 8 }}>
                        {index === 0 ? '🥇' : index === 1 ? '🥈' : index === 2 ? '🥉' : '⭐'}
                      </span>
                      <strong>{whale.pseudonym}</strong>
                      {whale.has_activity && <Tag color="red" style={{ marginLeft: 8 }}><FireOutlined /> 活跃</Tag>}
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 18, fontWeight: 'bold', color: '#1890ff' }}>
                        {whale.recommend_score}分
                      </div>
                    </div>
                  </div>
                  <div style={{ marginBottom: 8 }}>
                    <Tag color="green">💰 ${(whale.total_value || 0).toLocaleString()}</Tag>
                    <Tag color={whale.total_pnl > 0 ? 'green' : 'red'}>
                      {whale.total_pnl > 0 ? '+' : ''}${(whale.total_pnl || 0).toLocaleString()}
                    </Tag>
                  </div>
                  {/* 简化的趋势可视化 */}
                  <div style={{ marginBottom: 8 }}>
                    <div style={{ display: 'flex', alignItems: 'center', marginBottom: 4 }}>
                      <span style={{ fontSize: 12, color: '#666', marginRight: 8 }}>收益趋势:</span>
                      {whale.total_pnl > 0 ? (
                        <ArrowUpOutlined style={{ color: '#52c41a' }} />
                      ) : (
                        <ArrowDownOutlined style={{ color: '#f5222d' }} />
                      )}
                    </div>
                    <Progress 
                      percent={Math.min(Math.abs((whale.total_pnl || 0) / (whale.total_value || 1)) * 100, 100)}
                      size="small"
                      strokeColor={whale.total_pnl > 0 ? '#52c41a' : '#f5222d'}
                      showInfo={false}
                    />
                    <div style={{ fontSize: 11, color: '#999', textAlign: 'right' }}>
                      收益率: {((whale.total_pnl || 0) / (whale.total_value || 1) * 100).toFixed(1)}%
                    </div>
                  </div>
                  
                  <div style={{ fontSize: 12, color: '#666', marginBottom: 8 }}>
                    推荐理由: {whale.recommend_reasons?.join(' · ')}
                  </div>
                  <div>
                    <Button type="primary" size="small" onClick={() => generateSignal(whale.wallet)} loading={generatingSignal === whale.wallet}>
                      生成信号
                    </Button>
                    <Button size="small" style={{ marginLeft: 8 }} href={`/whales/${whale.wallet}`}>
                      查看详情
                    </Button>
                  </div>
                </Card>
              </Col>
            ))}
          </Row>
        </Card>
      )}
      
      <Tabs activeKey={activeTab} onChange={setActiveTab} type="card" style={{ marginBottom: 24 }}>
        <TabPane tab={<span><CrownOutlined /> 重点关注 ({whales.length})</span>} key="watched">
          <Card>
            <Table columns={watchedColumns} dataSource={whales} rowKey="wallet" loading={loading} pagination={{ pageSize: 10 }} />
          </Card>
        </TabPane>
        <TabPane tab={<span><TrophyOutlined /> Top 10 排行榜</span>} key="top10">
          <Card style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
              <span style={{ fontWeight: 'bold' }}>排序方式：</span>
              <Select value={sortBy} onChange={setSortBy} style={{ width: 200 }}>
                {SORT_OPTIONS.map(opt => <Option key={opt.value} value={opt.value}>{opt.label}</Option>)}
              </Select>
              <span style={{ color: '#666', fontSize: '14px' }}>{currentSortOption?.desc}</span>
            </div>
          </Card>
          <Row gutter={16} style={{ marginBottom: 24 }}>
            <Col span={8}>
              <Card><Statistic title="总持仓价值" value={totalValue} prefix="$" formatter={(value) => value?.toLocaleString()} /></Card>
            </Col>
            <Col span={8}>
              <Card><Statistic title="活跃鲸鱼" value={activeCount} suffix={`/ ${topWhales.length}`} valueStyle={{ color: '#cf1322' }} /></Card>
            </Col>
            <Col span={8}>
              <Card><Statistic title="重点关注" value={watchedCount} suffix={`/ ${topWhales.length}`} valueStyle={{ color: '#faad14' }} /></Card>
            </Col>
          </Row>
          <Card title={`🏆 ${currentSortOption?.label} Top 10`}>
            <Table columns={getTopColumns()} dataSource={topWhales} rowKey="wallet" loading={loading} pagination={false} />
          </Card>
        </TabPane>
        
        {/* 跟随信号标签页 */}
        <TabPane tab={<span><LineChartOutlined /> 跟随信号 ({signals.length})</span>} key="signals">
          <Card style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <span style={{ fontWeight: 'bold', marginRight: 16 }}>🐋 鲸鱼跟随信号</span>
                <span style={{ color: '#666' }}>自动检测聪明钱鲸鱼的大额调仓</span>
              </div>
              <Button type="primary" icon={<ReloadOutlined />} onClick={fetchSignals} loading={loadingSignals}>
                刷新信号
              </Button>
            </div>
          </Card>
          
          {loadingSignals ? (
            <div style={{ textAlign: 'center', padding: 48 }}>
              <Spin size="large" />
              <p style={{ marginTop: 16, color: '#666' }}>正在扫描鲸鱼交易...</p>
            </div>
          ) : signals.length === 0 ? (
            <Card>
              <Empty
                description="暂无跟随信号"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
              <div style={{ textAlign: 'center', color: '#666', marginTop: 8 }}>
                聪明钱鲸鱼近期没有大额调仓
              </div>
            </Card>
          ) : (
            <Row gutter={16}>
              {signals.map((signal, index) => (
                <Col span={12} key={index} style={{ marginBottom: 16 }}>
                  <Card
                    type="inner"
                    style={{ 
                      borderLeft: `4px solid ${signal.confidence >= 0.85 ? '#52c41a' : signal.confidence >= 0.75 ? '#faad14' : '#ff4d4f'}`
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 12 }}>
                      <div>
                        <Tag color={signal.direction === 'BUY' ? 'green' : 'red'} style={{ fontSize: 14 }}>
                          {signal.direction === 'BUY' ? '📈 买入' : '📉 卖出'}
                        </Tag>
                        <span style={{ marginLeft: 8, fontWeight: 'bold' }}>{signal.whale_pseudonym}</span>
                      </div>
                      <div style={{ textAlign: 'right' }}>
                        <div style={{ fontSize: 18, fontWeight: 'bold', color: signal.confidence >= 0.85 ? '#52c41a' : '#faad14' }}>
                          {(signal.confidence * 100).toFixed(0)}%
                        </div>
                        <div style={{ fontSize: 12, color: '#666' }}>置信度</div>
                      </div>
                    </div>
                    
                    <div style={{ marginBottom: 12 }}>
                      <div style={{ fontSize: 16, fontWeight: 'bold', marginBottom: 4 }}>{signal.market}</div>
                      <div style={{ color: '#666', fontSize: 13 }}>风险等级: {signal.risk_level}</div>
                    </div>
                    
                    <Row gutter={8} style={{ marginBottom: 12 }}>
                      <Col span={8}>
                        <div style={{ textAlign: 'center', padding: 8, backgroundColor: '#f5f5f5', borderRadius: 4 }}>
                          <div style={{ fontSize: 12, color: '#666' }}>建议仓位</div>
                          <div style={{ fontSize: 14, fontWeight: 'bold' }}>${signal.suggested_position?.toLocaleString()}</div>
                        </div>
                      </Col>
                      <Col span={8}>
                        <div style={{ textAlign: 'center', padding: 8, backgroundColor: '#f5f5f5', borderRadius: 4 }}>
                          <div style={{ fontSize: 12, color: '#666' }}>预期收益</div>
                          <div style={{ fontSize: 14, fontWeight: 'bold', color: '#52c41a' }}>+{(signal.expected_return * 100).toFixed(0)}%</div>
                        </div>
                      </Col>
                      <Col span={8}>
                        <div style={{ textAlign: 'center', padding: 8, backgroundColor: '#f5f5f5', borderRadius: 4 }}>
                          <div style={{ fontSize: 12, color: '#666' }}>信号时间</div>
                          <div style={{ fontSize: 12 }}>{new Date(signal.created_at).toLocaleTimeString()}</div>
                        </div>
                      </Col>
                    </Row>
                    
                    <div style={{ fontSize: 12, color: '#666', backgroundColor: '#fafafa', padding: 8, borderRadius: 4 }}>
                      {signal.reasoning}
                    </div>
                  </Card>
                </Col>
              ))}
            </Row>
          )}
          
          {/* 手动生成信号 */}
          <Card title="手动生成信号" style={{ marginTop: 24 }}>
            <div style={{ marginBottom: 16 }}>
              <span style={{ color: '#666' }}>选择鲸鱼手动生成跟随信号：</span>
            </div>
            <Row gutter={[8, 8]}>
              {whales.slice(0, 6).map((whale) => (
                <Col key={whale.wallet}>
                  <Button
                    size="small"
                    onClick={() => generateSignal(whale.wallet)}
                    loading={generatingSignal === whale.wallet}
                  >
                    {whale.pseudonym || whale.wallet.slice(0, 10)}...
                  </Button>
                </Col>
              ))}
            </Row>
          </Card>
        </TabPane>
      </Tabs>
    </div>
  );
};

export default Whales;
