# src/display.py
import gi, math, queue, numpy as np, logging
from enum import Enum
from opensimplex import OpenSimplex

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
import cairo

log = logging.getLogger(__name__)

# ======================================================================================
#  Final Animation Polish: Hyper-Reactive Fizz
#
#  This version incorporates the final user feedback to make the voice reactivity
#  unmistakable and satisfying.
# ======================================================================================


class Cfg:
    # --- Base Setup ---
    RADIUS = 30
    PADDING = 15
    REFRESH_HZ = 60

    # --- Colors ---
    CORE_FILL = (0.0, 0.0, 0.0, 0.95)
    CORE_LINE = (1.0, 1.0, 1.0, 1.0)
    SUCCESS_RIPPLE = (1.0, 1.0, 1.0, 0.9)

    # --- State: Idle ---
    IDLE_SCALE = 0.60
    IDLE_PULSE_AMP = 0.04
    IDLE_PULSE_PERIOD_S = 3.5

    # --- State: Listening (Dual-Metric Response) ---
    LISTENING_SCALE = 1.0
    # -- The Swell (RMS -> Size) --
    SWELL_SENSITIVITY = 10.0
    RADIUS_SWELL_FACTOR = 0.25
    SWELL_ATTACK_MS = 50
    SWELL_RELEASE_MS = 400

    # -- REWORK: The Fizz (Peak -> Surface Detail & Trill) --
    # Dramatically increased sensitivity to make perturbations immediate and obvious.
    FIZZ_SENSITIVITY = 0.5
    # Increased max amplitude to make the effect more pronounced.
    NOISE_MAX_AMP = 0.90
    LISTENING_NOISE_SPEED_BOOST = 3.5
    FIZZ_ATTACK_MS = 10
    FIZZ_RELEASE_MS = 70

    # --- State: Thinking ---
    THINKING_SCALE = 0.70
    THINKING_NOISE_SPEED_FACTOR = 10.0
    THINKING_NOISE_AMP = 0.06

    # --- Event: Success Ripple ---
    SUCCESS_RIPPLE_FADE_MS = 600
    SUCCESS_RIPPLE_END_SCALE = 2.5
    SUCCESS_RIPPLE_WIDTH = 3

    # --- General Animation ---
    TRANSITION_MS = 350
    BLOB_POINTS = 64
    NOISE_SPEED = 0.20


def ease_out_cubic(t):
    t -= 1
    return t * t * t + 1


class SystemState(Enum):
    INACTIVE = 0
    IDLE = 1
    LISTENING = 2
    THINKING = 3


class Tween:
    def __init__(self, val, ease=ease_out_cubic):
        self.start_val = val
        self.end_val = val
        self.value = val
        self.duration = 0.001
        self.elapsed = 0.0
        self.easing_fn = ease

    def set(self, val, dur):
        if dur <= 0:
            self.value = val
            self.elapsed = self.duration
            return
        self.start_val = self.value
        self.end_val = val
        self.duration = dur / 1000.0
        self.elapsed = 0.0

    def update(self, dt):
        if self.is_finished():
            return
        self.elapsed += dt
        if self.elapsed >= self.duration:
            self.value = self.end_val
        else:
            self.value = self.start_val + (
                self.end_val - self.start_val
            ) * self.easing_fn(self.elapsed / self.duration)

    def is_finished(self):
        return self.elapsed >= self.duration


