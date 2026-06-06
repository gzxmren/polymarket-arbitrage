import React from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, BarChart, Bar } from 'recharts';

interface PnLDataPoint {
  date: string;
  daily_pnl: number;
  cumulative_pnl: number;
  trade_count?: number;
}

interface PnLChartProps {
  data: PnLDataPoint[];
  whaleName: string;
  chartType?: 'line' | 'bar';
}

const PnLChart: React.FC<PnLChartProps> = ({ data, whaleName, chartType = 'line' }) => {
  // 格式化数据
  const chartData = data.map(item => ({
    date: new Date(item.date).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' }),
    dailyPnL: item.daily_pnl,
    cumulativePnL: item.cumulative_pnl,
    tradeCount: item.trade_count || 0
  }));

  // 计算颜色
  const getPnLColor = (value: number) => value >= 0 ? '#52c41a' : '#ff4d4f';
  const lastCumulative = chartData.length > 0 ? chartData[chartData.length - 1].cumulativePnL : 0;
  const lineColor = getPnLColor(lastCumulative);

  if (chartType === 'bar') {
    return (
      <div style={{ width: '100%', height: 300 }}>
        <h4 style={{ marginBottom: 16 }}>{whaleName} - 每日盈亏</h4>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="date" />
            <YAxis 
              tickFormatter={(value) => `$${value.toLocaleString()}`}
              label={{ value: '盈亏 ($)', angle: -90, position: 'insideLeft' }}
            />
            <Tooltip 
              formatter={(value: number) => [`$${value.toLocaleString()}`, '盈亏']}
              labelFormatter={(label) => `日期: ${label}`}
            />
            <Legend />
            <Bar 
              dataKey="dailyPnL" 
              name="每日盈亏" 
              fill="#1890ff"
              fillOpacity={0.8}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>
    );
  }

  return (
    <div style={{ width: '100%', height: 300 }}>
      <h4 style={{ marginBottom: 16 }}>{whaleName} - 盈亏走势</h4>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="date" />
          <YAxis 
            tickFormatter={(value) => `$${value.toLocaleString()}`}
            label={{ value: '盈亏 ($)', angle: -90, position: 'insideLeft' }}
          />
          <Tooltip 
            formatter={(value: number, name: string) => {
              const label = name === 'cumulativePnL' ? '累计盈亏' : '每日盈亏';
              return [`$${value.toLocaleString()}`, label];
            }}
            labelFormatter={(label) => `日期: ${label}`}
          />
          <Legend />
          <Line 
            type="monotone" 
            dataKey="cumulativePnL" 
            name="累计盈亏" 
            stroke={lineColor}
            strokeWidth={2}
            dot={false}
          />
          <Line 
            type="monotone" 
            dataKey="dailyPnL" 
            name="每日盈亏" 
            stroke="#1890ff"
            strokeWidth={1}
            strokeDasharray="5 5"
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};

export default PnLChart;
