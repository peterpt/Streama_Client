import sys
import os
import json
import base64
import logging
import traceback
from PySide2.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel, 
                               QStatusBar, QStackedWidget, QAction, QMessageBox)
from PySide2.QtCore import Qt, Slot, QThreadPool, QTimer, QObject
from PySide2.QtGui import QIcon, QPixmap, QPalette, QColor, QDesktopServices

# --- SETUP VLC PATHS ---
if sys.platform == "win32":
    app_dir = os.path.dirname(os.path.abspath(__file__))
    vlc_libs_path = os.path.join(app_dir, "vlc_libs")
    if os.path.exists(vlc_libs_path):
        print(f"[*] Found bundled VLC at: {vlc_libs_path}")
        if hasattr(os, 'add_dll_directory'):
            os.add_dll_directory(vlc_libs_path)
        os.environ['PATH'] = vlc_libs_path + ";" + os.environ['PATH']
        os.environ['VLC_PLUGIN_PATH'] = os.path.join(vlc_libs_path, "plugins")
    else:
        print(f"[!] WARNING: 'vlc_libs' folder not found at {vlc_libs_path}")

try:
    import assets
except ImportError:
    assets = None

# IMPORT LOCAL MODULES
from api import StreamaAPIClient
from player import VLCPlayerWidget
from ui_widgets import (SettingsDialog, BrowserWidget, MediaDetailWidget, 
                       LoginWorker, FetchConfigWorker, FetchDetailsWorker, SubtitleDownloadWorker)

LOG_FILE = "streama_browser.log"
logging.basicConfig(filename=LOG_FILE, filemode='w', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
SETTINGS_FILE = "settings.json"

def load_settings():
    defaults = {
        "server": "", "port": "8080", "ssl": False, "insecure_ssl": False, 
        "username": "", "password": "", "tmdb_api_key": "",
        "subtitle_size": 20, "subtitle_bold": False
    }
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
            defaults.update(settings)
            return defaults
    except:
        return defaults

def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Streama VLC Browser")
        self.resize(1024, 768)
        self.api_client = StreamaAPIClient()
        self.settings = load_settings()
        self.threadpool = QThreadPool()
        self.active_workers = []
        self.original_window_flags = self.windowFlags()
        
        self.browser_widget = None
        self.details_widget = None
        self.player_widget = None

        if assets and hasattr(assets, 'STREAMA_ICO_B64'):
            try:
                icon_data = base64.b64decode(assets.STREAMA_ICO_B64)
                icon_pixmap = QPixmap()
                icon_pixmap.loadFromData(icon_data)
                self.setWindowIcon(QIcon(icon_pixmap))
            except Exception as e:
                print(f"[!] Could not load icon from assets: {e}")

        self.initUI()
        self.update_ui_state(logged_in=False)

    def initUI(self):
        self.setStyleSheet("background-color: #2d2d2d; color: white;")
        self.stacked_widget = QStackedWidget()
        self.setCentralWidget(self.stacked_widget)

        self.welcome_widget = QWidget()
        welcome_layout = QVBoxLayout(self.welcome_widget)
        # FIX: No int cast
        welcome_layout.setAlignment(Qt.AlignCenter)
        welcome_text = QLabel("Welcome to Streama Browser!\nPlease configure your server and log in.")
        welcome_text.setStyleSheet("font-size: 20px; color: white;")
        # FIX: No int cast
        welcome_text.setAlignment(Qt.AlignCenter)
        welcome_layout.addStretch()
        welcome_layout.addWidget(welcome_text)
        welcome_layout.addStretch()
        self.stacked_widget.addWidget(self.welcome_widget)
        
        self.setStatusBar(QStatusBar(self))
        self.setup_menu()

    # --- FIX: FULLSCREEN LOGIC (Safe Type Casting) ---
    def toggle_fullscreen(self, checked=None):
        if self.isFullScreen():
            # EXIT Fullscreen
            self.setWindowFlags(self.original_window_flags)
            self.showNormal()
            self.menuBar().setVisible(True)
            self.statusBar().setVisible(True)
            
            # Show controls if player is active
            if self.player_widget and self.stacked_widget.currentWidget() == self.player_widget:
                self.player_widget.controls_container.setVisible(True)
                self.player_widget.video_frame.setCursor(Qt.ArrowCursor)
        else:
            # ENTER Fullscreen
            flags = int(Qt.Window) | int(Qt.FramelessWindowHint)
            self.setWindowFlags(Qt.WindowType(flags))
            
            self.showFullScreen()
            self.menuBar().setVisible(False)
            self.statusBar().setVisible(False)
            
            # Hide controls if player is active
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
        self.start_browser_session()

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
        self.details_widget.set_media(details_data)
        self.stacked_widget.setCurrentWidget(self.details_widget)
        self.statusBar().clearMessage()

    # --- VLC Playback Logic ---
    @Slot(object, object)
    def prepare_video_playback(self, media_data, selected_subtitle=None):
        video_files = media_data.get('videoFiles', [])
        if not video_files:
            QMessageBox.critical(self, "Error", "No video file found for this item.")
            return
        
        # Get Stream URL
        stream_url, error = self.api_client.get_stream_url(video_files[0].get('id'))
        if error:
            QMessageBox.critical(self, "Error", f"Could not get stream URL:\n{error}")
            return
            
        self.current_stream_url = stream_url
        
        # Handle Subtitle (Download to temp if exists)
        if selected_subtitle:
            sub_id = selected_subtitle.get('id')
            sub_url, _ = self.api_client.get_stream_url(sub_id, extension='srt')
            if sub_url:
                self.statusBar().showMessage("Downloading subtitles...")
                worker = SubtitleDownloadWorker(self.api_client.session, sub_url)
                worker.signals.subtitle_downloaded.connect(self.start_player_with_subs)
                worker.signals.subtitle_downloaded.connect(lambda: self._worker_finished(worker))
                self.active_workers.append(worker)
                self.threadpool.start(worker)
                return

        # If no subtitle, start immediately
        self.start_player_with_subs(None)

    @Slot(str)
    def start_player_with_subs(self, subtitle_path):
        cookies = self.api_client.session.cookies.get_dict()
        print(f"[*] Starting playback with cookies: {cookies}")
        
        sub_config = {
            "subtitle_size": self.settings.get("subtitle_size", 20),
            "subtitle_bold": self.settings.get("subtitle_bold", False)
        }
        
        self.statusBar().showMessage("Starting Player...")
        self.player_widget.play_stream(self.current_stream_url, subtitle_path, cookies, sub_config)
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
        # --- FIX: Stop player if running ---
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
    
    # Dark Theme
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
