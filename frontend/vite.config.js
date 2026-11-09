import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const gateway = 'http://localhost:4000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': gateway, '/auth': gateway, '/t': gateway },
  },
});
