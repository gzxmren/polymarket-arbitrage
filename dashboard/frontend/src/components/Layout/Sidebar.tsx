import React from 'react';
import { Layout, Menu } from 'antd';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  DashboardOutlined,
  TeamOutlined,
  LineChartOutlined,
  BellOutlined,
  SettingOutlined
} from '@ant-design/icons';

const { Sider } = Layout;

const Sidebar: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  const menuItems = [
    { key: '/', icon: <DashboardOutlined />, label: '仪表盘' },
    { key: '/whales', icon: <TeamOutlined />, label: '鲸鱼跟踪' },
    { key: '/arbitrage', icon: <LineChartOutlined />, label: '套利机会' },
    { key: '/alerts', icon: <BellOutlined />, label: '警报中心' },
    { key: '/settings', icon: <SettingOutlined />, label: '设置' },
  ];

  return (
    <Sider theme="dark" width={200}>
      <div style={{ height: 64, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', fontSize: 18, fontWeight: 'bold' }}>
        🐋 Polymarket
      </div>
      <Menu
        theme="dark"
        mode="inline"
        selectedKeys={[location.pathname]}
        items={menuItems}
        onClick={({ key }) => navigate(key)}
      />
    </Sider>
  );
};

export default Sidebar;
