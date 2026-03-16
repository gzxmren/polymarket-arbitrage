import React, { useEffect, useState } from 'react';
import { Card, Row, Col, Statistic, List, Badge } from 'antd';
import { TeamOutlined, LineChartOutlined, BellOutlined } from '@ant-design/icons';
import { getSummary } from '../services/api';

const Dashboard: React.FC = () => {
  const [summary, setSummary] = useState<any>(null);

  useEffect(() => {
    fetchSummary();
  }, []);

  const fetchSummary = async () => {
    try {
      const data = await getSummary();
      setSummary(data);
    } catch (error) {
      console.error('Failed to fetch summary:', error);
    }
  };

  return (
    <div>
      <h1 style={{ marginBottom: 24 }}>📊 仪表盘</h1>
      
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={8}>
          <Card>
            <Statistic
              title="重点鲸鱼"
              value={summary?.whales?.watched_count || 0}
              prefix={<TeamOutlined />}
              suffix="位"
            />
            <div style={{ marginTop: 8, color: '#52c41a' }}>
              💰 ${(summary?.whales?.total_value || 0).toLocaleString()}
            </div>
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="套利机会"
              value={0}
              prefix={<LineChartOutlined />}
              suffix="个"
            />
            <div style={{ marginTop: 8, color: '#999' }}>
              当前无机会
            </div>
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="未读警报"
              value={summary?.alerts?.unread_count || 0}
              prefix={<BellOutlined />}
              suffix="条"
            />
            <div style={{ marginTop: 8, color: '#ff4d4f' }}>
              需要关注
            </div>
          </Card>
        </Col>
      </Row>

      <Card title="🔔 最新警报" style={{ marginBottom: 24 }}>
        <List
          dataSource={summary?.alerts?.recent || []}
          renderItem={(item: any) => (
            <List.Item>
              <List.Item.Meta
                title={
                  <span>
                    <Badge status={item.is_read ? 'default' : 'processing'} />
                    {item.title}
                  </span>
                }
                description={item.message}
              />
            </List.Item>
          )}
        />
      </Card>
    </div>
  );
};

export default Dashboard;
