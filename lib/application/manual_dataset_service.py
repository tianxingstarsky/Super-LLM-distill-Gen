"""Author manual examples without a model call or an automatic quality claim."""
from __future__ import annotations

from collections.abc import Iterable

from lib.application.manual_dataset_ports import ManualDatasetPort
from lib.domain.manual_dataset import (MAX_ATTACHMENTS_BYTES, MAX_IMAGE_BYTES, MAX_IMAGES,
                                       attachment_name, dataset_name, identifier, sample_text)


class ManualDatasetApplication:
    def __init__(self, port: ManualDatasetPort):
        self._port = port

    def list_datasets(self) -> list[dict]:
        return self._port.list_datasets()

    def create_dataset(self, name: str) -> str:
        return self._port.create_dataset(dataset_name(name))

    def list_samples(self, dataset_id: str, limit: int = 20, offset: int = 0) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 1_000_000:
            raise ValueError("invalid_manual_dataset_page")
        return self._port.list_samples(identifier(dataset_id), limit=limit, offset=offset)

    def append_sample(self, dataset_id: str, *, question: str, answer: str,
                      attachments: Iterable[tuple[str, bytes]] = (), system: str = "") -> dict:
        dataset_id = identifier(dataset_id)
        question, answer, system = sample_text(question), sample_text(answer), sample_text(system, system=True)
        images = []
        total = 0
        try:
            for item in attachments:
                if len(images) >= MAX_IMAGES or not isinstance(item, (tuple, list)) or len(item) != 2:
                    raise ValueError("invalid_manual_dataset_attachments")
                name, payload = item
                attachment_name(name)
                if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_IMAGE_BYTES:
                    raise ValueError("invalid_manual_dataset_attachments")
                total += len(payload)
                if total > MAX_ATTACHMENTS_BYTES:
                    raise ValueError("invalid_manual_dataset_attachments")
                images.append((name, payload))
        except TypeError:
            raise ValueError("invalid_manual_dataset_attachments") from None
        return self._port.append_sample(dataset_id, question=question, answer=answer,
                                        attachments=images, system=system)

    def read_media(self, dataset_id: str, media_id: str) -> bytes:
        return self._port.read_media(identifier(dataset_id), identifier(media_id))

    def export_dataset(self, dataset_id: str) -> bytes:
        return self._port.export_dataset(identifier(dataset_id))
