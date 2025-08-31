# src/display.py
import gi, math, queue, numpy as np, logging, json, os, time
from enum import Enum
from opensimplex import OpenSimplex

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib
try:
    from gi.repository import Pango
except Exception:
    Pango = None
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

    # --- Bubbles UI ---
    BUBBLE_WIDTH = 460
    BUBBLE_HEIGHT = 320
    BUBBLE_MARGIN = 14
    SESSION_GAP_SEC = 20.0
    MAX_HISTORY_ITEMS = 50
    FONT_SIZE_PT = 13
    TITLE_FONT_SIZE_PT = 12
    TEXT_RGBA = (0.93, 0.94, 0.96, 0.98)  # light text for dark bg


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

    # Create transcript bubbles next to the blob
    try:
        bubbles = BubblesUI(win, dict_control, narration_path="narration.jsonl")
    except Exception as e:
        log.error(f"Failed to init BubblesUI: {e}")

    Gtk.main()


# =====================
#  Transcript Bubbles
# =====================

class TextBufferSession:
    def __init__(self, created_ts: float | None = None, text: str = ""):
        self.created_ts = created_ts if created_ts is not None else time.time()
        self.text = text

    def title(self) -> str:
        base = self.text.strip().split("\n", 1)[0]
        if len(base) > 60:
            base = base[:57] + "…"
        ts_str = time.strftime("%H:%M:%S", time.localtime(self.created_ts))
        return f"{ts_str} — {base}" if base else f"{ts_str}"


