    
# Streama Desktop Client

# Project under development

A standalone desktop client for the open-source [Streama](https://github.com/streamaserver/streama) media server. This application provides a seamless, native-like experience by wrapping the Streama web interface in a dedicated window, with added features like automatic login and integrated fullscreen control.

The client is built with Python and PySide6 (the official bindings for the Qt UI framework), using its powerful WebView component.

![Streama Client Screenshot](https://github.com/peterpt/Streama_Client/blob/main/media.png)


![Streama Player Screenshot](https://github.com/peterpt/Streama_Client/blob/main/app.png)
## Features

*   **Seamless Auto-Login:** Configure your credentials once, and the application logs you in automatically on startup, taking you directly to the dashboard.
*   **Integrated Fullscreen:** The player's fullscreen button is perfectly synchronized with the native desktop window. Toggling fullscreen from the web player, the `F11` key, or the `Esc` key works seamlessly.
*   **Clean and Professional UI:** A simple menu-driven interface keeps the focus on your media content.
*   **Self-Contained Assets:** The application icon and placeholder image are embedded directly into the application, requiring no external files after a one-time setup.
*   **Cross-Platform:** Built with Python and Qt, it can be adapted to run on Windows, macOS, and other Linux distributions.

## Requirements

### 1. System Dependencies
*   **Python 3** (developed with 3.11+) and `pip`.
*   **PySide6 for Qt6**, specifically the WebEngine module. This is best installed from your Linux distribution's package manager.

### 2. Python Dependencies
*   **requests**: For handling background authentication.

## Installation and Setup

Follow these steps to get the application running on a new Linux system (Debian/Devuan/Ubuntu based).

**1. Clone the Repository**
```bash
git clone https://github.com/peterpt/Streama_Client.git
cd Streama-Client

  

2. Install System Dependencies
Open a terminal and install the core Python and PySide6 libraries.
code Bash
    
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-pyside6.qtwebenginewidgets

  

    Note for other distributions:

        Fedora/RHEL: sudo dnf install python3-pyside6-webengine

        Arch Linux: sudo pacman -S pyside6-webengine

3. Install Python Package Dependencies
code Bash
    
pip3 install requests

Usage

    Login / Logout: Use the Login menu to connect to your server or to log out.

    Settings: Configure your server IP/domain, port, username, and password via the Settings -> Configure Server menu. This menu is disabled while you are logged in.

    Fullscreen:

        Click the fullscreen button inside the Streama video player.

        Use the View -> Toggle Fullscreen menu item.

        Press the F11 key to enter or exit fullscreen.

        Press the Esc key to exit fullscreen.

Project Files

    streama-client.py: The main application source code. This is the file you run.

    assets.py: (Auto-Generated) Contains the embedded image and icon data.

    settings.json: (Auto-Generated) Stores your server connection details after the first run.


## Notes
there are still some minor bugs in app due to pyside6 limitations , but right now the app displays subtitles if selected before starting the media

License

This project is licensed under the MIT License.

  

