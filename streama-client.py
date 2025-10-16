import sys
import json
import requests
import base64
import logging
import traceback
import os
import webbrowser
import re

try:
    import assets
except ImportError:
    print("[!] assets.py not found. Using default icon and no welcome image.")
    assets = None

from PySide6.QtWidgets import (QApplication, QMainWindow, QDialog, QLineEdit,
                               QCheckBox, QDialogButtonBox, QFormLayout, QVBoxLayout,
                               QMessageBox, QWidget, QLabel, QStatusBar, QScrollArea,
                               QGridLayout, QPushButton, QHBoxLayout, QSpacerItem,
                               QSizePolicy, QStackedWidget, QSlider, QStyle, QListWidget,
                               QListWidgetItem, QComboBox, QGraphicsView, QGraphicsScene,
                               QGraphicsProxyWidget)
from PySide6.QtCore import Qt, QObject, Slot, QRunnable, QThreadPool, Signal, QTimer, QUrl, QEvent, QIODevice, QByteArray, QBuffer, QRectF
from PySide6.QtGui import QAction, QPixmap, QPalette, QColor, QIcon, QDesktopServices, QIntValidator
from PySide6.QtNetwork import QNetworkRequest, QNetworkAccessManager, QNetworkReply
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget, QGraphicsVideoItem
from requests.packages.urllib3.exceptions import InsecureRequestWarning

# --- Setup Logging ---
LOG_FILE = "streama_browser.log"
logging.basicConfig(
    filename=LOG_FILE,
    filemode='w',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- Streama API Client Class ---
class StreamaAPIClient:
    def __init__(self):
        self.base_url = None
        self.session = requests.Session()
        self.tmdb_image_base_url = "https://image.tmdb.org/t/p/"

    def configure(self, server, port, ssl=False, insecure_ssl=False):
        protocol = "https" if ssl else "http"
        self.base_url = f"{protocol}://{server}:{port}"
        self.session.verify = not insecure_ssl
        if insecure_ssl:
            requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    def set_tmdb_image_base_url(self, url):
        self.tmdb_image_base_url = url if url else "https://image.tmdb.org/t/p/"
        print(f"[*] TMDB Image Base URL set to: {self.tmdb_image_base_url}")


    def _make_request(self, method, endpoint, **kwargs):
        if not self.base_url:
            return None, "API client is not configured."
        url = self.base_url + endpoint
        try:
            response = self.session.request(method, url, timeout=15, **kwargs)
            response.raise_for_status()
            return response.json(), None
        except requests.exceptions.RequestException as e:
            logging.error(f"API Request Failed: {e}")
            return None, str(e)
        except json.JSONDecodeError as e:
            logging.error(f"JSON Decode Failed for {url}: {e}")
            return None, "Failed to decode server response."

    def login(self, username, password):
        return self._make_request('POST', "/login/authenticate", data={"username": username, "password": password, "remember_me": "on"}, headers={'Accept': 'application/json, text/plain, */*','X-Requested-With': 'XMLHttpRequest'})
    def get_continue_watching(self, max_items=50):
        return self._make_request('GET', f"/dash/listContinueWatching.json?max={max_items}")
    def get_movies(self, max_items=50, offset=0):
        return self._make_request('GET', f"/dash/listMovies.json?max={max_items}&offset={offset}")
    def get_shows(self, max_items=50, offset=0):
        return self._make_request('GET', f"/dash/listShows.json?max={max_items}&offset={offset}")
    def get_generic_videos(self, max_items=50, offset=0):
        return self._make_request('GET', f"/dash/listGenericVideos.json?max={max_items}&offset={offset}")
    def search(self, query):
        return self._make_request('GET', f"/dash/searchMedia.json?query={query}")
    def get_video_details(self, video_id):
        return self._make_request('GET', f"/video/show.json?id={video_id}")
    def get_show_details(self, show_id):
        return self._make_request('GET', f"/tvShow/show.json?id={show_id}")
    def get_episodes_for_show(self, show_id):
        return self._make_request('GET', f"/tvShow/episodesForTvShow.json?id={show_id}")
    def get_tmdb_config(self):
        return self._make_request('GET', "/theMovieDb/hasKey.json")
    def get_stream_url(self, file_id, extension="mp4"):
        if not self.base_url:
            return None, "API Client not configured"
        return f"{self.base_url}/file/serve/{file_id}.{extension}", None

# --- Helper Functions & Settings Dialog ---
SETTINGS_FILE = "settings.json"
def load_settings():
    defaults = {
        "server": "", "port": "8080", "ssl": False, "insecure_ssl": False,
        "username": "", "password": "", "tmdb_api_key": "",
        "use_buffer": True, "buffer_size_mb": 5
    }
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f)
            defaults.update(settings)
            return defaults
    except (FileNotFoundError, json.JSONDecodeError):
        return defaults
def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)

def time_str_to_ms(time_str):
    """Converts an SRT or VTT time string to total milliseconds."""
    time_str = time_str.replace(',', '.')
    parts = time_str.split(':')
    if len(parts) == 3:
        h, m, s_ms = parts
    elif len(parts) == 2:
        h = 0
        m, s_ms = parts
    else:
        return 0
    try:
        s, ms = s_ms.split('.')
        return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)
    except (ValueError, IndexError):
        logging.warning(f"Could not parse time value: {time_str}")
        return 0

def parse_subtitles(subtitle_content):
    """Parses raw SRT or VTT subtitle content into a list of (start_ms, end_ms, text) tuples."""
    if not subtitle_content:
        return []
    subtitles = []
    content = subtitle_content.strip().replace('\r', '')
    is_vtt = content.startswith('WEBVTT')
    blocks = content.split('\n\n')
    for block in blocks:
        lines = block.strip().split('\n')
        if is_vtt and (lines[0] == 'WEBVTT' or not lines[0]):
            continue
        time_line_index = next((i for i, line in enumerate(lines) if '-->' in line), -1)
        if time_line_index != -1:
            try:
                start_str, end_str = lines[time_line_index].split(' --> ')
                end_str = end_str.split(' ')[0]
                start_ms = time_str_to_ms(start_str.strip())
                end_ms = time_str_to_ms(end_str.strip())
                text = "\n".join(lines[time_line_index+1:])
                text = re.sub(r'<[^>]+>', '', text)
                if text:
                    subtitles.append((start_ms, end_ms, text))
            except Exception as e:
                logging.warning(f"Could not parse subtitle block: {block} - Error: {e}")
    print(f"[*] Successfully parsed {len(subtitles)} subtitle cues.")
    return subtitles

