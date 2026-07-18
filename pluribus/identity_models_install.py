"""Explicit, checksum-verified installer for optional local identity models."""

from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from .storage import ensure_private_dir


MODEL_BUNDLE_ID = "opencv-yunet-sface-v1"


@dataclass(frozen=True)
class ModelSpec:
    filename: str
    url: str
    sha256: str
    byte_limit: int


# Immutable OpenCV Zoo Git LFS URLs.  The hashes are the LFS object hashes and
# were independently verified against the downloaded bytes.  Downloads occur
# only after POST /pluribus/identity/models/install with confirm=true.
MODEL_SPECS = (
    ModelSpec(
        filename="face_detection_yunet_2023mar.onnx",
        url=(
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
            "f12e12798e8314f7c074a6656816c048dcc95b7a/models/"
            "face_detection_yunet/face_detection_yunet_2023mar.onnx"
        ),
        sha256="8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        byte_limit=2_000_000,
    ),
    ModelSpec(
        filename="face_recognition_sface_2021dec.onnx",
        url=(
            "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
            "ba91a3b91d00d76e86540d4013f944bd6b514e39/models/"
            "face_recognition_sface/face_recognition_sface_2021dec.onnx"
        ),
        sha256="0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        byte_limit=50_000_000,
    ),
)


Downloader = Callable[[str, str, int], None]


def default_downloader(url: str, destination: str, byte_limit: int) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "media.githubusercontent.com":
        raise ValueError(
            "Identity models may only be downloaded from the pinned HTTPS host."
        )
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ComfyUI-Pluribus/identity-model-installer"},
    )
    written = 0
    with urllib.request.urlopen(request, timeout=60) as response, open(
        destination, "wb"
    ) as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > byte_limit:
                raise ValueError(
                    "Identity model download exceeded its verified size limit."
                )
            handle.write(chunk)


class IdentityModelInstaller:
    def __init__(self, model_dir: str, downloader: Downloader | None = None):
        self.model_dir = model_dir
        self.downloader = downloader or default_downloader

    def paths(self) -> dict[str, str]:
        return {
            "yunet": os.path.join(self.model_dir, MODEL_SPECS[0].filename),
            "sface": os.path.join(self.model_dir, MODEL_SPECS[1].filename),
        }

    def status(self) -> dict:
        files = []
        installed = True
        for spec in MODEL_SPECS:
            path = os.path.join(self.model_dir, spec.filename)
            valid = os.path.isfile(path) and _sha256_file(path) == spec.sha256
            installed = installed and valid
            files.append(
                {
                    "filename": spec.filename,
                    "sha256": spec.sha256,
                    "installed": valid,
                    "downloadBytesMaximum": spec.byte_limit,
                }
            )
        return {
            "modelId": MODEL_BUNDLE_ID,
            "installed": installed,
            "files": files,
            "installAction": {
                "method": "POST",
                "endpoint": "/pluribus/identity/models/install",
                "body": {"modelId": MODEL_BUNDLE_ID, "confirm": True},
                "downloadsAutomatically": False,
            },
        }

    def install(self, model_id: object, confirm: object) -> dict:
        if str(model_id or "") != MODEL_BUNDLE_ID:
            raise ValueError("modelId is not a supported identity model bundle.")
        if confirm is not True:
            raise ValueError("confirm must be true before any model is downloaded.")
        ensure_private_dir(self.model_dir)
        installed_files: list[str] = []
        for spec in MODEL_SPECS:
            final_path = os.path.join(self.model_dir, spec.filename)
            if os.path.isfile(final_path) and _sha256_file(final_path) == spec.sha256:
                installed_files.append(spec.filename)
                continue
            fd, temporary_path = tempfile.mkstemp(
                prefix=f".{spec.filename}.", suffix=".download", dir=self.model_dir
            )
            os.close(fd)
            try:
                os.chmod(temporary_path, 0o600)
                self.downloader(spec.url, temporary_path, spec.byte_limit)
                actual_hash = _sha256_file(temporary_path)
                if actual_hash != spec.sha256:
                    raise ValueError(
                        f"Checksum verification failed for {spec.filename}; "
                        "the downloaded file was discarded."
                    )
                os.replace(temporary_path, final_path)
                os.chmod(final_path, 0o600)
                installed_files.append(spec.filename)
            finally:
                try:
                    os.remove(temporary_path)
                except FileNotFoundError:
                    pass
        return {
            "state": "installed",
            "modelId": MODEL_BUNDLE_ID,
            "files": installed_files,
            "capabilities": self.status(),
        }


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
