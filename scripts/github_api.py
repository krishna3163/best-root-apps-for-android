"""GitHub REST API client with authentication, rate-limit awareness, and retries."""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import requests

logger = logging.getLogger("apk-sync")

# Retryable HTTP status codes
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
BASE_DELAY = 2  # seconds


class GitHubAPI:
    """Thin wrapper around the GitHub REST API v3."""

    BASE_URL = "https://api.github.com"

    def __init__(self, token: Optional[str] = None) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "User-Agent": "root-apps-sync/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        token = token or os.environ.get("GITHUB_TOKEN", "")
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self._remaining: Optional[int] = None
        self._reset: Optional[float] = None

    def _update_rate_limit(self, response: requests.Response) -> None:
        """Track rate-limit headers from every response."""
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        if remaining is not None:
            self._remaining = int(remaining)
        if reset is not None:
            self._reset = float(reset)

    def _wait_for_rate_limit(self) -> None:
        """Sleep if we're close to hitting the rate limit."""
        if self._remaining is not None and self._remaining < 5 and self._reset:
            wait = max(0, self._reset - time.time()) + 1
            logger.warning("Rate limit nearly exhausted (%d remaining). Sleeping %.0fs.", self._remaining, wait)
            time.sleep(wait)

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Perform an HTTP request with exponential-backoff retries."""
        self._wait_for_rate_limit()

        last_exc: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.session.request(method, url, timeout=30, **kwargs)
                self._update_rate_limit(response)

                if response.status_code in RETRYABLE_STATUS_CODES:
                    delay = BASE_DELAY ** attempt
                    if response.status_code == 429:
                        retry_after = response.headers.get("Retry-After")
                        if retry_after:
                            delay = max(delay, int(retry_after))
                    logger.warning(
                        "HTTP %d from %s (attempt %d/%d). Retrying in %ds...",
                        response.status_code, url, attempt, MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                    continue

                return response

            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                delay = BASE_DELAY ** attempt
                logger.warning(
                    "Network error on %s (attempt %d/%d): %s. Retrying in %ds...",
                    url, attempt, MAX_RETRIES, exc, delay,
                )
                time.sleep(delay)

        if last_exc:
            raise last_exc
        return response  # type: ignore[possibly-undefined]

    def get(self, endpoint: str, **kwargs: Any) -> requests.Response:
        """GET request against the GitHub API."""
        url = endpoint if endpoint.startswith("http") else f"{self.BASE_URL}{endpoint}"
        return self._request("GET", url, **kwargs)

    def get_latest_release(self, repo: str) -> Optional[dict[str, Any]]:
        """GET /repos/{owner}/{repo}/releases/latest"""
        resp = self.get(f"/repos/{repo}/releases/latest")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def get_releases(self, repo: str, per_page: int = 30) -> list[dict[str, Any]]:
        """GET /repos/{owner}/{repo}/releases"""
        resp = self.get(f"/repos/{repo}/releases", params={"per_page": per_page})
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        return resp.json()

    def get_release_by_tag(self, repo: str, tag: str) -> Optional[dict[str, Any]]:
        """GET /repos/{owner}/{repo}/releases/tags/{tag}"""
        resp = self.get(f"/repos/{repo}/releases/tags/{tag}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def download_asset(self, url: str, dest_path: str) -> str:
        """Download a release asset with high-speed streaming."""
        self._wait_for_rate_limit()

        is_direct = "github.com/" in url and "/releases/download/" in url
        headers = {}
        if not is_direct:
            headers = dict(self.session.headers)
            headers["Accept"] = "application/octet-stream"

        last_exc: Optional[Exception] = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                requester = requests if is_direct else self.session
                with requester.get(url, headers=headers if headers else None, stream=True, timeout=120, allow_redirects=True) as resp:
                    if not is_direct:
                        self._update_rate_limit(resp)
                    if resp.status_code in RETRYABLE_STATUS_CODES:
                        delay = BASE_DELAY ** attempt
                        logger.warning("Download HTTP %d (attempt %d/%d)", resp.status_code, attempt, MAX_RETRIES)
                        time.sleep(delay)
                        continue
                    resp.raise_for_status()
                    with open(dest_path, "wb") as fh:
                        for chunk in resp.iter_content(chunk_size=65536):
                            if chunk:
                                fh.write(chunk)
                return dest_path
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                delay = BASE_DELAY ** attempt
                logger.warning("Download error (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
                time.sleep(delay)

        if last_exc:
            raise last_exc
        raise RuntimeError(f"Failed to download {url}")

    def get_repo_release_by_tag(self, repo: str, tag: str) -> Optional[dict[str, Any]]:
        """Check if a release with *tag* already exists in *repo*."""
        return self.get_release_by_tag(repo, tag)

    def create_release(
        self,
        repo: str,
        tag: str,
        name: str,
        body: str,
        draft: bool = False,
        prerelease: bool = False,
    ) -> dict[str, Any]:
        """Create a new release in *repo*."""
        url = f"{self.BASE_URL}/repos/{repo}/releases"
        payload = {
            "tag_name": tag,
            "name": name,
            "body": body,
            "draft": draft,
            "prerelease": prerelease,
        }
        resp = self._request("POST", url, json=payload)
        resp.raise_for_status()
        return resp.json()

    def upload_release_asset(
        self,
        upload_url: str,
        filepath: str,
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        """Upload an asset to an existing release."""
        upload_url = upload_url.split("{")[0]
        filename = os.path.basename(filepath)
        if filename.endswith(".apk"):
            content_type = "application/vnd.android.package-archive"
        elif filename.endswith(".zip"):
            content_type = "application/zip"
        elif filename.endswith(".sha256") or filename.endswith(".txt"):
            content_type = "text/plain"

        with open(filepath, "rb") as fh:
            resp = self._request(
                "POST",
                upload_url,
                params={"name": filename},
                headers={"Content-Type": content_type},
                data=fh,
            )
        resp.raise_for_status()
        return resp.json()

    def get_release_assets(self, repo: str, release_id: int) -> list[dict[str, Any]]:
        """List assets attached to a release."""
        resp = self.get(f"/repos/{repo}/releases/{release_id}/assets")
        resp.raise_for_status()
        return resp.json()

    def get_repo_info(self, repo: str) -> Optional[dict[str, Any]]:
        """GET /repos/{owner}/{repo}"""
        resp = self.get(f"/repos/{repo}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
