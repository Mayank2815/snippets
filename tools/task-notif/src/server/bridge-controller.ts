import { Request, Response, Router } from 'express';
import { spawn } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'node:url';
import axios from 'axios';

const router = Router();
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ENGINE_URL = 'http://127.0.0.1:4320';
const scriptPath = path.resolve(__dirname, '../../engine/mac_engine.py');

async function isEngineAlive(): Promise<boolean> {
  try {
    const res = await axios.get(`${ENGINE_URL}/status`, { timeout: 1000 });
    return res.status === 200;
  } catch {
    return false;
  }
}

async function ensureEngineRunning(): Promise<void> {
  const alive = await isEngineAlive();
  if (!alive) {
    console.log(`[Bridge Controller] Launching mac_engine.py at ${scriptPath}`);
    const child = spawn('python3', [scriptPath], {
      detached: true,
      stdio: 'ignore',
    });
    child.unref();
    await new Promise((r) => setTimeout(r, 800));
  }
}

// Automation system status API route
router.get('/automation/status', async (req: Request, res: Response) => {
  try {
    const response = await axios.get(`${ENGINE_URL}/status`, { timeout: 1500 });
    res.json({
      status: response.data.status,
      message: 'Engine active on host environment (HTTP 4320)',
    });
  } catch (err) {
    res.json({
      status: 'OFFLINE',
      message: 'Host Engine Background Process Not Running',
    });
  }
});

// Start engine route
router.post('/automation/start', async (req: Request, res: Response) => {
  try {
    await ensureEngineRunning();
    const response = await axios.post(`${ENGINE_URL}/start`, {}, { timeout: 3000 });
    res.json(response.data);
  } catch (err: any) {
    console.error('[Bridge Controller Error]:', err.message);
    res.status(500).json({ success: false, error: err.message || 'Failed to communicate with engine process' });
  }
});

// Stop engine route
router.post('/automation/stop', async (req: Request, res: Response) => {
  try {
    const response = await axios.post(`${ENGINE_URL}/stop`, {}, { timeout: 3000 });
    res.json(response.data);
  } catch (err: any) {
    console.error('[Bridge Controller Error]:', err.message);
    res.status(500).json({ success: false, error: err.message || 'Failed to stop engine process' });
  }
});

export default router;
