# STT (Speech To Text)

A simple speech-to-text system that uses [nvidia/parakeet-tdt-0.6b-v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) under the hood. Dictation mode is toggled with a hotkey.

Uses xdotool to send the text generated as keystrokes into the active application.
Set up a keybind (e.g. `super+alt+v`) that runs one of the following commands:

* **Unix socket (default on Linux/macOS)**

  ```
  echo "toggle" | nc -U /tmp/sttdict.sock
  ```

* **TCP fallback (Windows / systems without `AF_UNIX`)**

  ```
  echo toggle | nc 127.0.0.1 8765
  ```

  If `nc`/`netcat` is unavailable, you can send the command with Python:

  ```bash
  python - <<'EOF'
  import socket
  s = socket.create_connection(("127.0.0.1", 8765))
  s.sendall(b"toggle")
  s.close()
  EOF
  ```

Pressing it again stops dictation. If you set `STT_SOCK_PATH` or `STT_TCP_PORT`, update the paths/ports accordingly.

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
