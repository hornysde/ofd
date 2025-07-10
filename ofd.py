from __future__ import annotations
from typing import Literal, Any, ClassVar, Iterable, Mapping, AsyncGenerator, Sequence
from dataclasses import dataclass

import time
import os
import argparse
import asyncio
import logging
import urllib.parse
import hashlib

import pydantic
import pydantic_xml
import aiohttp
import requests
import pywidevine
import pyffmpeg  # type: ignore

from tqdm import tqdm


class Endpoint:
    @staticmethod
    def _formatted(url_template: str):
        def format(**kwargs):
            return url_template.format(**kwargs)

        return format

    @staticmethod
    def _paged(url_template: str):
        def generate_pages(
            *, count: int, count_per_page: int, offset: int = 0, **kwargs
        ):
            for page_offset in range(0, count, count_per_page):
                yield url_template.format(
                    limit=count_per_page, offset=(offset + page_offset), **kwargs
                )

        return generate_pages

    sign_info = "https://raw.githubusercontent.com/DATAHOARDERS/dynamic-rules/main/onlyfans.json"
    user = _formatted("https://onlyfans.com/api2/v2/users/{user_id}")
    me = user(user_id="me")
    subscriptions = _paged(
        "https://onlyfans.com/api2/v2/subscriptions/subscribes?limit={limit}&offset={offset}&type=active"
    )
    posts_paid = _paged(
        "https://onlyfans.com/api2/v2/posts/paid?limit={limit}&offset={offset}"
    )
    posts = _paged(
        "https://onlyfans.com/api2/v2/users/{user_id}/posts?limit={limit}&offset={offset}&order=publish_date_desc&skip_users_dups=0"
    )
    posts_archived = _paged(
        "https://onlyfans.com/api2/v2/users/{user_id}/posts/archived?limit={limit}&offset={offset}&order=publish_date_desc"
    )
    messages = _paged(
        "https://onlyfans.com/api2/v2/chats/{user_id}/messages?limit={limit}&offset={offset}&order=desc"
    )
    license = _formatted(
        "https://onlyfans.com/api2/v2/users/media/{media_id}/drm/{response_type}/{post_id}?type=widevine"
    )


class SignInfo(pydantic.BaseModel):
    static_param: str
    checksum_indexes: list[int]
    checksum_constant: int
    format: str
    app_token: str

    @classmethod
    def from_download(cls) -> SignInfo:
        return SignInfo.model_validate(requests.get(Endpoint.sign_info).json())

    def sign(self, url: str, current_time: str) -> str:
        parsed = urllib.parse.urlparse(url)
        target = parsed.path + ("?" + parsed.query if parsed.query else "")
        message = "\n".join(
            [
                self.static_param,
                current_time,
                target,
                "0",
            ]
        ).encode("utf-8")
        sha1 = hashlib.sha1(message).hexdigest()
        checksum = (
            sum(ord(sha1[index]) for index in self.checksum_indexes)
            + self.checksum_constant
        )
        return self.format.format(sha1, abs(checksum))

    def make_header(self, url: str) -> dict[str, str]:
        current_time = str(int(round(time.time())))
        return {
            "app-token": self.app_token,
            "sign": self.sign(url, current_time),
            "time": current_time,
        }


class AuthInfo(pydantic.BaseModel):
    cookie: Cookie

    class Cookie(pydantic.BaseModel):
        auth_id: str
        sess: str

    x_bc: str
    user_agent: str

    @pydantic.field_validator("cookie", mode="before")
    @classmethod
    def parse_cookie(cls, value):
        if not isinstance(value, str):
            return value
        # Parse ; separated and = connected kv pairs
        cookie_dict = dict(
            item.strip().split("=", 1) for item in value.split(";") if "=" in item
        )
        return cls.Cookie.model_validate(cookie_dict)

    def make_header(self, url) -> dict[str, str]:
        return {
            "user-agent": self.user_agent,
            "referer": url,
            "x-bc": self.x_bc,
        }


