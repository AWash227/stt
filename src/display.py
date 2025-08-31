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
try:
    import sounddevice as sd  # for audio device listing
except Exception:
    sd = None
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
    HISTORY_BATCH = 400  # rows per idle batch for initial load


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
        # Defensive: handle None, NaN, and negative durations gracefully
        if dur is None:
            dur = 0
        try:
            dur = float(dur)
        except Exception:
            dur = 0.0
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
        alpha = float(self.tweens["master_alpha"].value)
        if alpha < 0.01:
            return False
        width = int(self.get_allocated_width())
        height = int(self.get_allocated_height())
        if width <= 0 or height <= 0:
            return False
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
            cr.set_line_width(max(0.5, Cfg.SUCCESS_RIPPLE_WIDTH * (1 - t**2)))
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
        return False

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
        # Accept lists, tuples, numpy arrays; coerce safely to float32 array
        try:
            arr = np.asarray(audio_chunk, dtype=np.float32)
        except Exception:
            arr = np.zeros(1, dtype=np.float32)
        if arr.size == 0:
            return
        # Guard against NaNs/Infs propagating through
        if not np.isfinite(arr).any():
            return
        arr = np.nan_to_num(arr, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
        rms = float(min(1.0, np.sqrt(np.mean(arr**2)) * Cfg.SWELL_SENSITIVITY))
        if rms > self.dynamic_swell:
            self.dynamic_swell = self.dynamic_swell * self.swell_attack_const + rms * (
                1 - self.swell_attack_const
            )
        else:
            self.dynamic_swell = self.dynamic_swell * self.swell_release_const
        peak = float(min(1.0, np.max(np.abs(arr)) * Cfg.FIZZ_SENSITIVITY))
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
    mic_control_q: queue.Queue | None = None,
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
    # Make blob clickable
    try:
        voice_widget.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
    except Exception:
        pass
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
        try:
            active = dict_control.is_active() if dict_control else False
        except Exception:
            active = False
        voice_widget.set_active(active)
        return True

    GLib.timeout_add(1000 // Cfg.REFRESH_HZ, poll_all_queues)
    GLib.timeout_add(200, poll_dictation_control)
    win.show_all()
    voice_widget.set_app_state(SystemState.IDLE)

    # Create transcript bubbles next to the blob
    try:
        bubbles = BubblesUI(
            win,
            dict_control,
            narration_path="narration.jsonl",
            mic_control_q=mic_control_q,
            show_on_init=False,
        )
    except Exception as e:
        log.error(f"Failed to init BubblesUI: {e}")

    # Toggle UI on blob click
    def on_blob_click(_widget, _event):
        try:
            if bubbles is None:
                return False
            visible = bubbles.win.get_visible()
            if visible:
                bubbles.win.hide()
            else:
                try:
                    bubbles._place_next_to_anchor()
                except Exception:
                    pass
                bubbles.win.show_all()
        except Exception:
            pass
        return True

    try:
        voice_widget.connect("button-press-event", on_blob_click)
    except Exception:
        pass

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
    def __init__(
        self,
        anchor_win: Gtk.Window,
        dict_control,
        narration_path: str = "narration.jsonl",
        mic_control_q: queue.Queue | None = None,
        show_on_init: bool = True,
    ):
        self.anchor_win = anchor_win
        self.dict_control = dict_control
        self.narration_path = narration_path
        self.mic_control_q = mic_control_q
        self.history: list[TextBufferSession] = []
        self.active: TextBufferSession | None = None
        self._file_pos = 0
        self._was_active = False
        self._user_moved = False
        self._dragging = False
        self._drag_origin = (0, 0)
        self._win_origin = (0, 0)
        self._suppress_reposition = False
        # Active editor buffer (shared model)
        self.active_buffer: Gtk.TextBuffer | None = Gtk.TextBuffer()
        self._editor_has_focus = False
        # Neovim/VTE integration state
        self._nvim_tmp_path = None
        self._nvim_last_mtime = 0.0
        self._nvim_sync_timer = None
        self._nvim_write_guard = False
        self._last_written_hash = None

        # Window
        # Use TOPLEVEL so the widget can accept keyboard focus (for SearchEntry)
        self.win = Gtk.Window(Gtk.WindowType.TOPLEVEL)
        scr = Gdk.Screen.get_default()
        self.win.set_app_paintable(True)
        self.win.set_accept_focus(True)
        self.win.set_focus_on_map(True)
        self.win.set_decorated(False)
        if scr.is_composited() and scr.get_rgba_visual():
            self.win.set_visual(scr.get_rgba_visual())
        self.win.set_keep_above(True)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_skip_pager_hint(True)
        self.win.set_default_size(Cfg.BUBBLE_WIDTH, Cfg.BUBBLE_HEIGHT)
        try:
            self.win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        except Exception:
            pass

        # Root container
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_margin_top(10)
        root.set_margin_bottom(10)
        root.set_margin_start(10)
        root.set_margin_end(10)
        self.root_box = root

        # Collapsible header (shows current buffer snippet)
        header = Gtk.EventBox()
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header_box.get_style_context().add_class("chip")
        header_box.set_margin_top(2)
        header_box.set_margin_bottom(4)
        header_box.set_margin_start(2)
        header_box.set_margin_end(2)
        self.header_box = header_box
        self.header_icon = Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.MENU)
        self.chev_ev = Gtk.EventBox()
        self.chev_ev.add(self.header_icon)
        header_box.pack_start(self.chev_ev, False, False, 0)
        # scrolling current buffer editor (TextView)
        self.header_scroller = Gtk.ScrolledWindow()
        self.header_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.header_scroller.set_size_request(-1, 84)
        self.header_scroller.set_overlay_scrolling(True)
        self.header_scroller.set_kinetic_scrolling(True)
        self.editor_textview = Gtk.TextView(buffer=self.active_buffer)
        self.editor_textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if Pango else Gtk.WrapMode.WORD)
        self.editor_textview.set_left_margin(6)
        self.editor_textview.set_right_margin(6)
        self.editor_textview.set_pixels_above_lines(2)
        self.editor_textview.set_pixels_below_lines(2)
        self.editor_textview.connect("focus-in-event", lambda *a: self._set_editor_focus(True))
        self.editor_textview.connect("focus-out-event", lambda *a: self._set_editor_focus(False))
        self.editor_textview.connect("populate-popup", self._populate_editor_menu)
        self.header_scroller.add(self.editor_textview)
        self.editor_scroller = self.header_scroller
        header_box.pack_start(self.header_scroller, True, True, 0)
        # small icon copy button
        self.header_copy_btn = Gtk.Button()
        self.header_copy_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.header_copy_btn.set_tooltip_text("Copy current buffer")
        self.header_copy_btn.get_style_context().add_class("btn")
        self.header_copy_btn.set_image(Gtk.Image.new_from_icon_name("edit-copy-symbolic", Gtk.IconSize.MENU))
        self.header_copy_btn.set_valign(Gtk.Align.START)
        self.header_copy_btn.set_halign(Gtk.Align.END)
        self.header_copy_btn.connect("clicked", self._on_copy)
        header_box.pack_end(self.header_copy_btn, False, False, 0)
        # fullscreen toggle
        self.header_full_btn = Gtk.Button()
        self.header_full_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.header_full_btn.set_tooltip_text("Toggle fullscreen")
        self.header_full_btn.get_style_context().add_class("btn")
        try:
            self.header_full_btn.set_image(Gtk.Image.new_from_icon_name("view-fullscreen-symbolic", Gtk.IconSize.MENU))
        except Exception:
            pass
        self.header_full_btn.set_valign(Gtk.Align.START)
        self.header_full_btn.set_halign(Gtk.Align.END)
        self.header_full_btn.connect("clicked", self._toggle_fullscreen)
        header_box.pack_end(self.header_full_btn, False, False, 4)
        header.add(header_box)
        root.pack_start(header, False, False, 0)

        self.main_revealer = Gtk.Revealer()
        self.main_revealer.set_transition_type(Gtk.RevealerTransitionType.CROSSFADE)
        self.main_revealer.set_transition_duration(240)
        self.main_revealer.set_reveal_child(False)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        # Audio source selector (minimal)
        src_frame = Gtk.Frame()
        src_frame.set_shadow_type(Gtk.ShadowType.NONE)
        src_frame.get_style_context().add_class("chip")
        src_frame.set_margin_start(2)
        src_frame.set_margin_end(2)
        src_frame.set_margin_top(2)
        src_frame.set_margin_bottom(4)
        src_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        src_box.set_margin_top(6)
        src_box.set_margin_bottom(6)
        src_box.set_margin_start(8)
        src_box.set_margin_end(8)
        src_label = Gtk.Label(label="Audio Source")
        src_label.get_style_context().add_class("secondary")
        src_label.set_xalign(0.0)
        src_box.pack_start(src_label, False, False, 0)
        self.src_combo = Gtk.ComboBoxText()
        self.src_combo.set_hexpand(True)
        self.src_combo.connect("changed", self._on_source_changed)
        src_box.pack_start(self.src_combo, True, True, 0)
        self.src_refresh_btn = Gtk.Button()
        self.src_refresh_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.src_refresh_btn.get_style_context().add_class("btn")
        self.src_refresh_btn.set_tooltip_text("Refresh devices")
        try:
            self.src_refresh_btn.set_image(Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.MENU))
        except Exception:
            pass
        self.src_refresh_btn.connect("clicked", lambda *_: self._refresh_audio_sources())
        src_box.pack_end(self.src_refresh_btn, False, False, 0)
        src_frame.add(src_box)
        content.pack_start(src_frame, False, False, 0)

        # History search + list (single top-level dropdown only)
        search_frame = Gtk.Frame()
        search_frame.set_shadow_type(Gtk.ShadowType.NONE)
        search_frame.get_style_context().add_class("chip")
        search_frame.set_margin_start(2)
        search_frame.set_margin_end(2)
        search_frame.set_margin_top(2)
        search_frame.set_margin_bottom(4)
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_box.set_margin_top(6)
        search_box.set_margin_bottom(6)
        search_box.set_margin_start(8)
        search_box.set_margin_end(8)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search history…")
        self.search_entry.set_can_focus(True)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("changed", self._on_search_changed)
        self.search_entry.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.search_entry.connect("button-press-event", lambda *a: (self.search_entry.grab_focus(), False)[1])
        search_box.pack_start(self.search_entry, True, True, 0)
        search_frame.add(search_box)
        content.pack_start(search_frame, False, False, 0)

        # (Removed Actions panel in favor of audio source selector)

        # History scroller with TreeView (performant for large lists)
        hist_scroller = Gtk.ScrolledWindow()
        hist_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
        # Let the scroller take remaining height; do not propagate child's natural height
        hist_scroller.set_propagate_natural_height(False)
        hist_scroller.set_min_content_height(220)
        hist_scroller.set_vexpand(True)
        hist_scroller.set_overlay_scrolling(True)
        hist_scroller.set_kinetic_scrolling(True)
        hist_scroller.set_hexpand(True)

        # Model: time_str, excerpt, full_text
        self.hist_store = Gtk.ListStore(str, str, str)
        self._search_query = ""
        self.hist_filter = self.hist_store.filter_new()
        self.hist_filter.set_visible_func(self._hist_visible_func)
        self.hist_view = Gtk.TreeView(model=self.hist_filter)
        self.hist_view.set_headers_visible(False)
        self.hist_view.set_enable_search(False)
        self.hist_view.set_fixed_height_mode(True)
        self.hist_view.set_vexpand(True)
        self.hist_view.set_hexpand(True)
        # Columns
        time_renderer = Gtk.CellRendererText()
        time_renderer.set_property("foreground", "#D0D6DE")
        time_renderer.set_property("scale", 0.85)
        time_col = Gtk.TreeViewColumn("time", time_renderer, text=0)
        time_col.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        time_col.set_fixed_width(64)

        text_renderer = Gtk.CellRendererText()
        text_renderer.set_property("ellipsize", Pango.EllipsizeMode.END if Pango else 3)
        text_renderer.set_property("foreground", "#FFFFFF")
        text_col = Gtk.TreeViewColumn("text", text_renderer, text=1)
        text_col.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        self.hist_view.append_column(time_col)
        text_col.set_expand(True)
        self.hist_view.append_column(text_col)
        try:
            self.hist_view.set_tooltip_column(2)  # show full text on hover
        except Exception:
            pass
        self.hist_view.connect("row-activated", self._on_hist_row_activated)
        self.hist_selection = self.hist_view.get_selection()
        self.hist_selection.set_mode(Gtk.SelectionMode.SINGLE)
        self.hist_selection.connect("changed", self._on_hist_selection_changed)
        # keep references for dynamic sizing
        self._hist_time_col = time_col
        self._hist_text_col = text_col
        self.hist_view.connect("size-allocate", self._on_hist_size_allocate)

        hist_scroller.add(self.hist_view)
        self.hist_scroller = hist_scroller
        hist_frame = Gtk.Frame()
        hist_frame.set_shadow_type(Gtk.ShadowType.NONE)
        hist_frame.get_style_context().add_class("chip")
        hist_frame.set_margin_start(2)
        hist_frame.set_margin_end(2)
        hist_frame.set_margin_top(2)
        hist_frame.set_margin_bottom(2)
        hist_frame.add(hist_scroller)
        content.pack_start(hist_frame, True, True, 0)

        # (Editor integrated into header)

        self.main_revealer.add(content)
        root.pack_end(self.main_revealer, True, True, 0)

        # Wire header interactions
        # drag on header background, toggle only on chevron
        header.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.POINTER_MOTION_MASK)
        header.connect("button-press-event", self._on_header_press)
        header.connect("motion-notify-event", self._on_header_motion)
        header.connect("button-release-event", self._on_header_release)
        self.chev_ev.connect("button-release-event", self._toggle_main)
        # enable drag move via header press

        self.win.add(root)
        self._install_css()

        # Populate history (incremental) and position window
        self._start_history_load()
        self._ensure_active_session()
        self._update_active_view()
        # Reflect active buffer changes back to session + header
        if self.active_buffer is not None:
            self.active_buffer.connect("changed", self._on_active_buffer_changed)

        # header autoscroll logic
        try:
            self._header_autoscroll = True
            vadj = self.header_scroller.get_vadjustment()
            def on_vadj_changed(adj):
                # if user is near bottom -> keep autoscrolling
                at_bottom = (adj.get_upper() - (adj.get_value() + adj.get_page_size())) < 8
                self._header_autoscroll = at_bottom
            vadj.connect("value-changed", on_vadj_changed)
        except Exception:
            pass

        # history autoscroll logic
        try:
            self._hist_autoscroll = True
            hvadj = self.hist_scroller.get_vadjustment()
            def on_hist_vadj_changed(adj):
                at_bottom = (adj.get_upper() - (adj.get_value() + adj.get_page_size())) < 8
                self._hist_autoscroll = at_bottom
            hvadj.connect("value-changed", on_hist_vadj_changed)
        except Exception:
            self._hist_autoscroll = True

        # show
        self._adjust_window_size()
        if show_on_init:
            self.win.show_all()

        # timers
        GLib.timeout_add(400, self._poll_tail)  # tail narration.jsonl
        GLib.timeout_add(500, self._track_active_toggle)  # detect active on/off

        # keep bubble next to blob
        self.anchor_win.connect("configure-event", self._reposition)
        self.win.connect("size-allocate", self._reposition)
        self.win.connect("window-state-event", self._on_window_state)
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
            color: rgba(250,252,255,0.98);
        }}
        button.btn {{
            background: rgba(255,255,255,0.06);
            border: 1px solid rgba(255,255,255,0.28);
            color: rgba(238,240,243,0.98);
            border-radius: 10px;
            padding: 2px 4px;
            min-height: 24px;
            min-width: 24px;
        }}
        button.btn:hover {{
            background: rgba(255,255,255,0.10);
        }}
        scrolledwindow, viewport {{
            background-color: transparent;
        }}
        treeview, treeview.view {{
            background-color: transparent;
        }}
        entry, searchentry {{
            color: rgba(255,255,255,0.98);
            background-color: transparent;
            border: none;
        }}
        textview, textview text {{
            background-color: transparent;
        }}
        frame.bubble > border, frame.chip > border {{
            border-radius: 18px;
        }}
        /* TreeView rows are compact by default; no extra padding here */
        """
        css.load_from_data(css_str.encode("utf-8"))
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(
            screen, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    # ---------- History and tailing ----------
    def _start_history_load(self):
        self.hist_store.clear()
        self.history = []
        self._initial_loader = self._iter_history_lines()
        GLib.idle_add(self._history_batch)
        # Populate audio sources once UI is up
        GLib.idle_add(self._refresh_audio_sources)

    def _iter_history_lines(self):
        path = self.narration_path
        if not os.path.exists(path):
            self._file_pos = 0
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                for ln in f:
                    try:
                        obj = json.loads(ln)
                        ts = float(obj.get("timestamp", time.time()))
                        txt = str(obj.get("text", "")).strip()
                    except Exception:
                        continue
                    if not txt:
                        continue
                    tstr = time.strftime("%H:%M:%S", time.localtime(ts))
                    yield (tstr, txt, ts)
            # after initial load, set file pos to EOF
            try:
                self._file_pos = os.path.getsize(path)
            except Exception:
                self._file_pos = 0
        except Exception:
            return

    def _history_batch(self):
        # Load in idle batches for responsiveness
        count = 0
        batch = Cfg.HISTORY_BATCH
        if not hasattr(self, "_initial_loader") or self._initial_loader is None:
            return False
        # track last timestamp to insert session separators
        if not hasattr(self, "_last_hist_ts"):
            self._last_hist_ts = None
        for tstr, txt, ts in self._initial_loader:
            # Insert session separator if gap exceeded
            try:
                if self._last_hist_ts is not None and (ts - self._last_hist_ts) > Cfg.SESSION_GAP_SEC:
                    self.hist_store.append(["", "— Session —", ""])
            except Exception:
                pass
            # column 1 shows the full text (renderer ellipsizes visually)
            self.hist_store.append([tstr, txt, txt])
            self.history.append(TextBufferSession(created_ts=ts, text=txt))
            self._trim_history()
            self._last_hist_ts = ts
            count += 1
            if count >= batch:
                return True  # keep going next idle
        # done
        self._initial_loader = None
        GLib.idle_add(self._scroll_hist_to_bottom)
        return False

    def _trim_history(self):
        """Ensure we only keep the most recent MAX_HISTORY_ITEMS in memory and view."""
        try:
            max_items = int(getattr(Cfg, "MAX_HISTORY_ITEMS", 0))
        except Exception:
            max_items = 0
        if max_items and len(self.history) > max_items:
            # Trim backing list
            excess = len(self.history) - max_items
            if excess > 0:
                del self.history[0:excess]
            # Trim GTK model from the top
            try:
                while len(self.hist_store) > max_items:
                    itr = self.hist_store.get_iter_first()
                    if itr is None:
                        break
                    self.hist_store.remove(itr)
            except Exception:
                pass

    def _hist_visible_func(self, model, itr, data=None):
        # Filter rows by search query (case-insensitive substring)
        q = getattr(self, "_search_query", "").strip().lower()
        if not q:
            return True
        time_str = (model.get_value(itr, 0) or "").lower()
        excerpt = (model.get_value(itr, 1) or "").lower()
        full = (model.get_value(itr, 2) or "").lower()
        return q in time_str or q in excerpt or q in full

    def _on_search_changed(self, entry):
        self._search_query = (entry.get_text() or "").strip()
        try:
            self.hist_filter.refilter()
        except Exception:
            pass

    # ---------- Audio source selection ----------
    def _refresh_audio_sources(self):
        if getattr(self, 'src_combo', None) is None:
            return False
        self.src_combo.get_model().clear()
        if sd is None:
            self.src_combo.append_text("(sounddevice not available)")
            self.src_combo.set_active(0)
            return False
        try:
            devs = sd.query_devices()
        except Exception as e:
            self.src_combo.append_text(f"(device query failed)")
            self.src_combo.set_active(0)
            return False
        count = 0
        for i, d in enumerate(devs):
            if d.get('max_input_channels', 0) <= 0:
                continue
            name = d.get('name', f'Device {i}')
            label = name
            low = name.lower()
            if 'monitor' in low or 'loopback' in low:
                label += "  • system output"
            self.src_combo.append_text(f"#{i}  {label}")
            count += 1
        if count == 0:
            self.src_combo.append_text("(no input devices)")
            self.src_combo.set_active(0)
        else:
            self.src_combo.set_active(0)
        return False

    def _on_source_changed(self, combo):
        if self.mic_control_q is None:
            return
        txt = combo.get_active_text() or ""
        # Expect a leading '#<idx>'
        try:
            if txt.startswith('#'):
                idx_str = txt.split()[0][1:]
                idx = int(idx_str)
                try:
                    self.mic_control_q.put(("set_device_index", idx))
                except Exception:
                    pass
        except Exception:
            pass

    def _poll_tail(self):
        # Handle file truncation/rotation gracefully
        try:
            current_size = os.path.getsize(self.narration_path)
            if self._file_pos > current_size:
                # File was truncated; restart from 0
                self._file_pos = 0
        except Exception:
            return True
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
                ts = float(obj.get("timestamp", time.time()))
            except Exception:
                txt = ""
            if not txt:
                continue
            # Insert session separator if gap exceeded
            try:
                if hasattr(self, "_last_hist_ts") and self._last_hist_ts is not None and (ts - self._last_hist_ts) > Cfg.SESSION_GAP_SEC:
                    self.hist_store.append(["", "— Session —", ""])
            except Exception:
                pass
            # Always append to the active buffer as new speech arrives
            # Start a new session when there is a long inactivity gap
            try:
                gap = (ts - getattr(self, "_last_hist_ts", ts)) if getattr(self, "_last_hist_ts", None) is not None else 0
            except Exception:
                gap = 0
            if gap > getattr(Cfg, "SESSION_GAP_SEC", 20.0):
                self._ensure_active_session(force_new=True)
            else:
                self._ensure_active_session()
            # Insert incoming speech at the current caret position
            self._insert_speech_at_cursor(txt)
            # Also reflect in history model as its own entry (performant append)
            tstr = time.strftime("%H:%M:%S", time.localtime(ts))
            self.hist_store.append([tstr, txt, txt])
            self.history.append(TextBufferSession(created_ts=ts, text=txt))
            self._trim_history()
            self._last_hist_ts = ts
        self._update_active_view()
        GLib.idle_add(self._scroll_hist_to_bottom)
        return True

    # (Actions extraction removed)

    def _track_active_toggle(self):
        now_active = self.dict_control.is_active() if self.dict_control else False
        if now_active and not self._was_active:
            # became active → start new session
            self._ensure_active_session(force_new=True)
            self._update_active_view()
        elif (not now_active) and self._was_active:
            # ended → push to history
            self.active = None
            self._update_active_view()
        self._was_active = now_active
        return True

    def _ensure_active_session(self, force_new: bool = False):
        if self.active is None or force_new:
            self.active = TextBufferSession()
            # reset editor buffer if present
            if self.active_buffer is not None:
                try:
                    self.active_buffer.set_text("")
                except Exception:
                    pass

    # ---------- UI helpers ----------

    def _update_active_view(self):
        # Reflect the TextBuffer text into the session model
        if self.active_buffer is not None:
            try:
                start, end = self.active_buffer.get_start_iter(), self.active_buffer.get_end_iter()
                new_text = self.active_buffer.get_text(start, end, True)
                if self.active is None:
                    self._ensure_active_session()
                if self.active is not None:
                    self.active.text = new_text
            except Exception:
                pass
        # Autoscroll to the end unless user scrolled up
        try:
            vadj = self.header_scroller.get_vadjustment()
            if getattr(self, "_header_autoscroll", True):
                GLib.idle_add(lambda: vadj.set_value(max(0, vadj.get_upper() - vadj.get_page_size())))
        except Exception:
            pass
        # Also sync to external editor file (nvim) if present
        try:
            self._sync_to_nvim_file(new_text or "")
        except Exception:
            pass
        # Update editor buffer if open and not actively edited
        try:
            if getattr(self, "editor_textview", None) is not None and not getattr(self, "_editor_has_focus", False):
                buf = self.editor_textview.get_buffer()
                cur_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
                if cur_text != (new_text or ""):
                    buf.set_text(new_text or "")
                    GLib.idle_add(self._scroll_editor_to_end_if_needed)
        except Exception:
            pass
        # Update speaking status chip if editor open
        try:
            self._update_status_chip()
        except Exception:
            pass

    def _on_hist_row_activated(self, tree, path, col):
        model = tree.get_model()  # filter model
        itr = model.get_iter(path)
        if not itr:
            return
        full_text = model.get_value(itr, 2)
        self.active = TextBufferSession(text=full_text)
        self._update_active_view()

    def _on_hist_selection_changed(self, selection):
        model, itr = selection.get_selected()  # model is filter
        if itr is None:
            return
        full_text = model.get_value(itr, 2)
        self.active = TextBufferSession(text=full_text)
        self._update_active_view()

    def _on_hist_size_allocate(self, widget, allocation):
        try:
            total_w = allocation.width
            time_w = self._hist_time_col.get_fixed_width() if hasattr(self, '_hist_time_col') and self._hist_time_col else 64
            padding = 24  # scrollbar + margins slack
            text_w = max(60, total_w - time_w - padding)
            if hasattr(self, '_hist_text_col') and self._hist_text_col:
                self._hist_text_col.set_fixed_width(text_w)
        except Exception:
            pass

    def _on_copy(self, *_):
        text = (self.active.text if self.active else "")
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(text, -1)

    def _on_copy_history(self, _btn, sess: TextBufferSession):
        try:
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(sess.text, -1)
        except Exception:
            pass

    # ---------- Editor actions ----------
    def _populate_editor_menu(self, widget, menu):
        try:
            menu.append(Gtk.SeparatorMenuItem())
            def add_item(label, cb):
                mi = Gtk.MenuItem(label=label)
                mi.connect("activate", lambda *_: cb())
                menu.append(mi)
                return mi
            add_item("Cut", lambda: self._editor_cmd("cut"))
            add_item("Copy", lambda: self._editor_cmd("copy"))
            add_item("Paste", lambda: self._editor_cmd("paste"))
            add_item("Select All", lambda: self._editor_cmd("select_all"))
            menu.show_all()
        except Exception:
            pass

    def _editor_cmd(self, cmd: str):
        try:
            tv = getattr(self, 'editor_textview', None)
            if tv is None:
                return
            buf = tv.get_buffer()
            clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            if cmd == "cut":
                buf.cut_clipboard(clip, True)
            elif cmd == "copy":
                buf.copy_clipboard(clip)
            elif cmd == "paste":
                buf.paste_clipboard(clip, None, True)
            elif cmd == "select_all":
                start, end = buf.get_bounds()
                buf.select_range(start, end)
        except Exception:
            pass

    # ---------- Active buffer model ----------
    def _on_active_buffer_changed(self, buf: Gtk.TextBuffer):
        # Keep session text and header label in sync
        try:
            start, end = buf.get_start_iter(), buf.get_end_iter()
            text = buf.get_text(start, end, True)
            if self.active is None:
                self._ensure_active_session()
            if self.active:
                self.active.text = text
            self._update_active_view()
        except Exception:
            pass

    def _insert_speech_at_cursor(self, txt: str):
        if not txt:
            return
        buf = self.active_buffer
        if buf is None:
            return
        try:
            insert_mark = buf.get_insert()
            it = buf.get_iter_at_mark(insert_mark)
            need_space = False
            if it.get_offset() > 0:
                prev = buf.get_text(buf.get_iter_at_offset(it.get_offset() - 1), it, True)
                if prev and not prev[-1].isspace() and not txt.startswith((" ", ".", ",", ";", ":", "!", "?")):
                    need_space = True
            to_insert = (" " + txt) if need_space else txt
            buf.insert(it, to_insert)
            # Ensure caret stays visible
            GLib.idle_add(self._scroll_editor_caret_visible)
        except Exception:
            pass

    def _scroll_editor_caret_visible(self):
        try:
            tv = getattr(self, 'editor_textview', None)
            if tv is None:
                return False
            buf = tv.get_buffer()
            tv.scroll_mark_onscreen(buf.get_insert())
        except Exception:
            pass
        return False

    # ---------- Editor (fullscreen) ----------
    def _ensure_editor_window(self):
        if getattr(self, "editor_win", None) is not None:
            return
        self.editor_win = Gtk.Window(Gtk.WindowType.TOPLEVEL)
        self.editor_win.set_decorated(False)
        self.editor_win.set_app_paintable(True)
        self.editor_win.set_accept_focus(True)
        self.editor_win.set_focus_on_map(True)
        try:
            scr = Gdk.Screen.get_default()
            if scr.is_composited() and scr.get_rgba_visual():
                self.editor_win.set_visual(scr.get_rgba_visual())
        except Exception:
            pass
        self.editor_win.set_keep_above(True)
        self.editor_win.set_skip_taskbar_hint(True)
        self.editor_win.set_skip_pager_hint(True)
        try:
            self.editor_win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        except Exception:
            pass
        # ESC closes
        self.editor_win.connect("key-press-event", self._on_editor_key)
        # layout
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        root.set_margin_top(12)
        root.set_margin_bottom(12)
        root.set_margin_start(12)
        root.set_margin_end(12)
        frame = Gtk.Frame()
        frame.get_style_context().add_class("bubble")
        frame.set_shadow_type(Gtk.ShadowType.NONE)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_top(10)
        vbox.set_margin_bottom(10)
        vbox.set_margin_start(10)
        vbox.set_margin_end(10)
        # top bar
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        # status chip
        self.status_chip = Gtk.Label(label="Idle")
        self.status_chip.get_style_context().add_class("chip")
        top.pack_start(self.status_chip, False, False, 0)
        # spacer
        top.pack_start(Gtk.Box(), True, True, 0)
        # close button
        close_btn = Gtk.Button()
        close_btn.get_style_context().add_class("btn")
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.set_tooltip_text("Close editor")
        try:
            close_btn.set_image(Gtk.Image.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU))
        except Exception:
            pass
        close_btn.connect("clicked", self._close_editor)
        top.pack_end(close_btn, False, False, 0)
        # fullscreen toggle button
        fs_btn = Gtk.Button()
        fs_btn.get_style_context().add_class("btn")
        fs_btn.set_relief(Gtk.ReliefStyle.NONE)
        fs_btn.set_tooltip_text("Toggle fullscreen")
        try:
            fs_btn.set_image(Gtk.Image.new_from_icon_name("view-fullscreen-symbolic", Gtk.IconSize.MENU))
        except Exception:
            pass
        fs_btn.connect("clicked", self._toggle_editor_fullscreen)
        top.pack_end(fs_btn, False, False, 4)
        vbox.pack_start(top, False, False, 0)
        # Prefer Neovim embedded with VTE; fallback to TextView if VTE unavailable
        if Vte is not None:
            try:
                self.terminal = Vte.Terminal()
                # Make terminal background as transparent as possible
                try:
                    rgba = Gdk.RGBA(0, 0, 0, 0)
                    self.terminal.set_color_background(rgba)
                except Exception:
                    pass
                # spawn nvim with autoread + autocmd to checktime regularly
                tmppath = self._ensure_nvim_tmpfile()
                argv = [
                    "nvim",
                    "-c",
                    "set autoread",
                    "-c",
                    "set updatetime=500",
                    "-c",
                    "autocmd CursorHold,CursorHoldI * checktime",
                    tmppath,
                ]
                self.terminal.spawn_async(
                    Vte.PtyFlags.DEFAULT,
                    os.getcwd(),
                    argv,
                    [],
                    GLib.SpawnFlags.SEARCH_PATH,
                    None,
                    None,
                    -1,
                    None,
                    None,
                )
                vbox.pack_start(self.terminal, True, True, 0)
                # start sync timer from nvim file
                self._start_nvim_sync_timer()
            except Exception:
                # fallback to TextView
                self._build_textview_editor(vbox)
        else:
            self._build_textview_editor(vbox)
        frame.add(vbox)
        root.pack_start(frame, True, True, 0)
        self.editor_win.add(root)
        # initial fill/status
        try:
            self._update_editor_text()
        except Exception:
            pass
        self._update_status_chip()

    def _build_textview_editor(self, vbox: Gtk.Box):
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_overlay_scrolling(True)
        scroller.set_kinetic_scrolling(True)
        self.editor_textview = Gtk.TextView()
        self.editor_textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if Pango else Gtk.WrapMode.WORD)
        self.editor_textview.set_left_margin(8)
        self.editor_textview.set_right_margin(8)
        self.editor_textview.connect("focus-in-event", lambda *a: self._set_editor_focus(True))
        self.editor_textview.connect("focus-out-event", lambda *a: self._set_editor_focus(False))
        buf = self.editor_textview.get_buffer()
        buf.connect("changed", self._on_editor_buffer_changed)
        scroller.add(self.editor_textview)
        self.editor_scroller = scroller
        vbox.pack_start(scroller, True, True, 0)

    # (fullscreen handled by window; no separate editor window)

    # (no external editor text sync; TextView is the source of truth)

    def _on_editor_buffer_changed(self, buf):
        # Keep header label in sync with editor contents
        try:
            self._update_active_view()
        except Exception:
            pass

    def _set_editor_focus(self, has_focus: bool):
        self._editor_has_focus = bool(has_focus)

    def _scroll_hist_to_bottom(self):
        try:
            if getattr(self, "_hist_autoscroll", True):
                adj = self.hist_scroller.get_vadjustment()
                adj.set_value(max(0, adj.get_upper() - adj.get_page_size()))
        except Exception:
            pass
        return False

    def _scroll_editor_to_end_if_needed(self):
        try:
            if getattr(self, "editor_scroller", None) is not None and getattr(self, "editor_textview", None) is not None:
                adj = self.editor_scroller.get_vadjustment()
                at_bottom = (adj.get_upper() - (adj.get_value() + adj.get_page_size())) < 8
                if at_bottom or not getattr(self, "_editor_has_focus", False):
                    buf = self.editor_textview.get_buffer()
                    end_iter = buf.get_end_iter()
                    buf.place_cursor(end_iter)
                    self.editor_textview.scroll_mark_onscreen(buf.get_insert())
        except Exception:
            pass
        return False

    # (no separate status chip in simplified UI)

    # (no Neovim/VTE file sync in simplified editor)

    # removed editing handlers (editor removed)

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
        if self._suppress_reposition:
            return False
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
        if not cur:
            # just expanded -> focus search for quick filtering
            try:
                self.search_entry.grab_focus()
            except Exception:
                pass

    def _toggle_fullscreen(self, *_):
        try:
            gdk_win = self.win.get_window()
            is_full = bool(gdk_win.get_state() & Gdk.WindowState.FULLSCREEN) if gdk_win else False
        except Exception:
            is_full = False
        if is_full:
            try:
                self.win.unfullscreen()
            except Exception:
                pass
        else:
            try:
                self.win.fullscreen()
            except Exception:
                pass

    def _on_window_state(self, _w, event):
        try:
            is_full = bool(event.new_window_state & Gdk.WindowState.FULLSCREEN)
        except Exception:
            is_full = False
        # Update icon and layout
        try:
            self.header_full_btn.set_image(
                Gtk.Image.new_from_icon_name(
                    "view-restore-symbolic" if is_full else "view-fullscreen-symbolic",
                    Gtk.IconSize.MENU,
                )
            )
        except Exception:
            pass
        self._apply_fullscreen_layout(is_full)
        return False

    def _apply_fullscreen_layout(self, is_full: bool):
        try:
            # Expand header to fill when fullscreen; hide history pane
            self.root_box.set_child_packing(self.root_box.get_children()[0], is_full, is_full, 0, Gtk.PackType.START)
        except Exception:
            pass
        try:
            if is_full:
                self.main_revealer.hide()
                self.header_scroller.set_size_request(-1, -1)
                self.editor_textview.grab_focus()
            else:
                self.main_revealer.show_all()
                self.header_scroller.set_size_request(-1, 84)
        except Exception:
            pass

    def _toggle_history(self, *_):
        # No-op if the history revealer has been removed
        if not hasattr(self, "hist_revealer") or self.hist_revealer is None:
            return False
        cur = self.hist_revealer.get_reveal_child()
        self.hist_revealer.set_reveal_child(not cur)
        try:
            if hasattr(self, "hist_icon") and self.hist_icon is not None:
                self.hist_icon.set_from_icon_name(
                    "pan-up-symbolic" if not cur else "pan-down-symbolic",
                    Gtk.IconSize.MENU,
                )
        except Exception:
            pass
        GLib.idle_add(self._adjust_window_size)
        
    def _adjust_window_size(self):
        # When collapsed, shrink to header; when expanded, use target size.
        self._suppress_reposition = True
        expanded = self.main_revealer.get_reveal_child()
        if not expanded:
            w_min, w_nat = self.header_box.get_preferred_width()
            h_min, h_nat = self.header_box.get_preferred_height()
            w = min(max(240, w_nat + 24), Cfg.BUBBLE_WIDTH)
            h = h_nat + 18
            self.win.resize(w, h)
        else:
            self.win.resize(Cfg.BUBBLE_WIDTH, Cfg.BUBBLE_HEIGHT)
        GLib.timeout_add(50, self._clear_reposition_suppression)
        return False

    def _clear_reposition_suppression(self):
        self._suppress_reposition = False
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
        # do not toggle here; chevron handles toggling
        return was_dragging
