'use strict';

const { app, BrowserWindow, ipcMain, dialog } = require('electron');
const path = require('path');
const fs   = require('fs');

const MUSIC_DIR  = process.env.MUSIC_DIR || path.join(require('os').homedir(), 'Music');
const AUDIO_EXTS = new Set(['.mp3', '.flac', '.ogg', '.wav', '.aac', '.m4a', '.opus', '.weba', '.webm']);

function createWindow() {
  const win = new BrowserWindow({
    width: 1060,
    height: 700,
    minWidth: 760,
    minHeight: 520,
    backgroundColor: '#0B0E18',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.loadFile('index.html');
}

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

/* ── Shared metadata parser ── */
async function parseOne(fp, parseFile) {
  try {
    const meta = await parseFile(fp, { duration: true, skipPostHeaders: true });
    const { title, artist, album } = meta.common;
    let artwork = null;
    const pic = meta.common.picture?.[0];
    if (pic) {
      artwork = `data:${pic.format};base64,${Buffer.from(pic.data).toString('base64')}`;
    }
    return {
      path: fp,
      title:    title  || path.basename(fp, path.extname(fp)),
      artist:   artist || '',
      album:    album  || '',
      duration: meta.format.duration || 0,
      artwork,
    };
  } catch {
    return { path: fp, title: path.basename(fp, path.extname(fp)), artist: '', album: '', duration: 0, artwork: null };
  }
}

/* ── Recursive library scan ── */
ipcMain.handle('scan-library', () => {
  try {
    const entries = fs.readdirSync(MUSIC_DIR, { recursive: true, withFileTypes: false });
    return entries
      .filter(e => AUDIO_EXTS.has(path.extname(e).toLowerCase()))
      .map(e => path.join(MUSIC_DIR, e))
      .sort();
  } catch (e) {
    console.error('scan-library:', e.message);
    return [];
  }
});

/* ── Streaming metadata for library load ──
   Processes BATCH_SIZE files in parallel, sends each batch to renderer
   as it completes so the list populates incrementally.             */
const BATCH_SIZE = 32;

ipcMain.on('start-metadata-stream', async (event, filePaths) => {
  const { parseFile } = await import('music-metadata');

  for (let i = 0; i < filePaths.length; i += BATCH_SIZE) {
    if (event.sender.isDestroyed()) return;
    const batch = filePaths.slice(i, i + BATCH_SIZE);
    const results = await Promise.all(batch.map(fp => parseOne(fp, parseFile)));
    event.sender.send('tracks-batch', results);
  }
  if (!event.sender.isDestroyed()) event.sender.send('tracks-done');
});

/* ── Small batch metadata for the + button ── */
ipcMain.handle('get-metadata', async (_event, filePaths) => {
  const { parseFile } = await import('music-metadata');
  return Promise.all(filePaths.map(fp => parseOne(fp, parseFile)));
});

/* ── File dialog ── */
ipcMain.handle('open-files', async () => {
  const win = BrowserWindow.getFocusedWindow();
  const result = await dialog.showOpenDialog(win, {
    properties: ['openFile', 'multiSelections'],
    filters: [{ name: 'Audio', extensions: ['mp3', 'flac', 'ogg', 'wav', 'aac', 'm4a', 'opus', 'weba', 'webm'] }],
  });
  return result.canceled ? [] : result.filePaths;
});
