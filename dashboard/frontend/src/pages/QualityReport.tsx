import React, { useEffect, useState } from 'react';
import { Card, Table, Tag, Button, Tabs, Statistic, Row, Col, Select, message, Spin, Empty, Progress, DatePicker, Timeline, Alert, List, Divider } from 'antd';
import { signalsAPI } from '../services/api';
import { 
  TrophyOutlined, 
  FireOutlined, 
  LineChartOutlined, 
  ReloadOutlined, 
  CheckCircleOutlined, 
  CloseCircleOutlined,
  ClockCircleOutlined,
  SettingOutlined,
  HistoryOutlined,
  DashboardOutlined,
  ArrowUpOutlined,
  ArrowDownOutlined
} from '@ant-design/icons';
import { Line, Column } from '@ant-design/charts';
import moment from 'moment';

const { TabPane } = Tabs;
const { Option } = Select;
const { RangePicker } = DatePicker;

interface SignalStats {
  total: number;
  wins: number;
  losses: number;
  pending: number;
  expired: number;
  win_rate: number;
  avg_pnl: number;
  total_pnl: number;
  pending_count: number;
  resolved_count: number;
  by_type: TypeStat[];
}

interface TypeStat {
  signal_type: string;
  count: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_pnl: number;
}

interface QualityReport {
  id: number;
  report_type: string;
  start_date: string;
  end_date: string;
  total_signals: number;
  win_count: number;
  loss_count: number;
  pending_count: number;
  win_rate: number;
  avg_pnl: number;
  total_pnl: number;
  best_strategy: string;
  worst_strategy: string;
  parsed_data?: {
    summary: any;
    by_type: any;
    confidence_distribution: any;
    time_trend: any[];
    recommendations: string[];
  };
}

