import React from 'react';
import { Layout, Badge, Avatar } from 'antd';
import { BellOutlined, UserOutlined } from '@ant-design/icons';

const { Header: AntHeader } = Layout;

const Header: React.FC = () => {
  return (
    <AntHeader style={{ background: '#fff', padding: '0 24px', display: 'flex', alignItems: 'center', justifyContent: 'flex-end' }}>
      <Badge count={5} style={{ marginRight: 24 }}>
        <BellOutlined style={{ fontSize: 20, cursor: 'pointer' }} />
      </Badge>
      <Avatar icon={<UserOutlined />} />
    </AntHeader>
  );
};

export default Header;
