import requests
import logging
import json
from requests.packages.urllib3.exceptions import InsecureRequestWarning


class StreamaAPIClient:
    def __init__(self):
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
