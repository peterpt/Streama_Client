import sys
import os
import json
import base64
import logging
import traceback
import ctypes.util 


def _check_dependencies():
    """Verify required third-party modules are present BEFORE we try to use
    them. If something is missing (e.g. a user ran the source without
    installing requirements), show a clear message instead of a raw
    ImportError traceback. We avoid importing Qt here since Qt itself may be
    the missing piece."""
    required = {
        'PySide2': "PySide2 (the GUI toolkit) — install with: pip install PySide2==5.12.6",
        'vlc': "python-vlc (the VLC bindings) — install with: pip install python-vlc",
        'requests': "requests (HTTP library) — install with: pip install requests",
    }
    missing = []
    for module, hint in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(hint)
    if missing:
        msg = ("Streama VLC Browser cannot start because some required "
               "components are missing:\n\n  - " + "\n  - ".join(missing) +
               "\n\nPlease install the missing components and try again.")
        # Try a native Windows message box first (no Qt needed), then fall
        # back to printing to the console / log.
        try:
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, msg, "Missing Dependencies", 0x10)
        except Exception:
            pass
        print(msg)
        try:
            with open("streama_browser.log", "a") as f:
                f.write(msg + "\n")
        except Exception:
            pass
        sys.exit(1)


_check_dependencies()

from PySide2.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel, 
                               QStatusBar, QStackedWidget, QAction, QMessageBox)
from PySide2.QtCore import Qt, Slot, QThreadPool, QTimer, QObject
from PySide2.QtGui import QIcon, QPixmap, QPalette, QColor, QDesktopServices

