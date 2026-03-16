import React from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer } from 'recharts';

interface DataPoint {
  timestamp: string;
  top5_ratio: number;
}

interface ConcentrationChartProps {
  data: DataPoint[];
  whaleName: string;
}

const ConcentrationChart: React.FC<ConcentrationChartProps> = ({ data, whaleName }) => {
  // 格式化数据
  const chartData = data.map(item => ({
    time: new Date(item.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
    ratio: Math.round(item.top5_ratio * 100),
  })).reverse();

  return (
    <div style={{ width: '100%', height: 300 }}>
      <h4 style={{ marginBottom: 16 }}>{whaleName} - 集中度趋势</h4>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="time" />
          <YAxis domain={[0, 100]} label={{ value: 'Top5占比(%)', angle: -90, position: 'insideLeft' }} />
          <Tooltip 
            formatter={(value: number) => [`${value}%`, 'Top5占比']}
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
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};

export default ConcentrationChart;
