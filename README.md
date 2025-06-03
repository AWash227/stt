# STT (Speech To Text)

A simple speech to text system that uses [nvidia/parakeet-tdt-0.6b-v2](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v2) under the hood to transcribe speech on keypress.

Uses xdotool to send the text generated as keystrokes into the active application.
Only tested on Arch Linux. You need to setup a keybind to send a message to a socket file located at `/tmp/sttdict.sock`