# --- VLC DETECTION & SETUP ---
def get_app_base_dir():
    """Return the directory the app should treat as its base for reading
    bundled resources (vlc_libs) and writing user data (cache, settings).

    - Frozen one-folder PyInstaller build: the folder containing the .exe.
    - Running from source: the folder containing this script.
    This is stable across launches (unlike a one-file temp dir), so cache
    posters and settings.json persist next to the executable.
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller sets sys.frozen; sys.executable is the .exe path.
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


APP_BASE_DIR = get_app_base_dir()


def setup_vlc_environment():
    app_dir = APP_BASE_DIR
    if sys.platform == "win32":
        vlc_libs_path = os.path.join(app_dir, "vlc_libs")
        if os.path.exists(vlc_libs_path):
            if hasattr(os, 'add_dll_directory'):
                os.add_dll_directory(vlc_libs_path)
            os.environ['PATH'] = vlc_libs_path + ";" + os.environ['PATH']
            os.environ['VLC_PLUGIN_PATH'] = os.path.join(vlc_libs_path, "plugins")
            return True
        # vlc_libs folder is missing — VLC will not load. Report this rather
        # than pretending everything is fine (which caused a later crash).
        logging.error(f"vlc_libs folder not found at {vlc_libs_path}")
        return False
    elif sys.platform.startswith('linux'):
        lib_name = ctypes.util.find_library('vlc')
        if lib_name:
            return True
        return False
    return False

vlc_available = setup_vlc_environment()

try:
    import assets
except ImportError:
    assets = None

# IMPORT LOCAL MODULES
from api import StreamaAPIClient
from player import VLCPlayerWidget
from ui_widgets import (SettingsDialog, ProfileSelectionDialog, BrowserWidget, MediaDetailWidget, 
                       LoginWorker, FetchConfigWorker, FetchDetailsWorker, SubtitleDownloadWorker,
                       MultiSubtitleDownloadWorker)

LOG_FILE = os.path.join(APP_BASE_DIR, "streama_browser.log")
logging.basicConfig(filename=LOG_FILE, filemode='w', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
SETTINGS_FILE = os.path.join(APP_BASE_DIR, "settings.json")

def load_settings():
    defaults = {
        "server": "", "port": "8080", "ssl": False, "insecure_ssl": False, 
        "username": "", "password": "", "tmdb_api_key": "",
        "subtitle_size": 20, "subtitle_bold": False
    }
    # If no settings file exists yet (first run), create a blank one with the
    # defaults so the user has a file to inspect/edit and the app has a stable
    # config location next to the executable.
    if not os.path.exists(SETTINGS_FILE):
        try:
            save_settings(defaults)
            logging.info(f"Created new settings file at {SETTINGS_FILE}")
        except Exception as e:
            logging.error(f"Could not create settings file: {e}")
        return dict(defaults)
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
            defaults.update(settings)
            return defaults
    except Exception as e:
        logging.error(f"Could not read settings ({e}); using defaults.")
        return dict(defaults)

def save_settings(settings):
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings, f, indent=4)
    except Exception as e:
        logging.error(f"Could not save settings: {e}")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Streama VLC Browser")
        self.resize(1024, 768)
        self.api_client = StreamaAPIClient(base_dir=APP_BASE_DIR)
        self.settings = load_settings()
        self.threadpool = QThreadPool()
        self.active_workers = []
        self.original_window_flags = self.windowFlags()
        
        self.browser_widget = None
        self.details_widget = None
        self.player_widget = None

        # Load Window Icon
        if assets and hasattr(assets, 'STREAMA_ICO_B64'):
            try:
                icon_data = base64.b64decode(assets.STREAMA_ICO_B64)
                icon_pixmap = QPixmap()
                icon_pixmap.loadFromData(icon_data)
                self.setWindowIcon(QIcon(icon_pixmap))
            except Exception as e:
                print(f"[!] Could not load icon from assets: {e}")

        if not vlc_available:
            QTimer.singleShot(500, self.show_vlc_error)

        self.initUI()
        self.update_ui_state(logged_in=False)

    def show_vlc_error(self):
        if sys.platform.startswith('linux'):
            msg = ("VLC Media Player is not installed or not found.\n\n"
                   "Since you are on Linux, please run:\n"
                   "sudo apt install vlc libvlc-dev")
        else:
            expected = os.path.join(APP_BASE_DIR, "vlc_libs")
            msg = ("VLC libraries were not found, so video playback is "
                   "unavailable.\n\n"
                   "Expected a 'vlc_libs' folder (with libvlc.dll, "
                   "libvlccore.dll and a 'plugins' folder) here:\n"
                   f"{expected}\n\n"
                   "Make sure the 'vlc_libs' folder sits next to the "
                   "application.")
        QMessageBox.critical(self, "VLC Not Found", msg)

    def initUI(self):
        self.setStyleSheet("background-color: #2d2d2d; color: white;")
        self.stacked_widget = QStackedWidget()
        self.setCentralWidget(self.stacked_widget)

        self.welcome_widget = QWidget()
        welcome_layout = QVBoxLayout(self.welcome_widget)
        welcome_layout.setAlignment(Qt.AlignCenter)
        
        # Add Top Stretch to center vertical content
        welcome_layout.addStretch()

        # --- LOGO LOGIC START ---
        if assets and hasattr(assets, 'STREAMA_JPG_B64'):
            try:
                logo_data = base64.b64decode(assets.STREAMA_JPG_B64)
                logo_pixmap = QPixmap()
                logo_pixmap.loadFromData(logo_data)
                
                if not logo_pixmap.isNull():
                    # Scale down if image is too large (e.g., width > 400px)
                    if logo_pixmap.width() > 400:
                        logo_pixmap = logo_pixmap.scaledToWidth(400, Qt.SmoothTransformation)
                    
                    logo_label = QLabel()
                    logo_label.setAlignment(Qt.AlignCenter)
                    logo_label.setPixmap(logo_pixmap)
                    # Add some margin below the logo
                    logo_label.setStyleSheet("margin-bottom: 20px;")
                    welcome_layout.addWidget(logo_label)
            except Exception as e:
                print(f"[!] Error loading welcome logo: {e}")
        # --- LOGO LOGIC END ---

        welcome_text = QLabel("Welcome to Streama Browser!\nPlease configure your server and log in.")
        welcome_text.setStyleSheet("font-size: 20px; color: white; font-weight: bold;")
        welcome_text.setAlignment(Qt.AlignCenter)
        
        welcome_layout.addWidget(welcome_text)
        
        # Add Bottom Stretch
        welcome_layout.addStretch()
        
        self.stacked_widget.addWidget(self.welcome_widget)
        
        self.setStatusBar(QStatusBar(self))
        self.setup_menu()

    def toggle_fullscreen(self, checked=None):
        if self.isFullScreen():
            self.setWindowFlags(self.original_window_flags)
            self.showNormal()
            self.menuBar().setVisible(True)
            self.statusBar().setVisible(True)
            if self.player_widget and self.stacked_widget.currentWidget() == self.player_widget:
                self.player_widget.controls_container.setVisible(True)
                self.player_widget.video_frame.setCursor(Qt.ArrowCursor)
        else:
            flags = int(Qt.Window) | int(Qt.FramelessWindowHint)
            self.setWindowFlags(Qt.WindowType(flags))
            self.showFullScreen()
            self.menuBar().setVisible(False)
            self.statusBar().setVisible(False)
            if self.player_widget and self.stacked_widget.currentWidget() == self.player_widget:
                self.player_widget.controls_container.setVisible(False)
                self.player_widget.video_frame.setCursor(Qt.BlankCursor)
        self.show()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen()
        else:
            super().keyPressEvent(event)

    def setup_menu(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&File")
        self.login_action = QAction("Login", self)
        self.login_action.triggered.connect(self.handle_login_click)
        self.logout_action = QAction("Logout", self)
        self.logout_action.triggered.connect(self.handle_logout_click)
        self.settings_action = QAction("Settings...", self)
        self.settings_action.triggered.connect(self.open_settings_dialog)
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        
        file_menu.addAction(self.login_action)
        file_menu.addAction(self.logout_action)
        file_menu.addSeparator()
        file_menu.addAction(self.settings_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

    def open_settings_dialog(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec_():
            self.settings = dialog.get_settings()
            save_settings(self.settings)
            self.statusBar().showMessage("Settings saved.", 3000)

    @Slot(QObject)
    def _worker_finished(self, worker):
        try:
            self.active_workers.remove(worker)
        except ValueError:
            pass

    def handle_login_click(self):
        if not self.settings.get("server") or not self.settings.get("username"):
            QMessageBox.warning(self, "Config Missing", "Please set server settings first.")
            self.open_settings_dialog()
            return
        self.api_client.configure(self.settings["server"], self.settings["port"], self.settings["ssl"], self.settings["insecure_ssl"])
        self.statusBar().showMessage("Logging in...")
        self.login_action.setEnabled(False)
        self.settings_action.setEnabled(False)
        worker = LoginWorker(self.api_client, self.settings["username"], self.settings["password"])
        worker.signals.login_finished.connect(self.on_login_finished)
        worker.signals.login_finished.connect(lambda: self._worker_finished(worker))
        self.active_workers.append(worker)
        self.threadpool.start(worker)

    def on_login_finished(self, success, message):
        self.statusBar().showMessage(message, 5000)
        if success:
            self.update_ui_state(logged_in=True)
            config_worker = FetchConfigWorker(self.api_client)
            config_worker.signals.config_finished.connect(self.on_config_loaded)
            config_worker.signals.config_finished.connect(lambda: self._worker_finished(config_worker))
            self.active_workers.append(config_worker)
            self.threadpool.start(config_worker)
        else:
            QMessageBox.critical(self, "Login Failed", message)
            self.update_ui_state(logged_in=False)

    @Slot(object, str)
    def on_config_loaded(self, config_data, error):
        if error or not isinstance(config_data, dict) or not config_data.get('key'):
            logging.warning(f"Could not load TMDB config. Data: {config_data}, Error: {error}")
        else:
            self.api_client.set_tmdb_image_base_url(config_data.get("images", {}).get("secure_base_url"))
        # Load and select a profile BEFORE fetching the dashboard. Continue
        # Watching is scoped to the active profile, so the profileId must be
        # set first or that list comes back empty.
        self.select_profile()
        self.start_browser_session()

    def select_profile(self):
        profiles, perr = self.api_client.load_profiles()
        if perr:
            logging.warning(f"Could not load profiles: {perr}")
            return
        if not profiles:
            logging.warning("No profiles returned for this user.")
            return
        # Always ask which profile to use (matches the web client, which
        # shows "Who's watching?" on every login even with one profile).
        dialog = ProfileSelectionDialog(profiles, self)
        dialog.exec_()
        chosen = dialog.get_selected_profile() or profiles[0]
        profile_id = chosen.get('id')
        self.api_client.set_current_profile_id(profile_id)
        name = chosen.get('profileName') or chosen.get('name') or profile_id
        self.statusBar().showMessage(f"Watching as: {name}", 4000)

    def start_browser_session(self):
        if not self.browser_widget:
            self.browser_widget = BrowserWidget(self)
            self.stacked_widget.addWidget(self.browser_widget)
            self.browser_widget.poster_clicked.connect(self.show_details)
        
        if not self.details_widget:
            self.details_widget = MediaDetailWidget(self)
            self.stacked_widget.addWidget(self.details_widget)
            self.details_widget.play_video.connect(self.prepare_video_playback)
            self.details_widget.go_back.connect(self.show_browser)
            self.details_widget.episode_selected.connect(self.show_details)
            self.details_widget.set_context(self.api_client, self.threadpool)
            
        if not self.player_widget:
            self.player_widget = VLCPlayerWidget(self)
            self.stacked_widget.addWidget(self.player_widget)
            self.player_widget.go_back.connect(self.go_from_player_to_details)

        self.browser_widget.set_context(self.api_client, self.threadpool, self.statusBar())
        self.stacked_widget.setCurrentWidget(self.browser_widget)
        QTimer.singleShot(50, self.browser_widget.load_initial_content)

    @Slot(object)
    def show_details(self, media_data):
        # The clicked item (e.g. from Continue Watching) may carry a
        # currentPlayTime resume position. The details fetch returns a fresh
        # server object WITHOUT it, so capture it here and re-apply it after.
        self._pending_resume_seconds = media_data.get('currentPlayTime') or 0
        worker = FetchDetailsWorker(self.api_client, media_data)
        worker.signals.details_finished.connect(self.on_details_loaded)
        worker.signals.fetch_error.connect(lambda e: QMessageBox.critical(self, "Error", f"Could not fetch details:\n{e}"))
        worker.signals.details_finished.connect(lambda: self._worker_finished(worker))
        self.active_workers.append(worker)
        self.threadpool.start(worker)
        self.statusBar().showMessage("Loading details...")

    @Slot(object, str)
    def on_details_loaded(self, details_data, error):
        if error:
            QMessageBox.critical(self, "Error", f"Could not fetch details:\n{error}")
            self.stacked_widget.setCurrentWidget(self.browser_widget)
            return
        # Re-attach the resume position that the fresh details object lacks.
        if isinstance(details_data, dict) and getattr(self, '_pending_resume_seconds', 0):
            details_data.setdefault('currentPlayTime', self._pending_resume_seconds)
        self.details_widget.set_media(details_data)
        self.stacked_widget.setCurrentWidget(self.details_widget)
        self.statusBar().clearMessage()

    @Slot(object, object)
    def prepare_video_playback(self, media_data, selected_subtitle=None):
        if not vlc_available:
            self.show_vlc_error()
            return

        video_files = media_data.get('videoFiles', [])
        if not video_files:
            QMessageBox.critical(self, "Error", "No video file found for this item.")
            return

        stream_url, error = self.api_client.get_stream_url(video_files[0].get('id'))
        if error:
            QMessageBox.critical(self, "Error", f"Could not get stream URL:\n{error}")
            return

        self.current_stream_url = stream_url
        # Capture the streama video id (for progress reporting) and the
        # resume position. currentPlayTime is in SECONDS (from the
        # continue-watching normalizer); play_stream wants milliseconds.
        self.current_video_id = media_data.get('id')
        resume_seconds = media_data.get('currentPlayTime') or 0
        self.current_resume_ms = int(resume_seconds * 1000)

        # Remember the user's explicit subtitle choice (multi-subtitle case),
        # or None for "No Subtitle" / single / zero case.
        if selected_subtitle is None:
            self._preferred_sub_label = None
        else:
            self._preferred_sub_label = str(
                selected_subtitle.get('language') or selected_subtitle.get('label')
                or selected_subtitle.get('originalFilename')
                or f"Subtitle {selected_subtitle.get('id')}"
            )

        # Download ALL subtitles so they can be switched inside the player.
        subtitles = media_data.get('subtitles', []) or []
        # With exactly one subtitle, auto-enable it; with multiple, honour the
        # user's pick (or off if they chose none).
        self._auto_enable_first_sub = (len(subtitles) <= 1)

        if subtitles:
            self.statusBar().showMessage(f"Downloading {len(subtitles)} subtitle(s)...")
            worker = MultiSubtitleDownloadWorker(self.api_client, subtitles)
            worker.signals.subtitle_downloaded.connect(self.start_player_with_tracks)
            worker.signals.subtitle_downloaded.connect(lambda: self._worker_finished(worker))
            self.active_workers.append(worker)
            self.threadpool.start(worker)
            return

        # No subtitles at all (e.g. native-language video).
        self.start_player_with_tracks("[]")

    @Slot(str)
    def start_player_with_tracks(self, tracks_json):
        try:
            tracks = json.loads(tracks_json) if tracks_json else []
        except Exception:
            tracks = []

        cookies = self.api_client.session.cookies.get_dict()
        sub_config = {
            "subtitle_size": self.settings.get("subtitle_size", 20),
            "subtitle_bold": self.settings.get("subtitle_bold", False)
        }

        self.statusBar().showMessage("Starting Player...")
        start_ms = getattr(self, 'current_resume_ms', 0)
        self.player_widget.play_stream(
            self.current_stream_url, None, cookies, sub_config,
            start_time=start_ms,
            api_client=self.api_client,
            video_id=getattr(self, 'current_video_id', None),
            subtitle_tracks=tracks,
            preferred_sub_label=getattr(self, '_preferred_sub_label', None),
            auto_enable_first_sub=getattr(self, '_auto_enable_first_sub', True),
        )
        self.stacked_widget.setCurrentWidget(self.player_widget)

    @Slot()
    def go_from_player_to_details(self):
        if self.isFullScreen():
            self.toggle_fullscreen(False)
        self.stacked_widget.setCurrentWidget(self.details_widget)

    @Slot()
    def show_browser(self):
        self.stacked_widget.setCurrentWidget(self.browser_widget)

    def handle_logout_click(self):
        if self.player_widget:
            self.player_widget.stop_and_exit()

        if self.browser_widget:
            self.browser_widget.clear_grid()
        self.stacked_widget.setCurrentWidget(self.welcome_widget)
        self.update_ui_state(logged_in=False)
        self.statusBar().showMessage("Logged out.", 3000)

    def update_ui_state(self, logged_in):
        self.login_action.setEnabled(not logged_in)
        self.logout_action.setEnabled(logged_in)
        self.settings_action.setEnabled(not logged_in)

    def closeEvent(self, event):
        if self.player_widget:
            self.player_widget.stop_and_exit()
        self.threadpool.clear()
        event.accept()

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.error("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))
    print("Error:", exc_value)

if __name__ == "__main__":
    sys.excepthook = handle_exception
    app = QApplication(sys.argv)
    
    dark_palette = QPalette()
    dark_palette.setColor(QPalette.Window, QColor(45, 45, 45))
    dark_palette.setColor(QPalette.WindowText, Qt.white)
    dark_palette.setColor(QPalette.Base, QColor(25, 25, 25))
    dark_palette.setColor(QPalette.AlternateBase, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.ToolTipBase, Qt.white)
    dark_palette.setColor(QPalette.ToolTipText, Qt.white)
    dark_palette.setColor(QPalette.Text, Qt.white)
    dark_palette.setColor(QPalette.Button, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.ButtonText, Qt.white)
    dark_palette.setColor(QPalette.BrightText, Qt.red)
    dark_palette.setColor(QPalette.Link, QColor(42, 130, 218))
    dark_palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    dark_palette.setColor(QPalette.HighlightedText, Qt.black)
    app.setPalette(dark_palette)
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())
