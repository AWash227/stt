# STT (Speech To Text)

A simple speech-to-text system that uses [nvidia/parakeet-tdt-0.6b-v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) under the hood. Dictation mode is toggled with a hotkey.

Uses xdotool to send the text generated as keystrokes into the active application.
Only tested on Arch Linux. Set up a keybind (e.g. `super+alt+v`) that runs:

```
echo "toggle" | nc -U /tmp/sttdict.sock
```

Pressing it again stops dictation. If you set `STT_SOCK_PATH`, update the socket path accordingly.