class CDMInfo(pydantic.BaseModel):
    client_id_path: str = ""
    private_key_path: str = ""

    def is_valid(self) -> bool:
        if self.client_id_path is None or not os.path.exists(self.client_id_path):
            return False
        if self.private_key_path is None or not os.path.exists(self.private_key_path):
            return False
        return True

    def make_cdm(self) -> pywidevine.Cdm | None:
        if not self.is_valid():
            return None
        with open(self.client_id_path, "rb") as f:
            client_id = f.read()
        with open(self.private_key_path, "rb") as f:
            private_key = f.read()
        return pywidevine.Cdm.from_device(
            pywidevine.Device(
                type_=pywidevine.device.DeviceTypes.ANDROID,
                security_level=3,
                flags=None,
                client_id=client_id,
                private_key=private_key,
            )
        )


class User(pydantic.BaseModel):
    id: int
    name: str
    username: str
    postsCount: int
    archivedPostsCount: int
    photosCount: int
    videosCount: int
    audiosCount: int
    avatar: str | None
    header: str | None

    @property
    def media_count(self):
        return self.photosCount + self.videosCount + self.audiosCount


class Me(User):
    subscribesCount: int


class Subscription(pydantic.BaseModel):
    id: int
    name: str
    username: str
    subscribePrice: float

    @property
    def is_paid(self) -> bool:
        return self.subscribePrice > 0.0


class Media(pydantic.BaseModel):
    id: int
    type: Literal["photo", "video", "audio", "gif"]
    files: Files

    class Files(pydantic.BaseModel):
        full: File

        class File(pydantic.BaseModel):
            url: str | None
            width: int
            height: int

        drm: DRM | None = None

        class DRM(pydantic.BaseModel):
            manifest: Manifest

            class Manifest(pydantic.BaseModel):
                hls: str
                dash: str

            signature: Signature

            class Signature(pydantic.BaseModel):
                hls: dict[str, str]
                dash: dict[str, str]

    def is_drm(self) -> bool:
        return self.files.drm is not None


class Post(pydantic.BaseModel):
    responseType: Literal["post", "message"]
    id: int
    # Voting posts have no media
    media: list[Media] = []


mpd_nsmap = {
    "": "urn:mpeg:dash:schema:mpd:2011",
    "cenc": "urn:mpeg:cenc:2013",
}


class MPD(pydantic_xml.BaseXmlModel, nsmap=mpd_nsmap):
    period: Period

    class Period(pydantic_xml.BaseXmlModel, tag="Period", nsmap=mpd_nsmap):
        adaptation_sets: list[AdaptationSet]

        class AdaptationSet(
            pydantic_xml.BaseXmlModel, tag="AdaptationSet", nsmap=mpd_nsmap
        ):
            mimeType: str = pydantic_xml.attr()
            content_protections: list[ContentProtection]

            class ContentProtection(
                pydantic_xml.BaseXmlModel, tag="ContentProtection", nsmap=mpd_nsmap
            ):
                default_KID: str = pydantic_xml.attr(ns="cenc")
                schemeIdUri: str = pydantic_xml.attr()
                pssh: str | None = pydantic_xml.element(ns="cenc", default=None)

            representations: list[Representation]

            class Representation(
                pydantic_xml.BaseXmlModel, tag="Representation", nsmap=mpd_nsmap
            ):
                id: str = pydantic_xml.attr()
                codecs: str = pydantic_xml.attr()
                bandwidth: int = pydantic_xml.attr()
                # Filename, construct a URL with the prefix of manifest URL
                base_url: str = pydantic_xml.element("BaseURL")

            widevine_uri: ClassVar[str] = (
                "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"
            )

            def _get_media(self) -> tuple[Representation, ContentProtection]:
                return (
                    # Highest bandwidth presentation
                    sorted(self.representations, key=lambda r: r.bandwidth)[-1],
                    # Widevine content protection
                    next(
                        c
                        for c in self.content_protections
                        if c.schemeIdUri == self.widevine_uri
                    ),
                )

    def get_media(self, mime_prefix: Literal["video", "audio"]):
        aset = next(
            a for a in self.period.adaptation_sets if a.mimeType.startswith(mime_prefix)
        )
        return aset._get_media()