const QualityReportPage: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<SignalStats | null>(null);
  const [reports, setReports] = useState<QualityReport[]>([]);
  const [latestReport, setLatestReport] = useState<QualityReport | null>(null);
  const [pendingSignals, setPendingSignals] = useState<any[]>([]);
  const [thresholdHistory, setThresholdHistory] = useState<any[]>([]);
  const [activeTab, setActiveTab] = useState('overview');
  const [days, setDays] = useState(30);

  useEffect(() => {
    fetchDashboardData();
  }, [days]);

  const fetchDashboardData = async () => {
    setLoading(true);
    try {
      const [dashboardRes, reportsRes, pendingRes, thresholdRes] = await Promise.all([
        signalsAPI.getDashboard({ days }),
        signalsAPI.getReports({ limit: 10 }),
        signalsAPI.getPending({ limit: 50 }),
        signalsAPI.getThresholdHistory({ limit: 10 })
      ]);

      if (dashboardRes.data.success) {
        setStats(dashboardRes.data.data.overall);
      }
      
      setReports(reportsRes.data.data || []);
      
      // 获取最新报告详情
      const latestRes = await signalsAPI.getLatestReport({ type: 'weekly' });
      if (latestRes.data.success) {
        setLatestReport(latestRes.data.data);
      }
      
      setPendingSignals(pendingRes.data.data || []);
      setThresholdHistory(thresholdRes.data.data || []);
    } catch (error) {
      console.error('Failed to fetch dashboard data:', error);
      message.error('加载数据失败');
    } finally {
      setLoading(false);
    }
  };

  const generateReport = async () => {
    try {
      message.loading('正在生成报告...', 0);
      // 这里调用后端生成报告的API
      await fetchDashboardData();
      message.destroy();
      message.success('报告生成完成');
    } catch (error) {
      message.destroy();
      message.error('生成报告失败');
    }
  };

  const getTypeColor = (type: string) => {
    const colors: Record<string, string> = {
      whale: 'blue',
      arbitrage: 'green',
      semantic: 'purple',
      news: 'orange'
    };
    return colors[type] || 'default';
  };

  const getTypeName = (type: string) => {
    const names: Record<string, string> = {
      whale: '鲸鱼信号',
      arbitrage: '套利信号',
      semantic: '语义套利',
      news: '新闻驱动'
    };
    return names[type] || type;
  };

  // 趋势图表配置
  const trendConfig = latestReport?.parsed_data?.time_trend ? {
    data: latestReport.parsed_data.time_trend,
    xField: 'date',
    yField: 'win_rate',
    smooth: true,
    point: {
      size: 4,
      shape: 'circle',
    },
    label: {
      style: {
        fill: '#aaa',
      },
    },
    yAxis: {
      label: {
        formatter: (v: number) => `${v.toFixed(1)}%`,
      },
    },
    tooltip: {
      formatter: (datum: any) => {
        return { name: '胜率', value: `${datum.win_rate.toFixed(1)}%` };
      },
    },
  } : null;

  // 类型分布图表配置
  const typeDistributionConfig = stats?.by_type ? {
    data: stats.by_type,
    xField: 'signal_type',
    yField: 'count',
    label: {
      position: 'top',
    },
    xAxis: {
      label: {
        formatter: (v: string) => getTypeName(v),
      },
    },
    color: ({ signal_type }: { signal_type: string }) => {
      const colors: Record<string, string> = {
        whale: '#1890ff',
        arbitrage: '#52c41a',
        semantic: '#722ed1',
        news: '#fa8c16'
      };
      return colors[signal_type] || '#999';
    },
  } : null;

  const columns = [
    {
      title: '类型',
      dataIndex: 'signal_type',
      key: 'type',
      render: (type: string) => <Tag color={getTypeColor(type)}>{getTypeName(type)}</Tag>,
    },
    {
      title: '预测',
      dataIndex: 'prediction',
      key: 'prediction',
      ellipsis: true,
    },
    {
      title: '市场',
      dataIndex: 'market_name',
      key: 'market',
      render: (name: string) => name || '-',
    },
    {
      title: '置信度',
      dataIndex: 'confidence',
      key: 'confidence',
      render: (v: number) => v ? `${(v * 100).toFixed(0)}%` : '-',
    },
    {
      title: '触发价格',
      dataIndex: 'trigger_price',
      key: 'trigger_price',
      render: (v: number) => v ? `$${v.toFixed(3)}` : '-',
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      render: (v: string) => moment(v).format('MM-DD HH:mm'),
    },
    {
      title: '状态',
      dataIndex: 'actual_result',
      key: 'status',
      render: (v: string) => {
        if (v === 'win') return <Tag color="success" icon={<CheckCircleOutlined />}>盈利</Tag>;
        if (v === 'loss') return <Tag color="error" icon={<CloseCircleOutlined />}>亏损</Tag>;
        if (v === 'pending') return <Tag color="processing" icon={<ClockCircleOutlined />}>待处理</Tag>;
        return <Tag>过期</Tag>;
      },
    },
  ];

  return (
    <div style={{ padding: '24px' }}>
      <Card 
        title={<><DashboardOutlined /> 信号质量监控</>}
        extra={
          <div>
            <Select value={days} onChange={setDays} style={{ width: 120, marginRight: 16 }}>
              <Option value={7}>最近7天</Option>
              <Option value={30}>最近30天</Option>
              <Option value={90}>最近90天</Option>
            </Select>
            <Button 
              type="primary" 
              icon={<ReloadOutlined />} 
              onClick={fetchDashboardData}
              loading={loading}
            >
              刷新
            </Button>
          </div>
        }
      >
        <Tabs activeKey={activeTab} onChange={setActiveTab}>
          <TabPane tab="总览" key="overview">
            <Row gutter={16} style={{ marginBottom: 24 }}>
              <Col span={6}>
                <Card>
                  <Statistic
                    title="总信号数"
                    value={stats?.total || 0}
                    prefix={<FireOutlined />}
                  />
                </Card>
              </Col>
              <Col span={6}>
                <Card>
                  <Statistic
                    title="胜率"
                    value={stats?.win_rate || 0}
                    precision={1}
                    suffix="%"
                    valueStyle={{ 
                      color: (stats?.win_rate || 0) >= 50 ? '#3f8600' : '#cf1322' 
                    }}
                    prefix={(stats?.win_rate || 0) >= 50 ? <ArrowUpOutlined /> : <ArrowDownOutlined />}
                  />
                </Card>
              </Col>
              <Col span={6}>
                <Card>
                  <Statistic
                    title="总盈亏"
                    value={stats?.total_pnl || 0}
                    precision={2}
                    suffix="%"
                    valueStyle={{ 
                      color: (stats?.total_pnl || 0) >= 0 ? '#3f8600' : '#cf1322' 
                    }}
                    prefix={(stats?.total_pnl || 0) >= 0 ? <ArrowUpOutlined /> : <ArrowDownOutlined />}
                  />
                </Card>
              </Col>
              <Col span={6}>
                <Card>
                  <Statistic
                    title="待处理"
                    value={stats?.pending_count || 0}
                    prefix={<ClockCircleOutlined />}
                    valueStyle={{ color: '#faad14' }}
                  />
                </Card>
              </Col>
            </Row>

            {stats?.by_type && stats.by_type.length > 0 && (
              <Row gutter={16} style={{ marginBottom: 24 }}>
                <Col span={24}>
                  <Card title="各策略表现">
                    <Row gutter={16}>
                      {stats.by_type.map((type: TypeStat) => (
                        <Col span={6} key={type.signal_type}>
                          <Card size="small" title={getTypeName(type.signal_type)}>
                            <Row>
                              <Col span={12}>
                                <Statistic 
                                  title="信号数" 
                                  value={type.count} 
                                  valueStyle={{ fontSize: 16 }}
                                />
                              </Col>
                              <Col span={12}>
                                <Statistic 
                                  title="胜率" 
                                  value={type.win_rate} 
                                  suffix="%"
                                  precision={1}
                                  valueStyle={{ 
                                    fontSize: 16,
                                    color: type.win_rate >= 50 ? '#3f8600' : '#cf1322'
                                  }}
                                />
                              </Col>
                            </Row>
                            <Progress 
                              percent={type.win_rate} 
                              size="small"
                              status={type.win_rate >= 50 ? 'success' : 'exception'}
                              style={{ marginTop: 8 }}
                            />
                          </Card>
                        </Col>
                      ))}
                    </Row>
                  </Card>
                </Col>
              </Row>
            )}

            {latestReport?.parsed_data?.recommendations && (
              <Row gutter={16} style={{ marginBottom: 24 }}>
                <Col span={24}>
                  <Card title="策略建议">
                    <List
                      dataSource={latestReport.parsed_data.recommendations}
                      renderItem={(item: string) => (
                        <List.Item>
                          <Alert message={item} type="info" showIcon />
                        </List.Item>
                      )}
                    />
                  </Card>
                </Col>
              </Row>
            )}
          </TabPane>

          <TabPane tab="趋势分析" key="trends">
            {latestReport?.parsed_data?.time_trend && (
              <Row gutter={16}>
                <Col span={24}>
                  <Card title="胜率趋势">
                    {trendConfig && <Line {...trendConfig} />}
                  </Card>
                </Col>
              </Row>
            )}
            
            {latestReport?.parsed_data?.confidence_distribution && (
              <Row gutter={16} style={{ marginTop: 24 }}>
                <Col span={24}>
                  <Card title="置信度分布">
                    <Row gutter={16}>
                      {Object.entries(latestReport.parsed_data.confidence_distribution).map(([key, value]: [string, any]) => (
                        <Col span={8} key={key}>
                          <Card size="small">
                            <Statistic
                              title={key === 'high_confidence' ? '高置信度 (>70%)' : 
                                     key === 'medium_confidence' ? '中置信度 (40-70%)' : '低置信度 (<40%)'}
                              value={value.win_rate}
                              suffix="%"
                              precision={1}
                            />
                            <div style={{ marginTop: 8 }}>
                              <Tag>信号数: {value.total}</Tag>
                              <Tag>盈利: {value.wins}</Tag>
                            </div>
                          </Card>
                        </Col>
                      ))}
                    </Row>
                  </Card>
                </Col>
              </Row>
            )}
          </TabPane>

          <TabPane tab="待处理信号" key="pending">
            <Table
              columns={columns}
              dataSource={pendingSignals}
              rowKey="id"
              loading={loading}
              pagination={{ pageSize: 10 }}
            />
          </TabPane>

          <TabPane tab="历史报告" key="reports">
            <Table
              columns={[
                { title: '类型', dataIndex: 'report_type', key: 'type' },
                { title: '周期', dataIndex: 'start_date', key: 'period', 
                  render: (_: any, record: QualityReport) => 
                    `${record.start_date?.slice(0, 10)} ~ ${record.end_date?.slice(0, 10)}` },
                { title: '总信号', dataIndex: 'total_signals', key: 'total' },
                { title: '胜率', dataIndex: 'win_rate', key: 'win_rate', 
                  render: (v: number) => `${v?.toFixed(1)}%` },
                { title: '总盈亏', dataIndex: 'total_pnl', key: 'pnl',
                  render: (v: number) => `${v?.toFixed(2)}%` },
                { title: '最佳策略', dataIndex: 'best_strategy', key: 'best' },
                { title: '最差策略', dataIndex: 'worst_strategy', key: 'worst' },
                { title: '生成时间', dataIndex: 'generated_at', key: 'time',
                  render: (v: string) => moment(v).format('YYYY-MM-DD HH:mm') },
              ]}
              dataSource={reports}
              rowKey="id"
              loading={loading}
            />
          </TabPane>

          <TabPane tab="阈值历史" key="thresholds">
            <Timeline mode="left">
              {thresholdHistory.map((item: any) => (
                <Timeline.Item 
                  key={item.id}
                  label={moment(item.created_at).format('YYYY-MM-DD HH:mm')}
                  dot={item.auto_optimized ? <SettingOutlined /> : <HistoryOutlined />}
                  color={item.auto_optimized ? 'blue' : 'green'}
                >
                  <Card size="small">
                    <p><strong>{item.threshold_type}</strong></p>
                    <p>
                      <Tag color="red">{item.old_value}</Tag>
                      <span> → </span>
                      <Tag
                      color="green">{item.new_value}</Tag>
                    </p>
                    {item.change_reason && <p style={{ color: '#666' }}>{item.change_reason}</p>}
                  </Card>
                </Timeline.Item>
              ))}
            </Timeline>
          </TabPane>
        </Tabs>
      </Card>
    </div>
  );
};

export default QualityReportPage;
