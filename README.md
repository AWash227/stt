# STT (Speech To Text)

A simple speech-to-text system that uses [nvidia/parakeet-tdt-0.6b-v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) under the hood. Dictation mode is toggled with a hotkey.

Uses xdotool to send the text generated as keystrokes into the active application.
Only tested on Arch Linux. Set up a keybind (e.g. `super+alt+v`) that runs:

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