class SettingsDialog(QDialog):
    def __init__(self, current_settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Server Configuration")
        self.serverInput = QLineEdit(current_settings.get("server", ""))
        self.portInput = QLineEdit(current_settings.get("port", "8080"))
        self.sslCheck = QCheckBox("Use SSL (https://)")
        self.sslCheck.setChecked(current_settings.get("ssl", False))
        self.insecureSslCheck = QCheckBox("Ignore SSL Certificate Errors")
        self.insecureSslCheck.setChecked(current_settings.get("insecure_ssl", False))
        self.usernameInput = QLineEdit(current_settings.get("username", ""))
        self.passwordInput = QLineEdit(current_settings.get("password", ""))
        self.passwordInput.setEchoMode(QLineEdit.Password)
        self.tmdbInput = QLineEdit(current_settings.get("tmdb_api_key", ""))
        self.bufferCheck = QCheckBox("Buffer before playing (for fast-start files)")
        self.bufferCheck.setChecked(current_settings.get("use_buffer", True))
        self.bufferCheck.setToolTip("If checked, download a small chunk before playing (fast). If unchecked, download the entire file first (robust).")
        self.bufferSizeInput = QLineEdit(str(current_settings.get("buffer_size_mb", 5)))
        self.bufferSizeInput.setValidator(QIntValidator(1, 50, self))
        self.bufferSizeInput.setToolTip("Buffer size in MB (1-50).")
        self.bufferSizeInput.setEnabled(self.bufferCheck.isChecked())
        self.bufferCheck.toggled.connect(self.bufferSizeInput.setEnabled)
        formLayout = QFormLayout()
        formLayout.addRow("Server IP or Domain:", self.serverInput)
        formLayout.addRow("Port:", self.portInput)
        formLayout.addRow(self.sslCheck)
        formLayout.addRow(self.insecureSslCheck)
        formLayout.addRow("Username:", self.usernameInput)
        formLayout.addRow("Password:", self.passwordInput)
        formLayout.addRow("TheMovieDB API Key:", self.tmdbInput)
        formLayout.addRow(self.bufferCheck)
        formLayout.addRow("Buffer Size (MB):", self.bufferSizeInput)
        self.buttonBox = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)
        mainLayout = QVBoxLayout()
        mainLayout.addLayout(formLayout)
        mainLayout.addWidget(self.buttonBox)
        self.setLayout(mainLayout)
    def get_settings(self):
        return {
            "server": self.serverInput.text().strip(),
            "port": self.portInput.text().strip(),
            "ssl": self.sslCheck.isChecked(),
            "insecure_ssl": self.insecureSslCheck.isChecked(),
            "username": self.usernameInput.text().strip(),
            "password": self.passwordInput.text(),
            "tmdb_api_key": self.tmdbInput.text().strip(),
            "use_buffer": self.bufferCheck.isChecked(),
            "buffer_size_mb": int(self.bufferSizeInput.text()) if self.bufferSizeInput.text().isdigit() else 5
        }

# --- Worker Threads ---
class WorkerSignals(QObject):
    login_finished = Signal(bool, str)
    page_finished = Signal(str, object, str)
    continue_watching_finished = Signal(list, str)
    search_finished = Signal(object, str)
    fetch_error = Signal(str)
    image_finished = Signal(QLabel, QPixmap)
    details_finished = Signal(object, str)
    config_finished = Signal(object, str)
    subtitle_finished = Signal(str, str)

class LoginWorker(QRunnable):
    def __init__(self, api_client, username, password):
        super().__init__()
        self.api_client = api_client
        self.username = username
        self.password = password
        self.signals = WorkerSignals()
    @Slot()
    def run(self):
        data, error = self.api_client.login(self.username, self.password)
        success = data.get("success") if data else False
        message = data.get('username') if success else (error or data.get("error", "Unknown error."))
        self.signals.login_finished.emit(success, message)
class FetchConfigWorker(QRunnable):
    def __init__(self, api_client):
        super().__init__()
        self.api_client = api_client
        self.signals = WorkerSignals()
    @Slot()
    def run(self):
        data, error = self.api_client.get_tmdb_config()
        self.signals.config_finished.emit(data, error)
class FetchPageWorker(QRunnable):
    def __init__(self, api_client, media_type, offset=0, max_items=50):
        super().__init__()
        self.signals = WorkerSignals()
        self.api_client = api_client
        self.media_type = media_type
        self.offset = offset
        self.max_items = max_items
    @Slot()
    def run(self):
        fetch_map = {'shows': self.api_client.get_shows, 'generic': self.api_client.get_generic_videos}
        fetch_func = fetch_map.get(self.media_type, self.api_client.get_movies)
        data, error = fetch_func(max_items=self.max_items, offset=self.offset)
        self.signals.page_finished.emit(self.media_type, data, error)
class FetchContinueWatchingWorker(QRunnable):
    def __init__(self, api_client):
        super().__init__()
        self.api_client = api_client
        self.signals = WorkerSignals()
    @Slot()
    def run(self):
        videos, error = self.api_client.get_continue_watching(max_items=50)
        self.signals.continue_watching_finished.emit(videos, error)
class SearchWorker(QRunnable):
    def __init__(self, api_client, query):
        super().__init__()
        self.api_client = api_client
        self.query = query
        self.signals = WorkerSignals()
    @Slot()
    def run(self):
        data, error = self.api_client.search(self.query)
        self.signals.search_finished.emit(data, error)
class FetchDetailsWorker(QRunnable):
    def __init__(self, api_client, media_data):
        super().__init__()
        self.api_client = api_client
        self.media_data = media_data
        self.signals = WorkerSignals()
    @Slot()
    def run(self):
        media_type = self.media_data.get('mediaType')
        media_id = self.media_data.get('id')
        if media_type == 'tvShow':
            show_data, show_error = self.api_client.get_show_details(media_id)
            if show_error:
                self.signals.details_finished.emit(None, show_error)
                return
            episodes_list, episodes_error = self.api_client.get_episodes_for_show(media_id)
            if episodes_error:
                self.signals.details_finished.emit(None, episodes_error)
                return
            show_data['episodes'] = episodes_list
            self.signals.details_finished.emit(show_data, None)
        else:
            data, error = self.api_client.get_video_details(media_id)
            if data and self.media_data.get('is_episode'):
                data['is_episode'] = True
            self.signals.details_finished.emit(data, error)
