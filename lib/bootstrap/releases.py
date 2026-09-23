"""Composition root for conversation release and quality-report use cases."""
from lib.application.release_service import ReleaseApplication
from lib.infrastructure.release_file_driver import FilesystemReleaseDriver


def release_application() -> ReleaseApplication:
    return ReleaseApplication(FilesystemReleaseDriver())
