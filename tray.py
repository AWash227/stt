import threading
from PIL import Image, ImageDraw
import pystray
import main


def _create_image():
    size = 64
    image = Image.new('RGB', (size, size), 'white')
    draw = ImageDraw.Draw(image)
    draw.ellipse((16, 16, 48, 48), fill='black')
    return image


def _toggle(icon=None, item=None):
    if main.dict_control.toggle():
        main.try_notify("Dictation started!")
    else:
        main.try_notify("Dictation stopped!")


def _exit(icon, item):
    main.shutdown_event.set()
    icon.stop()


def main_tray():
    threading.Thread(target=main.main, daemon=True).start()

    menu = pystray.Menu(
        pystray.MenuItem('Toggle dictation', _toggle, default=True),
        pystray.MenuItem('Exit', _exit)
    )
    icon = pystray.Icon('stt', _create_image(), 'STT', menu)
    icon.run()


if __name__ == '__main__':
    main_tray()
