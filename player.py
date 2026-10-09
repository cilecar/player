#!/usr/bin/env python3
"""Music player — PySide6 + VLC + mutagen"""

import os
import sys
import json
import math
import random
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import soundfile as sf
from mutagen import File as MuFile
import vlc

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter,
    QHBoxLayout, QVBoxLayout, QLabel, QListView,
    QSlider, QPushButton, QAbstractItemView,
    QStyledItemDelegate, QStyle,
)
from PySide6.QtCore import (
    Qt, QTimer, Signal, QObject, QThread, QSize, QRect, QRectF, QPointF,
    QAbstractListModel, QModelIndex, QRunnable, QThreadPool, Slot, QEvent,
)
from PySide6.QtGui import (
    QPainter, QPainterPath, QPen, QBrush, QColor, QPixmap, QImage,
    QLinearGradient, QFont, QRadialGradient, QPalette,
)

# ── Config ─────────────────────────────────────────────────────────────────
MUSIC_DIR  = Path(os.environ.get('MUSIC_DIR', Path.home() / 'Music'))
AUDIO_EXTS = {'.mp3', '.flac', '.ogg', '.wav', '.aac', '.m4a', '.opus'}
STATE_FILE = Path.home() / '.config' / 'player' / 'state.json'

BG, S1, S2, S3 = '#0B0E18', '#131826', '#1B2235', '#252E44'
ACC          = '#C8943A'
T1, T2, T3   = '#E2E6F0', '#7A8AAD', '#4A5570'
DIV          = '#1C2438'

THUMB_SZ  = 48
ART_SZ    = 210
ITEM_H    = 72
SCAN_WKRS = 24
WAVE_PTS  = 600
COL_BREAK = 500   # sidebar viewport px → 2 columns


# ── Helpers ────────────────────────────────────────────────────────────────

def fmt(secs: float) -> str:
    if not secs or secs < 0:
        return '0:00'
    s = int(secs)
    return f'{s // 60}:{s % 60:02d}'


def _tag_str(tag, *keys) -> str:
    for k in keys:
        v = tag.get(k)
        if v:
            return str(v[0]) if hasattr(v, '__iter__') and not isinstance(v, str) else str(v)
    return ''


# ── Metadata ───────────────────────────────────────────────────────────────

def load_meta(path: Path) -> dict:
    r = {'path': str(path), 'title': path.stem, 'artist': '',
         'duration': 0.0, '_art': None}
    try:
        tag = MuFile(str(path), easy=False)
        if tag is None:
            return r
        if hasattr(tag.info, 'length'):
            r['duration'] = tag.info.length
        r['title']  = _tag_str(tag, 'TIT2', 'title', '\xa9nam', 'TITLE') or path.stem
        r['artist'] = _tag_str(tag, 'TPE1', 'artist', '\xa9ART', 'ARTIST',
                                'albumartist', 'TPE2')
        # Artwork
        art = None
        pics = getattr(tag, 'pictures', None)       # FLAC / OGG
        if pics:
            art = bytes(pics[0].data)
        if not art:                                  # ID3 APIC
            for k, v in tag.items():
                if k.startswith('APIC'):
                    pic = v[0] if isinstance(v, list) else v
                    if hasattr(pic, 'data'):
                        art = bytes(pic.data)
                    break
        if not art and 'covr' in tag:               # MP4
            covr = tag['covr']
            if covr:
                art = bytes(covr[0])
        r['_art'] = art
    except Exception:
        pass
    return r


def _scale_crop(pix: QPixmap, size: int) -> QPixmap:
    pix = pix.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = (pix.width()  - size) // 2
    y = (pix.height() - size) // 2
    return pix.copy(x, y, size, size)


