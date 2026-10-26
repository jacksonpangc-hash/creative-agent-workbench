import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({plugins:[react()],server:{host:'127.0.0.1',port:5173,strictPort:true},test:{maxWorkers:2,testTimeout:15000,environment:'jsdom',include:['ui/**/*.test.tsx']}});
