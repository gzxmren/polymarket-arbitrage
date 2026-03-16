import { io } from 'socket.io-client';

const SOCKET_URL = process.env.REACT_APP_SOCKET_URL || 'http://localhost:5000';

export const socket = io(SOCKET_URL, {
  transports: ['websocket'],
  autoConnect: true,
});

export const subscribeToWhaleUpdates = (callback: (data: any) => void) => {
  socket.on('whale_update', callback);
  return () => {
    socket.off('whale_update', callback);
  };
};

export const subscribeToAlertUpdates = (callback: (data: any) => void) => {
  socket.on('alert_update', callback);
  return () => {
    socket.off('alert_update', callback);
  };
};

export default socket;