class ImageDownloader(QRunnable):
    def __init__(self, url, target_label, session=None):
        super().__init__()
        self.url = url
        self.target_label = target_label
        self.session = session
        self.signals = WorkerSignals()
    @Slot()
    def run(self):
        try:
            requester = self.session if self.session else requests
            response = requester.get(self.url, timeout=10)
            response.raise_for_status()
            pixmap = QPixmap()
            pixmap.loadFromData(response.content)
            self.signals.image_finished.emit(self.target_label, pixmap)
        except requests.exceptions.RequestException as e:
            logging.error(f"Image Download Failed: {e}")
class SubtitleDownloader(QRunnable):
    def __init__(self, url, session):
        super().__init__()
        self.url = url
        self.session = session
        self.signals = WorkerSignals()
    @Slot()
    def run(self):
        try:
            response = self.session.get(self.url, timeout=10)
            response.raise_for_status()
            self.signals.subtitle_finished.emit(response.content.decode('utf-8', errors='ignore'), None)
        except requests.exceptions.RequestException as e:
            logging.error(f"Subtitle Download Failed: {e}")
            self.signals.subtitle_finished.emit(None, str(e))

# --- Media Widgets ---
class ClickablePosterWidget(QWidget):
    clicked = Signal(object)
    def __init__(self, media_data, threadpool, api_client, parent=None):
        super().__init__(parent)
        self.media_data = media_data
        self.threadpool = threadpool
        self.api_client = api_client
        self.setFixedSize(180, 300)
        self.image_downloader = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        self.poster_label = QLabel(self)
        self.poster_label.setAlignment(Qt.AlignCenter)
        self.poster_label.setStyleSheet("background-color: #222; border-radius: 5px;")
        self.poster_label.setFixedSize(170, 255)
        title = media_data.get('title') or media_data.get('name', 'No Title')
        self.title_label = QLabel(title, self)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setWordWrap(True)
        layout.addWidget(self.poster_label)
        layout.addWidget(self.title_label)
        self.setCursor(Qt.PointingHandCursor)
        self.load_poster()
    def mousePressEvent(self, event):
        self.clicked.emit(self.media_data)
    def load_poster(self):
        poster_path = self.media_data.get('poster_image_src') or self.media_data.get('poster_path')
        if not poster_path:
            return
        full_url = self.api_client.base_url + poster_path if poster_path.startswith('/') else poster_path
        session = self.api_client.session if poster_path.startswith('/') else None
        self.image_downloader = ImageDownloader(full_url, self.poster_label, session=session)
        self.image_downloader.signals.image_finished.connect(self.set_poster_image)
        self.threadpool.start(self.image_downloader)
    @Slot(QLabel, QPixmap)
    def set_poster_image(self, target_label, pixmap):
        target_label.setPixmap(pixmap.scaled(target_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

def format_time(ms):
    s = round(ms / 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

class MediaDetailWidget(QWidget):
    play_video = Signal(object, object)
    go_back = Signal()
    episode_selected = Signal(object)
    def __init__(self, parent=None):
        super().__init__(parent)
        self.api_client = None
        self.threadpool = None
        self.media_data = None
        self.main_layout = QVBoxLayout(self)
        self.seasons = {}
        self.image_downloader = None
        self._setup_ui()
    def _setup_ui(self):
        self.back_button = QPushButton("← Back to Browser")
        self.main_layout.addWidget(self.back_button)
        self.header = QLabel()
        self.header.setWordWrap(True)
        self.main_layout.addWidget(self.header)
        body_layout = QHBoxLayout()
        self.poster_label = QLabel()
        self.poster_label.setFixedSize(300, 450)
        self.poster_label.setAlignment(Qt.AlignCenter)
        self.poster_label.setStyleSheet("background-color: #222; border-radius: 5px;")
        body_layout.addWidget(self.poster_label)
        details_container = QWidget()
        self.details_layout = QVBoxLayout(details_container)
        body_layout.addWidget(details_container, 1)
        self.overview_label = QLabel()
        self.overview_label.setWordWrap(True)
        self.overview_label.setAlignment(Qt.AlignTop)
        self.details_layout.addWidget(self.overview_label)
        self.season_label = QLabel("<b>Seasons:</b>")
        self.season_selector = QComboBox()
        self.episode_list = QListWidget()
        self.details_layout.addWidget(self.season_label)
        self.details_layout.addWidget(self.season_selector)
        self.details_layout.addWidget(self.episode_list)
        self.details_layout.addStretch()
        self.subtitle_label = QLabel("<b>Subtitles:</b>")
        self.subtitle_selector = QComboBox()
        self.details_layout.addWidget(self.subtitle_label)
        self.details_layout.addWidget(self.subtitle_selector)
        self.play_button = QPushButton("▶ Play")
        self.play_button.setFixedHeight(50)
        self.play_button.setStyleSheet("font-size: 20px; font-weight: bold;")
        self.details_layout.addWidget(self.play_button)
        self.main_layout.addLayout(body_layout)
    def set_context(self, api_client, threadpool):
        self.api_client = api_client
        self.threadpool = threadpool
        self.back_button.clicked.connect(self.go_back.emit)
        self.play_button.clicked.connect(self._on_play_clicked)
        self.season_selector.currentIndexChanged.connect(self.update_episode_list)
        self.episode_list.itemClicked.connect(self.on_episode_clicked)
    def set_media(self, media_data):
        self.media_data = media_data
        self.update_details()
    def update_details(self):
        title = self.media_data.get('title') or self.media_data.get('name', 'No Title')
        self.header.setText(f"<h1>{title}</h1>")
        self.overview_label.setText(f"<h3>Synopsis</h3><p>{self.media_data.get('overview', 'No description available.')}</p>")
        self.poster_label.clear()
        poster_path = self.media_data.get('poster_image_src') or self.media_data.get('poster_path') or self.media_data.get('still_path')
        if poster_path:
            is_external = poster_path.startswith('http') or not poster_path.startswith('/')
            session = self.api_client.session if not is_external else None
            full_url = self.api_client.base_url + poster_path if not is_external else poster_path
            if self.media_data.get('still_path') and not poster_path.startswith('http'):
                 full_url = self.api_client.tmdb_image_base_url + 'w300' + poster_path
                 session = None
            self.image_downloader = ImageDownloader(full_url, self.poster_label, session=session)
            self.image_downloader.signals.image_finished.connect(self.set_poster_image)
            self.threadpool.start(self.image_downloader)
        is_tv_show = self.media_data.get('mediaType') == 'tvShow'
        self.play_button.setVisible(not is_tv_show)
        self.season_label.setVisible(is_tv_show)
        self.season_selector.setVisible(is_tv_show)
        self.episode_list.setVisible(is_tv_show)
        self.populate_subtitles()
        if is_tv_show:
            self.populate_show_details()
    def populate_show_details(self):
        self.seasons.clear()
        self.season_selector.clear()
        episodes = self.media_data.get('episodes', [])
        for episode in episodes:
            season_num = episode.get('season_number', 0)
            if season_num not in self.seasons:
                self.seasons[season_num] = []
            self.seasons[season_num].append(episode)
        if self.seasons:
            for season_num in sorted(self.seasons.keys()):
                self.season_selector.addItem(f"Season {season_num}", userData=season_num)
            self.update_episode_list()
    def update_episode_list(self):
        season_num = self.season_selector.currentData()
        self.episode_list.clear()
        if season_num is not None and season_num in self.seasons:
            for episode in sorted(self.seasons[season_num], key=lambda x: x.get('episode_number', 0)):
                item_text = f"E{episode.get('episode_number', 0):02d}: {episode.get('name', 'Untitled')}"
                list_item = QListWidgetItem(item_text)
                list_item.setData(Qt.UserRole, episode)
                self.episode_list.addItem(list_item)
    def on_episode_clicked(self, item):
        episode_data = item.data(Qt.UserRole)
        episode_data['is_episode'] = True
        self.episode_selected.emit(episode_data)
    @Slot(QLabel, QPixmap)
    def set_poster_image(self, target_label, pixmap):
        target_label.setPixmap(pixmap.scaled(target_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
    def populate_subtitles(self):
        self.subtitle_selector.clear()
        subtitles = self.media_data.get('subtitles', [])
        is_visible = bool(subtitles) and not self.media_data.get('mediaType') == 'tvShow'
        self.subtitle_label.setVisible(is_visible)
        self.subtitle_selector.setVisible(is_visible)
        if is_visible:
            self.subtitle_selector.addItem("No Subtitle", userData=None)
            for sub in subtitles:
                filename = sub.get('originalFilename', f"Subtitle ID: {sub.get('id')}")
                self.subtitle_selector.addItem(filename, userData=sub)
    @Slot()
    def _on_play_clicked(self):
        selected_sub = self.subtitle_selector.currentData()
        self.play_video.emit(self.media_data, selected_sub)

class ClickableGraphicsView(QGraphicsView):
    clicked = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("border: 0px; background-color: black;")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)

class SubtitleLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setWordWrap(True)
        self.setFixedHeight(100)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setStyleSheet("""
            background-color: black;
            color: white;
            font-size: 20px;
            font-weight: bold;
            padding: 10px 15px;
        """)
        self.setVisible(False)

class VideoPlayerWidget(QWidget):
    go_back = Signal()
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.audio_output = QAudioOutput(self)
        self.media_player = QMediaPlayer(self)
        self.parsed_subtitles = []
        self.current_subtitle_index = 0

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.scene = QGraphicsScene(self)
        self.view = ClickableGraphicsView(self.scene)

        self.video_item = QGraphicsVideoItem()
        self.scene.addItem(self.video_item)
        self.media_player.setVideoOutput(self.video_item)
        self.media_player.setAudioOutput(self.audio_output)
        
        self.subtitle_label = SubtitleLabel(self) 
        
        self.overlay_label = QLabel("Press ESC to exit fullscreen")
        self.overlay_label.setAlignment(Qt.AlignCenter)
        self.overlay_label.setStyleSheet("background-color: rgba(0, 0, 0, 180); color: white; font-size: 18px; padding: 10px; border-radius: 5px;")
        self.overlay_proxy = QGraphicsProxyWidget()
        self.overlay_proxy.setWidget(self.overlay_label)
        self.overlay_proxy.setZValue(2)
        self.overlay_proxy.setVisible(False)
        self.scene.addItem(self.overlay_proxy)

        self.controls_container = QWidget(self)
        controls_layout = QHBoxLayout(self.controls_container)
        controls_layout.setContentsMargins(10, 5, 10, 5)
        
        self.play_pause_button = QPushButton(self)
        self.play_pause_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.progress_slider = QSlider(Qt.Orientation.Horizontal)
        self.time_label = QLabel("00:00 / 00:00")
        self.back_button = QPushButton("← Back")
        self.volume_button = QPushButton()
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setMaximumWidth(150)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.fullscreen_button = QPushButton()
        self.fullscreen_button.setCheckable(True)
        self.fullscreen_button.setToolTip("Toggle Fullscreen")
        
        if assets and hasattr(assets, 'FULLSCREEN_ENTER_B64'):
            try:
                enter_icon_data = base64.b64decode(assets.FULLSCREEN_ENTER_B64)
                self.enter_fullscreen_pixmap = QPixmap(); self.enter_fullscreen_pixmap.loadFromData(enter_icon_data)
                self.enter_fullscreen_icon = QIcon(self.enter_fullscreen_pixmap)
                exit_icon_data = base64.b64decode(assets.FULLSCREEN_EXIT_B64)
                self.exit_fullscreen_pixmap = QPixmap(); self.exit_fullscreen_pixmap.loadFromData(exit_icon_data)
                self.exit_fullscreen_icon = QIcon(self.exit_fullscreen_pixmap)
                self.fullscreen_button.setIcon(self.enter_fullscreen_icon)
            except Exception as e:
                print(f"[!] Could not load fullscreen icons: {e}")
                self.fullscreen_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMaxButton))
        else:
            self.fullscreen_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMaxButton))
        
        controls_layout.addWidget(self.play_pause_button)
        controls_layout.addWidget(self.progress_slider)
        controls_layout.addWidget(self.time_label)
        controls_layout.addSpacerItem(QSpacerItem(20, 10, QSizePolicy.Expanding, QSizePolicy.Minimum))
        controls_layout.addWidget(self.back_button)
        controls_layout.addSpacerItem(QSpacerItem(20, 10, QSizePolicy.Expanding, QSizePolicy.Minimum))
        controls_layout.addWidget(self.volume_button)
        controls_layout.addWidget(self.volume_slider)
        controls_layout.addWidget(self.fullscreen_button)

        main_layout.addWidget(self.view, 1)
        main_layout.addWidget(self.subtitle_label) 
        main_layout.addWidget(self.controls_container)

        self.overlay_timer = QTimer(self)
        self.overlay_timer.setInterval(5000)
        self.overlay_timer.setSingleShot(True)
        self.overlay_timer.timeout.connect(self.overlay_proxy.hide)
        
        self.back_button.clicked.connect(self.handle_back_button)
        self.play_pause_button.clicked.connect(self.toggle_playback)
        self.progress_slider.sliderMoved.connect(self.set_position)
        self.volume_button.clicked.connect(self.toggle_mute)
        self.volume_slider.valueChanged.connect(self.set_volume)
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen_mode)
        
        self.audio_output.volumeChanged.connect(lambda v: self.volume_slider.setValue(int(v * 100)))
        self.audio_output.mutedChanged.connect(self.update_volume_icon)
        self.update_volume_icon()

        self.media_player.playbackStateChanged.connect(self.update_play_pause_icon)
        self.media_player.positionChanged.connect(self.handle_position_changed)
        self.media_player.durationChanged.connect(self.update_duration)
        self.view.clicked.connect(self.toggle_controls_visibility)

    def play_from_device(self, device):
        self.controls_container.setVisible(True)
        self.media_player.setSourceDevice(device)
        self.media_player.play()

    def stop_playback(self):
        self.media_player.stop()
        self.media_player.setSourceDevice(None)

    def handle_back_button(self):
        self.stop_playback()
        self.go_back.emit()

    def resizeEvent(self, event):
        """Fit video in view and position overlays."""
        super().resizeEvent(event)
        
        if not self.video_item.nativeSize().isEmpty():
            self.scene.setSceneRect(self.video_item.boundingRect())
            self.view.fitInView(self.video_item, Qt.KeepAspectRatio)
        
        scene_rect = self.view.sceneRect()
        if scene_rect.isEmpty():
            return

        self.overlay_label.adjustSize()
        self.overlay_proxy.setPos(
            (scene_rect.width() - self.overlay_label.width()) / 2,
            (scene_rect.height() - self.overlay_label.height()) / 2
        )

    def toggle_controls_visibility(self):
        is_visible = self.controls_container.isVisible()
        self.controls_container.setVisible(not is_visible)

    def toggle_playback(self):
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def update_play_pause_icon(self, state):
        icon = QStyle.StandardPixmap.SP_MediaPause if state == QMediaPlayer.PlaybackState.PlayingState else QStyle.StandardPixmap.SP_MediaPlay
        self.play_pause_button.setIcon(self.style().standardIcon(icon))

    @Slot(int)
    def handle_position_changed(self, position):
        self.update_progress(position)
        self.update_subtitle_display(position)

    def update_progress(self, position):
        if not self.progress_slider.isSliderDown():
            self.progress_slider.setValue(position)
        self.update_time_label(position)

    def update_duration(self, duration):
        self.progress_slider.setRange(0, duration)
        video_size = self.video_item.nativeSize()
        if not video_size.isEmpty():
            self.video_item.setSize(video_size)
            self.resizeEvent(None)

    def update_time_label(self, position=None):
        pos = position if position is not None else self.media_player.position()
        self.time_label.setText(f"{format_time(pos)} / {format_time(self.media_player.duration())}")

    def set_position(self, position):
        self.media_player.setPosition(position)

    def toggle_fullscreen_mode(self, checked):
        self.main_window.toggle_fullscreen(checked)

    def set_volume(self, value):
        self.audio_output.setVolume(value / 100.0)

    def toggle_mute(self):
        self.audio_output.setMuted(not self.audio_output.isMuted())

    def update_volume_icon(self):
        icon = QStyle.StandardPixmap.SP_MediaVolumeMuted if self.audio_output.isMuted() or self.volume_slider.value() == 0 else QStyle.StandardPixmap.SP_MediaVolume
        self.volume_button.setIcon(self.style().standardIcon(icon))

    def set_subtitles(self, subtitles):
        self.parsed_subtitles = subtitles
        self.current_subtitle_index = 0
        self.subtitle_label.setVisible(bool(self.parsed_subtitles))

    def clear_subtitles(self):
        self.parsed_subtitles = []
        self.subtitle_label.setText("")
        self.subtitle_label.setVisible(False)

    def update_subtitle_display(self, current_ms):
        if not self.parsed_subtitles or not self.subtitle_label.isVisible():
            if self.subtitle_label.text():
                 self.subtitle_label.setText("")
            return
            
        found_sub = False
        for i, (start_ms, end_ms, text) in enumerate(self.parsed_subtitles):
            if start_ms <= current_ms <= end_ms:
                if self.subtitle_label.text() != text:
                    self.subtitle_label.setText(text)
                found_sub = True
                break
        
        if not found_sub and self.subtitle_label.text():
            self.subtitle_label.setText("")

