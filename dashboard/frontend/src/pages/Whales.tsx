import React, { useEffect, useState } from 'react';
import { Card, Table, Tag, Button, Tabs, Statistic, Row, Col, Select } from 'antd';
import { getWhales } from '../services/api';
import { TrophyOutlined, FireOutlined, CrownOutlined } from '@ant-design/icons';

const { TabPane } = Tabs;
const { Option } = Select;

const SORT_OPTIONS = [
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

  useEffect(() => {
    fetchWatchedWhales();
    fetchTopWhales();
  }, []);

  useEffect(() => {
    if (activeTab === 'top10') {
      fetchTopWhales();
    }
  }, [sortBy]);

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

  const watchedColumns = [
    { title: '鲸鱼', dataIndex: 'pseudonym', key: 'pseudonym', render: (text: string) => <strong>{text}</strong> },
    { title: '持仓价值', dataIndex: 'total_value', key: 'total_value', render: (value: number) => `$${value?.toLocaleString() || 0}` },
    { title: '市场数', dataIndex: 'position_count', key: 'position_count' },
    { title: 'Top5占比', dataIndex: 'top5_ratio', key: 'top5_ratio', render: (ratio: number) => {
      const percent = Math.round((ratio || 0) * 100);
      let color = 'default';
      if (percent >= 70) color = 'success';
      else if (percent >= 50) color = 'warning';
      return <Tag color={color}>{percent}%</Tag>;
    }},
    { title: '趋势', dataIndex: 'convergence_trend', key: 'convergence_trend', render: (trend: string) => {
      if (trend === 'converging') return <Tag color="red">🔥 正在收敛</Tag>;
      if (trend === 'diverging') return <Tag color="orange">正在分散</Tag>;
      return <Tag>稳定</Tag>;
    }},
    { title: '操作', key: 'action', render: (_: any, record: any) => <Button type="link" href={`/whales/${record.wallet}`}>查看详情</Button> },
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

  return (
    <div>
      <h1 style={{ marginBottom: 24 }}>🐋 鲸鱼跟踪</h1>
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
      </Tabs>
    </div>
  );
};

export default Whales;
