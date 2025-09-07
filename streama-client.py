import sys
import json
import os
import requests
import base64
from pathlib import Path
from PySide2.QtWidgets import (QApplication, QMainWindow, QDialog, QLineEdit, 
                               QCheckBox, QDialogButtonBox, QFormLayout, QVBoxLayout,
                               QAction, QMessageBox, QWidget, QStackedWidget, QLabel,
                               QSizePolicy)
from PySide2.QtWebEngineWidgets import QWebEngineView, QWebEngineProfile, QWebEnginePage
from PySide2.QtCore import (QUrl, Qt, QObject, Slot, QRunnable, QThreadPool, Signal, 
                              QFile, QTextStream, QIODevice, QEvent, QSize, 
                              qInstallMessageHandler)
from PySide2.QtWebChannel import QWebChannel
from PySide2.QtNetwork import QNetworkCookie
from PySide2.QtGui import QKeySequence, QPixmap, QIcon

try:
    import assets
except ImportError:
    print("FATAL: 'assets.py' not found. Please run 'compile_assets.py' first to generate it.")
    sys.exit(1)

SETTINGS_FILE = "settings.json"

def qt_message_handler(mode, context, message):
    if "QXcbConnection: XCB error: 3 (BadWindow)" in message: return
    print(f"Qt Message: {message}", file=sys.stderr)

def load_settings():
    defaults = {"server": "", "port": "8080", "ssl": False, "insecure_ssl": False, "username": "", "password": ""}
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings = json.load(f); defaults.update(settings); return defaults
    except (FileNotFoundError, json.JSONDecodeError):
        return defaults
def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f: json.dump(settings, f, indent=4)
class SettingsDialog(QDialog):
    def __init__(self, current_settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configure Streama Server")
        self.serverInput = QLineEdit(current_settings.get("server", ""))
        self.portInput = QLineEdit(current_settings.get("port", "8080"))
        self.sslCheck = QCheckBox("Use SSL (https://)")
        self.sslCheck.setChecked(current_settings.get("ssl", False))
        self.insecureSslCheck = QCheckBox("Ignore SSL Certificate Errors (for self-signed certs)")
        self.insecureSslCheck.setChecked(current_settings.get("insecure_ssl", False))
        self.usernameInput = QLineEdit(current_settings.get("username", ""))
        self.passwordInput = QLineEdit(current_settings.get("password", ""))
        self.passwordInput.setEchoMode(QLineEdit.EchoMode.Password)
        formLayout = QFormLayout()
        formLayout.addRow("Server IP or Domain:", self.serverInput); formLayout.addRow("Port:", self.portInput)
        formLayout.addRow(self.sslCheck); formLayout.addRow(self.insecureSslCheck)
        formLayout.addRow("Username:", self.usernameInput); formLayout.addRow("Password:", self.passwordInput)
        self.buttonBox = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttonBox.accepted.connect(self.accept); self.buttonBox.rejected.connect(self.reject)
        mainLayout = QVBoxLayout(); mainLayout.addLayout(formLayout); mainLayout.addWidget(self.buttonBox)
        self.setLayout(mainLayout)
    def get_settings(self):
        return {"server": self.serverInput.text().strip(), "port": self.portInput.text().strip(), "ssl": self.sslCheck.isChecked(), "insecure_ssl": self.insecureSslCheck.isChecked(), "username": self.usernameInput.text().strip(), "password": self.passwordInput.text()}

class CustomWebEnginePage(QWebEnginePage):
    def __init__(self, main_window, profile, parent=None):
        super().__init__(profile, parent)
        self.main_window = main_window
    def certificateError(self, error):
        if self.main_window.settings.get("insecure_ssl", False):
            print(f"[!] Ignoring SSL Certificate Error: {error.errorDescription()}"); error.acceptCertificate(); return True
        return False
    
    # --- THE KEY FIX: Suppress ALL JavaScript console messages ---
    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceId):
        """Override to suppress all console messages from the web page for a cleaner output."""
        return # Silently ignore all JS console messages

class LoginWorkerSignals(QObject):
    finished = Signal(bool, object, str)
class LoginWorker(QRunnable):
    def __init__(self, settings):
        super().__init__(); self.settings = settings; self.signals = LoginWorkerSignals()
    @Slot()
    def run(self):
        try:
            protocol = "https" if self.settings.get("ssl") else "http"
            base_url = f"{protocol}://{self.settings.get('server')}:{self.settings.get('port')}"
            login_url = f"{base_url}/login/authenticate"
            session = requests.Session(); session.verify = not self.settings.get("insecure_ssl")
            payload = {"username": self.settings.get("username"), "password": self.settings.get("password"), "remember_me": "on"}
            headers = {'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'}
            response = session.post(login_url, data=payload, headers=headers, timeout=10)
            response.raise_for_status(); data = response.json()
            if data.get("success"): self.signals.finished.emit(True, session, "Login successful!")
            else: self.signals.finished.emit(False, None, data.get('error', 'Invalid username or password.'))
        except requests.exceptions.Timeout: self.signals.finished.emit(False, None, "Connection timed out. Check server address and port.")
        except requests.exceptions.RequestException as e: self.signals.finished.emit(False, None, f"Connection error: {e}")
        except Exception as e: self.signals.finished.emit(False, None, f"An unknown error occurred: {e}")