class Session:
    def __init__(self, auth: AuthInfo, sign: SignInfo):
        self.auth = auth
        self.sign = sign
        self.session: aiohttp.ClientSession | None = None

    async def create(self):
        await self.close()
        self.session = aiohttp.ClientSession(cookies=self.auth.cookie.model_dump())

    async def close(self):
        if self.session is None:
            return
        await self.session.close()
        self.session = None

    def make_headers(self, url: str) -> dict[str, str]:
        headers = {
            "accept": "application/json, text/plain, */*",
            "connection": "keep-alive",
        }
        headers.update(self.auth.make_header(url))
        headers.update(self.sign.make_header(url))
        return headers

    async def get(self, url: str, cookies: Mapping[str, str] | None = None):
        assert self.session is not None
        return await self.session.get(
            url, headers=self.make_headers(url), cookies=cookies
        )

    async def get_json(self, url: str) -> dict[str, Any]:
        response = await self.get(url)
        return await response.json()

    async def head(self, url: str, cookies: Mapping[str, str] | None = None):
        assert self.session is not None
        return await self.session.head(
            url, headers=self.make_headers(url), cookies=cookies
        )

    async def post(self, url: str, data: Any):
        assert self.session is not None
        return await self.session.post(url, data=data, headers=self.make_headers(url))