class BrowserWidget(QWidget):
    poster_clicked = Signal(object)
    PAGE_SIZE = 50
    def __init__(self, parent=None):
        super().__init__(parent)
        self.api_client = None
        self.threadpool = None
        self.status_bar = None
        self.active_worker_threads = []
        self.current_page = 1
        self.total_items = 0
        self.current_list_type = None
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(300)
        self.search_timer.timeout.connect(self._perform_search)
        self.initUI()
    def set_context(self, api_client, threadpool, status_bar):
        self.api_client = api_client
        self.threadpool = threadpool
        self.status_bar = status_bar
    def initUI(self):
        main_layout = QVBoxLayout(self)
        top_bar = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search automatically...")
        self.search_input.textChanged.connect(self.handle_search_text_changed)
        show_movies_button = QPushButton("Show All Movies")
        show_movies_button.clicked.connect(self.load_all_movies)
        show_tv_button = QPushButton("Show All TV Shows")
        show_tv_button.clicked.connect(self.load_all_shows)
        show_generic_button = QPushButton("Show Generic Videos")
        show_generic_button.clicked.connect(self.load_all_generic)
        top_bar.addWidget(self.search_input)
        top_bar.addStretch()
        top_bar.addWidget(show_movies_button)
        top_bar.addWidget(show_tv_button)
        top_bar.addWidget(show_generic_button)
        main_layout.addLayout(top_bar)
        self.list_header = QLabel("")
        self.list_header.setStyleSheet("font-size: 16px; font-weight: bold; margin: 5px;")
        main_layout.addWidget(self.list_header)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.poster_container = QWidget()
        self.poster_grid = QGridLayout(self.poster_container)
        self.poster_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll_area.setWidget(self.poster_container)
        main_layout.addWidget(self.scroll_area)
        self.pagination_bar = QWidget()
        self.pagination_bar.setVisible(False)
        pagination_layout = QHBoxLayout(self.pagination_bar)
        self.back_to_continue_button = QPushButton("← Back to Continue Watching")
        self.back_to_continue_button.clicked.connect(self.load_continue_watching)
        self.prev_page_button = QPushButton("Previous")
        self.prev_page_button.clicked.connect(lambda: self.go_to_page(self.current_list_type, self.current_page - 1))
        self.page_label = QLabel("")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.next_page_button = QPushButton("Next")
        self.next_page_button.clicked.connect(lambda: self.go_to_page(self.current_list_type, self.current_page + 1))
        pagination_layout.addWidget(self.back_to_continue_button)
        pagination_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Expanding, QSizePolicy.Minimum))
        pagination_layout.addWidget(self.prev_page_button)
        pagination_layout.addWidget(self.page_label)
        pagination_layout.addWidget(self.next_page_button)
        main_layout.addWidget(self.pagination_bar)

    @Slot(QObject)
    def _worker_finished(self, worker):
        """Removes a worker from this widget's active list."""
        try:
            self.active_worker_threads.remove(worker)
        except ValueError:
            pass

    def load_initial_content(self):
        self.status_bar.showMessage("Fetching dashboard content...")
        self.load_continue_watching(is_initial_load=True)
    def load_continue_watching(self, is_initial_load=False):
        self.current_list_type = 'continue_watching'
        self.status_bar.showMessage("Fetching 'Continue Watching' list...")
        self.clear_grid()
        self.list_header.setText("Continue Watching")
        self.pagination_bar.setVisible(False)
        worker = FetchContinueWatchingWorker(self.api_client)
        worker.signals.continue_watching_finished.connect(lambda v, e: self.populate_grid_from_list(v, e, is_initial_load))
        worker.signals.fetch_error.connect(self.on_fetch_error)
        worker.signals.continue_watching_finished.connect(lambda: self._worker_finished(worker))
        self.active_worker_threads.append(worker)
        self.threadpool.start(worker)
    def load_all_movies(self):
        self.current_page = 0
        self.total_items = 0
        self.go_to_page('movies', 1)
    def load_all_shows(self):
        self.current_page = 0
        self.total_items = 0
        self.go_to_page('shows', 1)
    def load_all_generic(self):
        self.current_page = 0
        self.total_items = 0
        self.go_to_page('generic', 1)
    @Slot(str)
    def handle_search_text_changed(self, text):
        self.search_timer.stop()
        if len(text) >= 2:
            self.search_timer.start()
        elif not text:
            self.load_continue_watching()
    def _perform_search(self):
        query = self.search_input.text()
        if len(query) < 2:
            return
        self.current_list_type = 'search'
        self.status_bar.showMessage(f"Searching for '{query}'...")
        self.clear_grid()
        self.list_header.setText(f"Search Results for '{query}'")
        self.pagination_bar.setVisible(False)
        worker = SearchWorker(self.api_client, query)
        worker.signals.search_finished.connect(self.populate_from_search)
        worker.signals.fetch_error.connect(self.on_fetch_error)
        worker.signals.search_finished.connect(lambda: self._worker_finished(worker))
        self.active_worker_threads.append(worker)
        self.threadpool.start(worker)
    def go_to_page(self, media_type, page_num):
        self.current_page = page_num
        offset = (page_num - 1) * self.PAGE_SIZE
        self.status_bar.showMessage(f"Fetching page {page_num} of {media_type}...")
        self.clear_grid()
        self.list_header.setText(f"All {media_type.replace('_', ' ').title()}")
        worker = FetchPageWorker(self.api_client, media_type, offset=offset, max_items=self.PAGE_SIZE)
        worker.signals.page_finished.connect(self.populate_page)
        worker.signals.fetch_error.connect(self.on_fetch_error)
        worker.signals.page_finished.connect(lambda: self._worker_finished(worker))
        self.active_worker_threads.append(worker)
        self.threadpool.start(worker)
    def clear_grid(self):
        while self.poster_grid.count():
            item = self.poster_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
    @Slot(list, str, bool)
    def populate_grid_from_list(self, media_list, error, is_initial_load=False):
        if error:
            self.on_fetch_error(error)
            return
        self.add_items_to_grid(media_list)
        if not media_list and is_initial_load:
            self.load_all_movies()
        elif not media_list:
            self.list_header.setText("Nothing here!")
        self.status_bar.showMessage(f"Displaying {len(media_list)} items.", 5000)
    @Slot(str, object, str)
    def populate_page(self, media_type, data, error):
        if error:
            self.on_fetch_error(error)
            return
        self.total_items = data.get('total', 0)
        media_list = data.get('list', [])
        if not media_list and self.current_page == 1:
            if media_type == 'movies': self.load_all_shows()
            elif media_type == 'shows': self.load_all_generic()
            else: self.list_header.setText("No content found on the server.")
            return
        self.add_items_to_grid(media_list)
        self.update_pagination_controls()
    @Slot(object, str)
    def populate_from_search(self, data, error):
        if error:
            self.on_fetch_error(error)
            return
        all_media = data.get('movies', []) + data.get('shows', []) + data.get('genericVideos', [])
        self.add_items_to_grid(all_media)
        self.status_bar.showMessage(f"Found {len(all_media)} results.", 5000)
    def add_items_to_grid(self, media_list):
        columns = max(1, self.width() // 200)
        for i, media in enumerate(media_list):
            if not media:
                continue
            poster_widget = ClickablePosterWidget(media, self.threadpool, self.api_client)
            poster_widget.clicked.connect(self.poster_clicked.emit)
            self.poster_grid.addWidget(poster_widget, i // columns, i % columns)
    def update_pagination_controls(self):
        self.pagination_bar.setVisible(True)
        total_pages = (self.total_items + self.PAGE_SIZE - 1) // self.PAGE_SIZE
        self.page_label.setText(f"Page {self.current_page} of {total_pages}")
        self.prev_page_button.setEnabled(self.current_page > 1)
        self.next_page_button.setEnabled(self.current_page < total_pages)
        self.status_bar.showMessage(f"Displaying page {self.current_page}. Total items: {self.total_items}", 5000)
    @Slot(str)
    def on_fetch_error(self, error):
        self.status_bar.showMessage(f"Error: {error}", 5000)
        QMessageBox.critical(self, "Error", f"Could not fetch list:\n{error}")

# --- Main Window ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Streama Movie Browser")
        self.resize(1024, 768)
        self.api_client = StreamaAPIClient()
        self.settings = load_settings()
        self.threadpool = QThreadPool()
        self.active_workers = []
        self.network_manager = None
        self.network_reply = None
        self.download_buffer = None
        self.player_buffer_device = None
        self.playback_started = False
        self.buffer_target = 0
        self.is_cleaning_up = False
        self.original_window_flags = self.windowFlags()
        
        self.browser_widget = None
        self.details_widget = None
        self.player_widget = None
        
        # --- MODIFIED --- Add the flag for the one-time workaround
        self.is_first_playback = True

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
        welcome_layout.setAlignment(Qt.AlignCenter)
        welcome_text = QLabel("Welcome to Streama Browser!\nPlease configure your server and log in.")
        welcome_text.setStyleSheet("font-size: 20px; color: white;")
        welcome_text.setAlignment(Qt.AlignCenter)

        welcome_image = QLabel()
        welcome_image.setAlignment(Qt.AlignCenter)
        if assets and hasattr(assets, 'STREAMA_JPG_B64'):
            try:
                image_data = base64.b64decode(assets.STREAMA_JPG_B64)
                image_pixmap = QPixmap()
                image_pixmap.loadFromData(image_data)
                scaled_pixmap = image_pixmap.scaled(800, 600, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                welcome_image.setPixmap(scaled_pixmap)
            except Exception as e:
                print(f"[!] Could not load welcome image: {e}")
                welcome_image.setText("(Welcome Image Not Found)")

        welcome_layout.addStretch()
        welcome_layout.addWidget(welcome_text)
        welcome_layout.addWidget(welcome_image)
        welcome_layout.addStretch()
        self.stacked_widget.addWidget(self.welcome_widget)
        
        self.setStatusBar(QStatusBar(self))
        self.setup_menu()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.isFullScreen():
            if self.player_widget:
                self.player_widget.fullscreen_button.setChecked(False)
                self.player_widget.toggle_fullscreen_mode(False)
        else:
            super().keyPressEvent(event)

    def toggle_fullscreen(self, checked):
        if checked:
            self.original_window_flags = self.windowFlags()
            self.menuBar().setVisible(False)
            self.statusBar().setVisible(False)
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
            self.showFullScreen()
            if self.player_widget:
                self.player_widget.controls_container.setVisible(False)
                self.setCursor(Qt.BlankCursor)
                self.player_widget.overlay_proxy.show()
                self.player_widget.overlay_timer.start()
        else:
            self.setWindowFlags(self.original_window_flags)
            self.menuBar().setVisible(True)
            self.statusBar().setVisible(True)
            self.showNormal()
            if self.player_widget:
                self.player_widget.controls_container.setVisible(True)
                self.setCursor(Qt.ArrowCursor)
                self.player_widget.overlay_proxy.hide()
                self.player_widget.overlay_timer.stop()

    def setup_menu(self):
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&File")
        self.login_action = QAction("Login", self)
        self.login_action.triggered.connect(self.handle_login_click)
        self.logout_action = QAction("Logout", self)
        self.logout_action.triggered.connect(self.handle_logout_click)
        self.settings_action = QAction("Settings...", self)
        self.settings_action.triggered.connect(self.open_settings_dialog)
        view_log_action = QAction("View Error Log", self)
        view_log_action.triggered.connect(self.view_error_log)
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(self.login_action)
        file_menu.addAction(self.logout_action)
        file_menu.addSeparator()
        file_menu.addAction(self.settings_action)
        file_menu.addAction(view_log_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

    @Slot()
    def view_error_log(self):
        log_path = os.path.abspath(LOG_FILE)
        if os.path.exists(log_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(log_path))
        else:
            QMessageBox.information(self, "Log File", "Log file is empty or does not exist yet.")

    def open_settings_dialog(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
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
        if error or not config_data or not config_data.get('key'):
            logging.warning(f"Could not load TMDB config. Error: {error}")
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
            self.details_widget.play_video.connect(self.play_video)
            self.details_widget.go_back.connect(self.show_browser)
            self.details_widget.episode_selected.connect(self.show_details)
            self.details_widget.set_context(self.api_client, self.threadpool)

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
    
    # --- MODIFIED --- This is the new function that handles the one-time resize workaround.
    @Slot(QMediaPlayer.PlaybackState)
    def _handle_first_play_resize(self, state):
        if self.is_first_playback and state == QMediaPlayer.PlaybackState.PlayingState:
            self.is_first_playback = False # Set flag to false so this never runs again.

            def force_resize():
                print("[*] Forcing initial resize via window move workaround (1-second delay).")
                pos = self.pos()
                self.move(pos.x() + 1, pos.y())
                self.move(pos)
                # Clean up the connection so this slot isn't called unnecessarily anymore.
                if self.player_widget:
                    try:
                        self.player_widget.media_player.playbackStateChanged.disconnect(self._handle_first_play_resize)
                    except (TypeError, RuntimeError):
                        pass # It might already be disconnected, which is fine.
            
            # Use a short delay to ensure the first frame is painted before moving the window.
            QTimer.singleShot(1000, force_resize)

    @Slot(object, object)
    def play_video(self, media_data, selected_subtitle=None):
        if not self.player_widget:
            self.player_widget = VideoPlayerWidget(self)
            self.stacked_widget.addWidget(self.player_widget)
            self.player_widget.go_back.connect(self.go_from_player_to_details)
            # --- MODIFIED --- Connect the one-shot resize handler only when the player is first created.
            if self.is_first_playback:
                self.player_widget.media_player.playbackStateChanged.connect(self._handle_first_play_resize)

        self.cleanup_after_playback()
        self.is_cleaning_up = False
        self.playback_started = False
        self.download_buffer = QByteArray()
        self.player_widget.clear_subtitles()

        if selected_subtitle:
            sub_id = selected_subtitle.get('id')
            if sub_id:
                sub_url, _ = self.api_client.get_stream_url(sub_id, extension='srt')
                if sub_url:
                    print(f"[*] User selected subtitle, downloading from: {sub_url}")
                    sub_worker = SubtitleDownloader(sub_url, self.api_client.session)
                    sub_worker.signals.subtitle_finished.connect(self.on_subtitle_loaded)
                    sub_worker.signals.subtitle_finished.connect(lambda: self._worker_finished(sub_worker))
                    self.active_workers.append(sub_worker)
                    self.threadpool.start(sub_worker)

        video_files = media_data.get('videoFiles', [])
        if not video_files:
            QMessageBox.critical(self, "Error", "No video file found for this item.")
            return
        stream_url, error = self.api_client.get_stream_url(video_files[0].get('id'))
        if error or not stream_url:
            QMessageBox.critical(self, "Error", f"Could not get video stream URL:\n{error or 'No file ID.'}")
            return
        cookies = self.api_client.session.cookies
        cookie_header = "; ".join([f"{n}={v}" for n,v in cookies.items()])
        if not cookie_header:
            QMessageBox.critical(self, "Error", "Login session cookie not found.")
            return
        self.network_manager = QNetworkAccessManager()
        self.request = QNetworkRequest(QUrl(stream_url))
        self.request.setRawHeader(b'Cookie', cookie_header.encode())
        self.network_reply = self.network_manager.get(self.request)
        self.network_reply.readyRead.connect(self.append_to_buffer)
        self.network_reply.errorOccurred.connect(self.on_stream_error)
        
        use_buffer = self.settings.get("use_buffer", True)
        if use_buffer:
            buffer_mb = self.settings.get("buffer_size_mb", 5)
            self.buffer_target = buffer_mb * 1024 * 1024
            print(f"[*] Using PRE-BUFFERING mode ({buffer_mb} MB target).")
            self.statusBar().showMessage(f"Buffering: 0.00 MB")
            self.network_reply.downloadProgress.connect(self.on_download_progress)
            self.network_reply.finished.connect(self.on_download_finished)
        else:
            self.buffer_target = 0
            print("[*] Using FULL DOWNLOAD mode.")
            self.statusBar().showMessage(f"Downloading: 0.00 MB")
            self.network_reply.downloadProgress.connect(self.on_full_download_progress)
            self.network_reply.finished.connect(self.on_robust_download_finished)

    def append_to_buffer(self):
        if self.network_reply and self.download_buffer is not None:
            self.download_buffer.append(self.network_reply.readAll())
        if self.settings.get("use_buffer", True) and not self.playback_started and self.download_buffer and self.download_buffer.size() >= self.buffer_target:
            self.start_playback()

    @Slot()
    def start_playback(self):
        if self.playback_started:
            return
        self.playback_started = True
        print(f"[*] Starting playback...")
        self.statusBar().showMessage("Starting playback...", 3000)
        
        self.player_buffer_device = QBuffer(self.download_buffer)
        self.player_buffer_device.open(QIODevice.ReadOnly)
        self.player_widget.play_from_device(self.player_buffer_device)
        self.stacked_widget.setCurrentWidget(self.player_widget)

    @Slot(str, str)
    def on_subtitle_loaded(self, subtitle_content, error):
        if error:
            self.statusBar().showMessage(f"Could not load subtitles: {error}", 4000)
            logging.error(f"Subtitle load failed: {error}")
            return
        if self.player_widget and subtitle_content:
            self.player_widget.set_subtitles(parse_subtitles(subtitle_content))
    def on_download_progress(self, bytes_rec, bytes_total):
        if not self.playback_started:
            self.statusBar().showMessage(f"Buffering: {bytes_rec / (1024*1024):.2f} MB")
    
    def on_full_download_progress(self, bytes_received, bytes_total):
        megabytes_rec = bytes_received / (1024 * 1024)
        if bytes_total > 0:
            megabytes_total = bytes_total / (1024 * 1024)
            self.statusBar().showMessage(f"Downloading video: {megabytes_rec:.2f} MB / {megabytes_total:.2f} MB")
        else:
            self.statusBar().showMessage(f"Downloading video: {megabytes_rec:.2f} MB")

    def on_download_finished(self):
        print("[*] Network download finished.")
        if not self.playback_started and self.download_buffer and self.download_buffer.size() > 0:
            self.start_playback()
    
    def on_robust_download_finished(self):
        print("[*] Full network download finished.")
        self.start_playback()

    def on_stream_error(self, error_code):
        if self.is_cleaning_up or not self.network_reply:
            return
        err_str = self.network_reply.errorString()
        print(f"[!] Video Stream Error: {err_str}")
        if error_code != QNetworkReply.NetworkError.OperationCanceledError:
            QMessageBox.critical(self, "Stream Error", f"Could not load video:\n{err_str}")
        self.cleanup_after_playback()
    @Slot()
    def cleanup_after_playback(self):
        if self.is_cleaning_up:
            return
        self.is_cleaning_up = True
        print("[*] Playback session cleanup started...")
        if self.player_widget:
            self.player_widget.clear_subtitles()
        if self.playback_started:
            if self.player_widget:
                self.player_widget.stop_playback()
        reply = self.network_reply
        if reply:
            self.network_reply = None
            reply.abort()
            reply.deleteLater()
        if self.player_buffer_device:
            self.player_buffer_device.close()
            self.player_buffer_device = None
        self.download_buffer = None
        self.playback_started = False
        print("[*] Playback session cleaned up.")
    @Slot()
    def go_from_player_to_details(self):
        if self.isFullScreen():
            if self.player_widget:
                self.player_widget.fullscreen_button.setChecked(False)
            self.toggle_fullscreen(False)
        self.cleanup_after_playback()
        if self.details_widget:
            self.stacked_widget.setCurrentWidget(self.details_widget)
    @Slot()
    def show_browser(self):
        self.cleanup_after_playback()
        if self.browser_widget:
            self.stacked_widget.setCurrentWidget(self.browser_widget)
    def handle_logout_click(self):
        self.cleanup_after_playback()
        if self.browser_widget:
            self.browser_widget.clear_grid()
        self.stacked_widget.setCurrentWidget(self.welcome_widget)
        self.update_ui_state(logged_in=False)
        self.statusBar().showMessage("Logged out.", 3000)
    def update_ui_state(self, logged_in):
        self.login_action.setEnabled(not logged_in)
        self.logout_action.setEnabled(logged_in)
        self.settings_action.setEnabled(not logged_in)

    # --- MODIFIED --- This is the new function to handle safe shutdown.
    def closeEvent(self, event):
        """Safely clean up resources before closing."""
        print("[*] Close event triggered. Cleaning up...")
        self.cleanup_after_playback()
        self.threadpool.clear() # Clear any pending tasks
        self.threadpool.waitForDone() # Wait for active tasks to finish
        event.accept() # Allow the window to close

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.error("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))
    error_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    error_dialog = QMessageBox()
    error_dialog.setIcon(QMessageBox.Critical)
    error_dialog.setText("An unexpected error occurred.")
    error_dialog.setInformativeText(f"Please check the log file for details.\n\nLog file: {os.path.abspath(LOG_FILE)}")
    error_dialog.setStandardButtons(QMessageBox.Ok)
    error_dialog.setDetailedText(error_msg)
    error_dialog.exec()

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
    sys.exit(app.exec())
