import React from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { Layout } from 'antd';
import Sidebar from './components/Layout/Sidebar';
import Header from './components/Layout/Header';
import Dashboard from './pages/Dashboard';
import Whales from './pages/Whales';
import WhaleDetail from './pages/WhaleDetail';
import Arbitrage from './pages/Arbitrage';
import SemanticArbitrage from './pages/SemanticArbitrage';
import NewsDriven from './pages/NewsDriven';
import Alerts from './pages/Alerts';
import Settings from './pages/Settings';
import QualityReport from './pages/QualityReport';
import LeaderboardWales from './pages/LeaderboardWales';
import LeaderboardTrends from './pages/LeaderboardTrends';

const { Content } = Layout;

const App: React.FC = () => {
  return (
    <Router>
      <Layout style={{ minHeight: '100vh' }}>
        <Sidebar />
        <Layout>
          <Header />
          <Content style={{ margin: '24px 16px', padding: 24, background: '#fff' }}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/whales" element={<Whales />} />
              <Route path="/whales/:wallet" element={<WhaleDetail />} />
              <Route path="/arbitrage" element={<Arbitrage />} />
              <Route path="/semantic-arbitrage" element={<SemanticArbitrage />} />
              <Route path="/news-driven" element={<NewsDriven />} />
              <Route path="/alerts" element={<Alerts />} />
              <Route path="/quality-report" element={<QualityReport />} />
              <Route path="/leaderboard-whales" element={<LeaderboardWales />} />
              <Route path="/leaderboard-trends" element={<LeaderboardTrends />} />
              <Route path="/settings" element={<Settings />} />
            </Routes>
          </Content>
        </Layout>
      </Layout>
    </Router>
  );
};

export default App;