class BubblesUI:
    def __init__(self, anchor_win: Gtk.Window, dict_control, narration_path: str = "narration.jsonl"):
        self.anchor_win = anchor_win
        self.dict_control = dict_control
        self.narration_path = narration_path
        self.history: list[TextBufferSession] = []
        self.active: TextBufferSession | None = None
        self._file_pos = 0
        self._was_active = False
        self._user_moved = False
        self._dragging = False
        self._drag_origin = (0, 0)
        self._win_origin = (0, 0)

        # Window
        self.win = Gtk.Window(Gtk.WindowType.POPUP)
        scr = Gdk.Screen.get_default()
        self.win.set_app_paintable(True)
        self.win.set_accept_focus(True)
        if scr.is_composited() and scr.get_rgba_visual():
            self.win.set_visual(scr.get_rgba_visual())
        self.win.set_keep_above(True)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_skip_pager_hint(True)
        self.win.set_default_size(Cfg.BUBBLE_WIDTH, Cfg.BUBBLE_HEIGHT)

        # Root container
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_margin_top(10)
        root.set_margin_bottom(10)
        root.set_margin_start(10)
        root.set_margin_end(10)

        # Collapsible header (toggles the whole panel)
        header = Gtk.EventBox()
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header_box.get_style_context().add_class("chip")
        header_box.set_margin_top(2)
        header_box.set_margin_bottom(4)
        header_box.set_margin_start(2)
        header_box.set_margin_end(2)
        self.header_box = header_box
        self.header_icon = Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.MENU)
        header_box.pack_start(self.header_icon, False, False, 0)
        self.header_label = Gtk.Label(label="Transcript")
        self.header_label.set_xalign(0.0)
        self.header_label.set_line_wrap(True)
        if Pango:
            self.header_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.header_label.set_max_width_chars(42)
        header_box.pack_start(self.header_label, True, True, 0)
        # small icon copy button
        self.header_copy_btn = Gtk.Button()
        self.header_copy_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.header_copy_btn.set_tooltip_text("Copy current buffer")
        self.header_copy_btn.get_style_context().add_class("btn")
        self.header_copy_btn.set_image(Gtk.Image.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.MENU))
        self.header_copy_btn.connect("clicked", self._on_copy)
        header_box.pack_end(self.header_copy_btn, False, False, 0)
        header.add(header_box)
        root.pack_start(header, False, False, 0)

        self.main_revealer = Gtk.Revealer()
        self.main_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.main_revealer.set_transition_duration(120)
        self.main_revealer.set_reveal_child(False)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        # History header + animated revealer
        hist_header_ev = Gtk.EventBox()
        hist_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hist_header.set_margin_top(2)
        hist_header.set_margin_bottom(2)
        hist_header.set_margin_start(2)
        hist_header.set_margin_end(2)
        hist_header.get_style_context().add_class("subtle")
        self.hist_icon = Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.MENU)
        hist_header.pack_start(self.hist_icon, False, False, 0)
        self.hist_label = Gtk.Label(label="History")
        self.hist_label.set_xalign(0.0)
        hist_header.pack_start(self.hist_label, True, True, 0)
        hist_header_ev.add(hist_header)
        content.pack_start(hist_header_ev, False, False, 0)

        self.hist_revealer = Gtk.Revealer()
        self.hist_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.hist_revealer.set_transition_duration(100)
        self.hist_revealer.set_reveal_child(False)

        # History scroller with list inside a rounded frame
        hist_scroller = Gtk.ScrolledWindow()
        hist_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        hist_scroller.set_propagate_natural_height(True)
        hist_scroller.set_min_content_height(160)
        hist_scroller.set_vexpand(True)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.set_activate_on_single_click(True)
        self.listbox.connect("row-activated", self._on_history_activate)
        self.listbox.set_header_func(self._list_header_func, None)
        self.listbox.set_vexpand(True)
        hist_scroller.add(self.listbox)
        hist_frame = Gtk.Frame()
        hist_frame.set_shadow_type(Gtk.ShadowType.NONE)
        hist_frame.get_style_context().add_class("bubble")
        hist_frame.set_margin_start(2)
        hist_frame.set_margin_end(2)
        hist_frame.set_margin_top(2)
        hist_frame.set_margin_bottom(2)
        hist_frame.add(hist_scroller)
        self.hist_revealer.add(hist_frame)
        content.pack_start(self.hist_revealer, True, True, 0)

        # Active bubble
        active_frame = Gtk.Frame()
        active_frame.set_shadow_type(Gtk.ShadowType.NONE)
        active_frame.get_style_context().add_class("bubble")

        active_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        active_box.set_margin_top(12)
        active_box.set_margin_bottom(12)
        active_box.set_margin_start(12)
        active_box.set_margin_end(12)

        # Active editor (no separate title; header acts as visualiser)

        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.textview.set_left_margin(8)
        self.textview.set_right_margin(8)
        self.textview.connect("button-press-event", self._on_active_click)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self.textview)
        active_box.pack_start(scroller, True, True, 0)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.copy_btn = Gtk.Button()
        self.copy_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.copy_btn.set_tooltip_text("Copy current buffer")
        self.copy_btn.get_style_context().add_class("btn")
        self.copy_btn.set_image(Gtk.Image.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.MENU))
        self.copy_btn.connect("clicked", self._on_copy)
        self.done_btn = Gtk.Button(label="Done")
        self.done_btn.connect("clicked", self._on_done)
        self.done_btn.set_no_show_all(True)
        self.done_btn.hide()
        btn_box.pack_start(self.copy_btn, False, False, 0)
        btn_box.pack_end(self.done_btn, False, False, 0)
        active_box.pack_start(btn_box, False, False, 0)

        active_frame.add(active_box)
        content.pack_end(active_frame, True, True, 0)

        self.main_revealer.add(content)
        root.pack_end(self.main_revealer, True, True, 0)

        # Wire header toggles
        # enable drag + toggle on release
        header.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        header.connect("button-press-event", self._on_header_press)
        header.connect("motion-notify-event", self._on_header_motion)
        header.connect("button-release-event", self._on_header_release)
        hist_header_ev.connect("button-release-event", self._toggle_history)
        # enable drag move via header press

        self.win.add(root)
        self._install_css()

        # Populate history and position window
        self._load_history_from_file()
        self._ensure_active_session()
        self._refresh_history_list()
        self._update_active_view()

        # show
        self._adjust_window_size()
        self.win.show_all()

        # timers
        GLib.timeout_add(400, self._poll_tail)  # tail narration.jsonl
        GLib.timeout_add(500, self._track_active_toggle)  # detect active on/off

        # keep bubble next to blob
        self.anchor_win.connect("configure-event", self._reposition)
        self.win.connect("size-allocate", self._reposition)
        GLib.idle_add(self._place_next_to_anchor)

    def _install_css(self):
        css = Gtk.CssProvider()
        css_str = f"""
        .bubble {{
            background-color: rgba(8,8,10,0.28); /* dark translucent, no white */
            border: 1px solid rgba(255,255,255,0.30);
            border-radius: 18px;
        }}
        .chip {{
            background-color: rgba(8,8,10,0.22);
            border: 1px solid rgba(255,255,255,0.25);
            border-radius: 14px;
            padding: 6px 10px;
        }}
        .subtle {{
            color: rgba(220,222,226,0.9);
        }}
        .secondary {{
            color: rgba(210,212,216,0.8);
        }}
        textview, textview text, label {{
            color: rgba(238,240,243,0.98);
        }}
        button.btn {{
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.28);
            color: rgba(238,240,243,0.98);
            border-radius: 10px;
            padding: 2px 6px;
            min-height: 0;
            min-width: 0;
        }}
        button.btn:hover {{
            background: rgba(255,255,255,0.10);
        }}
        scrolledwindow, viewport {{
            background-color: transparent;
        }}
        frame.bubble > border {{
            border-radius: 18px;
        }}
        list row {{
            padding: 4px 6px;
        }}
        """
        css.load_from_data(css_str.encode("utf-8"))
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(
            screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    # ---------- History and tailing ----------
    def _load_history_from_file(self):
        if not os.path.exists(self.narration_path):
            self._file_pos = 0
            return
        sessions: list[TextBufferSession] = []
        try:
            with open(self.narration_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
                self._file_pos = f.tell()
        except Exception:
            lines = []
        cur: TextBufferSession | None = None
        prev_ts = None
        for ln in lines[-10000:]:  # limit to last 10k lines for speed
            try:
                obj = json.loads(ln)
                ts = float(obj.get("timestamp", time.time()))
                txt = str(obj.get("text", "")).strip()
            except Exception:
                continue
            if not txt:
                continue
            if prev_ts is None or (ts - prev_ts) > Cfg.SESSION_GAP_SEC:
                # new session
                if cur and cur.text.strip():
                    sessions.append(cur)
                cur = TextBufferSession(created_ts=ts, text="")
            prev_ts = ts
            if cur is None:
                cur = TextBufferSession(created_ts=ts, text="")
            cur.text = (cur.text + (" " if cur.text else "") + txt).strip()
        if cur and cur.text.strip():
            sessions.append(cur)
        # keep only recent
        self.history = sessions[-Cfg.MAX_HISTORY_ITEMS:]

    def _poll_tail(self):
        try:
            with open(self.narration_path, "r", encoding="utf-8") as f:
                f.seek(self._file_pos)
                new = f.read()
                self._file_pos = f.tell()
        except Exception:
            return True
        if not new:
            return True
        for ln in new.splitlines():
            try:
                obj = json.loads(ln)
                txt = str(obj.get("text", "")).strip()
            except Exception:
                txt = ""
            if not txt:
                continue
            # Append only when dictation is active
            if self.dict_control and self.dict_control.is_active():
                self._ensure_active_session()
                if self.active:
                    self.active.text = (self.active.text + (" " if self.active.text else "") + txt).strip()
        self._update_active_view()
        return True

    def _track_active_toggle(self):
        now_active = self.dict_control.is_active() if self.dict_control else False
        if now_active and not self._was_active:
            # became active → start new session
            self._ensure_active_session(force_new=True)
            self._update_active_view()
        elif (not now_active) and self._was_active:
            # ended → push to history
            if self.active and self.active.text.strip():
                self.history.append(self.active)
                self.history = self.history[-Cfg.MAX_HISTORY_ITEMS:]
                self._refresh_history_list()
            self.active = None
            self._update_active_view()
        self._was_active = now_active
        return True

    def _ensure_active_session(self, force_new: bool = False):
        if self.active is None or force_new:
            self.active = TextBufferSession()

    # ---------- UI helpers ----------
    def _refresh_history_list(self):
        # clear
        for row in list(self.listbox.get_children()):
            self.listbox.remove(row)
        for sess in reversed(self.history):  # newest first
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            box.set_margin_top(2)
            box.set_margin_bottom(2)
            box.set_margin_start(6)
            box.set_margin_end(6)
            # Left: title + excerpt hierarchy
            left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            t_lbl = Gtk.Label(label=time.strftime("%H:%M:%S", time.localtime(sess.created_ts)))
            t_lbl.set_xalign(0.0)
            t_lbl.get_style_context().add_class("secondary")
            if Pango:
                attrs = Pango.AttrList()
                attrs.insert(Pango.attr_size_new(int(Pango.SCALE * 9)))
                t_lbl.set_attributes(attrs)
            excerpt = sess.text.strip().split("\n", 1)[0]
            if Pango:
                ex_lbl = Gtk.Label()
                ex_lbl.set_xalign(0.0)
                ex_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                ex_lbl.set_max_width_chars(40)
                ex_lbl.set_text(excerpt)
            else:
                ex_lbl = Gtk.Label(label=excerpt)
                ex_lbl.set_xalign(0.0)
            left.pack_start(t_lbl, False, False, 0)
            left.pack_start(ex_lbl, False, False, 0)
            box.pack_start(left, True, True, 0)
            # Right: copy button
            copy_btn = Gtk.Button()
            copy_btn.set_relief(Gtk.ReliefStyle.NONE)
            copy_btn.get_style_context().add_class("btn")
            copy_btn.set_tooltip_text("Copy")
            copy_btn.set_image(Gtk.Image.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.MENU))
            copy_btn.connect("clicked", self._on_copy_history, sess)
            box.pack_end(copy_btn, False, False, 0)
            row.add(box)
            row.sess = sess  # attach
            self.listbox.add(row)
        self.listbox.show_all()

    def _update_active_view(self):
        buf = self.textview.get_buffer()
        cur_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        new_text = self.active.text if self.active else ""
        if cur_text != new_text:
            buf.set_text(new_text)
        # Header summary line for collapsed view
        snippet = (new_text.strip().split("\n", 1)[0] if new_text else "")
        if Pango:
            if len(snippet) > 60:
                snippet = snippet[:57] + "…"
        self.header_label.set_text(snippet if snippet else "Transcript")

    def _on_history_activate(self, listbox, row):
        sess = getattr(row, "sess", None)
        if not sess:
            return
        # save current active
        if self.active and self.active.text.strip():
            self.history.append(self.active)
            self.history = self.history[-Cfg.MAX_HISTORY_ITEMS:]
        # copy into new active
        self.active = TextBufferSession(text=sess.text)
        self._refresh_history_list()
        self._update_active_view()

    def _on_copy(self, *_):
        buf = self.textview.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)

    def _on_copy_history(self, _btn, sess: TextBufferSession):
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(sess.text, -1)

    def _on_active_click(self, *_):
        # make editable
        self.textview.set_editable(True)
        self.textview.set_cursor_visible(True)
        self.done_btn.show()
        self.done_btn.set_no_show_all(False)

    def _on_done(self, *_):
        # save edits back to active
        buf = self.textview.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        if self.active is None:
            self.active = TextBufferSession(text=text)
        else:
            self.active.text = text
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.done_btn.hide()

    # ---------- Positioning ----------
    def _place_next_to_anchor(self):
        try:
            ax, ay = self.anchor_win.get_position()
            aw, ah = self.anchor_win.get_size()
        except Exception:
            return False
        # Monitor geometry for clamping
        screen = Gdk.Screen.get_default()
        gdk_win = self.anchor_win.get_window()
        try:
            mon_idx = screen.get_monitor_at_window(gdk_win) if gdk_win else screen.get_primary_monitor()
        except Exception:
            mon_idx = screen.get_primary_monitor()
        g = screen.get_monitor_geometry(mon_idx)

        # Desired default size
        bw, bh = self.win.get_size()
        max_w = min(Cfg.BUBBLE_WIDTH, g.width - 2 * Cfg.BUBBLE_MARGIN)
        max_h = min(Cfg.BUBBLE_HEIGHT, g.height - 2 * Cfg.BUBBLE_MARGIN)
        bw = max_w
        bh = min(max_h, bh)
        self.win.resize(bw, bh)

        # Prefer left of anchor; fallback to right, then above, then below
        def clamp(v, lo, hi):
            return max(lo, min(hi, v))

        placed = False
        # try left
        if ax - g.x >= (bw + Cfg.BUBBLE_MARGIN):
            x = ax - bw - Cfg.BUBBLE_MARGIN
            y = ay + ah - bh
            placed = True
        # try right
        elif (g.x + g.width) - (ax + aw) >= (bw + Cfg.BUBBLE_MARGIN):
            x = ax + aw + Cfg.BUBBLE_MARGIN
            y = ay + ah - bh
            placed = True
        # try above
        elif (ay - g.y) >= (bh + Cfg.BUBBLE_MARGIN):
            x = ax + aw - bw
            y = ay - bh - Cfg.BUBBLE_MARGIN
            placed = True
        # fallback: below
        else:
            x = ax + aw - bw
            y = ay + ah + Cfg.BUBBLE_MARGIN
            placed = True

        # clamp to monitor
        x = clamp(x, g.x + Cfg.BUBBLE_MARGIN, g.x + g.width - bw - Cfg.BUBBLE_MARGIN)
        y = clamp(y, g.y + Cfg.BUBBLE_MARGIN, g.y + g.height - bh - Cfg.BUBBLE_MARGIN)
        self.win.move(x, y)
        return False

    def _reposition(self, *_args):
        if not getattr(self, '_user_moved', False):
            GLib.idle_add(self._place_next_to_anchor)
        return False

    def _list_header_func(self, row, before, data):
        # Add subtle separators for hierarchy
        if before is None:
            row.set_header(None)
        else:
            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            row.set_header(sep)

    def _toggle_main(self, *_):
        cur = self.main_revealer.get_reveal_child()
        self.main_revealer.set_reveal_child(not cur)
        # rotate icon
        try:
            self.header_icon.set_from_icon_name(
                "pan-up-symbolic" if not cur else "pan-down-symbolic",
                Gtk.IconSize.MENU,
            )
        except Exception:
            pass
        GLib.idle_add(self._adjust_window_size)
        GLib.idle_add(self._place_next_to_anchor)

    def _toggle_history(self, *_):
        cur = self.hist_revealer.get_reveal_child()
        self.hist_revealer.set_reveal_child(not cur)
        try:
            self.hist_icon.set_from_icon_name(
                "pan-up-symbolic" if not cur else "pan-down-symbolic",
                Gtk.IconSize.MENU,
            )
        except Exception:
            pass
        GLib.idle_add(self._adjust_window_size)
        GLib.idle_add(self._place_next_to_anchor)

    def _adjust_window_size(self):
        # When collapsed, shrink to header; when expanded, use target size.
        expanded = self.main_revealer.get_reveal_child()
        if not expanded:
            w_min, w_nat = self.header_box.get_preferred_width()
            h_min, h_nat = self.header_box.get_preferred_height()
            w = min(max(240, w_nat + 24), Cfg.BUBBLE_WIDTH)
            h = h_nat + 18
            self.win.resize(w, h)
        else:
            self.win.resize(Cfg.BUBBLE_WIDTH, Cfg.BUBBLE_HEIGHT)
        return False

    def _on_header_press(self, _widget, event):
        if getattr(event, 'button', 0) != 1:
            return False
        self._dragging = True
        self._user_moved = True
        self._drag_origin = (event.x_root, event.y_root)
        try:
            x, y = self.win.get_position()
        except Exception:
            x, y = (0, 0)
        self._win_origin = (x, y)
        return False

    def _on_header_motion(self, _widget, event):
        if not self._dragging:
            return False
        dx = int(event.x_root - self._drag_origin[0])
        dy = int(event.y_root - self._drag_origin[1])
        new_x = self._win_origin[0] + dx
        new_y = self._win_origin[1] + dy
        self.win.move(new_x, new_y)
        return True

    def _on_header_release(self, _widget, event):
        if getattr(event, 'button', 0) != 1:
            return False
        was_dragging = self._dragging
        self._dragging = False
        if was_dragging:
            # do not toggle if it was a drag
            return True
        # treat as click -> toggle
        self._toggle_main()
        return True
