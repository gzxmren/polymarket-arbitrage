import React, { useState, useEffect } from 'react';
import { Card, Table, Button, Tag, Statistic, Row, Col, message, Spin } from 'antd';
import { SyncOutlined, TrophyOutlined, FireOutlined, DollarOutlined } from '@ant-design/icons';
import { api } from '../services/api';

interface LeaderboardWhale {
  wallet: string;
  rank: number;
  username: string;
  x_username: string;
  verified: boolean;
  volume: number;
  pnl: number;
  first_seen: string;
  last_updated: string;
  priority_level: string;
  watch_status: string;
}

interface LeaderboardSummary {
  total_tracked: number;
  priority_distribution: Record<string, number>;
  total_pnl: number;
  total_volume: number;
  active_today: number;
}

const LeaderboardWales: React.FC = () => {
  const [whales, setWhales] = useState<LeaderboardWhale[]>([]);
  const [summary, setSummary] = useState<LeaderboardSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [whalesRes, summaryRes] = await Promise.all([
        api.get('/whales/leaderboard'),
        api.get('/whales/leaderboard/summary')
      ]);
      
      if (whalesRes.data.success) {
        setWhales(whalesRes.data.whales);
      }
      
      if (summaryRes.data.success) {
        setSummary(summaryRes.data);
      }
    } catch (error) {
      message.error('获取数据失败');
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  const handleSync = async () => {
    setSyncing(true);
    try {
      const res = await api.post('/whales/leaderboard/sync');
      if (res.data.success) {
        message.success('同步成功！');
        fetchData();
      } else {
        message.error('同步失败: ' + res.data.error);
      }
    } catch (error) {
      message.error('同步请求失败');
      console.error(error);
    } finally {
      setSyncing(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case 'critical': return 'red';
      case 'high': return 'orange';
      case 'medium': return 'yellow';
      case 'low': return 'green';
      default: return 'default';
    }
  };

  const columns = [
    {
      title: '排名',
      dataIndex: 'rank',
      key: 'rank',
      width: 80,
      render: (rank: number) => {
        if (rank <= 3) {
          return <TrophyOutlined style={{ color: rank === 1 ? '#FFD700' : rank === 2 ? '#C0C0C0' : '#CD7F32', fontSize: 20 }} />;
        }
        return rank;
      }
    },
    {
      title: '用户名',
      dataIndex: 'username',
      key: 'username',
      render: (username: string, record: LeaderboardWhale) => (
        <div>
          <div>{username || record.wallet.slice(0, 20) + '...'}</div>
          {record.x_username && (
            <a href={`https://x.com/${record.x_username}`} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, color: '#1890ff' }}>
              @{record.x_username}
            </a>
          )}
        </div>
      )
    },
    {
      title: '钱包',
      dataIndex: 'wallet',
      key: 'wallet',
      render: (wallet: string) => (
        <span style={{ fontFamily: 'monospace', fontSize: 12 }}>
          {wallet.slice(0, 15)}...{wallet.slice(-4)}
        </span>
      )
    },
    {
      title: '优先级',
      dataIndex: 'priority_level',
      key: 'priority_level',
      render: (priority: string) => (
        <Tag color={getPriorityColor(priority)}>{priority.toUpperCase()}</Tag>
      )
    },
    {
      title: '盈亏 (PnL)',
      dataIndex: 'pnl',
      key: 'pnl',
      render: (pnl: number) => (
        <span style={{ color: pnl >= 0 ? '#52c41a' : '#f5222d', fontWeight: 'bold' }}>
          ${pnl.toLocaleString('en-US', { maximumFractionDigits: 0 })}
        </span>
      ),
      sorter: (a: LeaderboardWhale, b: LeaderboardWhale) => a.pnl - b.pnl
    },
    {
      title: '成交量',
      dataIndex: 'volume',
      key: 'volume',
      render: (volume: number) => (
        <span>${(volume / 1e6).toFixed(2)}M</span>
      ),
      sorter: (a: LeaderboardWhale, b: LeaderboardWhale) => a.volume - b.volume
    },
    {
      title: '状态',
      dataIndex: 'watch_status',
      key: 'watch_status',
      render: (status: string) => (
        <Tag color={status === 'active' ? 'green' : 'default'}>
          {status === 'active' ? <FireOutlined /> : null}
          {status}
        </Tag>
      )
    },
    {
      title: '最后更新',
      dataIndex: 'last_updated',
      key: 'last_updated',
      render: (date: string) => new Date(date).toLocaleString('zh-CN')
    }
  ];

  return (
    <div style={{ padding: 24 }}>
      <Card
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <TrophyOutlined style={{ fontSize: 24, color: '#FFD700' }} />
            <span style={{ fontSize: 20, fontWeight: 'bold' }}>Leaderboard 顶级鲸鱼</span>
          </div>
        }
        extra={
          <Button
            type="primary"
            icon={<SyncOutlined spin={syncing} />}
            onClick={handleSync}
            loading={syncing}
          >
            手动同步
          </Button>
        }
      >
        {summary && (
          <Row gutter={16} style={{ marginBottom: 24 }}>
            <Col span={6}>
              <Statistic
                title="跟踪总数"
                value={summary.total_tracked}
                prefix={<FireOutlined />}
              />
            </Col>
            <Col span={6}>
              <Statistic
                title="总盈亏"
                value={summary.total_pnl}
                prefix={<DollarOutlined />}
                formatter={(value) => `$${(value as number).toLocaleString('en-US', { maximumFractionDigits: 0 })}`}
              />
            </Col>
            <Col span={6}>
              <Statistic
                title="总成交量"
                value={summary.total_volume}
                prefix={<DollarOutlined />}
                formatter={(value) => `$${((value as number) / 1e6).toFixed(2)}M`}
              />
            </Col>
            <Col span={6}>
              <Statistic
                title="今日活跃"
                value={summary.active_today}
                prefix={<FireOutlined style={{ color: '#ff4d4f' }} />}
              />
            </Col>
          </Row>
        )}

        {summary && (
          <div style={{ marginBottom: 16 }}>
            <span style={{ marginRight: 16 }}>优先级分布:</span>
            {Object.entries(summary.priority_distribution).map(([priority, count]) => (
              <Tag key={priority} color={getPriorityColor(priority)} style={{ marginRight: 8 }}>
                {priority.toUpperCase()}: {count}
              </Tag>
            ))}
          </div>
        )}

        <Spin spinning={loading}>
          <Table
            columns={columns}
            dataSource={whales}
            rowKey="wallet"
            pagination={{ pageSize: 25 }}
            scroll={{ x: 1200 }}
          />
        </Spin>
      </Card>
    </div>
  );
};

export default LeaderboardWales;
