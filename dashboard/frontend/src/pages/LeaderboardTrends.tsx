import React, { useState, useEffect } from 'react';
import { 
  Card, Table, Button, Tag, Statistic, Row, Col, message, Spin, 
  Progress, Select, Tabs, Typography, Space, Tooltip, Empty
} from 'antd';
import { 
  SyncOutlined, FireOutlined, TrophyOutlined, ArrowUpOutlined, 
  ArrowDownOutlined, MinusOutlined, StarOutlined, LineChartOutlined,
  RiseOutlined, FallOutlined, QuestionCircleOutlined
} from '@ant-design/icons';
import { api } from '../services/api';
import type { ColumnsType } from 'antd/es/table';

const { Title, Text } = Typography;
const { TabPane } = Tabs;

interface TrendData {
  wallet: string;
  username: string;
  rank: number;
  pnl: number;
  volume: number;
  priority_level: string;
  rank_change_7d: number | null;
  rank_change_30d: number | null;
  pnl_change_7d: number | null;
  pnl_change_30d: number | null;
  trend_direction: string;
  momentum_score: number;
  updated_at: string;
}

interface TrendSummary {
  total_tracked: number;
  direction_distribution: Record<string, number>;
  momentum_distribution: Record<string, number>;
  average_momentum: number;
  hot_whales_count: number;
}

interface TrendDetail {
  wallet: string;
  username: string;
  current_rank: number;
  current_pnl: number;
  current_volume: number;
  rank_change_7d: number | null;
  rank_change_30d: number | null;
  pnl_change_7d: number | null;
  pnl_change_30d: number | null;
  trend_direction: string;
  momentum_score: number;
  history: Array<{
    date: string;
    rank: number;
    pnl: number;
    volume: number;
  }>;
}

/**
 * Leaderboard 趋势分析页面
 * 
 * 功能:
 * - 展示鲸鱼排名趋势 (上升、下降、稳定、新上榜)
 * - 动量评分可视化
 * - 趋势筛选和排序
 * - 单个鲸鱼趋势详情
 */