class OnlyFans:
    # This is the max count server can return
    count_per_page = 50
    count_per_batch = 200
    max_batch_count = 100

    def __init__(
        self,
        auth: AuthInfo | dict,
        cdm: CDMInfo | dict | None = None,
        sign: SignInfo | dict | None = None,
    ):
        auth = AuthInfo.model_validate(auth)
        sign = SignInfo.model_validate(sign) if sign else SignInfo.from_download()
        self.session = Session(auth, sign)
        self.cdm = CDMInfo.model_validate(cdm).make_cdm() if cdm else None
        self.me: Me | None = None
        # Cache
        self.users: dict[int, User] = {}
        self.subscriptions: list[Subscription] | None = None
        self.posts: dict[int, list[Post]] = {}
        self.posts_archived: dict[int, list[Post]] = {}
        self.messages: dict[int, list[Post]] = {}

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *_):
        await self.disconnect()

    def clear_cache(self):
        self.users.clear()
        self.subscriptions = None
        self.posts.clear()
        self.posts_archived.clear()

    async def connect(self):
        await self.session.create()
        self.me = Me.model_validate(await self.session.get_json(Endpoint.me))

    async def disconnect(self):
        await self.session.close()

    async def get_user(self, user_id: int) -> User:
        if user_id in self.users:
            return self.users[user_id]

        user = User.model_validate(
            await self.session.get_json(Endpoint.user(user_id=user_id))
        )

        self.users[user_id] = user
        return user

    async def get_subscriptions(self) -> list[Subscription]:
        if self.subscriptions is not None:
            return self.subscriptions

        assert self.me is not None

        coroutines = [
            self.session.get_json(url)
            for url in Endpoint.subscriptions(
                count=self.me.subscribesCount, count_per_page=self.count_per_page
            )
        ]
        subscriptions = [
            Subscription.model_validate(sub)
            for subs in await asyncio.gather(*coroutines)
            for sub in subs
        ]
        assert len(subscriptions) == self.me.subscribesCount, (
            f"Expect {self.me.subscribesCount} subscriptions, got {len(subscriptions)}"
        )

        self.subscriptions = subscriptions
        return subscriptions

    async def get_posts(
        self, user_id: int, archived: bool = False, progress: tqdm | None = None
    ) -> list[Post]:
        cache = self.posts_archived if archived else self.posts
        if user_id in cache:
            if progress is not None:
                progress.update(len(cache[user_id]))
            return cache[user_id]
        user = await self.get_user(user_id)
        if (archived and user.archivedPostsCount == 0) or (
            not archived and user.postsCount == 0
        ):
            return []

        async def request_posts(url: str) -> list[Post]:
            posts = [
                Post.model_validate(post) for post in await self.session.get_json(url)
            ]
            if progress is not None:
                progress.update(len(posts))
            return posts

        endpoint = Endpoint.posts_archived if archived else Endpoint.posts
        coroutines = [
            request_posts(url)
            for url in endpoint(
                user_id=user_id,
                count=user.postsCount,
                count_per_page=self.count_per_page,
            )
        ]
        posts = [post for posts in await asyncio.gather(*coroutines) for post in posts]
        expected_count = user.archivedPostsCount if archived else user.postsCount
        assert len(posts) == expected_count, (
            f"Expect {expected_count} posts, got {len(posts)}"
        )

        cache[user.id] = posts
        return posts

    async def get_messages(
        self, user_id: int, progress: tqdm | None = None
    ) -> list[Post]:
        if user_id in self.messages:
            if progress is not None:
                progress.update(len(self.messages[user_id]))
            return self.messages[user_id]

        async def request_messages(url: str) -> list[Post]:
            # Server response also contains a "hasMore" field which is in fact redundant
            # It remains true in last page, it will be false if and only if response contains zero message
            messages = [
                Post.model_validate(message)
                for message in (await self.session.get_json(url))["list"]
            ]
            if progress is not None:
                progress.update(len(messages))
            return messages

        # Number of messages is unknown beforehand, send request iteratively
        messages: list[Post] = []
        for i in range(self.max_batch_count):
            coroutines = [
                request_messages(url)
                for url in Endpoint.messages(
                    user_id=user_id,
                    count=self.count_per_batch,
                    count_per_page=self.count_per_page,
                    offset=i * self.count_per_batch,
                )
            ]
            messages_list = await asyncio.gather(*coroutines)
            messages.extend(
                message for messages in messages_list for message in messages
            )
            # No more messages
            if len(messages_list[-1]) == 0:
                break

        self.messages[user_id] = messages
        return messages

    async def get_content_size(
        self, url: str, cookies: Mapping[str, str] | None = None
    ) -> int:
        response = await self.session.head(url, cookies=cookies)
        return int(response.headers.get("Content-Length", 0))

    async def get_contents_size(
        self,
        urls: Sequence[str],
        cookie_jars: Iterable[Mapping[str, str] | None] | None = None,
    ) -> AsyncGenerator[int]:
        cookie_jars = cookie_jars or [None] * len(urls)
        for size_future in asyncio.as_completed(
            [
                self.get_content_size(url, cookies)
                for url, cookies in zip(urls, cookie_jars)
            ]
        ):
            yield await size_future


