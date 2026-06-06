import React from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, AreaChart, Area } from 'recharts';

interface DataPoint {
  timestamp?: string;
  date?: string;
  top5_ratio: number;
  hhi?: number;
}

interface ConcentrationChartProps {
  data: DataPoint[];
  whaleName: string;
  chartType?: 'line' | 'area';
  showHHI?: boolean;
}

const ConcentrationChart: React.FC<ConcentrationChartProps> = ({ 
  data, 
  whaleName, 
  chartType = 'line',
  showHHI = false
}) => {
  // 格式化数据
  const chartData = data.map(item => {
    const timeStr = item.timestamp 
      ? new Date(item.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
      : item.date 
        ? new Date(item.date).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
        : '';
    
    return {
      time: timeStr,
      ratio: Math.round((item.top5_ratio || 0) * 100),
      hhi: Math.round((item.hhi || 0) * 100) / 100,
    };
  }).reverse();

  if (chartType === 'area') {
    return (
      <div style={{ width: '100%', height: 300 }}>
        <h4 style={{ marginBottom: 16 }}>{whaleName} - 集中度趋势</h4>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="time" />
            <YAxis domain={[0, 100]} label={{ value: 'Top5占比(%)', angle: -90, position: 'insideLeft' }} />
            <Tooltip 
              formatter={(value: number, name: string) => {
                if (name === 'Top5占比') return [`${value}%`, name];
                return [value, name];
              }}
              labelFormatter={(label) => `时间: ${label}`}
            />
            <Legend />
            <Area 
              type="monotone" 
              dataKey="ratio" 
              name="Top5占比" 
              stroke="#1890ff" 
              fill="#1890ff"
              fillOpacity={0.3}
              strokeWidth={2}
            />
            {showHHI && (
              <Area 
                type="monotone" 
                dataKey="hhi" 
                name="HHI指数" 
                stroke="#52c41a" 
                fill="#52c41a"
                fillOpacity={0.2}
                strokeWidth={1}
                yAxisId={1}
              />
            )}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    );
  }

  return (
    <div style={{ width: '100%', height: 300 }}>
      <h4 style={{ marginBottom: 16 }}>{whaleName} - 集中度趋势</h4>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="time" />
          <YAxis domain={[0, 100]} label={{ value: 'Top5占比(%)', angle: -90, position: 'insideLeft' }} />
          <Tooltip 
            formatter={(value: number, name: string) => {
              if (name === 'Top5占比') return [`${value}%`, name];
              return [value, name];
            }}
            labelFormatter={(label) => `时间: ${label}`}
          />
          <Legend />
          <Line 
            type="monotone" 
            dataKey="ratio" 
            name="Top5占比" 
            stroke="#1890ff" 
            strokeWidth={2}
            dot={false}
          />
          {showHHI && (
            <Line 
              type="monotone" 
              dataKey="hhi" 
              name="HHI指数" 
              stroke="#52c41a" 
              strokeWidth={1}
              strokeDasharray="5 5"
              dot={false}
              yAxisId={1}
            />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};

export default ConcentrationChart;
