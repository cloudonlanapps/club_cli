"""The CLI's calls to the server's media API.

`/v1/media` holds the files; `/v1/<owner>/by_id/<id>/media` links them to a
user, event, group, venue or evaluation. This replaced the `/v1/uploaded` +
`/gallery` API, which the server retired in May 2026 and no deployment serves
(#37).
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import click
import httpx


def _post_file(
    url: str,
    headers: dict[str, str],
    filepath: str | Path,
    data: dict[str, str],
) -> httpx.Response:
    path = Path(filepath)
    if not path.exists():
        raise click.ClickException(f"File not found: {filepath}")
    with open(path, "rb") as f:
        return httpx.post(
            url,
            files={"file": (path.name, f)},
            data=data,
            headers=headers,
        )


class MediaApi:
    """`/v1/media` and the per-owner `/v1/users/.../media` links."""

    def add_file(self, base_url, headers, filepath, *, preserve_original=False, form=None):
        data: dict[str, str] = {}
        if preserve_original:
            data["preserveOriginal"] = "true"
        data.update(form or {})
        return _post_file(f"{base_url}/v1/media", headers, filepath, data)

    def download_url(self, base_url, uuid, filename=None):
        """The download URL, with the server's filename when it is known.

        The route ignores the trailing segment for the lookup, so it is
        decorative — but it makes the URL end in a real extension, which is
        what an extension-sniffing renderer reads and what a browser saves the
        file under. [filename] comes from the upload response's `ref`
        (club_server#424); without it the URL still works and still saves as
        `download`.
        """
        url = f"{base_url}/v1/media/by_id/{uuid}/download"
        return f"{url}/{quote(filename)}" if filename else url

    def list_uploads(
        self, base_url, headers, *, offset, limit, media_type, conversion_status, include_deleted=False,
    ):
        params: dict[str, str | int | bool] = {"offset": offset, "limit": limit}
        if media_type:
            params["mediaType"] = media_type
        if conversion_status:
            params["conversionStatus"] = conversion_status
        if include_deleted:
            params["includeDeleted"] = True
        return httpx.get(f"{base_url}/v1/media", params=params, headers=headers)

    def get_upload(self, base_url, headers, upload_id):
        return httpx.get(f"{base_url}/v1/media/by_id/{upload_id}", headers=headers)

    def delete_upload(self, base_url, headers, upload_id):
        return httpx.delete(f"{base_url}/v1/media/by_id/{upload_id}", headers=headers)

    def download(self, base_url, headers, uuid, *, variant, head=False):
        # HEAD answers the type and size without a byte of the file (#424).
        verb = httpx.head if head else httpx.get
        return verb(
            f"{base_url}/v1/media/by_id/{uuid}/download",
            params={"variant": variant},
            headers=headers,
        )

    def list_user_gallery(self, base_url, headers, username):
        return httpx.get(
            f"{base_url}/v1/users/by_id/{username}/media", headers=headers
        )

    def link_to_user_gallery(self, base_url, headers, username, *, tag, uuid):
        return httpx.post(
            f"{base_url}/v1/users/by_id/{username}/media",
            json={"tag": tag, "mediaUuid": uuid},
            headers=headers,
        )

    def remove_from_user_gallery(self, base_url, headers, username, *, tag, item_id):
        return httpx.delete(
            f"{base_url}/v1/users/by_id/{username}/media/{tag}/{item_id}",
            headers=headers,
        )

    def search_links(
        self,
        base_url,
        headers,
        *,
        tag=None,
        owner_type=None,
        media_type=None,
        is_encrypted=None,
        offset=0,
        limit=100,
    ):
        params: dict[str, str | int] = {"offset": offset, "limit": limit}
        if tag:
            params["tag"] = tag
        if owner_type:
            params["ownerType"] = owner_type
        if media_type:
            params["mediaType"] = media_type
        if is_encrypted is not None:
            params["isEncrypted"] = "true" if is_encrypted else "false"
        return httpx.get(f"{base_url}/v1/media/links", params=params, headers=headers)

    def encrypt_media(self, base_url, headers, uuid):
        return httpx.post(
            f"{base_url}/v1/media/by_id/{uuid}/encrypt", headers=headers
        )
