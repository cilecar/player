'use strict';

const { contextBridge, ipcRenderer } = require('electron');

let _batchListener = null;

contextBridge.exposeInMainWorld('api', {
  openFiles:        ()       => ipcRenderer.invoke('open-files'),
  getMetadata:      (paths)  => ipcRenderer.invoke('get-metadata', paths),
  scanLibrary:      ()       => ipcRenderer.invoke('scan-library'),

  startLibraryScan: (paths)  => ipcRenderer.send('start-metadata-stream', paths),

  onTracksBatch: (cb) => {
    _batchListener = (_e, batch) => cb(batch);
    ipcRenderer.on('tracks-batch', _batchListener);
  },
  offTracksBatch: () => {
    if (_batchListener) { ipcRenderer.removeListener('tracks-batch', _batchListener); _batchListener = null; }
  },
  onTracksDone: (cb) => ipcRenderer.once('tracks-done', cb),
});
