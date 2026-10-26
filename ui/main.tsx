import React from 'react';
import {createRoot} from 'react-dom/client';
import Bootstrap from './Bootstrap';
import './styles.css';
import './workbench.css';
createRoot(document.getElementById('root')!).render(<React.StrictMode><Bootstrap/></React.StrictMode>);
