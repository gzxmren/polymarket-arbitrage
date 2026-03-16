import React, { useState, useEffect } from 'react';
import { Card, Form, Input, Button, Switch, Slider, InputNumber, message, Tabs, Divider } from 'antd';
import { SaveOutlined, ReloadOutlined, BellOutlined, SafetyOutlined, DatabaseOutlined } from '@ant-design/icons';
import { getSettings, saveSettings } from '../services/api';

const Settings: React.FC = () => {
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [settings, setSettings] = useState({
    telegram_bot_token: '',
    telegram_chat_id: '-5052636342',
    enable_whale_alerts: true,
    enable_arbitrage_alerts: true,
    enable_summary_report: true,
    whale_threshold_value: 100000,
    whale_threshold_changes: 5,
    pair_cost_threshold: 0.995,
    min_gap_threshold: 0.05,
    max_position_per_trade: 100,
    stop_loss_pct: 20,
  });

  useEffect(() => {
    // 加载设置
    fetchSettings();
  }, []);

  const fetchSettings = async () => {
    try {
      const response = await getSettings();
      const data = response.data;
      console.log('加载设置:', data);
      
      // 确保布尔值正确转换（处理可能的字符串"true"/"false"）
      const formatBool = (val: any) => {
        if (typeof val === 'boolean') return val;
        if (typeof val === 'string') return val === 'true';
        return Boolean(val);
      };
      
      const formattedData = {
        ...data,
        enable_whale_alerts: formatBool(data.enable_whale_alerts),
        enable_arbitrage_alerts: formatBool(data.enable_arbitrage_alerts),
        enable_summary_report: formatBool(data.enable_summary_report),
      };
      
      console.log('格式化后:', formattedData);
      setSettings(formattedData);
      
      // 使用resetFields确保表单完全重置
      form.resetFields();
      form.setFieldsValue(formattedData);
    } catch (error) {
      console.error('加载设置失败:', error);
    }
  };

  const handleSave = async (values: any) => {
    setLoading(true);
    try {
      await saveSettings(values);
      message.success('设置已保存');
      setSettings(values);
    } catch (error) {
      console.error('保存失败:', error);
      message.error('保存失败');
    } finally {
      setLoading(false);
    }
  };

  const handleReset = () => {
    form.resetFields();
    message.info('已重置为默认设置');
  };

  const telegramItems = [
    {
      key: 'telegram',
      label: 'Telegram 配置',
      icon: <BellOutlined />,
      children: (
        <>
          <Form.Item
            name="telegram_bot_token"
            label="Bot Token"
            rules={[{ required: true, message: '请输入 Bot Token' }]}
          >
            <Input.Password placeholder="输入 Telegram Bot Token" />
          </Form.Item>
          
          <Form.Item
            name="telegram_chat_id"
            label="Chat ID"
            rules={[{ required: true, message: '请输入 Chat ID' }]}
          >
            <Input placeholder="输入 Chat ID (如: -5052636342)" />
          </Form.Item>
          
          <Divider />
          
          <Form.Item name="enable_whale_alerts" valuePropName="checked">
            <Switch checkedChildren="开启" unCheckedChildren="关闭" defaultChecked={true} /> 鲸鱼活动警报
          </Form.Item>
          
          <Form.Item name="enable_arbitrage_alerts" valuePropName="checked">
            <Switch checkedChildren="开启" unCheckedChildren="关闭" defaultChecked={true} /> 套利机会警报
          </Form.Item>
          
          <Form.Item name="enable_summary_report" valuePropName="checked">
            <Switch checkedChildren="开启" unCheckedChildren="关闭" defaultChecked={true} /> 重点鲸鱼概况 (每4小时)
          </Form.Item>
        </>
      ),
    },
    {
      key: 'thresholds',
      label: '阈值设置',
      icon: <DatabaseOutlined />,
      children: (
        <>
          <h4>鲸鱼跟踪阈值</h4>
          <Form.Item
            name="whale_threshold_value"
            label="重点鲸鱼持仓价值阈值"
            rules={[{ required: true }]}
          >
            <InputNumber 
              min={10000} 
              max={1000000} 
              step={10000}
              formatter={value => `$ ${value}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
              style={{ width: 200 }}
            />
          </Form.Item>
          
          <Form.Item
            name="whale_threshold_changes"
            label="高变动阈值 (次)"
            rules={[{ required: true }]}
          >
            <InputNumber min={1} max={20} style={{ width: 200 }} />
          </Form.Item>
          
          <Divider />
          
          <h4>套利阈值</h4>
          <Form.Item
            name="pair_cost_threshold"
            label="Pair Cost 阈值"
            rules={[{ required: true }]}
          >
            <InputNumber 
              min={0.9} 
              max={1.0} 
              step={0.001}
              style={{ width: 200 }}
            />
          </Form.Item>
          
          <Form.Item
            name="min_gap_threshold"
            label="跨平台价差阈值"
            rules={[{ required: true }]}
          >
            <InputNumber 
              min={0.01} 
              max={0.5} 
              step={0.01}
              formatter={(value: number | undefined) => `${((value || 0) * 100).toFixed(0)}%`}
              parser={(value: string | undefined) => {
                const num = parseFloat((value || '0').replace('%', ''));
                return Math.min(Math.max(num / 100, 0.01), 0.5);
              }}
              style={{ width: 200 }}
            />
          </Form.Item>
        </>
      ),
    },
    {
      key: 'risk',
      label: '风险控制',
      icon: <SafetyOutlined />,
      children: (
        <>
          <h4>交易风险控制（自动交易时使用）</h4>
          <Form.Item
            name="max_position_per_trade"
            label="单笔最大仓位"
            rules={[{ required: true }]}
          >
            <InputNumber 
              min={10} 
              max={10000} 
              step={10}
              formatter={value => `$ ${value}`}
              style={{ width: 200 }}
            />
          </Form.Item>
          
          <Form.Item
            name="stop_loss_pct"
            label="止损比例"
            rules={[{ required: true }]}
          >
            <Slider 
              min={5} 
              max={50} 
              marks={{ 5: '5%', 20: '20%', 50: '50%' }}
              tipFormatter={value => `${value}%`}
            />
          </Form.Item>
          
          <Divider />
          
          <p style={{ color: '#999' }}>
            ⚠️ 风险控制设置仅在接入自动交易时生效
          </p>
        </>
      ),
    },
  ];

  return (
    <div>
      <h1 style={{ marginBottom: 24 }}>⚙️ 设置</h1>
      
      <Form
        form={form}
        layout="vertical"
        initialValues={settings}
        onFinish={handleSave}
      >
        <Card>
          <Tabs items={telegramItems} />
          
          <Divider />
          
          <Form.Item>
            <Button 
              type="primary" 
              htmlType="submit" 
              icon={<SaveOutlined />}
              loading={loading}
              style={{ marginRight: 16 }}
            >
              保存设置
            </Button>
            <Button 
              icon={<ReloadOutlined />}
              onClick={handleReset}
            >
              重置默认
            </Button>
          </Form.Item>
        </Card>
      </Form>
    </div>
  );
};

export default Settings;