class VoiceWidget(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.state = SystemState.INACTIVE
        self.time = 0.0
        self.noise_gen = OpenSimplex(seed=np.random.randint(0, 10000))
        self.tweens = {
            "master_alpha": Tween(0.0),
            "blob_scale": Tween(Cfg.IDLE_SCALE),
            "noise_amp_factor": Tween(0.0),
            "noise_speed_factor": Tween(1.0),
            "success_ripple_alpha": Tween(0.0),
        }
        self.dynamic_swell = 0.0
        self.dynamic_fizz = 0.0
        dt = 1000 / Cfg.REFRESH_HZ
        self.swell_attack_const = math.exp(-dt / Cfg.SWELL_ATTACK_MS)
        self.swell_release_const = math.exp(-dt / Cfg.SWELL_RELEASE_MS)
        self.fizz_attack_const = math.exp(-dt / Cfg.FIZZ_ATTACK_MS)
        self.fizz_release_const = math.exp(-dt / Cfg.FIZZ_RELEASE_MS)
        self.connect("draw", self._on_draw)
        GLib.timeout_add(1000 // Cfg.REFRESH_HZ, self._on_tick)

    def _on_tick(self):
        dt = 1.0 / Cfg.REFRESH_HZ
        self.time += dt
        for tween in self.tweens.values():
            tween.update(dt)
        self.queue_draw()
        return True

    def _on_draw(self, w, cr):
        alpha = self.tweens["master_alpha"].value
        if alpha < 0.01:
            return
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        cx, cy = width / 2.0, height / 2.0

        ripple_alpha = self.tweens["success_ripple_alpha"].value
        if ripple_alpha > 0.01:
            t = (
                self.tweens["success_ripple_alpha"].elapsed
                / self.tweens["success_ripple_alpha"].duration
            )
            radius = Cfg.RADIUS * t * Cfg.SUCCESS_RIPPLE_END_SCALE
            cr.set_source_rgba(
                *Cfg.SUCCESS_RIPPLE[:3], Cfg.SUCCESS_RIPPLE[3] * ripple_alpha
            )
            cr.set_line_width(Cfg.SUCCESS_RIPPLE_WIDTH * (1 - t**2))
            cr.arc(cx, cy, radius, 0, 2 * math.pi)
            cr.stroke()

        blob_scale = self.tweens["blob_scale"].value
        idle_pulse = 1 + Cfg.IDLE_PULSE_AMP * math.sin(
            self.time * 2 * math.pi / Cfg.IDLE_PULSE_PERIOD_S
        )
        radius = (
            Cfg.RADIUS
            * blob_scale
            * idle_pulse
            * (1 + self.dynamic_swell * Cfg.RADIUS_SWELL_FACTOR)
        )

        if self.state == SystemState.LISTENING:
            goo_factor = self.dynamic_fizz * Cfg.NOISE_MAX_AMP
            noise_speed = Cfg.NOISE_SPEED * (
                1 + self.dynamic_fizz * Cfg.LISTENING_NOISE_SPEED_BOOST
            )
        else:
            goo_factor = self.tweens["noise_amp_factor"].value
            noise_speed = Cfg.NOISE_SPEED * self.tweens["noise_speed_factor"].value

        cr.move_to(cx + radius, cy)
        for i in range(Cfg.BLOB_POINTS + 1):
            th = 2 * math.pi * i / Cfg.BLOB_POINTS
            noise = self.noise_gen.noise3(
                math.cos(th) * 1.5, math.sin(th) * 1.5, self.time * noise_speed
            )
            r2 = radius * (1 + noise * goo_factor)
            cr.line_to(cx + r2 * math.cos(th), cy + r2 * math.sin(th))

        cr.set_source_rgba(*Cfg.CORE_FILL[:3], Cfg.CORE_FILL[3] * alpha)
        cr.fill_preserve()
        cr.set_source_rgba(*Cfg.CORE_LINE)
        cr.set_line_width(1.5)
        cr.stroke()

    def set_app_state(self, new_state):
        if self.state == new_state:
            return
        log.info(f"Display state: {self.state.name} -> {new_state.name}")
        self.state = new_state
        T = Cfg.TRANSITION_MS
        if new_state == SystemState.INACTIVE:
            self.tweens["master_alpha"].set(0.0, T)
        elif new_state == SystemState.IDLE:
            self.tweens["master_alpha"].set(1.0, T)
            self.tweens["blob_scale"].set(Cfg.IDLE_SCALE, T)
            self.tweens["noise_amp_factor"].set(0.0, T)
            self.tweens["noise_speed_factor"].set(1.0, T)
        elif new_state == SystemState.LISTENING:
            self.tweens["blob_scale"].set(Cfg.LISTENING_SCALE, T)
        elif new_state == SystemState.THINKING:
            self.tweens["blob_scale"].set(Cfg.THINKING_SCALE, T)
            self.tweens["noise_amp_factor"].set(Cfg.THINKING_NOISE_AMP, T)
            self.tweens["noise_speed_factor"].set(
                Cfg.THINKING_NOISE_SPEED_FACTOR, T * 0.8
            )

    def process_audio_chunk(self, audio_chunk):
        if len(audio_chunk) == 0:
            return
        rms = min(1.0, np.sqrt(np.mean(audio_chunk**2)) * Cfg.SWELL_SENSITIVITY)
        if rms > self.dynamic_swell:
            self.dynamic_swell = self.dynamic_swell * self.swell_attack_const + rms * (
                1 - self.swell_attack_const
            )
        else:
            self.dynamic_swell = self.dynamic_swell * self.swell_release_const
        peak = min(1.0, np.max(np.abs(audio_chunk)) * Cfg.FIZZ_SENSITIVITY)
        if peak > self.dynamic_fizz:
            self.dynamic_fizz = self.dynamic_fizz * self.fizz_attack_const + peak * (
                1 - self.fizz_attack_const
            )
        else:
            self.dynamic_fizz = self.dynamic_fizz * self.fizz_release_const

    def trigger_success(self):
        self.tweens["success_ripple_alpha"].set(1.0, 10)
        GLib.timeout_add(
            10,
            lambda: self.tweens["success_ripple_alpha"].set(
                0.0, Cfg.SUCCESS_RIPPLE_FADE_MS
            ),
        )
        self.set_app_state(SystemState.IDLE)

    def process_pipeline_event(self, event):
        if event == "asr_start":
            self.set_app_state(SystemState.THINKING)
        elif event == "output_start":
            self.trigger_success()

    def set_active(self, is_active):
        current_state = self.state
        if is_active and current_state in [SystemState.INACTIVE, SystemState.IDLE]:
            self.set_app_state(SystemState.LISTENING)
        elif not is_active and current_state not in [
            SystemState.IDLE,
            SystemState.INACTIVE,
        ]:
            self.set_app_state(SystemState.IDLE)


def start_display(
    monitor_q: queue.Queue,
    vad_q: queue.Queue,
    pipeline_event_q: queue.Queue,
    dict_control,
):
    scr = Gdk.Screen.get_default()
    win = Gtk.Window(Gtk.WindowType.POPUP)
    win.set_app_paintable(True)
    win.set_accept_focus(False)
    if scr.is_composited() and scr.get_rgba_visual():
        win.set_visual(scr.get_rgba_visual())
    win.set_keep_above(True)
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win_size = 2 * (Cfg.RADIUS + Cfg.PADDING)
    g = scr.get_monitor_geometry(scr.get_primary_monitor())
    win.move(g.x + g.width - win_size - 20, g.y + g.height - win_size - 20)
    win.set_default_size(win_size, win_size)
    voice_widget = VoiceWidget()
    win.add(voice_widget)

    def poll_all_queues():
        try:
            voice_widget.process_audio_chunk(monitor_q.get_nowait())
        except queue.Empty:
            voice_widget.process_audio_chunk(np.array([0.0]))
        try:
            if not pipeline_event_q.empty():
                voice_widget.process_pipeline_event(pipeline_event_q.get_nowait())
        except queue.Empty:
            pass
        return True

    def poll_dictation_control():
        voice_widget.set_active(dict_control.is_active())
        return True

    GLib.timeout_add(1000 // Cfg.REFRESH_HZ, poll_all_queues)
    GLib.timeout_add(200, poll_dictation_control)
    win.show_all()
    voice_widget.set_app_state(SystemState.IDLE)
    Gtk.main()
