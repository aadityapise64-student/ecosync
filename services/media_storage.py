"""Private user-media storage with an optional Supabase Storage backend.

All access is routed through the Flask server so the Supabase secret key stays
server-side and the application's existing ownership checks remain in force.
"""

import os
from pathlib import PurePosixPath
from urllib.parse import quote

import requests
from flask import current_app


class MediaStorageError(RuntimeError):
    """A private media upload or download could not be completed."""


def supabase_storage_enabled() -> bool:
    return bool(
        current_app.config.get('SUPABASE_URL', '').strip()
        and current_app.config.get('SUPABASE_SECRET_KEY', '').strip()
    )


def _object_path(filename: str) -> str:
    # Filenames in the existing schema are generated UUID names, never paths.
    if (
        not filename
        or filename in {'.', '..'}
        or PurePosixPath(filename).name != filename
        or '/' in filename
        or '\\' in filename
        or any(ord(char) < 32 for char in filename)
    ):
        raise MediaStorageError('Invalid media object name.')
    return f'media/{filename}'


def _request_headers(content_type: str | None = None) -> dict[str, str]:
    headers = {
        'apikey': current_app.config['SUPABASE_SECRET_KEY'].strip(),
    }
    if content_type:
        headers['Content-Type'] = content_type
    return headers


def _object_url(object_path: str) -> str:
    bucket = quote(current_app.config.get('SUPABASE_STORAGE_BUCKET', 'user-media'), safe='')
    path = quote(object_path, safe='/')
    return f"{current_app.config['SUPABASE_URL'].rstrip('/')}/storage/v1/object/{bucket}/{path}"


def persist_file(local_path: str, filename: str, content_type: str | None = None) -> None:
    """Copy a local upload into the configured private bucket, if enabled.

    The caller keeps the local file until the database transaction succeeds,
    then removes it. In local mode this function is intentionally a no-op.
    """
    if not supabase_storage_enabled():
        return

    object_path = _object_path(filename)
    try:
        with open(local_path, 'rb') as media_file:
            response = requests.post(
                _object_url(object_path),
                data=media_file,
                headers={
                    **_request_headers(content_type or 'application/octet-stream'),
                    'x-upsert': 'false',
                },
                timeout=current_app.config.get('SUPABASE_STORAGE_TIMEOUT', 20),
            )
        if not response.ok:
            raise MediaStorageError(f'Supabase Storage upload failed (HTTP {response.status_code}).')
    except requests.RequestException as exc:
        raise MediaStorageError('Supabase Storage could not be reached for upload.') from exc
    except OSError as exc:
        raise MediaStorageError('The uploaded image could not be read for storage.') from exc


def read_file(filename: str) -> bytes | None:
    """Read a private object from Supabase Storage, or return None when absent."""
    if not supabase_storage_enabled():
        return None

    object_path = _object_path(filename)
    url = _object_url(object_path)
    # Private buckets require the authenticated object route. The server-side
    # secret API key is supplied only in the API key header.
    url = url.replace('/storage/v1/object/', '/storage/v1/object/authenticated/', 1)
    try:
        response = requests.get(
            url,
            headers=_request_headers(),
            timeout=current_app.config.get('SUPABASE_STORAGE_TIMEOUT', 20),
        )
        if response.status_code == 404:
            return None
        if not response.ok:
            raise MediaStorageError(f'Supabase Storage download failed (HTTP {response.status_code}).')
        return response.content
    except requests.RequestException as exc:
        raise MediaStorageError('Supabase Storage could not be reached for download.') from exc


def delete_file(filename: str) -> None:
    """Best-effort delete for compensating a failed database transaction."""
    if not supabase_storage_enabled():
        return

    object_path = _object_path(filename)
    bucket = quote(current_app.config.get('SUPABASE_STORAGE_BUCKET', 'user-media'), safe='')
    url = f"{current_app.config['SUPABASE_URL'].rstrip('/')}/storage/v1/object/{bucket}"
    try:
        response = requests.delete(
            url,
            json={'prefixes': [object_path]},
            headers=_request_headers('application/json'),
            timeout=current_app.config.get('SUPABASE_STORAGE_TIMEOUT', 20),
        )
        if not response.ok:
            current_app.logger.warning('Supabase Storage cleanup failed (HTTP %s).', response.status_code)
    except requests.RequestException:
        current_app.logger.warning('Supabase Storage cleanup could not reach the provider.')


def remove_local_file(path: str) -> None:
    """Remove a temporary local copy without failing the request on cleanup."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        current_app.logger.warning('Temporary uploaded media could not be removed.')
