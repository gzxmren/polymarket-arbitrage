import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Card, Tabs, Table, Tag, Button, Badge, Empty, Popconfirm, message, Modal, Descriptions, Spin, Alert } from 'antd';
import { BellOutlined, CheckOutlined, EyeOutlined, ReloadOutlined } from '@ant-design/icons';
import { getAlerts, markAlertRead, getAlertDetail } from '../services/api';

const WS_URL = process.env.REACT_APP_WS_URL || 'ws://localhost:5000';

const Alerts: React.FC = () => {
  const [alerts, setAlerts] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('all');
  const [detailModalVisible, setDetailModalVisible] = useState(false);
  const [selectedAlert, setSelectedAlert] = useState<any>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [wsConnected, setWsConnected] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const fetchAlerts = useCallback(async () => {
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
      setLastUpdated(new Date());
    } catch (error) {
      console.error('Failed to fetch alerts:', error);
    } finally {
      setLoading(false);
    }
  }, [activeTab]);

  useEffect(() => {
    fetchAlerts();
  }, [fetchAlerts]);

  // WebSocket 连接
  useEffect(() => {
    const connectWebSocket = () => {
      try {
        const ws = new WebSocket(WS_URL);
        
        ws.onopen = () => {
          console.log('WebSocket connected');
          setWsConnected(true);
        };
        
        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            if (data.type === 'alert') {
              fetchAlerts();
              message.info(`新警报: ${data.alert?.title || '收到新消息'}`);
            }
          } catch (e) {
            console.error('WebSocket message parse error:', e);
          }
        };
        
        ws.onclose = () => {
          console.log('WebSocket disconnected');
          setWsConnected(false);
          setTimeout(connectWebSocket, 5000);
        };
        
        ws.onerror = (error) => {
          console.error('WebSocket error:', error);
          setWsConnected(false);
        };
        
        wsRef.current = ws;
      } catch (e) {
        console.error('WebSocket connection error:', e);
      }
    };
    
    connectWebSocket();
    
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [fetchAlerts]);

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

  const handleViewDetail = async (record: any) => {
    setDetailLoading(true);
    setSelectedAlert(record);
    setDetailModalVisible(true);
    
    try {
      const response = await getAlertDetail(record.id);
      setSelectedAlert(response.data);
      
      if (!record.is_read) {
        handleMarkRead(record.id);
      }
    } catch (error) {
      console.error('获取警报详情失败:', error);
    } finally {
      setDetailLoading(false);
    }
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

  const getMessageTypeLabel = (messageType: string) => {
    const labels: any = {
      'new_watched': '新加入重点监控',
      'activity': '有新的交易活动',
      'new': '新建仓',
      'increased': '加仓',
      'decreased': '减仓',
      'closed': '清仓',
    };
    return labels[messageType] || messageType;
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
      dataIndex: 'message',
      key: 'message',
      ellipsis: true,
      render: (messageType: string) => getMessageTypeLabel(messageType),
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

  const renderAlertDetail = () => {
    if (!selectedAlert) return null;
    
    const data = selectedAlert.parsed_data || {};
    const wallet = data.wallet || '';
    const shortWallet = wallet ? `${wallet.slice(0, 8)}...${wallet.slice(-6)}` : 'Unknown';
    const whaleInfo = selectedAlert.whale_info;
    
    return (
      <Spin spinning={detailLoading}>
        <div>
          {/* 警报头部 */}
          <div style={{ 
            background: selectedAlert.type === 'whale' ? '#e6f7ff' : '#f6ffed', 
            border: `1px solid ${selectedAlert.type === 'whale' ? '#91d5ff' : '#b7eb8f'}`, 
            borderRadius: 8, 
            padding: 16,
            marginBottom: 16
          }}>
            <h3 style={{ margin: '0 0 12px 0', color: selectedAlert.type === 'whale' ? '#1890ff' : '#52c41a' }}>
              {selectedAlert.type === 'whale' ? '🐋 鲸鱼活动警报' : '🔔 系统警报'}
            </h3>
            <p style={{ margin: '4px 0', fontSize: 16 }}>
              <strong>交易者:</strong> {shortWallet}
            </p>
            <p style={{ margin: '4px 0', color: '#666' }}>
              <code>{wallet}</code>
            </p>
          </div>

          {/* 警报详情 */}
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="警报类型">
              {getMessageTypeLabel(selectedAlert.message)}
            </Descriptions.Item>
            <Descriptions.Item label="警报时间">
              {new Date(selectedAlert.created_at).toLocaleString('zh-CN')}
            </Descriptions.Item>
            {data.timestamp && (
              <Descriptions.Item label="事件时间">
                {new Date(data.timestamp).toLocaleString('zh-CN')}
              </Descriptions.Item>
            )}
          </Descriptions>

          {/* 鲸鱼信息 */}
          {whaleInfo && (
            <div style={{ marginTop: 16 }}>
              <h4>📊 鲸鱼概况</h4>
              <Descriptions column={2} bordered size="small">
                <Descriptions.Item label="总持仓价值">
                  ${whaleInfo.total_value?.toLocaleString()}
                </Descriptions.Item>
                <Descriptions.Item label="持仓市场数">
                  {whaleInfo.position_count}
                </Descriptions.Item>
                <Descriptions.Item label="Top5集中度">
                  {Math.round((whaleInfo.top5_ratio || 0) * 100)}%
                </Descriptions.Item>
                <Descriptions.Item label="盈亏">
                  <Tag color={whaleInfo.total_pnl > 0 ? 'green' : 'red'}>
                    {whaleInfo.total_pnl > 0 ? '+' : ''}${whaleInfo.total_pnl?.toLocaleString()}
                  </Tag>
                </Descriptions.Item>
              </Descriptions>
            </div>
          )}

          {/* 最近持仓 */}
          {selectedAlert.whale_positions && selectedAlert.whale_positions.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <h4>💼 主要持仓</h4>
              <Table 
                dataSource={selectedAlert.whale_positions}
                rowKey="market"
                size="small"
                pagination={false}
                columns={[
                  { title: '市场', dataIndex: 'market', key: 'market' },
                  { title: '方向', dataIndex: 'outcome', key: 'outcome', render: (t: string) => <Tag>{t}</Tag> },
                  { title: '价值', dataIndex: 'value', key: 'value', render: (v: number) => `$${v?.toLocaleString()}` },
                ]}
              />
            </div>
          )}

          {/* 最近变动 */}
          {selectedAlert.recent_changes && selectedAlert.recent_changes.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <h4>📈 最近变动</h4>
              <Table 
                dataSource={selectedAlert.recent_changes}
                rowKey="id"
                size="small"
                pagination={{ pageSize: 5 }}
                columns={[
                  { title: '时间', dataIndex: 'timestamp', key: 'timestamp', render: (t: string) => new Date(t).toLocaleString('zh-CN') },
                  { title: '类型', dataIndex: 'type', key: 'type', render: (t: string) => <Tag>{getMessageTypeLabel(t)}</Tag> },
                  { title: '市场', dataIndex: 'market', key: 'market' },
                  { title: '变动量', dataIndex: 'change_amount', key: 'change_amount', render: (v: number) => v ? `${v > 0 ? '+' : ''}${v.toFixed(2)}` : '-' },
                ]}
              />
            </div>
          )}

          {/* 提示信息 */}
          <div style={{ marginTop: 16, padding: 12, background: '#f5f5f5', borderRadius: 4 }}>
            <p style={{ margin: 0, color: '#666' }}>
              💡 <strong>提示:</strong> 该鲸鱼有新的交易活动，建议关注其持仓方向。
              可作为市场情绪参考，但需独立判断。
            </p>
          </div>

          {/* 操作按钮 */}
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
      </Spin>
    );
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
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <h1 style={{ margin: 0 }}>🔔 警报中心</h1>
          {wsConnected ? (
            <Tag color="green">● 实时连接</Tag>
          ) : (
            <Tag color="red">● 离线</Tag>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          {lastUpdated && (
            <span style={{ color: '#999', fontSize: 14 }}>
              更新于: {lastUpdated.toLocaleTimeString('zh-CN')}
            </span>
          )}
          <Button 
            icon={<ReloadOutlined />} 
            onClick={fetchAlerts}
            loading={loading}
          >
            刷新
          </Button>
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
        title="警报详情"
        open={detailModalVisible}
        onCancel={() => setDetailModalVisible(false)}
        footer={[
          <Button key="close" onClick={() => setDetailModalVisible(false)}>
            关闭
          </Button>,
        ]}
        width={700}
      >
        {renderAlertDetail()}
      </Modal>
    </div>
  );
};

export default Alerts;
