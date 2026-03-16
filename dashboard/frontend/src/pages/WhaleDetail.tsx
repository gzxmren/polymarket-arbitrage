import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Card, Descriptions, Table, Tag, Button, Spin, message, Row, Col, Alert, Progress, Statistic, Tabs } from 'antd';
import { ArrowLeftOutlined, WalletOutlined, ReloadOutlined, RobotOutlined, ReadOutlined } from '@ant-design/icons';
import { getWhaleDetail, getWhaleHistory, getWhaleAnalysis, getWhaleDeepAnalysis, generateWhaleDeepAnalysis } from '../services/api';
import ConcentrationChart from '../components/Charts/ConcentrationChart';
import WhaleNews from '../components/News/WhaleNews';

const WhaleDetail: React.FC = () => {
  const { wallet } = useParams<{ wallet: string }>();
  const navigate = useNavigate();
  const [whale, setWhale] = useState<any>(null);
  const [history, setHistory] = useState<any[]>([]);
  const [analysis, setAnalysis] = useState<any>(null);
  const [deepAnalysis, setDeepAnalysis] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [deepAnalysisLoading, setDeepAnalysisLoading] = useState(false);
  const [deepAnalysisGenerating, setDeepAnalysisGenerating] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());

  useEffect(() => {
    if (wallet) {
      fetchWhaleData();
      fetchAnalysis();
      fetchDeepAnalysis();
    }
  }, [wallet]);

  const fetchWhaleData = async () => {
    setLoading(true);
    try {
      const [detailRes, historyRes] = await Promise.all([
        getWhaleDetail(wallet!),
        getWhaleHistory(wallet!),
      ]);
      setWhale(detailRes.data);
      setHistory(historyRes.data.history || []);
    } catch (error) {
      message.error('获取鲸鱼数据失败');
    } finally {
      setLoading(false);
    }
  };

  const fetchAnalysis = async () => {
    setAnalysisLoading(true);
    try {
      const res = await getWhaleAnalysis(wallet!);
      setAnalysis(res.data);
      setLastUpdated(new Date());
    } catch (error) {
      console.error('获取分析失败:', error);
    } finally {
      setAnalysisLoading(false);
    }
  };

  const fetchDeepAnalysis = async () => {
    setDeepAnalysisLoading(true);
    try {
      const res = await getWhaleDeepAnalysis(wallet!);
      if (res.data && res.data.exists !== false) {
        setDeepAnalysis(res.data);
      }
    } catch (error) {
      console.error('获取深度分析失败:', error);
    } finally {
      setDeepAnalysisLoading(false);
    }
  };

  const generateDeepAnalysis = async (forceRefresh: boolean = false, retryCount: number = 0) => {
    setDeepAnalysisGenerating(true);
    try {
      const res = await generateWhaleDeepAnalysis(wallet!, forceRefresh);
      setDeepAnalysis(res.data);
      message.success('深度分析已生成');
    } catch (error: any) {
      console.error('生成深度分析失败:', error);
      
      // 详细错误信息
      let errorMsg = '生成深度分析失败';
      if (error.code === 'ECONNABORTED') {
        errorMsg = '请求超时，请稍后重试';
      } else if (error.response?.data?.error) {
        errorMsg = `生成失败: ${error.response.data.error}`;
      } else if (error.message) {
        errorMsg = `生成失败: ${error.message}`;
      }
      
      // 自动重试（最多3次）
      if (retryCount < 2) {
        console.log(`自动重试 ${retryCount + 1}/3...`);
        setTimeout(() => {
          generateDeepAnalysis(forceRefresh, retryCount + 1);
        }, 2000);
        return;  // 不设置loading为false，保持loading状态
      }
      
      message.error(errorMsg);
    } finally {
      setDeepAnalysisGenerating(false);
    }
  };

  const handleRefreshAnalysis = () => {
    fetchAnalysis();
    message.success('分析已更新');
  };

  const positionColumns = [
    {
      title: '市场',
      dataIndex: 'market',
      key: 'market',
      render: (text: string) => <strong>{text}</strong>,
    },
    {
      title: '方向',
      dataIndex: 'outcome',
      key: 'outcome',
      render: (outcome: string) => <Tag color="blue">{outcome}</Tag>,
    },
    {
      title: '持仓量',
      dataIndex: 'size',
      key: 'size',
      render: (size: number) => size?.toFixed(2) || 0,
    },
    {
      title: '均价',
      dataIndex: 'avg_price',
      key: 'avg_price',
      render: (price: number) => `$${price?.toFixed(3) || 0}`,
    },
    {
      title: '现价',
      dataIndex: 'cur_price',
      key: 'cur_price',
      render: (price: number) => `$${price?.toFixed(3) || 0}`,
    },
    {
      title: '价值',
      dataIndex: 'value',
      key: 'value',
      render: (value: number) => `$${value?.toLocaleString() || 0}`,
    },
    {
      title: '盈亏',
      dataIndex: 'pnl',
      key: 'pnl',
      render: (pnl: number) => (
        <Tag color={pnl > 0 ? 'green' : pnl < 0 ? 'red' : 'default'}>
          {pnl > 0 ? '+' : ''}${pnl?.toFixed(0) || 0}
        </Tag>
      ),
    },
  ];

  const changeColumns = [
    {
      title: '时间',
      dataIndex: 'timestamp',
      key: 'timestamp',
      render: (time: string) => new Date(time).toLocaleString('zh-CN'),
    },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      render: (type: string) => {
        const colors: any = {
          'new': 'green',
          'increased': 'blue',
          'decreased': 'orange',
          'closed': 'red',
        };
        const labels: any = {
          'new': '新建仓',
          'increased': '加仓',
          'decreased': '减仓',
          'closed': '清仓',
        };
        return <Tag color={colors[type] || 'default'}>{labels[type] || type}</Tag>;
      },
    },
    {
      title: '市场',
      dataIndex: 'market',
      key: 'market',
    },
    {
      title: '变动',
      key: 'change',
      render: (_: any, record: any) => {
        if (record.change_amount) {
          return `${record.change_amount > 0 ? '+' : ''}${record.change_amount.toFixed(2)}`;
        }
        return '-';
      },
    },
    {
      title: '现持仓',
      dataIndex: 'new_size',
      key: 'new_size',
      render: (size: number) => size?.toFixed(2) || '-',
    },
  ];

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '100px' }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!whale) {
    return (
      <div>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/whales')}>
          返回列表
        </Button>
        <p>鲸鱼数据不存在</p>
      </div>
    );
  }

  return (
    <div>
      <Button 
        icon={<ArrowLeftOutlined />} 
        onClick={() => navigate('/whales')}
        style={{ marginBottom: 16 }}
      >
        返回列表
      </Button>

      <h1 style={{ marginBottom: 24 }}>
        <WalletOutlined /> {whale.pseudonym}
      </h1>

      {/* AI 分析卡片 */}
      {analysis && (
        <Card 
          title={
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span><RobotOutlined /> AI 分析意见</span>
              <Button 
                size="small" 
                icon={<ReloadOutlined />}
                onClick={handleRefreshAnalysis}
                loading={analysisLoading}
              >
                刷新
              </Button>
            </div>
          }
          style={{ marginBottom: 16 }}
        >
          {/* 更新时间 */}
          <Alert 
            type="info" 
            showIcon={false}
            message={
              <div style={{ fontSize: '12px', color: '#666' }}>
                ⏰ 更新时间: {lastUpdated.toLocaleString()}
              </div>
            }
            style={{ marginBottom: 16 }}
          />

          <Row gutter={16}>
            {/* 左侧：核心指标 */}
            <Col span={12}>
              <Card size="small" title="📈 核心指标" style={{ marginBottom: 16 }}>
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="信号强度">
                    <Tag color={analysis.signal_strength.level === 'extreme' ? 'red' : analysis.signal_strength.level === 'high' ? 'orange' : 'default'}>
                      {analysis.signal_strength.emoji} {analysis.signal_strength.desc} ({analysis.signal_strength.changes_count}次变动)
                    </Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="策略判断">
                    <Tag color={analysis.strategy_assessment.top5_ratio >= 0.7 ? 'green' : analysis.strategy_assessment.top5_ratio >= 0.5 ? 'orange' : 'default'}>
                      {analysis.strategy_assessment.emoji} {analysis.strategy_assessment.concentration_level} ({Math.round(analysis.strategy_assessment.top5_ratio * 100)}%)
                    </Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="盈亏状态">
                    <span style={{ color: analysis.pnl_status.is_positive ? '#52c41a' : analysis.pnl_status.is_negative ? '#f5222d' : '#999' }}>
                      {analysis.pnl_status.emoji} {analysis.pnl_status.is_positive ? '+' : ''}${analysis.pnl_status.value?.toLocaleString()} ({analysis.pnl_status.is_positive ? '+' : ''}{analysis.pnl_status.percent}%)
                    </span>
                  </Descriptions.Item>
                  <Descriptions.Item label="操作建议">
                    <Tag color={analysis.recommendation.priority === 'high' ? 'red' : analysis.recommendation.priority === 'medium' ? 'orange' : 'default'}>
                      {analysis.recommendation.emoji} {analysis.recommendation.action}
                    </Tag>
                  </Descriptions.Item>
                </Descriptions>
                
                {/* 解读 */}
                <Alert 
                  message="💡 解读" 
                  description={analysis.interpretation}
                  type="info" 
                  style={{ marginTop: 16 }}
                />
                
                {/* 风险提示 */}
                {analysis.risk_warning && analysis.risk_warning !== "暂无显著风险" && (
                  <Alert 
                    message="⚠️ 风险提示" 
                    description={analysis.risk_warning}
                    type="warning" 
                    style={{ marginTop: 8 }}
                  />
                )}
              </Card>
            </Col>

            {/* 右侧：多维度评分 */}
            <Col span={12}>
              <Card size="small" title="📊 多维度评估" style={{ marginBottom: 16 }}>
                {/* 综合评分 */}
                <div style={{ marginBottom: 16 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                    <span>综合评分</span>
                    <span style={{ fontWeight: 'bold', color: analysis.composite_score >= 80 ? '#52c41a' : analysis.composite_score >= 60 ? '#faad14' : '#f5222d' }}>
                      ⭐ {analysis.composite_score}/100
                    </span>
                  </div>
                  <Progress percent={analysis.composite_score} status="active" strokeColor={analysis.composite_score >= 80 ? '#52c41a' : analysis.composite_score >= 60 ? '#faad14' : '#f5222d'} />
                </div>

                {/* 跟单评分 */}
                <div style={{ marginBottom: 16 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                    <span>适合跟单指数</span>
                    <span style={{ fontWeight: 'bold', color: analysis.copy_score >= 80 ? '#52c41a' : analysis.copy_score >= 60 ? '#faad14' : '#f5222d' }}>
                      🤖 {analysis.copy_score}/100
                    </span>
                  </div>
                  <Progress percent={analysis.copy_score} status="active" strokeColor={analysis.copy_score >= 80 ? '#52c41a' : analysis.copy_score >= 60 ? '#faad14' : '#f5222d'} />
                </div>

                {/* 分项评分 */}
                <Descriptions column={1} size="small">
                  <Descriptions.Item label="资金实力">
                    <Progress percent={analysis.dimensions.fund_strength.score} size="small" format={() => `${analysis.dimensions.fund_strength.score}分`} />
                    <span style={{ fontSize: '12px', color: '#666' }}>{analysis.dimensions.fund_strength.desc}</span>
                  </Descriptions.Item>
                  <Descriptions.Item label="活跃度">
                    <Progress percent={analysis.dimensions.activity.score} size="small" format={() => `${analysis.dimensions.activity.score}分`} />
                    <span style={{ fontSize: '12px', color: '#666' }}>{analysis.dimensions.activity.desc}</span>
                  </Descriptions.Item>
                  <Descriptions.Item label="策略明确">
                    <Progress percent={analysis.dimensions.concentration.score} size="small" format={() => `${analysis.dimensions.concentration.score}分`} />
                    <span style={{ fontSize: '12px', color: '#666' }}>{analysis.dimensions.concentration.desc}</span>
                  </Descriptions.Item>
                  <Descriptions.Item label="盈利能力">
                    <Progress percent={analysis.dimensions.profitability.score} size="small" format={() => `${analysis.dimensions.profitability.score}分`} />
                    <span style={{ fontSize: '12px', color: '#666' }}>{analysis.dimensions.profitability.desc}</span>
                  </Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>
          </Row>
        </Card>
      )}

      {/* 深度分析卡片 */}
      <Card 
        title={
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span><RobotOutlined /> AI 深度分析</span>
            {deepAnalysis ? (
              <Tag color="green">✅ 已生成</Tag>
            ) : (
              <Button 
                type="primary" 
                size="small"
                onClick={() => generateDeepAnalysis(false)}
                loading={deepAnalysisGenerating}
              >
                生成深度分析
              </Button>
            )}
          </div>
        }
        style={{ marginBottom: 16 }}
        loading={deepAnalysisLoading}
      >
        {deepAnalysis ? (
          <>
            <div style={{ whiteSpace: 'pre-wrap', lineHeight: '1.8' }}>
              {deepAnalysis.content}
            </div>
            <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid #eee', fontSize: '12px', color: '#999' }}>
              <span>⏰ {new Date(deepAnalysis.generated_at).toLocaleString()}</span>
              {deepAnalysis.cost > 0 && <span style={{ marginLeft: 16 }}>💰 ${deepAnalysis.cost.toFixed(4)}</span>}
              {deepAnalysis.from_cache && <Tag style={{ marginLeft: 16 }}>来自缓存</Tag>}
              <Button 
                size="small" 
                style={{ marginLeft: 16 }}
                onClick={() => generateDeepAnalysis(true)}
                loading={deepAnalysisGenerating}
              >
                强制刷新
              </Button>
            </div>
          </>
        ) : (
          <div style={{ textAlign: 'center', padding: '40px 0' }}>
            <p style={{ color: '#999' }}>暂无深度分析</p>
            <p style={{ fontSize: '12px', color: '#bbb' }}>点击上方按钮生成，大约需要2-5秒</p>
            <p style={{ fontSize: '12px', color: '#bbb' }}>配置 OPENAI_API_KEY 后可获得真实 AI 分析</p>
          </div>
        )}
      </Card>

      <Row gutter={16}>
        <Col span={16}>
          <Card title="📊 集中度趋势" style={{ marginBottom: 16 }}>
            {history.length > 0 ? (
              <ConcentrationChart 
                data={history} 
                whaleName={whale.pseudonym}
              />
            ) : (
              <p>暂无历史数据</p>
            )}
          </Card>
        </Col>
        <Col span={8}>
          <Card title="📈 基本信息" style={{ marginBottom: 16 }}>
            <Descriptions column={1}>
              <Descriptions.Item label="钱包">
                <code>{whale.wallet?.slice(0, 10)}...{whale.wallet?.slice(-6)}</code>
              </Descriptions.Item>
              <Descriptions.Item label="持仓价值">
                ${whale.total_value?.toLocaleString()}
              </Descriptions.Item>
              <Descriptions.Item label="市场数">
                {whale.position_count}
              </Descriptions.Item>
              <Descriptions.Item label="Top5占比">
                <Tag color={whale.top5_ratio >= 0.7 ? 'green' : whale.top5_ratio >= 0.5 ? 'orange' : 'default'}>
                  {Math.round(whale.top5_ratio * 100)}%
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="趋势">
                {whale.convergence_trend === 'converging' ? (
                  <Tag color="red">🔥 正在收敛</Tag>
                ) : whale.convergence_trend === 'diverging' ? (
                  <Tag color="orange">正在分散</Tag>
                ) : (
                  <Tag>稳定</Tag>
                )}
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>

      {/* 新闻与持仓标签页 */}
      <Card style={{ marginBottom: 16 }}>
        <Tabs
          defaultActiveKey="positions"
          items={[
            {
              key: 'positions',
              label: (
                <span>
                  💼 持仓明细
                </span>
              ),
              children: (
                <Table 
                  columns={positionColumns}
                  dataSource={whale.positions || []}
                  rowKey="market"
                  pagination={{ pageSize: 10 }}
                />
              )
            },
            {
              key: 'news',
              label: (
                <span>
                  <ReadOutlined /> 相关新闻
                </span>
              ),
              children: <WhaleNews wallet={wallet!} />
            },
            {
              key: 'history',
              label: (
                <span>
                  📈 历史变动
                </span>
              ),
              children: (
                <Table 
                  columns={changeColumns}
                  dataSource={whale.changes || []}
                  rowKey="id"
                  pagination={{ pageSize: 10 }}
                />
              )
            }
          ]}
        />
      </Card>
    </div>
  );
};

export default WhaleDetail;