const LeaderboardTrends: React.FC = () => {
  const [trends, setTrends] = useState<TrendData[]>([]);
  const [summary, setSummary] = useState<TrendSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [updating, setUpdating] = useState(false);
  const [selectedTrend, setSelectedTrend] = useState<string | null>(null);
  const [selectedWhale, setSelectedWhale] = useState<TrendDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  /**
   * 获取趋势数据
   */
  const fetchTrends = async () => {
    setLoading(true);
    try {
      const params = selectedTrend ? { trend: selectedTrend } : {};
      const res = await api.get('/whales/leaderboard/trends', { params });
      
      if (res.data.success) {
        setTrends(res.data.trends);
      } else {
        message.error('获取趋势数据失败');
      }
    } catch (error) {
      message.error('请求失败');
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  /**
   * 获取趋势摘要
   */
  const fetchSummary = async () => {
    try {
      const res = await api.get('/whales/leaderboard/trends/summary');
      
      if (res.data.success) {
        setSummary(res.data.summary);
      }
    } catch (error) {
      console.error('获取摘要失败:', error);
    }
  };

  /**
   * 更新所有趋势数据
   */
  const handleUpdateTrends = async () => {
    setUpdating(true);
    try {
      const res = await api.post('/whales/leaderboard/trends/update');
      
      if (res.data.success) {
        const stats = res.data.stats;
        message.success(
          `更新完成！成功: ${stats.success}, 失败: ${stats.failed}, 新上榜: ${stats.new_entries}, 上升: ${stats.rising}`
        );
        fetchTrends();
        fetchSummary();
      } else {
        message.error('更新失败: ' + res.data.error);
      }
    } catch (error) {
      message.error('更新请求失败');
      console.error(error);
    } finally {
      setUpdating(false);
    }
  };

  /**
   * 获取鲸鱼趋势详情
   */
  const fetchWhaleDetail = async (wallet: string) => {
    setDetailLoading(true);
    try {
      const res = await api.get(`/whales/leaderboard/trends/${wallet}`);
      
      if (res.data.success) {
        setSelectedWhale(res.data.detail);
      } else {
        message.warning('未找到趋势详情');
      }
    } catch (error) {
      console.error('获取详情失败:', error);
    } finally {
      setDetailLoading(false);
    }
  };

  useEffect(() => {
    fetchTrends();
    fetchSummary();
  }, [selectedTrend]);

  /**
   * 获取趋势方向的颜色和图标
   */
  const getTrendStyle = (direction: string) => {
    switch (direction) {
      case 'rising':
        return { color: '#52c41a', icon: <RiseOutlined />, text: '快速上升' };
      case 'falling':
        return { color: '#f5222d', icon: <FallOutlined />, text: '排名下降' };
      case 'stable':
        return { color: '#1890ff', icon: <MinusOutlined />, text: '保持稳定' };
      case 'new':
        return { color: '#722ed1', icon: <StarOutlined />, text: '新上榜' };
      case 'mixed':
        return { color: '#faad14', icon: <QuestionCircleOutlined />, text: '混合趋势' };
      default:
        return { color: '#8c8c8c', icon: null, text: direction };
    }
  };

  /**
   * 获取优先级颜色
   */
  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case 'critical': return 'red';
      case 'high': return 'orange';
      case 'medium': return 'gold';
      case 'low': return 'green';
      default: return 'default';
    }
  };

  /**
   * 格式化金额
   */
  const formatMoney = (value: number | null) => {
    if (value === null) return 'N/A';
    const absValue = Math.abs(value);
    if (absValue >= 1e6) return `$${(absValue / 1e6).toFixed(2)}M`;
    if (absValue >= 1e3) return `$${(absValue / 1e3).toFixed(1)}K`;
    return `$${absValue.toFixed(0)}`;
  };

  /**
   * 渲染排名变化
   */
  const renderRankChange = (change: number | null) => {
    if (change === null) return <Text type="secondary">N/A</Text>;
    
    if (change > 0) {
      return (
        <Space>
          <ArrowUpOutlined style={{ color: '#52c41a' }} />
          <Text style={{ color: '#52c41a' }}>+{change}</Text>
        </Space>
      );
    } else if (change < 0) {
      return (
        <Space>
          <ArrowDownOutlined style={{ color: '#f5222d' }} />
          <Text style={{ color: '#f5222d' }}>{change}</Text>
        </Space>
      );
    } else {
      return <Text type="secondary">0</Text>;
    }
  };

  /**
   * 渲染盈亏变化
   */
  const renderPnlChange = (change: number | null) => {
    if (change === null) return <Text type="secondary">N/A</Text>;
    
    const color = change >= 0 ? '#52c41a' : '#f5222d';
    return (
      <Text style={{ color, fontWeight: 'bold' }}>
        {change >= 0 ? '+' : ''}{formatMoney(change)}
      </Text>
    );
  };

  /**
   * 趋势表格列定义
   */
  const columns: ColumnsType<TrendData> = [
    {
      title: '排名',
      dataIndex: 'rank',
      key: 'rank',
      width: 60,
      render: (rank: number) => (
        rank <= 3 ? (
          <TrophyOutlined style={{ 
            color: rank === 1 ? '#FFD700' : rank === 2 ? '#C0C0C0' : '#CD7F32',
            fontSize: 18
          }} />
        ) : (
          <Text strong>{rank}</Text>
        )
      )
    },
    {
      title: '用户名',
      dataIndex: 'username',
      key: 'username',
      width: 150,
      render: (username: string, record) => (
        <a onClick={() => fetchWhaleDetail(record.wallet)}>
          {username || record.wallet.slice(0, 15) + '...'}
        </a>
      )
    },
    {
      title: '趋势方向',
      dataIndex: 'trend_direction',
      key: 'trend_direction',
      width: 120,
      filters: [
        { text: '快速上升', value: 'rising' },
        { text: '排名下降', value: 'falling' },
        { text: '保持稳定', value: 'stable' },
        { text: '新上榜', value: 'new' },
        { text: '混合趋势', value: 'mixed' },
      ],
      onFilter: (value, record) => record.trend_direction === value,
      render: (direction: string) => {
        const style = getTrendStyle(direction);
        return (
          <Tag color={style.color} icon={style.icon}>
            {style.text}
          </Tag>
        );
      }
    },
    {
      title: '排名变化 (7天)',
      dataIndex: 'rank_change_7d',
      key: 'rank_change_7d',
      width: 100,
      sorter: (a, b) => (a.rank_change_7d || 0) - (b.rank_change_7d || 0),
      render: renderRankChange
    },
    {
      title: '排名变化 (30天)',
      dataIndex: 'rank_change_30d',
      key: 'rank_change_30d',
      width: 100,
      sorter: (a, b) => (a.rank_change_30d || 0) - (b.rank_change_30d || 0),
      render: renderRankChange
    },
    {
      title: '盈亏变化 (7天)',
      dataIndex: 'pnl_change_7d',
      key: 'pnl_change_7d',
      width: 120,
      sorter: (a, b) => (a.pnl_change_7d || 0) - (b.pnl_change_7d || 0),
      render: renderPnlChange
    },
    {
      title: '动量评分',
      dataIndex: 'momentum_score',
      key: 'momentum_score',
      width: 150,
      sorter: (a, b) => a.momentum_score - b.momentum_score,
      render: (score: number) => (
        <Progress 
          percent={score} 
          size="small"
          strokeColor={
            score >= 70 ? '#52c41a' :
            score >= 50 ? '#1890ff' :
            score >= 30 ? '#faad14' : '#f5222d'
          }
          format={(percent) => `${percent?.toFixed(0)}`}
        />
      )
    },
    {
      title: '优先级',
      dataIndex: 'priority_level',
      key: 'priority_level',
      width: 80,
      render: (priority: string) => (
        <Tag color={getPriorityColor(priority)}>
          {priority?.toUpperCase()}
        </Tag>
      )
    },
    {
      title: '当前盈亏',
      dataIndex: 'pnl',
      key: 'pnl',
      width: 100,
      sorter: (a, b) => a.pnl - b.pnl,
      render: (pnl: number) => (
        <Text style={{ color: pnl >= 0 ? '#52c41a' : '#f5222d', fontWeight: 'bold' }}>
          {formatMoney(pnl)}
        </Text>
      )
    }
  ];

  /**
   * 趋势筛选按钮
   */
  const trendFilters = [
    { key: null, label: '全部', icon: <LineChartOutlined /> },
    { key: 'rising', label: '🔥 快速上升', color: '#52c41a' },
    { key: 'stable', label: '➡️ 保持稳定', color: '#1890ff' },
    { key: 'falling', label: '⬇️ 排名下降', color: '#f5222d' },
    { key: 'new', label: '✨ 新上榜', color: '#722ed1' },
  ];

  return (
    <div style={{ padding: 24 }}>
      <Card
        title={
          <Space>
            <FireOutlined style={{ fontSize: 24, color: '#ff4d4f' }} />
            <Title level={4} style={{ margin: 0 }}>
              Leaderboard 历史趋势分析
            </Title>
          </Space>
        }
        extra={
          <Button
            type="primary"
            icon={<SyncOutlined spin={updating} />}
            onClick={handleUpdateTrends}
            loading={updating}
          >
            更新趋势数据
          </Button>
        }
      >
        {/* 摘要统计 */}
        {summary && (
          <Row gutter={16} style={{ marginBottom: 24 }}>
            <Col span={4}>
              <Statistic
                title="跟踪总数"
                value={summary.total_tracked}
                prefix={<TrophyOutlined />}
              />
            </Col>
            <Col span={4}>
              <Statistic
                title="平均动量"
                value={summary.average_momentum}
                suffix="分"
                prefix={<FireOutlined />}
              />
            </Col>
            <Col span={4}>
              <Statistic
                title="热门鲸鱼"
                value={summary.hot_whales_count}
                prefix={<RiseOutlined style={{ color: '#52c41a' }} />}
              />
            </Col>
            <Col span={4}>
              <Statistic
                title="快速上升"
                value={summary.direction_distribution?.rising || 0}
                valueStyle={{ color: '#52c41a' }}
              />
            </Col>
            <Col span={4}>
              <Statistic
                title="快速下降"
                value={summary.direction_distribution?.falling || 0}
                valueStyle={{ color: '#f5222d' }}
              />
            </Col>
            <Col span={4}>
              <Statistic
                title="新上榜"
                value={summary.direction_distribution?.new || 0}
                valueStyle={{ color: '#722ed1' }}
              />
            </Col>
          </Row>
        )}

        {/* 趋势筛选 */}
        <Space style={{ marginBottom: 16 }}>
          <Text>趋势筛选:</Text>
          {trendFilters.map(filter => (
            <Button
              key={filter.key || 'all'}
              type={selectedTrend === filter.key ? 'primary' : 'default'}
              onClick={() => setSelectedTrend(filter.key)}
              style={filter.color ? { borderColor: filter.color } : {}}
            >
              {filter.label}
            </Button>
          ))}
        </Space>

        <Tabs defaultActiveKey="table">
          <TabPane tab="趋势列表" key="table">
            <Spin spinning={loading}>
              {trends.length > 0 ? (
                <Table
                  columns={columns}
                  dataSource={trends}
                  rowKey="wallet"
                  pagination={{ pageSize: 25 }}
                  scroll={{ x: 1200 }}
                  onRow={(record) => ({
                    onClick: () => fetchWhaleDetail(record.wallet),
                    style: { cursor: 'pointer' }
                  })}
                />
              ) : (
                <Empty description="暂无趋势数据，请点击'更新趋势数据'按钮" />
              )}
            </Spin>
          </TabPane>
          
          <TabPane tab="鲸鱼详情" key="detail">
            <Spin spinning={detailLoading}>
              {selectedWhale ? (
                <Card>
                  <Title level={5}>
                    {selectedWhale.username || selectedWhale.wallet.slice(0, 20) + '...'}
                  </Title>
                  
                  <Row gutter={16} style={{ marginBottom: 16 }}>
                    <Col span={6}>
                      <Statistic
                        title="当前排名"
                        value={selectedWhale.current_rank}
                        prefix={<TrophyOutlined />}
                      />
                    </Col>
                    <Col span={6}>
                      <Statistic
                        title="动量评分"
                        value={selectedWhale.momentum_score}
                        suffix="分"
                      />
                    </Col>
                    <Col span={6}>
                      <Statistic
                        title="排名变化 (7天)"
                        value={selectedWhale.rank_change_7d || 'N/A'}
                        prefix={
                          selectedWhale.rank_change_7d > 0 
                            ? <ArrowUpOutlined style={{ color: '#52c41a' }} />
                            : selectedWhale.rank_change_7d < 0
                              ? <ArrowDownOutlined style={{ color: '#f5222d' }} />
                              : null
                        }
                        valueStyle={{
                          color: selectedWhale.rank_change_7d > 0 ? '#52c41a' :
                                 selectedWhale.rank_change_7d < 0 ? '#f5222d' : '#8c8c8c'
                        }}
                      />
                    </Col>
                    <Col span={6}>
                      <Statistic
                        title="盈亏变化 (7天)"
                        value={formatMoney(selectedWhale.pnl_change_7d)}
                        valueStyle={{
                          color: selectedWhale.pnl_change_7d >= 0 ? '#52c41a' : '#f5222d'
                        }}
                      />
                    </Col>
                  </Row>

                  {/* 趋势方向 */}
                  <Space style={{ marginBottom: 16 }}>
                    <Text>趋势方向:</Text>
                    <Tag color={getTrendStyle(selectedWhale.trend_direction).color}>
                      {getTrendStyle(selectedWhale.trend_direction).text}
                    </Tag>
                  </Space>

                  {/* 历史数据 */}
                  {selectedWhale.history && selectedWhale.history.length > 0 && (
                    <Card title="历史排名变化 (最近30天)" size="small">
                      <Table
                        dataSource={selectedWhale.history}
                        rowKey="date"
                        pagination={false}
                        size="small"
                        columns={[
                          { title: '日期', dataIndex: 'date', key: 'date' },
                          { title: '排名', dataIndex: 'rank', key: 'rank' },
                          { 
                            title: '盈亏', 
                            dataIndex: 'pnl', 
                            key: 'pnl',
                            render: (v: number) => formatMoney(v)
                          },
                          { 
                            title: '成交量', 
                            dataIndex: 'volume', 
                            key: 'volume',
                            render: (v: number) => formatMoney(v)
                          }
                        ]}
                      />
                    </Card>
                  )}
                </Card>
              ) : (
                <Empty description="点击表格中的用户名查看详情" />
              )}
            </Spin>
          </TabPane>
        </Tabs>
      </Card>
    </div>
  );
};

export default LeaderboardTrends;