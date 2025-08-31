import sys
import threading
import queue
import signal
import logging
import time

logging.basicConfig(level=logging.INFO, format="[%(levelname)s][%(name)s] %(message)s")

import mic, vad, asr, llm, output, control, display, config

log = logging.getLogger(__name__)


def main():
    shutdown_event = threading.Event()
    threads: list[threading.Thread] = []

    # graceful CTRL-C / SIGTERM
    def cleanup(sig, frame):
        if shutdown_event.is_set():
            return
        log.info("Shutdown requested…")
        shutdown_event.set()
        if config.Display.ENABLED:
            try:
                from gi.repository import Gtk, GLib

                GLib.idle_add(Gtk.main_quit)
            except ImportError:
                pass
        for t in threads:
            t.join(timeout=2.0)
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # ────────────────────────────────
    # Queues
    audio_q = queue.Queue()
    vad2agg_q = queue.Queue()
    agg2asr_q = queue.Queue()
    raw_text_q = queue.Queue()
    final_text_q = queue.Queue()
    monitor_q = queue.Queue()
    vad_event_q = queue.Queue()
    pipeline_event_q = queue.Queue()
    mic_control_q = queue.Queue()
    # ────────────────────────────────

    dict_control = control.DictationControl()
    control.socket_listener(dict_control, shutdown_event)

    # Mic
    threads.append(
        threading.Thread(
            target=mic.worker,
            args=(audio_q, monitor_q, shutdown_event, mic_control_q),
            name="Mic",
        )
    )

    # VAD
    vad_model = vad.load_model()
    if vad_model is None:
        sys.exit(1)

    threads.append(
        threading.Thread(
            target=vad.worker,
            args=(
                vad_model,
                audio_q,
                vad2agg_q,
                dict_control,
                vad_event_q,
                shutdown_event,
            ),
            name="VAD",
        )
    )

    # ASR
    asr_model = asr.load_model()
    if asr_model is None:
        sys.exit(1)

    threads.append(
        threading.Thread(
            target=asr.worker,
            args=(asr_model, vad2agg_q, raw_text_q, pipeline_event_q, shutdown_event),
            name="ASR",
        )
    )

    # Optional LLM post-process
    output_source_q = raw_text_q
    if config.LLM.ENABLED:
        threads.append(
            threading.Thread(
                target=llm.worker,
                args=(raw_text_q, final_text_q, pipeline_event_q, shutdown_event),
                name="LLM",
            )
        )
        output_source_q = final_text_q

    # Output
    threads.append(
        threading.Thread(
            target=output.worker,
            args=(output_source_q, pipeline_event_q, dict_control, shutdown_event),
            name="Output",
        )
    )

    # On-screen blob
    if config.Display.ENABLED:
        threads.append(
            threading.Thread(
                target=display.start_display,
                args=(monitor_q, vad_event_q, pipeline_event_q, dict_control, mic_control_q),
                name="Display",
            )
        )

    # ────────────────────────────────
    # Launch everything
    for t in threads:
        t.start()

    print("\nReady!  Press Ctrl+C to quit.")
    while not shutdown_event.is_set():
        time.sleep(1)


if __name__ == "__main__":
    if "--list-mics" in sys.argv:
        try:
            import sounddevice as sd

            print("\nAvailable audio devices:\n")
            print(sd.query_devices())
            print("\nSet INPUT_DEVICE_INDEX in src/config.py to pick one.\n")
        except Exception as e:
            log.error(f"Could not list audio devices: {e}")
        sys.exit(0)
    main()
