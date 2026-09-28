import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { KeepAlivePanel } from './KeepAlivePanel.js';
import './styles.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <div className="wrap">
      <header className="top">
        <h1>Keep Alive</h1>
        <span className="next">dev and QA instances, kept awake while you work on them</span>
      </header>
      <KeepAlivePanel />
    </div>
  </StrictMode>,
);
