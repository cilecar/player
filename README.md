# Player

Десктопный музыкальный плеер для локальной библиотеки. Есть две реализации:

- **`player.py`**: PySide6 + VLC. Тёмная тема, обложки, waveform трека, сетка библиотеки,
  многопоточное сканирование, состояние сохраняется между запусками.
- **Electron** (`main.js`, `renderer.js`): тот же плеер на веб-технологиях, метаданные читает
  через `music-metadata`.

Форматы: `.mp3`, `.flac`, `.ogg`, `.wav`, `.aac`, `.m4a`, `.opus`.

## Папка с музыкой

По умолчанию `~/Music`, переопределяется переменной окружения:

```sh
export MUSIC_DIR=~/Музыка
```

## Запуск: Python-версия

Нужен установленный VLC (`sudo dnf install vlc` / `sudo apt install vlc`).

```sh
pip install PySide6 python-vlc mutagen numpy soundfile
python3 player.py
```

## Запуск: Electron-версия

```sh
npm install
npm start
```
