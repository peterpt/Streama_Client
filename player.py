import sys
import os
import time
import threading
import vlc
from PySide2.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QSlider, QLabel, QSizePolicy, QMenu, QAction)
from PySide2.QtCore import Qt, Signal, QTimer
from PySide2.QtGui import QPalette, QColor, QCursor

def format_time(ms):
    if ms < 0: ms = 0
    s = round(ms / 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

class VLCPlayerWidget(QWidget):
    go_back = Signal()
    
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        
        # --- State Variables ---
        self.instance = None
        self.player = None
        self.current_vlc_args = []
        self.current_subtitle_file = None  # <--- FIXED: Initialized here

        # --- Continue-Watching / viewing-status reporting ---
        self.api_client = None          # set per-playback in play_stream
        self.current_video_id = None    # streama video id of what's playing
        self._last_status_save = 0.0    # monotonic time of last save
        self._status_interval = 5.0     # seconds between saves (matches web client)
        self._completed_reported = False
        self._pending_seek_ms = 0       # resume position to seek to
        self._seek_attempts = 0

        # --- Multi-subtitle / audio track support ---
        # Subtitles to load as VLC slaves once playback starts. Each item:
        # {"path": <local srt path>, "label": <display name>}.
        self._pending_subtitle_tracks = []
        self._subtitle_temp_files = []   # all temp srt files, for cleanup
        self._slaves_added = False
        
        # --- Main Layout ---
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # --- Video Frame ---
        self.video_frame = QFrame()
        self.video_frame.setStyleSheet("background-color: black;")
        self.video_frame.setContextMenuPolicy(Qt.CustomContextMenu)
        self.video_frame.customContextMenuRequested.connect(self.show_context_menu)
        
        main_layout.addWidget(self.video_frame, 1)

        # --- Controls Container ---
        self.controls_container = QWidget(self)
        self.controls_container.setFixedHeight(60)
        self.controls_container.setStyleSheet("""
            QWidget { background-color: #222; color: #eee; border-top: 1px solid #444; }
            QSlider::groove:horizontal { border: 1px solid #444; height: 8px; background: #333; margin: 2px 0; border-radius: 4px; }
            QSlider::handle:horizontal { background: #3daee9; border: 1px solid #3daee9; width: 14px; height: 14px; margin: -4px 0; border-radius: 7px; }
            QPushButton { background-color: #444; border: none; border-radius: 3px; padding: 5px; min-width: 60px; }
            QPushButton:hover { background-color: #555; }
            QPushButton:pressed { background-color: #333; }
        """)
        
        controls_layout = QHBoxLayout(self.controls_container)
        controls_layout.setContentsMargins(10, 5, 10, 5)
        
        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_playback)
        
        self.time_label = QLabel("00:00 / 00:00")
        
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.slider.setRange(0, 1000) 
        
        self.slider.sliderPressed.connect(self.slider_pressed)
        self.slider.sliderReleased.connect(self.slider_released)
        self.slider.sliderMoved.connect(self.slider_moved)
        self.is_slider_active = False

        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.valueChanged.connect(self.set_volume)

        self.fullscreen_btn = QPushButton("Full")
        self.fullscreen_btn.clicked.connect(self.toggle_fullscreen)

        self.subs_btn = QPushButton("Subs")
        self.subs_btn.clicked.connect(self.show_subtitle_menu_from_button)
        self.audio_btn = QPushButton("Audio")
        self.audio_btn.clicked.connect(self.show_audio_menu_from_button)

        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self.stop_and_exit)

        controls_layout.addWidget(self.play_button)
        controls_layout.addWidget(self.time_label)
        controls_layout.addWidget(self.slider)
        controls_layout.addWidget(QLabel("Vol"))
        controls_layout.addWidget(self.volume_slider)
        controls_layout.addWidget(self.subs_btn)
        controls_layout.addWidget(self.audio_btn)
        controls_layout.addWidget(self.fullscreen_btn)
        controls_layout.addWidget(self.back_button)
        
        main_layout.addWidget(self.controls_container, 0)

        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.update_ui)
        
        # Initialize default VLC instance
        self.init_vlc_instance()

    def init_vlc_instance(self, sub_size=None, sub_bold=False):
        """
        Recreates the VLC instance if subtitle settings change.
        """
        if sub_size is None:
            sub_size = 20
            
        # Build VLC Arguments
        args = ['--no-xlib', '--verbose=2']
        args.append(f"--freetype-fontsize={sub_size}")
        
        if sub_bold:
            args.append("--freetype-bold")
        else:
            args.append("--no-freetype-bold")
            
        # If arguments haven't changed, reuse the existing instance
        if self.instance and self.current_vlc_args == args:
            return

        print(f"[*] Initializing VLC with Subtitle Size: {sub_size}px, Bold: {sub_bold}")
        
        # Cleanup old player
        if self.player:
            self.player.stop()
            self.player.release()
            self.player = None
        if self.instance:
            self.instance.release()
            self.instance = None
            
        # Create new Instance
        try:
            self.instance = vlc.Instance(args)
            if not self.instance:
                raise Exception("Failed to create VLC Instance")
                
            self.player = self.instance.media_player_new()
            self.current_vlc_args = args
            
            # Restore volume preference
            self.player.audio_set_volume(self.volume_slider.value())
            
        except Exception as e:
            print(f"[!] VLC Initialization Error: {e}")
            # Fallback to default
            if "--freetype-fontsize" in str(args):
                print("[!] Retrying with default VLC settings...")
                self.init_vlc_instance(sub_size=None, sub_bold=False)

    def play_stream(self, url, subtitle_path=None, cookie_dict=None, sub_config=None,
                    start_time=0, api_client=None, video_id=None, subtitle_tracks=None,
                    preferred_sub_label=None, auto_enable_first_sub=True):
        # 1. Save subtitle path for cleanup later
        # Clean up any previous subtitle temp files we won't reuse
        self._cleanup_subtitle_files()
        self.current_subtitle_file = subtitle_path  # primary (first) subtitle

        # Build the list of subtitle tracks to load as slaves. Accept either
        # the new subtitle_tracks list [{"path","label"}, ...] or fall back to
        # the single subtitle_path for backward compatibility.
        self._pending_subtitle_tracks = []
        self._slaves_added = False
        if subtitle_tracks:
            self._pending_subtitle_tracks = [t for t in subtitle_tracks if t.get('path')]
        elif subtitle_path:
            self._pending_subtitle_tracks = [{'path': subtitle_path, 'label': 'Subtitle'}]
        # Track temp files for cleanup.
        self._subtitle_temp_files = [t['path'] for t in self._pending_subtitle_tracks]
        # Preferred starting subtitle behaviour.
        self._preferred_sub_label = preferred_sub_label
        self._auto_enable_first_sub = auto_enable_first_sub

        # 1b. Set up viewing-status reporting for this playback
        self.api_client = api_client
        self.current_video_id = video_id
        self._last_status_save = 0.0
        self._completed_reported = False

        # 2. Extract Settings from Config
        config = sub_config if sub_config else {}
        size = config.get('subtitle_size', 20)
        is_bold = config.get('subtitle_bold', False)

        # 3. Ensure VLC is initialized with these settings
        self.init_vlc_instance(sub_size=size, sub_bold=is_bold)
        
        if not self.player:
            print("[!] Player not initialized, cannot play.")
            return

        # 4. Prepare Media URL (Auth)
        play_url = url
        if cookie_dict and 'JSESSIONID' in cookie_dict:
            jsessionid = cookie_dict['JSESSIONID']
            play_url = f"{url};jsessionid={jsessionid}"

        media = self.instance.media_new(play_url)
        
        # HTTP Headers
        if cookie_dict:
            cookie_str = ";".join([f"{k}={v}" for k, v in cookie_dict.items()])
            media.add_option(":http-user-agent=StreamaDesktop/1.0")
            media.add_option(f':http-cookie="{cookie_str}"')
            media.add_option(":http-reconnect=true")
        
        # Subtitles are added as "slaves" AFTER playback starts (VLC needs
        # the media open first), so we don't add a :sub-file option here.

        self.player.set_media(media)
        
        # Attach to Window
        if sys.platform.startswith('linux'):
            self.player.set_xwindow(self.video_frame.winId())
        elif sys.platform == "win32":
            self.player.set_hwnd(self.video_frame.winId())
        
        self.player.play()

        # Load subtitle tracks as slaves once the media is open.
        if self._pending_subtitle_tracks:
            self._slave_attempts = 0
            self._try_add_slaves()

        # Resume: seek to start_time (ms) once VLC is actually seekable.
        # For large MP4s over HTTP, the player needs a moment before a seek
        # will take, so we retry until it reports seekable, then verify the
        # position actually moved.
        if start_time and start_time > 0:
            self._pending_seek_ms = int(start_time)
            self._seek_attempts = 0
            print(f"[*] Resume requested at {start_time} ms "
                  f"({start_time // 1000}s)")
            self._try_resume_seek()
        else:
            print("[*] No resume position (starting from beginning).")

        self.play_button.setText("Pause")
        self.timer.start()

    def _try_resume_seek(self):
        if not self.player:
            return
        # Give up after ~10s of trying (40 * 250ms) for slow streams.
        if self._seek_attempts >= 40:
            print("[!] Resume seek gave up (player never became seekable).")
            return
        self._seek_attempts += 1

        playing = self.player.is_playing()
        seekable = self.player.is_seekable()
        length = self.player.get_length()

        if playing and seekable and length > 0:
            self.player.set_time(self._pending_seek_ms)
            print(f"[*] Resume seek applied at attempt {self._seek_attempts} "
                  f"-> {self._pending_seek_ms} ms (length={length} ms)")
        else:
            QTimer.singleShot(250, self._try_resume_seek)

    def toggle_playback(self):
        if not self.player: return
        if self.player.is_playing():
            self.player.pause()
            self.play_button.setText("Play")
        else:
            self.player.play()
            self.play_button.setText("Pause")

    def slider_pressed(self):
        self.is_slider_active = True

    def slider_released(self):
        self.is_slider_active = False
        if self.player:
            pos = self.slider.value() / 1000.0
            self.player.set_position(pos)

    def slider_moved(self, val):
        pass

    def set_volume(self, val):
        if self.player:
            self.player.audio_set_volume(val)

    def update_ui(self):
        if self.player and not self.is_slider_active and self.player.is_playing():
            pos = self.player.get_position()
            if pos >= 0:
                self.slider.setValue(int(pos * 1000))
            cur = self.player.get_time()
            total = self.player.get_length()
            self.time_label.setText(f"{format_time(cur)} / {format_time(total)}")

            # Report progress to the server (throttled to once per interval).
            # This is what makes the item show up in "Continue Watching".
            now = time.monotonic()
            if now - self._last_status_save >= self._status_interval:
                self._last_status_save = now
                self._report_viewing_status(cur, total)

    def _report_viewing_status(self, cur_ms, total_ms):
        # VLC reports milliseconds; the server expects seconds.
        if not self.api_client or not self.current_video_id:
            return
        if total_ms is None or total_ms <= 0:
            return  # runtime not known yet; web client also skips this case
        current_s = max(0, cur_ms / 1000.0)
        runtime_s = total_ms / 1000.0
        vid = self.current_video_id

        # Fire on a daemon thread so a slow/unreachable server never
        # stalls the UI or stutters playback.
        def _send():
            try:
                self.api_client.save_viewing_status(vid, current_s, runtime_s)
            except Exception as e:
                print(f"[!] viewingStatus save failed: {e}")

        threading.Thread(target=_send, daemon=True).start()

    def mouseDoubleClickEvent(self, event):
        self.toggle_fullscreen()

    def _try_add_slaves(self):
        """Add downloaded subtitle files to VLC as slave tracks, once the
        media is open enough to accept them. Retries for slow streams."""
        if not self.player or self._slaves_added:
            return
        if getattr(self, '_slave_attempts', 0) >= 40:
            print("[!] Could not add subtitle slaves (media never ready).")
            return
        self._slave_attempts += 1

        # vlc.Media.slaves_add needs the media; the player must be playing.
        if not self.player.is_playing():
            QTimer.singleShot(250, self._try_add_slaves)
            return

        try:
            import vlc as _vlc
            added = 0
            for track in self._pending_subtitle_tracks:
                path = track.get('path')
                if not path or not os.path.exists(path):
                    continue
                # Build a proper file:// URI for the slave.
                uri = path
                if not uri.startswith('file://'):
                    uri = 'file://' + os.path.abspath(path)
                # type 0 = subtitle; 4th arg select=False so we don't force-on.
                self.player.add_slave(_vlc.MediaSlaveType.subtitle, uri, False)
                added += 1
            self._slaves_added = True
            print(f"[*] Added {added} subtitle track(s) to player.")
            if added > 0:
                QTimer.singleShot(300, self._apply_preferred_subtitle)
        except Exception as e:
            print(f"[!] add_slave failed: {e}")

    def _apply_preferred_subtitle(self):
        """Enable the right subtitle on start:
          - if a preferred label was given (user picked one), match it;
          - else if auto_enable_first_sub, turn on the first subtitle;
          - else leave subtitles off."""
        if not self.player:
            return
        try:
            desc = self.player.video_get_spu_description() or []
            # Normalize names to strings.
            tracks = []
            for spu_id, name in desc:
                label = name.decode('utf-8', 'replace') if isinstance(name, bytes) else str(name)
                tracks.append((spu_id, label))

            pref = getattr(self, '_preferred_sub_label', None)
            if pref:
                # Find a track whose label contains the preferred label
                # (VLC may prefix/suffix the slave name).
                for spu_id, label in tracks:
                    if spu_id > 0 and (pref in label or label in pref):
                        self.player.video_set_spu(spu_id)
                        print(f"[*] Enabled preferred subtitle: {label}")
                        return
                # Fall through to first if no match.

            if getattr(self, '_auto_enable_first_sub', True):
                for spu_id, label in tracks:
                    if spu_id > 0:
                        self.player.video_set_spu(spu_id)
                        print(f"[*] Auto-enabled subtitle: {label}")
                        return
            else:
                # Leave off (user chose "No Subtitle" among several).
                self.player.video_set_spu(-1)
                print("[*] Subtitles left off by user choice.")
        except Exception as e:
            print(f"[!] applying subtitle failed: {e}")

    def _build_subtitle_menu(self, menu):
        """Populate a menu with available subtitle tracks + Off."""
        if not self.player:
            return
        try:
            desc = self.player.video_get_spu_description() or []
            current = self.player.video_get_spu()
        except Exception:
            desc, current = [], -1
        if not desc:
            action = QAction("No subtitles available", self)
            action.setEnabled(False)
            menu.addAction(action)
            return
        for spu_id, name in desc:
            label = name.decode('utf-8', 'replace') if isinstance(name, bytes) else str(name)
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(spu_id == current)
            action.triggered.connect(lambda checked, sid=spu_id: self.player.video_set_spu(sid))
            menu.addAction(action)

    def _build_audio_menu(self, menu):
        """Populate a menu with available audio tracks."""
        if not self.player:
            return
        try:
            desc = self.player.audio_get_track_description() or []
            current = self.player.audio_get_track()
        except Exception:
            desc, current = [], -1
        if not desc or len(desc) <= 1:
            action = QAction("Only one audio track", self)
            action.setEnabled(False)
            menu.addAction(action)
            if not desc:
                return
        for track_id, name in desc:
            label = name.decode('utf-8', 'replace') if isinstance(name, bytes) else str(name)
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(track_id == current)
            action.triggered.connect(lambda checked, tid=track_id: self.player.audio_set_track(tid))
            menu.addAction(action)

    def show_subtitle_menu_from_button(self):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #333; color: white; } QMenu::item:selected { background-color: #555; }")
        self._build_subtitle_menu(menu)
        menu.exec_(self.subs_btn.mapToGlobal(self.subs_btn.rect().topLeft()))

    def show_audio_menu_from_button(self):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #333; color: white; } QMenu::item:selected { background-color: #555; }")
        self._build_audio_menu(menu)
        menu.exec_(self.audio_btn.mapToGlobal(self.audio_btn.rect().topLeft()))

    def _cleanup_subtitle_files(self):
        """Remove all temp subtitle files from the previous playback."""
        files = list(getattr(self, '_subtitle_temp_files', []))
        if self.current_subtitle_file:
            files.append(self.current_subtitle_file)
        for path in files:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        self._subtitle_temp_files = []
        self.current_subtitle_file = None

    def show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #333; color: white; } QMenu::item:selected { background-color: #555; }")

        subs_menu = menu.addMenu("Subtitles")
        self._build_subtitle_menu(subs_menu)
        audio_menu = menu.addMenu("Audio")
        self._build_audio_menu(audio_menu)
        menu.addSeparator()

        aspect_menu = menu.addMenu("Aspect Ratio")
        ar_default = QAction("Default", self)
        ar_default.triggered.connect(lambda: self.player.video_set_aspect_ratio(None))
        aspect_menu.addAction(ar_default)
        ar_16_9 = QAction("16:9", self)
        ar_16_9.triggered.connect(lambda: self.player.video_set_aspect_ratio("16:9"))
        aspect_menu.addAction(ar_16_9)
        ar_4_3 = QAction("4:3", self)
        ar_4_3.triggered.connect(lambda: self.player.video_set_aspect_ratio("4:3"))
        aspect_menu.addAction(ar_4_3)
        menu.exec_(self.video_frame.mapToGlobal(pos))

    def toggle_fullscreen(self):
        self.main_window.toggle_fullscreen(not self.main_window.isFullScreen())

    def stop_and_exit(self):
        # Capture position BEFORE stopping (stop() resets the clock).
        if self.player:
            try:
                cur_ms = self.player.get_time()
                total_ms = self.player.get_length()
            except Exception:
                cur_ms, total_ms = -1, -1
            self._final_viewing_status(cur_ms, total_ms)
            self.player.stop()
        self.timer.stop()
        # Clean up all subtitle temp files (may be several for multi-track).
        self._cleanup_subtitle_files()
        self.api_client = None
        self.current_video_id = None
        self.go_back.emit()

    def _final_viewing_status(self, cur_ms, total_ms):
        # On exit, send one last progress update so the resume point is
        # accurate. If the user watched to ~the end, mark it completed so
        # it leaves "Continue Watching" instead of lingering at 99%.
        if not self.api_client or not self.current_video_id:
            return
        if total_ms is None or total_ms <= 0:
            return
        vid = self.current_video_id
        near_end = (cur_ms / total_ms) >= 0.95 if cur_ms >= 0 else False
        current_s = max(0, cur_ms / 1000.0)
        runtime_s = total_ms / 1000.0

        def _send():
            try:
                if near_end and not self._completed_reported:
                    self._completed_reported = True
                    self.api_client.mark_completed(vid)
                else:
                    self.api_client.save_viewing_status(vid, current_s, runtime_s)
            except Exception as e:
                print(f"[!] final viewingStatus failed: {e}")

        threading.Thread(target=_send, daemon=True).start()
