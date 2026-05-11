import React from 'react';
import ReactDOM from 'react-dom/client';
import './index.css';
import App from './App.jsx';
import ShareView from './ShareView.jsx';

// Lightweight client routing: anything under /share/<token> goes to the public ShareView,
// everything else goes to the auth-gated app.
const path = window.location.pathname;
const shareMatch = path.match(/^\/share\/([^\/]+)\/?$/);

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {shareMatch ? <ShareView token={shareMatch[1]} /> : <App />}
  </React.StrictMode>
);
