import sys
import os
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
        self.current_subtitle_file = None
        
        # --- Initialize VLC Once ---
        # We assume standard options here to prevent init crashes
        self.instance = vlc.Instance('--no-xlib', '--verbose=2') 
        self.player = self.instance.media_player_new()

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
        
        self.back_button = QPushButton("Back")
        self.back_button.clicked.connect(self.stop_and_exit)

        controls_layout.addWidget(self.play_button)
        controls_layout.addWidget(self.time_label)
        controls_layout.addWidget(self.slider)
        controls_layout.addWidget(QLabel("Vol"))
        controls_layout.addWidget(self.volume_slider)
        controls_layout.addWidget(self.fullscreen_btn)
        controls_layout.addWidget(self.back_button)
        
        main_layout.addWidget(self.controls_container, 0)

        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.update_ui)

    def mouseDoubleClickEvent(self, event):
        self.toggle_fullscreen()

    def show_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #333; color: white; } QMenu::item:selected { background-color: #555; }")
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

    def play_stream(self, url, subtitle_path=None, cookie_dict=None, sub_config=None):
        self.current_subtitle_file = subtitle_path
        
        # URL Rewriting for Auth
        if cookie_dict and 'JSESSIONID' in cookie_dict:
            jsessionid = cookie_dict['JSESSIONID']
            url = f"{url};jsessionid={jsessionid}"

        # Create Media
        media = self.instance.media_new(url)
        
        # Add Headers (Cookies/UA)
        if cookie_dict:
            cookie_str = ";".join([f"{k}={v}" for k, v in cookie_dict.items()])
            media.add_option(":http-user-agent=StreamaDesktop/1.0")
            media.add_option(f':http-cookie="{cookie_str}"')
            media.add_option(":http-reconnect=true")
        
        if subtitle_path and os.path.exists(subtitle_path):
            media.add_option(f":sub-file={subtitle_path}")
            
            # --- APPLY SUBTITLE SETTINGS ON MEDIA ---
            if sub_config:
                size = sub_config.get('subtitle_size', 20)
                is_bold = sub_config.get('subtitle_bold', False)
                
                print(f"[*] Applying Subtitle Settings -> Size: {size}, Bold: {is_bold}")
                
                # Force FreeType renderer
                media.add_option(":text-renderer=freetype")
                media.add_option(f":freetype-size={size}")
                media.add_option(":freetype-color=16777215") # White
                media.add_option(":freetype-outline-thickness=2")
                
                if is_bold:
                    media.add_option(":freetype-bold")
                else:
                    media.add_option(":no-freetype-bold")
            
        self.player.set_media(media)
        
        if sys.platform.startswith('linux'):
            self.player.set_xwindow(self.video_frame.winId())
        elif sys.platform == "win32":
            self.player.set_hwnd(self.video_frame.winId())
        
        self.player.play()
        self.play_button.setText("Pause")
        self.timer.start()

    def toggle_playback(self):
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
        pos = self.slider.value() / 1000.0
        self.player.set_position(pos)

    def slider_moved(self, val):
        pass

    def set_volume(self, val):
        self.player.audio_set_volume(val)

    def update_ui(self):
        if not self.is_slider_active and self.player.is_playing():
            pos = self.player.get_position()
            if pos >= 0:
                self.slider.setValue(int(pos * 1000))
            cur = self.player.get_time()
            total = self.player.get_length()
            self.time_label.setText(f"{format_time(cur)} / {format_time(total)}")

    def toggle_fullscreen(self):
        self.main_window.toggle_fullscreen(not self.main_window.isFullScreen())

    def stop_and_exit(self):
        self.player.stop()
        self.timer.stop()
        if self.current_subtitle_file and os.path.exists(self.current_subtitle_file):
            try:
                os.remove(self.current_subtitle_file)
            except:
                pass
        self.current_subtitle_file = None
        self.go_back.emit()
