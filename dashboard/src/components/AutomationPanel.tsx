import React, { useState, useEffect } from 'react';

export const AutomationPanel: React.FC = () => {
    const [status, setStatus] = useState<'IDLE' | 'RUNNING' | 'ERROR'>('IDLE');
    const [loading, setLoading] = useState(false);

    const fetchEngineStatus = async () => {
        try {
            const res = await fetch('/automation/status');
            const data = await res.json();
            setStatus(data.status);
        } catch (err) {
            console.error("Unable to connect to loopback control pipe.", err);
        }
    };

    useEffect(() => {
        fetchEngineStatus();
        const poller = setInterval(fetchEngineStatus, 5000); // 5s telemetry loop refresh
        return () => clearInterval(poller);
    }, []);

    const handleToggleEngine = async () => {
        setLoading(true);
        const endpoint = status === 'RUNNING' ? 'stop' : 'start';
        try {
            const res = await fetch(`/automation/${endpoint}`, { method: 'POST' });
            const data = await res.json();
            if (res.ok && data.success) {
                await fetchEngineStatus();
            } else {
                alert(data.message || data.error || "Engine operation failed.");
            }
        } catch (err: any) {
            alert(`Error connecting to engine bridge: ${err.message || err}`);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div style={{ padding: '24px', backgroundColor: '#1e1e2e', borderRadius: '12px', color: '#cdd6f4' }}>
            <h2 style={{ color: '#f5c2e7', marginBottom: '8px' }}>⚡ Core Emulation Engine Control Console</h2>
            <p style={{ color: '#a6adc8', fontSize: '14px' }}>Status compliance tracker utility mapped to Hubstaff algorithmic parameters.</p>

            <div style={{ margin: '24px 0', display: 'flex', alignItems: 'center', gap: '16px' }}>
                <span style={{
                    padding: '8px 16px',
                    borderRadius: '20px',
                    fontWeight: 'bold',
                    fontSize: '12px',
                    backgroundColor: status === 'RUNNING' ? '#a6e3a1' : status === 'ERROR' ? '#f38ba8' : '#6c7086',
                    color: '#11111b'
                }}>
                    ENGINE STATUS: {status}
                </span>

                <button
                    onClick={handleToggleEngine}
                    disabled={loading}
                    style={{
                        padding: '10px 24px',
                        border: 'none',
                        borderRadius: '6px',
                        fontWeight: 'bold',
                        cursor: 'pointer',
                        backgroundColor: status === 'RUNNING' ? '#f38ba8' : '#89b4fa',
                        color: '#11111b',
                        transition: 'all 0.2s ease-in-out'
                    }}
                >
                    {loading ? 'Processing Pipeline...' : status === 'RUNNING' ? 'DEACTIVATE SESSION' : 'ACTIVATE SESSION'}
                </button>
            </div>

            <div style={{ fontSize: '12px', color: '#7f849c', borderTop: '1px solid #313244', paddingTop: '12px' }}>
                * Setup Notes: Ensure local macOS user login terminal session retains full TCC Accessibility accessibility bounds.
            </div>
        </div>
    );
};