class JsBridge(QObject):
    def __init__(self, main_window):
        super().__init__(); self.main_window = main_window
    @Slot()
    def toggle_fullscreen_from_js(self):
        self.main_window.toggle_fullscreen()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("Streama Client");
        if hasattr(assets, 'STREAMA_ICO_B64') and assets.STREAMA_ICO_B64:
            pixmap = QPixmap(); pixmap.loadFromData(base64.b64decode(assets.STREAMA_ICO_B64)); self.setWindowIcon(QIcon(pixmap))
        self.settings = load_settings(); self.is_fullscreen = False; self.threadpool = QThreadPool()
        self.qwebchannel_js_content = ""; self._load_qwebchannel_js()
        self.original_pixmap = None
        self.initUI()
        self.show_placeholder_page()

    def initUI(self):
        self.placeholder_widget = QWidget(); self.placeholder_widget.setStyleSheet("background-color: #2d2d2d;")
        placeholder_layout = QVBoxLayout(self.placeholder_widget)
        self.image_label = QLabel(); self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.welcome_label = QLabel(); self.welcome_label.setAlignment(Qt.AlignCenter)
        self.welcome_label.setStyleSheet("color: white; font-size: 24px; font-weight: bold;")
        placeholder_layout.addWidget(self.image_label, 1); placeholder_layout.addWidget(self.welcome_label, 0)
        self.browser = QWebEngineView(); self.profile = QWebEngineProfile()
        self.custom_page = CustomWebEnginePage(main_window=self, profile=self.profile)
        self.browser.setPage(self.custom_page)
        self.js_bridge = JsBridge(self); self.channel = QWebChannel()
        self.channel.registerObject("pyBridge", self.js_bridge); self.browser.page().setWebChannel(self.channel)
        self.browser.loadFinished.connect(self.inject_js_bridge)
        self.stacked_widget = QStackedWidget()
        self.stacked_widget.addWidget(self.placeholder_widget); self.stacked_widget.addWidget(self.browser)
        self.setCentralWidget(self.stacked_widget)
        self.setup_menu_and_actions()

    def show_placeholder_page(self):
        if hasattr(assets, 'STREAMA_JPG_B64') and assets.STREAMA_JPG_B64:
            self.original_pixmap = QPixmap(); self.original_pixmap.loadFromData(base64.b64decode(assets.STREAMA_JPG_B64))
            self.image_label.setVisible(True)
            self.welcome_label.setText("<h2>Welcome to Streama Client</h2><p>Please log in via the Login menu.</p>")
            self.update_placeholder_image()
        else:
            self.original_pixmap = None; self.image_label.setVisible(False)
            self.welcome_label.setText("<h1>Welcome to Streama Client</h1><p>Please configure your server via Settings -> Configure Server.</p><p><i>(assets.py not found or streama.jpg missing)</i></p>")
        self.stacked_widget.setCurrentIndex(0); self.update_ui_state(logged_in=False)
        self.setWindowTitle("Streama Client")
        
    def update_placeholder_image(self):
        if self.original_pixmap and self.image_label.isVisible():
            scaled_pixmap = self.original_pixmap.scaled(self.image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image_label.setPixmap(scaled_pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.stacked_widget.currentIndex() == 0: self.update_placeholder_image()

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange:
            if self.windowState() & Qt.WindowMaximized:
                screen = self.screen();
                if screen: self.setMaximumSize(screen.availableSize())
            else: self.setMaximumSize(16777215, 16777215)
        super().changeEvent(event)

    def update_ui_state(self, logged_in):
        self.login_action.setVisible(not logged_in); self.logout_action.setVisible(logged_in)
        self.settings_action.setEnabled(not logged_in)

    def handle_login_click(self):
        if not self.settings.get("server") or not self.settings.get("username"):
            self.open_settings_dialog(); return
        self.login_action.setEnabled(False); self.settings_action.setEnabled(False) 
        self.statusBar().showMessage("Connecting..."); QApplication.processEvents()
        login_worker = LoginWorker(self.settings); login_worker.signals.finished.connect(self.on_login_finished)
        self.threadpool.start(login_worker)

    def on_login_finished(self, success, session, message):
        self.login_action.setEnabled(True)
        self.statusBar().showMessage(message)
        protocol = "https" if self.settings.get("ssl") else "http"
        base_url = f"{protocol}://{self.settings.get('server')}:{self.settings.get('port')}"
        if success:
            dash_url = f"{base_url}/#!/dash"
            cookie_store = self.browser.page().profile().cookieStore()
            cookie_store.deleteAllCookies() 
            for cookie in session.cookies:
                qt_cookie = QNetworkCookie(cookie.name.encode(), cookie.value.encode())
                qt_cookie.setDomain(cookie.domain); qt_cookie.setPath(cookie.path)
                cookie_store.setCookie(qt_cookie, QUrl(base_url))
            self.browser.load(QUrl(dash_url)); self.stacked_widget.setCurrentIndex(1); self.update_ui_state(logged_in=True)
        else:
            QMessageBox.critical(self, "Login Failed", message); self.show_placeholder_page()
        self.setWindowTitle("Streama Client")

    def handle_logout_click(self):
        self.browser.load(QUrl("about:blank"))
        self.browser.page().profile().cookieStore().deleteAllCookies()
        self.show_placeholder_page(); self.statusBar().showMessage("Logged out.")
        
    def _load_qwebchannel_js(self):
        qrc_file = QFile(":/qtwebchannel/qwebchannel.js")
        if qrc_file.open(QIODevice.ReadOnly): self.qwebchannel_js_content = QTextStream(qrc_file).readAll(); qrc_file.close()
        else: print("FATAL ERROR: Could not load qwebchannel.js from Qt resources.")

    def inject_js_bridge(self, ok):
        if not ok or not self.qwebchannel_js_content: return
        hook_script = """
        new QWebChannel(qt.webChannelTransport, function(channel) {
            window.pyBridge = channel.objects.pyBridge;
            const observer = new MutationObserver((mutations) => {
                const btn = document.querySelector('.player-fill-screen.ion-arrow-expand, .player-fill-screen.ion-arrow-shrink');
                if (btn && !btn.hasAttribute('data-hooked')) {
                    btn.addEventListener('click', (e) => { 
                        e.stopImmediatePropagation();
                        window.pyBridge.toggle_fullscreen_from_js(); 
                    }, true);
                    btn.setAttribute('data-hooked', 'true');
                }
            });
            observer.observe(document.body, { childList: true, subtree: true });
        });"""
        self.browser.page().runJavaScript(self.qwebchannel_js_content); self.browser.page().runJavaScript(hook_script)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self.is_fullscreen: self.toggle_fullscreen()
        else: super().keyPressEvent(event)

    def toggle_fullscreen(self):
        if self.is_fullscreen:
            self.showNormal(); self.menuBar().setVisible(True); self.statusBar().setVisible(True); self.is_fullscreen = False
        else:
            self.menuBar().setVisible(False); self.statusBar().setVisible(False); self.showFullScreen(); self.is_fullscreen = True

    def open_settings_dialog(self):
        if self.logout_action.isVisible(): return
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec_() == QDialog.Accepted:
            self.settings = dialog.get_settings(); save_settings(self.settings); 
        elif not self.settings.get("server"): sys.exit(0)

    def setup_menu_and_actions(self):
        menu = self.menuBar()
        login_menu = menu.addMenu("&Login"); settings_menu = menu.addMenu("&Settings"); view_menu = menu.addMenu("&View")
        self.login_action = login_menu.addAction("Login"); self.login_action.triggered.connect(self.handle_login_click)
        self.logout_action = login_menu.addAction("Logout"); self.logout_action.triggered.connect(self.handle_logout_click)
        self.settings_action = settings_menu.addAction("Configure Server"); self.settings_action.triggered.connect(self.open_settings_dialog)
        fullscreen_action = QAction("Toggle Fullscreen", self); fullscreen_action.setShortcut(QKeySequence(Qt.Key_F11)); fullscreen_action.triggered.connect(self.toggle_fullscreen)
        exit_fullscreen_action = QAction("Exit Fullscreen", self); exit_fullscreen_action.setShortcut(QKeySequence(Qt.Key_Escape)); exit_fullscreen_action.triggered.connect(lambda: self.is_fullscreen and self.toggle_fullscreen())
        view_menu.addAction(fullscreen_action); self.addAction(fullscreen_action); self.addAction(exit_fullscreen_action)

    def closeEvent(self, event):
        print("[*] Shutting down application...")
        self.browser.stop()
        self.browser.setPage(None)
        if hasattr(self, 'custom_page'): self.custom_page.deleteLater(); self.custom_page = None
        if hasattr(self, 'channel'): self.channel = None
        event.accept()

if __name__ == "__main__":
    qInstallMessageHandler(qt_message_handler)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    if os.name == 'posix' and os.geteuid() == 0:
        print("[!] SECURITY WARNING: Running as root. Disabling Chromium sandbox for compatibility.")
        if '--no-sandbox' not in sys.argv: sys.argv.append('--no-sandbox')
    app = QApplication(sys.argv)
    window = MainWindow()
    window.showMaximized()
    sys.exit(app.exec_())