def make_pixmap(data: bytes | None, title: str, size: int) -> QPixmap:
    if data:
        img = QImage.fromData(data)
        if not img.isNull():
            return _scale_crop(QPixmap.fromImage(img), size)
    # Vinyl placeholder
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    h = sum(ord(c) * (i + 1) for i, c in enumerate(title)) % 360
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    g = QRadialGradient(size / 2, size / 2, size * .75)
    g.setColorAt(0, QColor.fromHsl(h, 50, 42))
    g.setColorAt(1, QColor.fromHsl(h, 35, 14))
    p.fillRect(0, 0, size, size, g)
    p.setPen(QPen(QColor(255, 255, 255, 10), 1))
    step = max(size // 10, 6)
    for r in range(step, size // 2, step):
        p.drawEllipse(QRectF(size/2-r, size/2-r, r*2, r*2))
    r2 = size // 8
    cg = QRadialGradient(size / 2, size / 2)
    cg.setColorAt(0, QColor.fromHsl(h, 60, 52))
    cg.setColorAt(1, QColor.fromHsl(h, 45, 32))
    p.setBrush(cg); p.setPen(Qt.NoPen)
    p.drawEllipse(QRectF(size/2-r2, size/2-r2, r2*2, r2*2))
    if title:
        f = QFont(); f.setPointSize(max(7, size // 6)); f.setWeight(QFont.DemiBold)
        p.setFont(f); p.setPen(QColor(255, 255, 255, 195))
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, title[0].upper())
    p.end()
    return pix


# ── Waveform ───────────────────────────────────────────────────────────────

def analyze_waveform(path: str, n: int = WAVE_PTS) -> list[float]:
    try:
        with sf.SoundFile(path) as f:
            total   = len(f)
            ch_size = max(1, total // n)
            env: list[float] = []
            f.seek(0)
            while True:
                block = f.read(ch_size, dtype='float32', always_2d=True)
                if not len(block):
                    break
                mono = block.mean(axis=1)
                env.append(float(np.sqrt(np.mean(mono ** 2))))
        if not env:
            return []
        mx = max(env)
        if mx > 0:
            env = [v / mx for v in env]
        return (env + [0.0] * n)[:n]
    except Exception:
        return []


class WaveformJob(QRunnable):
    class Sig(QObject):
        done = Signal(str, list)
    def __init__(self, path: str):
        super().__init__()
        self.path    = path
        self.signals = self.Sig()
    def run(self):
        self.signals.done.emit(self.path, analyze_waveform(self.path))


# ── Library scanner ────────────────────────────────────────────────────────

class LibraryScanner(QThread):
    batch_ready = Signal(list)
    done        = Signal(int)

    def __init__(self, directory: Path):
        super().__init__()
        self.directory = directory

    def run(self):
        paths = sorted(p for p in self.directory.rglob('*')
                       if p.suffix.lower() in AUDIO_EXTS)
        total = 0
        for i in range(0, len(paths), SCAN_WKRS):
            chunk = paths[i:i + SCAN_WKRS]
            with ThreadPoolExecutor(max_workers=len(chunk)) as pool:
                results = list(pool.map(load_meta, chunk))
            self.batch_ready.emit(results)
            total += len(results)
        self.done.emit(total)


# ── Track source model ─────────────────────────────────────────────────────

class TrackModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self._t: list[dict] = []

    def rowCount(self, parent=QModelIndex()):
        return len(self._t)

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role == Qt.UserRole:
            return self._t[index.row()]
        return None

    def append(self, tracks: list[dict]):
        r0 = len(self._t)
        self.beginInsertRows(QModelIndex(), r0, r0 + len(tracks) - 1)
        self._t.extend(tracks)
        self.endInsertRows()

    def track(self, i: int) -> dict | None:
        return self._t[i] if 0 <= i < len(self._t) else None

    def find_path(self, path: str) -> int:
        for i, t in enumerate(self._t):
            if t['path'] == path:
                return i
        return -1

    def __len__(self):
        return len(self._t)


# ── Grid proxy model ───────────────────────────────────────────────────────
# Wraps TrackModel and groups N tracks per proxy row.
# QListView stays in stable TopToBottom mode — no QListView grid bugs.

class GridProxy(QAbstractListModel):
    def __init__(self, source: TrackModel):
        super().__init__()
        self._src  = source
        self._cols = 1
        source.rowsInserted.connect(self._src_changed)
        source.modelReset.connect(self._src_changed)

    def _src_changed(self, *_):
        self.beginResetModel()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        n = len(self._src)
        return (n + self._cols - 1) // self._cols if n else 0

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.UserRole:
            return None
        base = index.row() * self._cols
        return [t for c in range(self._cols)
                if (t := self._src.track(base + c)) is not None]

    def set_cols(self, n: int):
        if n == self._cols:
            return
        self.beginResetModel()
        self._cols = n
        self.endResetModel()

    def abs_index(self, proxy_row: int, col: int) -> int:
        return proxy_row * self._cols + col

    @property
    def cols(self) -> int:
        return self._cols


# ── List view subclass (handles column-aware clicks) ──────────────────────

class TrackListView(QListView):
    track_clicked = Signal(int)   # absolute track index

    def __init__(self, proxy: GridProxy):
        super().__init__()
        self._proxy = proxy

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        idx = self.indexAt(event.pos())
        if not idx.isValid():
            return
        tracks = idx.data(Qt.UserRole)
        if not tracks:
            return
        vr   = self.visualRect(idx)
        cols = self._proxy.cols
        w    = vr.width() // cols
        col  = min((event.pos().x() - vr.left()) // max(w, 1), len(tracks) - 1)
        self.track_clicked.emit(self._proxy.abs_index(idx.row(), col))


# ── Sidebar header ─────────────────────────────────────────────────────────

class SidebarHeader(QWidget):
    """Paints 'Library' label with a white rounded underline."""
    def __init__(self):
        super().__init__()
        self.setFixedHeight(50)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        f = QFont()
        f.setPointSizeF(13); f.setWeight(QFont.SemiBold)
        p.setFont(f)

        fm     = p.fontMetrics()
        text   = 'Library'
        tx, ty = 16, self.height() - 16
        tw     = fm.horizontalAdvance(text)

        # Text
        p.setPen(QColor(T2))
        p.drawText(tx, ty, text)

        # Rounded underline
        lh = 2.5
        ly = ty + 5
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 100))
        p.drawRoundedRect(QRectF(tx, ly, tw, lh), lh / 2, lh / 2)


# ── Grid delegate ──────────────────────────────────────────────────────────

class GridDelegate(QStyledItemDelegate):
    def __init__(self, cur_fn, cols_fn):
        super().__init__()
        self._cur  = cur_fn   # () → int
        self._cols = cols_fn  # () → int

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), ITEM_H)

    def paint(self, painter: QPainter, option, index):
        tracks = index.data(Qt.UserRole)
        if not tracks:
            return
        cols = self._cols()
        w    = option.rect.width() // cols
        for i, t in enumerate(tracks):
            abs_idx = index.row() * cols + i
            rect    = QRect(option.rect.left() + i * w, option.rect.top(),
                            w, ITEM_H)
            self._paint_track(painter, rect, t, abs_idx == self._cur(),
                              i < len(tracks) - 1)

    def _paint_track(self, painter: QPainter, r: QRect, t: dict,
                     is_cur: bool, has_right: bool):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        is_hover = False  # hover not easily available here; skip for perf

        if is_cur:
            painter.fillRect(r, QColor(200, 148, 58, 26))
            painter.fillRect(QRect(r.left(), r.top(), 3, r.height()), QColor(ACC))

        # Thumbnail
        pad = 10 + (3 if is_cur else 0)
        tx  = r.left() + pad
        ty  = r.top()  + (r.height() - THUMB_SZ) // 2
        thumb: QPixmap | None = t.get('thumb')
        if thumb:
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(tx, ty, THUMB_SZ, THUMB_SZ), 6, 6)
            painter.setClipPath(clip)
            painter.drawPixmap(tx, ty, thumb)
            painter.setClipping(False)
        else:
            painter.fillRect(QRect(tx, ty, THUMB_SZ, THUMB_SZ), QColor(S3))

        # Text
        ix = tx + THUMB_SZ + 10
        iw = r.right() - ix - 44

        f = QFont(); f.setPointSizeF(10.5); f.setWeight(QFont.Medium)
        painter.setFont(f); painter.setPen(QColor(ACC) if is_cur else QColor(T1))
        fm    = painter.fontMetrics()
        title = fm.elidedText(t['title'], Qt.ElideRight, max(iw, 1))
        painter.drawText(QRect(ix, r.top() + 10, iw, 22),
                         Qt.AlignLeft | Qt.AlignVCenter, title)

        f.setPointSizeF(9.0); f.setWeight(QFont.Normal)
        painter.setFont(f); painter.setPen(QColor(T2))
        fm     = painter.fontMetrics()
        artist = fm.elidedText(t['artist'] or 'Unknown artist', Qt.ElideRight, max(iw, 1))
        painter.drawText(QRect(ix, r.top() + 34, iw, 20),
                         Qt.AlignLeft | Qt.AlignVCenter, artist)

        # Duration
        f.setPointSizeF(8.5); painter.setFont(f); painter.setPen(QColor(T3))
        painter.drawText(QRect(r.right() - 44, r.top() + 10, 38, 20),
                         Qt.AlignRight | Qt.AlignVCenter, fmt(t['duration']))

        # Bottom separator
        painter.setPen(QPen(QColor(DIV), 1))
        painter.drawLine(r.left() + 10, r.bottom(), r.right() - 10, r.bottom())

        # Right separator between 2-column items
        if has_right:
            painter.setPen(QPen(QColor(DIV), 1))
            painter.drawLine(r.right(), r.top() + 8, r.right(), r.bottom() - 8)

        painter.restore()