class Downloader:
    # This is as fast as it can go without triggering "CloudFlare Error 1015: You are being rate limited"
    key_request_period_s = 2.5

    def __init__(self, api: OnlyFans, user: User, directory: str):
        self.api = api
        self.user = user
        self.directory = f"{directory}/{user.username}"

    @classmethod
    async def for_user(cls, user_id: int, api: OnlyFans, directory: str) -> Downloader:
        user = await api.get_user(user_id)
        return Downloader(api, user, directory)

    @classmethod
    async def for_subscriptions(cls, api: OnlyFans, directory: str) -> list[Downloader]:
        return [
            await cls.for_user(subscription.id, api, directory)
            for subscription in await api.get_subscriptions()
        ]

    async def download_file(
        self,
        url: str,
        progress: tqdm | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> str:
        os.makedirs(self.directory, exist_ok=True)
        filename = self.get_filename(url)
        path = f"{self.directory}/{filename}"
        # Only open file after getting the response,
        # otherwise there will be many zero sized file complicating clean up
        response = await self.api.session.get(url, cookies)
        try:
            with open(path, "wb") as f:
                async for chunk, _ in response.content.iter_chunks():
                    f.write(chunk)
                    if progress is not None:
                        progress.update(len(chunk))
        except:
            # Do not leave a half written file
            os.remove(path)
            raise
        return path

    async def download_files(
        self,
        urls: Sequence[str],
        progress: tqdm,
        cookie_jars: Iterable[Mapping[str, str] | None] | None = None,
    ) -> list[str]:
        async for size in self.api.get_contents_size(urls, cookie_jars):
            progress.total += size
            progress.refresh()
        cookie_jars = cookie_jars or [None] * len(urls)
        return await asyncio.gather(
            *(
                self.download_file(url, progress, cookies)
                for url, cookies in zip(urls, cookie_jars)
            )
        )

    async def download_profile(self):
        urls = [url for url in (self.user.avatar, self.user.header) if url is not None]
        if len(urls) == 0:
            return (0, 0)

        with tqdm(
            total=0,
            leave=False,
            unit="B",
            unit_scale=True,
            desc="Downloading avatar and header",
        ) as progress:
            paths = await self.download_files(urls, progress)
        already_downloaded_count = 0
        # Avatar and header do not have universally unique names
        # Append hash to file name to detect content change
        for path in paths:
            with open(path, "rb") as f:
                digest = hashlib.sha1(f.read()).hexdigest()
            name, extension = path.rsplit(".", 1)
            new_path = f"{name}_{digest}.{extension}"
            if os.path.exists(new_path):
                os.remove(path)
                already_downloaded_count += 1
                continue
            os.rename(path, new_path)
        return (len(urls) - already_downloaded_count, already_downloaded_count)

    async def download_medias(self, medias: Iterable[Media]) -> tuple[int, int]:
        # Media with this url can be downloaded directly, others are drm protected media
        all_urls = [
            media.files.full.url for media in medias if media.files.full.url is not None
        ]
        urls = [url for url in all_urls if not self.is_url_downloaded(url)]
        already_downloaded_count = len(all_urls) - len(urls)

        with tqdm(
            total=0, leave=False, unit="B", unit_scale=True, desc="Downloading media"
        ) as progress:
            just_downloaded_count = len(await self.download_files(urls, progress))
        return (just_downloaded_count, already_downloaded_count)

    async def download_posts(self) -> tuple[int, int]:
        with tqdm(
            total=self.user.postsCount + self.user.archivedPostsCount,
            leave=False,
            desc="Indexing posts",
        ) as progress:
            posts = await self.api.get_posts(
                self.user.id, archived=False, progress=progress
            )
            posts += await self.api.get_posts(
                self.user.id, archived=True, progress=progress
            )
        return await self.download_medias(
            media for post in posts for media in post.media
        )

    async def download_messages(self):
        with tqdm(total=0, leave=False, desc="Indexing messages") as progress:
            messages = await self.api.get_messages(self.user.id, progress=progress)
        return await self.download_medias(
            media for message in messages for media in message.media
        )

    @dataclass
    class DRMMedia:
        # From post index
        post: Post
        media: Media
        # From DASH manifest
        video: MPD.Period.AdaptationSet.Representation | None = None
        audio: MPD.Period.AdaptationSet.Representation | None = None
        protection: MPD.Period.AdaptationSet.ContentProtection | None = None

        @property
        def license_url(self):
            return Endpoint.license(
                media_id=self.media.id,
                response_type=self.post.responseType,
                post_id=self.post.id,
            )

        @property
        def video_url(self):
            return (
                self.media.files.drm.manifest.dash.rsplit("/", 1)[0]
                + "/"
                + self.video.base_url
            )

        @property
        def audio_url(self):
            return (
                self.media.files.drm.manifest.dash.rsplit("/", 1)[0]
                + "/"
                + self.audio.base_url
            )

        @property
        def final_filename(self):
            # Suffix raw video filename with "_drm"
            name, extension = self.video.base_url.rsplit(".", 1)
            return f"{name}_drm.{extension}"

    async def download_medias_drm(self, drm_medias: Sequence[DRMMedia]):
        if self.api.cdm is None:
            return

        # Get DASH manifest

        progress = tqdm(
            total=len(drm_medias), leave=False, desc="Indexing drm manifests"
        )

        async def add_manifest(drm: Downloader.DRMMedia):
            assert drm.media.files.drm is not None
            response = await self.api.session.get(
                drm.media.files.drm.manifest.dash, drm.media.files.drm.signature.dash
            )
            if response.status == 403:
                # CloudFront may return 403 if on VPN (with low reputation IPs)
                raise Exception(
                    "Permission denied to retrieve manifest. Disconnect any VPN."
                )
            mpd = MPD.from_xml(await response.read())
            # All files in one manifest is protected by the same key
            video, protection = mpd.get_media("video")
            audio, _ = mpd.get_media("audio")
            drm.video = video
            drm.audio = audio
            drm.protection = protection
            progress.update()

        await asyncio.gather(*[add_manifest(drm) for drm in drm_medias])
        progress.close()

        # Download raw encrypted content

        progress = tqdm(
            total=0, leave=False, unit="B", unit_scale=True, desc="Downloading drm"
        )
        # Filter for drm that are not already downloaded
        drms = [
            drm for drm in drm_medias if not self.is_file_downloaded(drm.final_filename)
        ]
        already_downloaded_count = len(drm_medias) - len(drms)
        # Filter for files need to be downloaded
        urls = []
        cookies = []
        for drm in drms:
            assert drm.video is not None
            assert drm.audio is not None
            assert drm.media.files.drm is not None

            if not self.is_file_downloaded(drm.video.base_url):
                urls.append(drm.video_url)
                cookies.append(drm.media.files.drm.signature.dash)
            if not self.is_file_downloaded(drm.audio.base_url):
                urls.append(drm.audio_url)
                cookies.append(drm.media.files.drm.signature.dash)
        await self.download_files(urls, progress, cookies)
        progress.close()

        # Decrypt drm content

        progress = tqdm(total=len(drms), leave=False, desc="Decrypting drm")
        cdm = self.api.cdm
        ffmpeg = pyffmpeg.FFmpeg()
        scheduled_time = time.monotonic()
        for drm in drms:
            assert drm.video is not None
            assert drm.audio is not None
            assert drm.protection is not None

            # Limit the rate of key request
            await asyncio.sleep(scheduled_time - time.monotonic())
            scheduled_time += self.key_request_period_s

            # Skip duplicated media
            # If raw file does not exist here, it means it appeared before and has been processed
            raw_video_path = f"{self.directory}/{drm.video.base_url}"
            raw_audio_path = f"{self.directory}/{drm.audio.base_url}"
            if not os.path.exists(raw_video_path) or not os.path.exists(raw_audio_path):
                continue

            # Get key
            # Do this synchronously as the license server has very strict rate limit
            session_id = cdm.open()
            challenge = cdm.get_license_challenge(
                session_id, pywidevine.PSSH(drm.protection.pssh)
            )
            response = await self.api.session.post(drm.license_url, challenge)
            license_raw = await response.content.read()
            cdm.parse_license(session_id, license_raw)

            key = next(k for k in cdm.get_keys(session_id) if k.type == "CONTENT")
            cdm.close(session_id)

            # Decrypt
            decrypted_video_path = f"{self.directory}/decrypted_{drm.video.base_url}"
            decrypted_audio_path = f"{self.directory}/decrypted_{drm.audio.base_url}"
            for raw_path, decrypted_path in (
                (raw_video_path, decrypted_video_path),
                (raw_audio_path, decrypted_audio_path),
            ):
                ffmpeg.options(
                    [
                        # -decryption_key must appear before -i
                        "-decryption_key",
                        key.key.hex(),
                        "-i",
                        raw_path,
                        "-c",
                        "copy",
                        "-y",
                        decrypted_path,
                    ]
                )
                # Remove raw encrypted files
                os.remove(raw_path)

            # Stich video and audio
            final_path = f"{self.directory}/{drm.final_filename}"
            ffmpeg.options(
                [
                    "-i",
                    decrypted_video_path,
                    "-i",
                    decrypted_audio_path,
                    "-c",
                    "copy",
                    "-movflags",
                    "use_metadata_tags",
                    "-y",
                    final_path,
                ]
            )
            # Remove separate video and audio file
            os.remove(decrypted_video_path)
            os.remove(decrypted_audio_path)
            progress.update()
            assert os.path.exists(final_path), (
                f"Decryption failed, missing final file {final_path}"
            )
        progress.close()
        return (len(drm_medias) - already_downloaded_count, already_downloaded_count)

    async def download_posts_drm(self) -> tuple[int, int]:
        with tqdm(
            total=self.user.postsCount + self.user.archivedPostsCount,
            leave=False,
            desc="Indexing posts",
        ) as progress:
            posts = await self.api.get_posts(
                self.user.id, archived=False, progress=progress
            )
            posts += await self.api.get_posts(
                self.user.id, archived=True, progress=progress
            )
        # Filter for drm media from all post media
        drm_medias = [
            Downloader.DRMMedia(p, m) for p in posts for m in p.media if m.is_drm()
        ]
        if len(drm_medias) == 0:
            return (0, 0)

        return await self.download_medias_drm(drm_medias)

    async def download_messages_drm(self) -> tuple[int, int]:
        with tqdm(total=0, leave=False, desc="Indexing messages") as progress:
            messages = await self.api.get_messages(self.user.id, progress=progress)
        # Filter for drm media from all message media
        drm_medias = [
            Downloader.DRMMedia(message, media)
            for message in messages
            for media in message.media
            if media.is_drm()
        ]
        if len(drm_medias) == 0:
            return (0, 0)

        return await self.download_medias_drm(drm_medias)

    @staticmethod
    def get_filename(url: str):
        return urllib.parse.urlparse(url).path.rsplit("/", 1)[1]

    def is_file_downloaded(self, filename: str):
        return os.path.exists(f"{self.directory}/{filename}")

    def is_url_downloaded(self, url: str):
        filename = Downloader.get_filename(url)
        return self.is_file_downloaded(filename)


def print_result(counts: tuple[int, int]):
    for word, num in zip(("New", "Existing"), counts):
        print(f"{word:<15}{num:>7}")


async def main():
    # Shut pyffmpeg up
    logging.getLogger("pyffmpeg").handlers = []

    parser = argparse.ArgumentParser(description="Brutally simple OnlyFans downloader.")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to the config file (default: config.json).",
    )
    parser.add_argument(
        "--output",
        default="downloads",
        help="Directory to save the downloaded media (default: downloads).",
    )
    args = parser.parse_args()

    with open(args.config, "r") as f:
        config_text = f.read()
        auth = AuthInfo.model_validate_json(config_text)
        cdm = CDMInfo.model_validate_json(config_text)
    async with OnlyFans(auth=auth, cdm=cdm) as api:
        print(f"Logged in as {api.me.name}")
        for downloader in await Downloader.for_subscriptions(api, args.output):
            print(f"====== {downloader.user.name}")
            print("== Avatar and header")
            print_result(await downloader.download_profile())
            print("== Posts")
            print_result(await downloader.download_posts())
            print("== Messages")
            print_result(await downloader.download_messages())
            if downloader.api.cdm is None:
                print("Skip DRM")
                continue
            print("== Posts DRM")
            print_result(await downloader.download_posts_drm())
            print("== Messages DRM")
            print_result(await downloader.download_messages_drm())
            # TODO: download paid post and stories (stories, archived_stories, highlights)


if __name__ == "__main__":
    asyncio.run(main())
