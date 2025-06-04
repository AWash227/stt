import os
import socket
import argparse
import tempfile

SOCK_PATH = os.environ.get(
    "STT_SOCK_PATH",
    os.path.join(tempfile.gettempdir(), "sttdict.sock"),
)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Send control commands to the running STT instance"
    )
    parser.add_argument(
        "command",
        choices=["toggle", "stop"],
        help="Command to send to the STT control socket",
    )
    args = parser.parse_args(argv)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(SOCK_PATH)
        sock.sendall(args.command.encode())
    except Exception as e:
        print(f"[ERROR] {e}")
        return 1
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

