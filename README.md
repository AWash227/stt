# STT (Speech To Text)

A simple speech-to-text system that uses [nvidia/parakeet-tdt-0.6b-v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) under the hood. Dictation mode is toggled with a hotkey. The model is downloaded automatically the first time you run the program.

Uses `xdotool` on Linux to send the text generated as keystrokes. When it is
unavailable (such as on Windows) the script falls back to the `keyboard` or
`pyautogui` libraries. Primarily developed on Arch Linux. Set up a keybind

(e.g. `super+alt+v`) that runs:

```bash
python toggle.py toggle
# or: python -m toggle toggle
```

Pressing it again stops dictation. If you set `STT_SOCK_PATH`, update the socket path accordingly.
On Windows (where Unix sockets may be unavailable), the code falls back to a
TCP socket on `localhost:8765`. Override the port with `STT_PORT` if needed.

## Running the CLI

Use `start.sh` on Unix-like systems or `start.ps1` on Windows. Both scripts
resolve their own location so they work when invoked via a symlink. You can
put them on your `PATH` for convenience. For example:

```bash
ln -s /path/to/repo/start.sh ~/bin/stt
```

On Windows PowerShell:

```powershell
New-Item -ItemType SymbolicLink -Path "$env:USERPROFILE\bin\stt.ps1" -Target "C:\\path\\to\\repo\\start.ps1"
```

Then running `stt` (or `stt.ps1`) will start the application using the
virtual environment.

## Windows Setup

For a smooth installation on Windows, make sure you have:
- The [Visual C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) installed. They provide the compilers needed by some Python packages.
- [Python 3.11.9](https://www.python.org/downloads/release/python-3119/) installed and added to your `PATH`.

After installing these, open a **new** PowerShell window and verify:

```powershell
python --version
```

It should report `Python 3.11.9`.


## Tray icon

Launch `python -m tray` to run the program with a small system tray icon.
Double-click the icon to toggle dictation mode. Right click the icon to exit.

## Notifications

When dictation is toggled, the script tries to display a desktop notification.
On Linux it uses `notify-send` if available. On Windows it looks for
`win10toast` and falls back to `plyer` if present. Use `--no-notify` to
disable these messages.

## Building a standalone executable

A simple `Makefile` target packages the application using PyInstaller:

```bash
make package
```

This runs `pyinstaller --onefile tray.py` and places the executable in the `dist/` directory.

### Windows build

1. Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install the requirements and PyInstaller:

```powershell
pip install -r requirements.txt
pip install pyinstaller
```

Before running `pyinstaller`, clear any cached model files (for example under
`~/.cache/torch` or `~/.cache/huggingface`) so the model is not bundled inside
the executable and will be downloaded on first run.

3. Build the executable:

```powershell
pyinstaller --onefile tray.py
```

(Or run `make package` if `make` is available.) The resulting `tray.exe` will appear under `dist`.

### GPU requirements

The Nemo ASR model works best with a recent NVIDIA GPU and drivers providing CUDA support. CPU inference is possible but significantly slower.
