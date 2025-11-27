import os
import requests
import logging
import tempfile
from PySide2.QtWidgets import (QWidget, QLabel, QVBoxLayout, QHBoxLayout, 
                               QDialog, QLineEdit, QCheckBox, QDialogButtonBox, 
                               QFormLayout, QPushButton, QScrollArea, QGridLayout, 
                               QSpacerItem, QSizePolicy, QComboBox, QListWidget, QListWidgetItem,
                               QSpinBox) # <--- Added QSpinBox
from PySide2.QtCore import Qt, Signal, Slot, QRunnable, QObject, QTimer
from PySide2.QtGui import QPixmap

# --- WORKER SIGNALS & THREADS ---
class WorkerSignals(QObject):
    login_finished = Signal(bool, str)
    page_finished = Signal(str, object, str)
    continue_watching_finished = Signal(list, str)
    search_finished = Signal(object, str)
    image_finished = Signal(QLabel, QPixmap)
    details_finished = Signal(object, str)
    config_finished = Signal(object, str)
    subtitle_downloaded = Signal(str)
    fetch_error = Signal(str)

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

class SubtitleDownloadWorker(QRunnable):
    def __init__(self, session, url):
        super().__init__()
        self.session = session
        self.url = url
        self.signals = WorkerSignals()
    @Slot()
    def run(self):
        try:
            print(f"[*] Downloading subtitle from: {self.url}")
            r = self.session.get(self.url)
            r.raise_for_status()
            fd, path = tempfile.mkstemp(suffix=".srt")
            with os.fdopen(fd, 'wb') as tmp:
                tmp.write(r.content)
            self.signals.subtitle_downloaded.emit(path)
        except Exception as e:
            print(f"[!] Subtitle download failed: {e}")
            self.signals.subtitle_downloaded.emit("")

# --- VISUAL WIDGETS ---

class SettingsDialog(QDialog):
    def __init__(self, current_settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Server Configuration")
        
        # --- Connection Settings ---
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
        
        # --- Subtitle Settings ---
        self.subSizeInput = QSpinBox()
        self.subSizeInput.setRange(10, 50)
        self.subSizeInput.setValue(int(current_settings.get("subtitle_size", 20)))
        self.subSizeInput.setSuffix(" px")
        
        self.subBoldCheck = QCheckBox("Bold Subtitles")
        self.subBoldCheck.setChecked(current_settings.get("subtitle_bold", False))

        formLayout = QFormLayout()
        formLayout.addRow(QLabel("<b>Server Connection</b>"))
        formLayout.addRow("Server IP or Domain:", self.serverInput)
        formLayout.addRow("Port:", self.portInput)
        formLayout.addRow(self.sslCheck)
        formLayout.addRow(self.insecureSslCheck)
        formLayout.addRow("Username:", self.usernameInput)
        formLayout.addRow("Password:", self.passwordInput)
        formLayout.addRow("TheMovieDB API Key:", self.tmdbInput)
        
        formLayout.addRow(QLabel("<b>Subtitle Appearance</b>"))
        formLayout.addRow("Font Size:", self.subSizeInput)
        formLayout.addRow(self.subBoldCheck)
        
        self.buttonBox = QDialogButtonBox()
        self.buttonBox.addButton(QDialogButtonBox.Save)
        self.buttonBox.addButton(QDialogButtonBox.Cancel)
        
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
            # Save new settings
            "subtitle_size": self.subSizeInput.value(),
            "subtitle_bold": self.subBoldCheck.isChecked()
        }

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
        self.poster_grid.setAlignment(Qt.AlignTop)
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
        if not isinstance(media_list, list):
            print(f"[!] Warning: Expected list, got {type(media_list)}. Resetting to empty.")
            media_list = []
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
