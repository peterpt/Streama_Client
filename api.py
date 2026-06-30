import os
import requests
import logging
import json
from requests.packages.urllib3.exceptions import InsecureRequestWarning


class CacheManager:
    """Local offline cache for poster thumbnails and title metadata, keyed by
    the Streama server's media id. Lets the app display covers and details
    without any network/TMDB access once a title has been browsed once.

    Layout (created on first use, inside the app's working directory):
        cache/
            posters/   <id>.jpg   (small JPEG thumbnails, ~w185)
            metadata/  <id>.json  (the media object as received)
    """
    def __init__(self, base_dir=None):
        root = base_dir or os.getcwd()
        self.cache_dir = os.path.join(root, "cache")
        self.posters_dir = os.path.join(self.cache_dir, "posters")
        self.metadata_dir = os.path.join(self.cache_dir, "metadata")
        self._ensure_dirs()

    def _ensure_dirs(self):
        for d in (self.cache_dir, self.posters_dir, self.metadata_dir):
            try:
                os.makedirs(d, exist_ok=True)
            except OSError as e:
                logging.error(f"Could not create cache dir {d}: {e}")

    @staticmethod
    def make_key(media_obj):
        """Build a cache key that is unique ACROSS media types. The server
        numbers movies, TV shows, and episodes independently, so the same
        numeric id can belong to a movie AND a show — keying on id alone makes
        them collide (a show would show a movie's cover). Namespacing by
        mediaType prevents that."""
        if not isinstance(media_obj, dict):
            return None
        media_id = media_obj.get('id')
        if media_id is None:
            return None
        mtype = media_obj.get('mediaType') or 'item'
        # Sanitize to keep filenames safe.
        mtype = str(mtype).replace('/', '_').replace('\\', '_')
        return f"{mtype}_{media_id}"

    # --- Posters ---
    def poster_path(self, media_id):
        return os.path.join(self.posters_dir, f"{media_id}.jpg")

    def has_poster(self, media_id):
        p = self.poster_path(media_id)
        return bool(media_id) and os.path.exists(p) and os.path.getsize(p) > 0

    def read_poster(self, media_id):
        try:
            with open(self.poster_path(media_id), "rb") as f:
                return f.read()
        except OSError:
            return None

    def write_poster(self, media_id, data):
        if not media_id or not data:
            return
        try:
            with open(self.poster_path(media_id), "wb") as f:
                f.write(data)
        except OSError as e:
            logging.error(f"Could not write poster {media_id}: {e}")

    # --- Metadata ---
    def metadata_path(self, media_id):
        return os.path.join(self.metadata_dir, f"{media_id}.json")

    def has_metadata(self, media_id):
        return bool(media_id) and os.path.exists(self.metadata_path(media_id))

    def read_metadata(self, media_id):
        try:
            with open(self.metadata_path(media_id), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def write_metadata(self, media_id, media_obj):
        if not media_id or not isinstance(media_obj, dict):
            return
        try:
            with open(self.metadata_path(media_id), "w", encoding="utf-8") as f:
                json.dump(media_obj, f, ensure_ascii=False)
        except OSError as e:
            logging.error(f"Could not write metadata {media_id}: {e}")

    def list_cached_metadata(self):
        """Return all cached media objects (for offline browsing)."""
        items = []
        try:
            for name in os.listdir(self.metadata_dir):
                if name.endswith(".json"):
                    obj = self.read_metadata(name[:-5])
                    if obj:
                        items.append(obj)
        except OSError:
            pass
        return items


class StreamaAPIClient:
    def __init__(self, base_dir=None):
        self.base_url = None
        self.session = requests.Session()
        # Set a standard User-Agent (helps with some server restrictions)
        self.session.headers.update({'User-Agent': 'StreamaDesktop/1.0'})
        self.tmdb_image_base_url = "https://image.tmdb.org/t/p/"
        # Streama tracks viewing-status (Continue Watching) PER PROFILE.
        # The web client sends the active profile id as a 'profileId' header
        # on every request. Without it, the server resolves to a profile with
        # no history and listContinueWatching returns an empty list.
        self.current_profile_id = None
        self.profiles = []
        # Offline cache for posters + metadata. base_dir should be the app's
        # stable directory (next to the .exe when frozen) so the cache
        # persists across launches.
        self.cache = CacheManager(base_dir=base_dir)

    def cached_poster_thumb_url(self, poster_path):
        """Build a SMALL TMDB thumbnail URL (w185) for a poster path, to keep
        cached images light. Falls back to the given path if it's already a
        full/relative non-TMDB URL (e.g. a server-hosted still)."""
        if not poster_path:
            return None
        # Server-relative paths are served by Streama itself, not TMDB.
        if poster_path.startswith('/'):
            return self.base_url + poster_path
        if poster_path.startswith('http'):
            return poster_path
        # A bare TMDB path like "/abc.jpg" combined with a small size.
        return self.tmdb_image_base_url + 'w185' + poster_path

    def configure(self, server, port, ssl=False, insecure_ssl=False):
        protocol = "https" if ssl else "http"
        self.base_url = f"{protocol}://{server}:{port}"
        self.session.verify = not insecure_ssl
        if insecure_ssl:
            requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    def set_tmdb_image_base_url(self, url):
        self.tmdb_image_base_url = url if url else "https://image.tmdb.org/t/p/"

    def set_current_profile_id(self, profile_id):
        self.current_profile_id = profile_id

    def _make_request(self, method, endpoint, **kwargs):
        if not self.base_url:
            return None, "API client is not configured."
        url = self.base_url + endpoint

        # Attach the active profile id header (mirrors the web httpInterceptor).
        if self.current_profile_id is not None:
            headers = dict(kwargs.pop('headers', {}) or {})
            headers['profileId'] = str(self.current_profile_id)
            kwargs['headers'] = headers

        try:
            response = self.session.request(method, url, timeout=15, **kwargs)
            response.raise_for_status()
            return response.json(), None
        except requests.exceptions.RequestException as e:
            logging.error(f"API Request Failed: {e}")
            return None, str(e)
        except json.JSONDecodeError as e:
            logging.error(f"JSON Decode Failed: {e}")
            return None, "Failed to decode server response."

    def login(self, username, password):
        # We ensure X-Requested-With is present for AJAX-style logins
        headers = {'Accept': 'application/json, text/plain, */*', 'X-Requested-With': 'XMLHttpRequest'}
        return self._make_request('POST', "/login/authenticate", data={"username": username, "password": password, "remember_me": "on"}, headers=headers)

    def get_user_profiles(self):
        return self._make_request('GET', "/profile/getUserProfiles.json")

    def load_profiles(self):
        """
        Fetch the user's profiles. Returns (profiles_list, error).
        Does NOT auto-select — the client decides (auto-pick if one,
        prompt if several). Use set_current_profile_id() to activate one.
        """
        data, error = self.get_user_profiles()
        if error:
            return None, error
        # Normalize to a plain list of profile dicts.
        if isinstance(data, list):
            profiles = data
        elif isinstance(data, dict):
            profiles = data.get('list') or data.get('profiles') or []
        else:
            profiles = []
        self.profiles = profiles
        return profiles, None

    def get_continue_watching(self, max_items=50):
        return self._make_request('GET', f"/dash/listContinueWatching.json?max={max_items}")
    def get_movies(self, max_items=50, offset=0):
        return self._make_request('GET', f"/dash/listMovies.json?max={max_items}&offset={offset}")
    def get_shows(self, max_items=50, offset=0):
        return self._make_request('GET', f"/dash/listShows.json?max={max_items}&offset={offset}")
    def get_generic_videos(self, max_items=50, offset=0):
        return self._make_request('GET', f"/dash/listGenericVideos.json?max={max_items}&offset={offset}")
    def search(self, query):
        return self._make_request('GET', "/dash/searchMedia.json", params={"query": query})
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

    def save_viewing_status(self, video_id, current_time_s, runtime_s):
        # Mirrors the official web client: GET /viewingStatus/save.json
        # with currentTime and runtime in SECONDS. This is what populates
        # the "Continue Watching" list on the server.
        return self._make_request(
            'GET', "/viewingStatus/save.json",
            params={
                "videoId": video_id,
                "currentTime": int(current_time_s),
                "runtime": int(runtime_s),
            },
        )

    def mark_completed(self, video_id):
        return self._make_request(
            'GET', "/viewingStatus/markCompleted.json",
            params={"id": video_id},
        )