# ── Waveform widget ────────────────────────────────────────────────────────

class WaveformWidget(QWidget):
    seek_requested = Signal(float)

    def __init__(self):
        super().__init__()
        self.envelope: list[float] = []
        self.progress = 0.0
        self._phase   = 0.0
        self.setMinimumHeight(64)
        self.setMaximumHeight(80)
        self.setCursor(Qt.PointingHandCursor)
        t = QTimer(self); t.setInterval(50); t.timeout.connect(self._idle_tick); t.start()

    def _idle_tick(self):
        if not self.envelope:
            self._phase += 0.022; self.update()

    def set_envelope(self, env):
        self.envelope = env; self.update()

    def set_progress(self, p):
        self.progress = max(0.0, min(1.0, p)); self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.seek_requested.emit(event.position().x() / self.width())

    def paintEvent(self, _):
        p    = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cy   = h / 2; maxA = cy - 5

        if not self.envelope:
            self._draw_idle(p, w, h, cy, maxA); return

        n   = len(self.envelope)
        pgx = self.progress * w

        path = QPainterPath()
        path.moveTo(0.0, cy - self.envelope[0] * maxA)
        for i in range(1, n):
            path.lineTo(i / (n - 1) * w, cy - self.envelope[i] * maxA)
        path.lineTo(w, cy + self.envelope[-1] * maxA)
        for i in range(n - 2, -1, -1):
            path.lineTo(i / (n - 1) * w, cy + self.envelope[i] * maxA)
        path.closeSubpath()

        def fill(a):
            gr = QLinearGradient(0, 0, 0, h)
            gr.setColorAt(0, QColor(200, 148, 58, a)); gr.setColorAt(.5, QColor(200, 148, 58, a // 5))
            gr.setColorAt(1, QColor(200, 148, 58, a))
            p.fillPath(path, QBrush(gr))

        fill(50)
        p.save(); p.setClipRect(QRectF(0, 0, pgx, h)); fill(155); p.restore()

        top = QPainterPath(); bot = QPainterPath()
        top.moveTo(0.0, cy - self.envelope[0] * maxA)
        bot.moveTo(0.0, cy + self.envelope[0] * maxA)
        for i in range(1, n):
            x = i / (n - 1) * w
            top.lineTo(x, cy - self.envelope[i] * maxA)
            bot.lineTo(x, cy + self.envelope[i] * maxA)
        for path_ in (top, bot):
            p.setPen(QPen(QColor(200, 148, 58, 85), 1.2)); p.drawPath(path_)
            p.save(); p.setClipRect(QRectF(0, 0, pgx, h))
            p.setPen(QPen(QColor(200, 148, 58, 220), 1.4)); p.drawPath(path_)
            p.restore()
        p.setPen(QPen(QColor(200, 148, 58, 170), 1.5))
        p.drawLine(QPointF(pgx, 4), QPointF(pgx, h - 4))

    def _draw_idle(self, p, w, h, cy, maxA):
        t = self._phase; amp = maxA * 0.18; n = 200
        path = QPainterPath()
        for i in range(n + 1):
            x = i / n * w; env = math.sin(i / n * math.pi)
            v = math.sin(i / n * math.pi * 3.2 + t) * amp * env
            path.moveTo(x, cy - abs(v)) if i == 0 else path.lineTo(x, cy - abs(v))
        for i in range(n, -1, -1):
            x = i / n * w; env = math.sin(i / n * math.pi)
            v = math.sin(i / n * math.pi * 3.2 + t) * amp * env
            path.lineTo(x, cy + abs(v))
        path.closeSubpath()
        p.fillPath(path, QBrush(QColor(200, 148, 58, 18)))


# ── Main window ────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Player')
        self.resize(1060, 700)
        self.setMinimumSize(760, 520)

        self._src          = TrackModel()
        self._proxy        = GridProxy(self._src)
        self._cur          = -1
        self._playing      = False
        self._shuffle      = False
        self._repeat       = 0
        self._pool         = QThreadPool.globalInstance()
        self._saved_path   = None
        self._restore_done = False

        self._vlc = vlc.Instance('--no-video', '--quiet')
        self._mp  = self._vlc.media_player_new()

        self._build_ui()
        self._apply_style()

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        try:
            s = json.loads(STATE_FILE.read_text())
            self._saved_path = s.get('current_path')
        except Exception:
            pass

        self._start_scan()

    # ── UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        splitter = QSplitter(Qt.Horizontal, self)
        self.setCentralWidget(splitter)

        # Sidebar
        sidebar = QWidget(); sidebar.setObjectName('sidebar')
        sl      = QVBoxLayout(sidebar)
        sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(0)
        sl.addWidget(SidebarHeader())

        self._list = TrackListView(self._proxy)
        self._list.setModel(self._proxy)
        self._list.setItemDelegate(
            GridDelegate(lambda: self._cur, lambda: self._proxy.cols))
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setSelectionMode(QAbstractItemView.NoSelection)
        self._list.setMouseTracking(True)
        self._list.setUniformItemSizes(True)
        self._list.track_clicked.connect(self._on_track_click)
        self._list.viewport().installEventFilter(self)
        sl.addWidget(self._list, 1)

        splitter.addWidget(sidebar)

        # Player panel
        panel = QWidget(); panel.setObjectName('panel')
        pl    = QVBoxLayout(panel)
        pl.setContentsMargins(40, 28, 40, 28); pl.setAlignment(Qt.AlignCenter)

        inner = QWidget(); inner.setMaximumWidth(420)
        il    = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0); il.setSpacing(18); il.setAlignment(Qt.AlignCenter)

        self._art = QLabel()
        self._art.setFixedSize(ART_SZ, ART_SZ)
        self._art.setAlignment(Qt.AlignCenter)
        self._art.setObjectName('art')
        self._art.setPixmap(make_pixmap(None, '—', ART_SZ))
        il.addWidget(self._art, 0, Qt.AlignCenter)

        self._lbl_title  = QLabel('—'); self._lbl_title.setObjectName('now-title')
        self._lbl_artist = QLabel('No track loaded'); self._lbl_artist.setObjectName('now-artist')
        self._lbl_title.setAlignment(Qt.AlignCenter)
        self._lbl_artist.setAlignment(Qt.AlignCenter)
        il.addWidget(self._lbl_title); il.addWidget(self._lbl_artist)

        self._wave = WaveformWidget()
        self._wave.seek_requested.connect(self._on_seek)
        il.addWidget(self._wave)

        tw = QWidget(); tl = QHBoxLayout(tw); tl.setContentsMargins(0, 0, 0, 0)
        self._t_cur = QLabel('0:00'); self._t_cur.setObjectName('time')
        self._t_tot = QLabel('0:00'); self._t_tot.setObjectName('time')
        tl.addWidget(self._t_cur); tl.addStretch(); tl.addWidget(self._t_tot)
        il.addWidget(tw)

        cw = QWidget(); cl = QHBoxLayout(cw)
        cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(14); cl.setAlignment(Qt.AlignCenter)
        self._btn_shuf = self._mk_btn('⇌', check=True)
        self._btn_prev = self._mk_btn('⏮')
        self._btn_play = self._mk_btn('▶', big=True)
        self._btn_next = self._mk_btn('⏭')
        self._btn_rep  = self._mk_btn('↻', check=True)
        self._btn_shuf.clicked.connect(self._toggle_shuffle)
        self._btn_prev.clicked.connect(self._prev)
        self._btn_play.clicked.connect(self._toggle_play)
        self._btn_next.clicked.connect(self._next)
        self._btn_rep.clicked.connect(self._cycle_repeat)
        for b in (self._btn_shuf, self._btn_prev, self._btn_play, self._btn_next, self._btn_rep):
            cl.addWidget(b)
        il.addWidget(cw)

        vw = QWidget(); vl = QHBoxLayout(vw); vl.setContentsMargins(0, 0, 0, 0); vl.setSpacing(10)
        vl.addWidget(QLabel('🔈'))
        self._vol = QSlider(Qt.Horizontal); self._vol.setRange(0, 100); self._vol.setValue(80)
        self._vol.setObjectName('vol'); self._vol.valueChanged.connect(lambda v: self._mp.audio_set_volume(v))
        vl.addWidget(self._vol); vl.addWidget(QLabel('🔊'))
        il.addWidget(vw)

        pl.addWidget(inner, 0, Qt.AlignCenter)
        splitter.addWidget(panel)
        splitter.setStretchFactor(0, 0); splitter.setStretchFactor(1, 1)
        splitter.setSizes([290, 770])

    def _mk_btn(self, text, big=False, check=False):
        b = QPushButton(text); b.setCheckable(check)
        b.setObjectName('play-btn' if big else 'ctrl-btn')
        b.setFixedSize(54, 54) if big else b.setFixedSize(38, 38)
        return b

    # ── Style ────────────────────────────────────────────────────────────

    def _apply_style(self):
        self.setStyleSheet(f"""
        * {{ font-family: -apple-system, "Segoe UI", Ubuntu, system-ui, sans-serif; }}
        QMainWindow, QWidget {{ background: {BG}; color: {T1}; }}
        QWidget#sidebar  {{ background: {S1}; border-right: 1px solid {S3}; }}

        QListView {{ background: {S1}; border: none; outline: none; }}
        QListView::item {{ background: transparent; border: none; }}
        QListView::item:hover {{ background: {S2}; }}
        QListView::item:selected {{ background: transparent; border: none; }}
        QScrollBar:vertical {{ background: {S1}; width: 4px; border: none; }}
        QScrollBar::handle:vertical {{ background: {S3}; border-radius: 2px; min-height: 24px; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}

        QWidget#panel {{ background: {BG}; }}
        QLabel#now-title  {{ font-size: 18px; font-weight: 650; color: {T1}; letter-spacing: -0.3px; }}
        QLabel#now-artist {{ font-size: 13px; color: {T2}; }}
        QLabel#time       {{ font-size: 11px; color: {T3}; }}
        QLabel#art        {{ border-radius: 14px; background: {S2}; }}

        QPushButton#ctrl-btn {{
            background: transparent; border: none; color: {T2};
            font-size: 18px; border-radius: 19px;
        }}
        QPushButton#ctrl-btn:hover   {{ color: {T1}; background: {S2}; }}
        QPushButton#ctrl-btn:checked {{ color: {ACC}; }}
        QPushButton#play-btn {{
            background: {ACC}; border: none; color: {BG};
            font-size: 20px; border-radius: 27px; font-weight: bold;
        }}
        QPushButton#play-btn:hover   {{ background: #D4A050; }}
        QPushButton#play-btn:pressed {{ background: #B07830; }}

        QSlider#vol::groove:horizontal {{ height: 3px; background: {S3}; border-radius: 2px; }}
        QSlider#vol::handle:horizontal {{
            width: 12px; height: 12px; margin: -5px 0;
            background: {ACC}; border-radius: 6px;
        }}
        QSlider#vol::sub-page:horizontal {{ background: {ACC}; border-radius: 2px; }}
        """)

    # ── Column switching (no gridSize — uses proxy model) ─────────────────

    def eventFilter(self, obj, event):
        if obj is self._list.viewport() and event.type() == QEvent.Type.Resize:
            self._update_cols()
        return False

    def _update_cols(self):
        vw   = self._list.viewport().width()
        cols = 2 if vw >= COL_BREAK else 1
        self._proxy.set_cols(cols)
        # Scroll back to current track after model reset
        if self._cur >= 0:
            proxy_row = self._cur // self._proxy.cols
            self._list.scrollTo(self._proxy.index(proxy_row, 0),
                                 QAbstractItemView.EnsureVisible)

    # ── Library ───────────────────────────────────────────────────────────

    def _start_scan(self):
        self._scanner = LibraryScanner(MUSIC_DIR)
        self._scanner.batch_ready.connect(self._on_batch)
        self._scanner.done.connect(
            lambda n: self.setWindowTitle(f'Player — {n} tracks'))
        self._scanner.start()

    @Slot(list)
    def _on_batch(self, raw: list[dict]):
        processed = []
        for m in raw:
            art        = m.pop('_art', None)
            m['thumb'] = make_pixmap(art, m['title'], THUMB_SZ)
            m['_art']  = art
            processed.append(m)
        self._src.append(processed)

        if not self._restore_done and self._saved_path:
            i = self._src.find_path(self._saved_path)
            if i >= 0:
                self._load_track(i); self._restore_done = True; return

        if self._cur == -1 and len(self._src) > 0:
            self._load_track(0)

    # ── Playback ──────────────────────────────────────────────────────────

    def _load_track(self, i: int):
        t = self._src.track(i)
        if not t:
            return
        prev = self._cur; self._cur = i

        media = self._vlc.media_new(t['path'])
        self._mp.set_media(media)
        self._mp.audio_set_volume(self._vol.value())

        self._lbl_title.setText(t['title'])
        self._lbl_artist.setText(t['artist'] or 'Unknown artist')
        self._t_tot.setText(fmt(t['duration']))
        self._t_cur.setText('0:00')
        self._art.setPixmap(make_pixmap(t.get('_art'), t['title'], ART_SZ))
        self.setWindowTitle(
            f"{t['title']}  ·  {t['artist']}" if t['artist'] else t['title'])

        self._wave.set_envelope([]); self._wave.set_progress(0.0)
        job = WaveformJob(t['path']); job.signals.done.connect(self._on_waveform)
        self._pool.start(job)

        cols = self._proxy.cols
        for idx in {prev, i}:
            if idx >= 0:
                self._list.update(self._proxy.index(idx // cols, 0))
        self._list.scrollTo(self._proxy.index(i // cols, 0),
                            QAbstractItemView.EnsureVisible)

    @Slot(str, list)
    def _on_waveform(self, path: str, env: list[float]):
        t = self._src.track(self._cur)
        if t and t['path'] == path:
            self._wave.set_envelope(env)

    def _play(self):
        self._mp.play(); self._playing = True; self._btn_play.setText('⏸')

    def _pause(self):
        self._mp.pause(); self._playing = False; self._btn_play.setText('▶')

    def _toggle_play(self):
        if not len(self._src): return
        if self._cur == -1: self._load_track(0); self._play(); return
        self._pause() if self._playing else self._play()

    def _on_track_ended(self):
        if self._repeat == 2:
            t = self._src.track(self._cur)
            if t:
                self._mp.set_media(self._vlc.media_new(t['path']))
                self._mp.audio_set_volume(self._vol.value())
                self._wave.set_progress(0.0); self._t_cur.setText('0:00')
                self._play()
            return
        if self._shuffle:
            n = random.randrange(len(self._src))
        else:
            n = (self._cur + 1) % len(self._src)
            if n == 0 and self._repeat == 0:
                self._playing = False; self._btn_play.setText('▶'); return
        self._load_track(n); self._play()

    def _next(self):
        if not len(self._src): return
        was = self._playing
        n   = random.randrange(len(self._src)) if self._shuffle \
              else (self._cur + 1) % len(self._src)
        self._load_track(n)
        if was: self._play()

    def _prev(self):
        if not len(self._src): return
        was = self._playing
        if self._mp.get_time() > 3000: self._mp.set_time(0); return
        n = random.randrange(len(self._src)) if self._shuffle \
            else (self._cur - 1) % len(self._src)
        self._load_track(n)
        if was: self._play()

    def _toggle_shuffle(self):
        self._shuffle = self._btn_shuf.isChecked()

    def _cycle_repeat(self):
        self._repeat = (self._repeat + 1) % 3
        self._btn_rep.setChecked(self._repeat > 0)
        self._btn_rep.setText(['↻', '↻', '➀'][self._repeat])

    def _on_seek(self, pos: float):
        self._mp.set_position(pos); self._wave.set_progress(pos)

    def _on_track_click(self, abs_idx: int):
        if abs_idx == self._cur:
            self._toggle_play()
        else:
            self._load_track(abs_idx); self._play()

    # ── Poll ──────────────────────────────────────────────────────────────

    def _tick(self):
        state = self._mp.get_state()
        if state == vlc.State.Ended:
            self._on_track_ended(); return
        if state == vlc.State.Playing:
            self._wave.set_progress(self._mp.get_position())
            self._t_cur.setText(fmt(self._mp.get_time() / 1000))
            t = self._src.track(self._cur)
            if t and not t['duration'] and self._mp.get_length() > 0:
                t['duration'] = self._mp.get_length() / 1000
                self._t_tot.setText(fmt(t['duration']))

    # ── Keyboard ─────────────────────────────────────────────────────────

    def keyPressEvent(self, event):
        k = event.key()
        if   k == Qt.Key_Space:  self._toggle_play()
        elif k == Qt.Key_Right:  self._mp.set_time(min(self._mp.get_time()+5000, self._mp.get_length()))
        elif k == Qt.Key_Left:   self._mp.set_time(max(0, self._mp.get_time()-5000))
        elif k == Qt.Key_Up:     self._vol.setValue(min(100, self._vol.value()+5))
        elif k == Qt.Key_Down:   self._vol.setValue(max(0,   self._vol.value()-5))
        else: super().keyPressEvent(event)

    def _save_state(self):
        t = self._src.track(self._cur)
        if not t: return
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps({'current_path': t['path']}))
        except Exception:
            pass

    def closeEvent(self, event):
        self._save_state(); self._mp.stop(); self._timer.stop()
        super().closeEvent(event)


# ── Entry ──────────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName('Player')
    pal = app.palette()
    pal.setColor(QPalette.Window,          QColor(BG))
    pal.setColor(QPalette.WindowText,      QColor(T1))
    pal.setColor(QPalette.Base,            QColor(S1))
    pal.setColor(QPalette.AlternateBase,   QColor(S2))
    pal.setColor(QPalette.Text,            QColor(T1))
    pal.setColor(QPalette.Button,          QColor(S2))
    pal.setColor(QPalette.ButtonText,      QColor(T1))
    pal.setColor(QPalette.Highlight,       QColor(200, 148, 58, 100))
    pal.setColor(QPalette.HighlightedText, QColor(T1))
    app.setPalette(pal)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
