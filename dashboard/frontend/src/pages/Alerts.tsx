import React, { useState, useEffect } from 'react';
import { Card, Tabs, Table, Tag, Button, Badge, Empty, Popconfirm, message, Modal, Descriptions } from 'antd';
import { BellOutlined, CheckOutlined, DeleteOutlined, EyeOutlined } from '@ant-design/icons';
import { getAlerts, markAlertRead } from '../services/api';

const Alerts: React.FC = () => {
  const [alerts, setAlerts] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('all');
  const [detailModalVisible, setDetailModalVisible] = useState(false);
  const [selectedAlert, setSelectedAlert] = useState<any>(null);

  useEffect(() => {
    fetchAlerts();
  }, [activeTab]);

  const fetchAlerts = async () => {
    setLoading(true);
    try {
      const params: any = { limit: 50 };
      if (activeTab === 'unread') {
        params.unread_only = 'true';
      } else if (activeTab !== 'all') {
        params.type = activeTab;
      }
      
      const response = await getAlerts(params);
      setAlerts(response.data.alerts || []);
    } catch (error) {
      console.error('Failed to fetch alerts:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleMarkRead = async (id: number) => {
    try {
      await markAlertRead(id);
      message.success('已标记为已读');
      fetchAlerts();
    } catch (error) {
      message.error('标记失败');
    }
  };

  const handleMarkAllRead = async () => {
    const unreadAlerts = alerts.filter(a => !a.is_read);
    for (const alert of unreadAlerts) {
      await markAlertRead(alert.id);
    }
    message.success(`已标记 ${unreadAlerts.length} 条警报为已读`);
    fetchAlerts();
  };

  const getAlertTypeColor = (type: string) => {
    switch (type) {
      case 'whale': return 'blue';
      case 'arbitrage': return 'green';
      case 'system': return 'orange';
      default: return 'default';
    }
  };

  const getAlertTypeLabel = (type: string) => {
    switch (type) {
      case 'whale': return '鲸鱼';
      case 'arbitrage': return '套利';
      case 'system': return '系统';
      default: return type;
    }
  };

  const columns = [
    {
      title: '状态',
      dataIndex: 'is_read',
      key: 'is_read',
      width: 80,
      render: (isRead: boolean) => (
        <Badge status={isRead ? 'default' : 'processing'} text={isRead ? '已读' : '未读'} />
      ),
    },
    {
      title: '类型',
      key: 'type',
      width: 100,
      render: (_: any, record: any) => {
        // 从record.type获取警报类型（whale/arbitrage/system）
        const alertType = record.type || 'whale';
        return <Tag color={getAlertTypeColor(alertType)}>{getAlertTypeLabel(alertType)}</Tag>;
      },
    },
    {
      title: '标题',
      dataIndex: 'data',
      key: 'title',
      render: (data: string, record: any) => {
        try {
          const parsedData = JSON.parse(data || '{}');
          const wallet = parsedData.wallet;
          const shortWallet = wallet ? `${wallet.slice(0, 8)}...${wallet.slice(-6)}` : 'Unknown';
          return <strong style={{ opacity: record.is_read ? 0.6 : 1 }}>鲸鱼 {shortWallet}</strong>;
        } catch {
          return <strong style={{ opacity: record.is_read ? 0.6 : 1 }}>{record.title}</strong>;
        }
      },
    },
    {
      title: '内容',
      dataIndex: 'data',
      key: 'content',
      ellipsis: true,
      render: (data: string, record: any) => {
        try {
          const parsedData = JSON.parse(data || '{}');
          const messageType = record.message;
          const typeLabels: any = {
            'new_watched': '新加入重点监控',
            'activity': '有新的交易活动',
            'new': '新建仓',
            'increased': '加仓',
            'decreased': '减仓',
            'closed': '清仓',
          };
          return typeLabels[messageType] || messageType;
        } catch {
          return record.message;
        }
      },
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (time: string) => new Date(time).toLocaleString('zh-CN'),
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_: any, record: any) => (
        <>
          <Button 
            type="link" 
            icon={<EyeOutlined />}
            onClick={() => handleViewDetail(record)}
          >
            查看
          </Button>
          {!record.is_read && (
            <Button 
              type="link" 
              icon={<CheckOutlined />}
              onClick={() => handleMarkRead(record.id)}
            >
              已读
            </Button>
          )}
        </>
      ),
    },
  ];

  const handleViewDetail = (record: any) => {
    setSelectedAlert(record);
    setDetailModalVisible(true);
    // 标记为已读
    if (!record.is_read) {
      handleMarkRead(record.id);
    }
  };

  const renderAlertDetail = () => {
    if (!selectedAlert) return null;
    
    try {
      const data = JSON.parse(selectedAlert.data || '{}');
      const wallet = data.wallet || '';
      const shortWallet = wallet ? `${wallet.slice(0, 8)}...${wallet.slice(-6)}` : 'Unknown';
      
      // 这里应该获取鲸鱼的完整数据
      // 暂时显示基础信息
      return (
        <div>
          <div style={{ 
            background: '#f6ffed', 
            border: '1px solid #b7eb8f', 
            borderRadius: 8, 
            padding: 16,
            marginBottom: 16
          }}>
            <h3 style={{ margin: '0 0 12px 0', color: '#52c41a' }}>🐋 鲸鱼活动警报</h3>
            <p style={{ margin: '4px 0', fontSize: 16 }}>
              <strong>交易者:</strong> {shortWallet}
            </p>
            <p style={{ margin: '4px 0', color: '#666' }}>
              <code>{wallet}</code>
            </p>
          </div>

          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="警报类型">
              {selectedAlert.message === 'new_watched' ? '🆕 新加入重点监控' : 
               selectedAlert.message === 'activity' ? '📈 交易活动' : '💼 持仓变动'}
            </Descriptions.Item>
            <Descriptions.Item label="时间">
              {new Date(data.timestamp).toLocaleString('zh-CN')}
            </Descriptions.Item>
            <Descriptions.Item label="钱包地址">
              {wallet}
            </Descriptions.Item>
          </Descriptions>

          <div style={{ marginTop: 16, padding: 12, background: '#f5f5f5', borderRadius: 4 }}>
            <p style={{ margin: 0, color: '#666' }}>
              💡 <strong>提示:</strong> 该鲸鱼有新的交易活动，建议关注其持仓方向。
              可作为市场情绪参考，但需独立判断。
            </p>
          </div>

          <div style={{ marginTop: 16, textAlign: 'center' }}>
            <Button 
              type="primary" 
              href={`/whales/${wallet}`}
              target="_blank"
            >
              查看鲸鱼详情 →
            </Button>
          </div>
        </div>
      );
    } catch (e) {
      return <div>无法解析警报详情</div>;
    }
  };

  const unreadCount = alerts.filter(a => !a.is_read).length;

  const tabItems = [
    {
      key: 'all',
      label: `全部 (${alerts.length})`,
    },
    {
      key: 'unread',
      label: (
        <span>
          未读 {unreadCount > 0 && <Badge count={unreadCount} style={{ marginLeft: 4 }} />}
        </span>
      ),
    },
    {
      key: 'whale',
      label: '鲸鱼',
    },
    {
      key: 'arbitrage',
      label: '套利',
    },
    {
      key: 'system',
      label: '系统',
    },
  ];

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <h1>🔔 警报中心</h1>
        {unreadCount > 0 && (
          <Popconfirm
            title="确定要标记所有警报为已读吗？"
            onConfirm={handleMarkAllRead}
            okText="确定"
            cancelText="取消"
          >
            <Button type="primary" icon={<CheckOutlined />}>
              全部标记已读 ({unreadCount})
            </Button>
          </Popconfirm>
        )}
      </div>
      
      <Card>
        <Tabs 
          items={tabItems} 
          activeKey={activeTab}
          onChange={setActiveTab}
        />
        {alerts.length > 0 ? (
          <Table 
            columns={columns} 
            dataSource={alerts} 
            rowKey="id"
            loading={loading}
            pagination={{ pageSize: 20 }}
          />
        ) : (
          <Empty description="暂无警报" />
        )}
      </Card>

      {/* 详情弹窗 */}
      <Modal
        title="🔔 警报详情"
        open={detailModalVisible}
        onCancel={() => setDetailModalVisible(false)}
        footer={[
          <Button key="close" onClick={() => setDetailModalVisible(false)}>
            关闭
          </Button>,
        ]}
        width={600}
      >
        {renderAlertDetail()}
      </Modal>
    </div>
  );
};

export default Alerts;
