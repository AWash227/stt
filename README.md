# STT (Speech To Text)

A simple speech-to-text system that uses [nvidia/parakeet-tdt-0.6b-v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) under the hood. Dictation mode is toggled with a hotkey.

Uses `xdotool` on Linux to send the text generated as keystrokes. When it is
unavailable (such as on Windows) the script falls back to the `keyboard` or
`pyautogui` libraries. Primarily developed on Arch Linux. Set up a keybind
(e.g. `super+alt+v`) that runs:

```
echo "toggle" | nc -U /tmp/sttdict.sock
```

Pressing it again stops dictation. If you set `STT_SOCK_PATH`, update the socket path accordingly.

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

## Building a standalone executable

A simple `Makefile` target packages the application using PyInstaller:

```bash
make package
```

This runs `pyinstaller --onefile main.py` and places the executable in the `dist/` directory.

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

3. Build the executable:

```powershell
pyinstaller --onefile main.py
```

(Or run `make package` if `make` is available.) The resulting `main.exe` will appear under `dist`.

### GPU requirements

The Nemo ASR model works best with a recent NVIDIA GPU and drivers providing CUDA support. CPU inference is possible but significantly slower.
