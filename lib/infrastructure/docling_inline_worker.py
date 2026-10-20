"""Official Paddle layout inference in its own offline, pinned environment."""
from pathlib import Path
import hashlib
import importlib.metadata
import os
import sys


LAYOUT_PACKAGES = {"paddleocr": "3.7.0", "paddlex": "3.7.0", "onnxruntime": "1.31.0"}


def run_inline_layout(request):
    versions = {name: importlib.metadata.version(name) for name in LAYOUT_PACKAGES}
    if request.get("action") == "inspect":
        return {
            "protocol": 1, "packages": versions, "network_disabled": True,
            "metadata_files": {name: str(importlib.metadata.distribution(name)._path / "METADATA")
                               for name in versions},
            "site_packages": [str(Path(path).resolve()) for path in sys.path
                              if Path(path).name in {"site-packages", "dist-packages"}],
        }
    if request.get("action") != "detect" or versions != LAYOUT_PACKAGES:
        raise ValueError("document_docling_version_mismatch")
    images = request["images"]
    if not isinstance(images, list) or len(images) > 10000:
        raise ValueError("document_docling_invalid_request")
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["OMP_NUM_THREADS"] = "2"
    os.environ["MKL_NUM_THREADS"] = "2"
    from PIL import Image
    import numpy as np
    from paddleocr import LayoutDetection
    detector = LayoutDetection(
        model_name="PP-DocLayoutV3", model_dir=request["model_directory"],
        engine="onnxruntime", device="cpu", threshold=.3,
        engine_config={"intra_op_num_threads": 2, "inter_op_num_threads": 1,
                       "execution_mode": "sequential"},
    )
    output = []
    try:
        for item in images:
            path = Path(item["path"])
            with Image.open(path) as original:
                if original.width * original.height > 20_000_000:
                    raise ValueError("document_docling_invalid_output")
                image = original.convert("RGB")
            results = detector.predict(
                np.asarray(image), batch_size=1, filter_overlap_boxes=False,
                layout_merge_bboxes_mode="union", layout_shape_mode="rect",
            )
            if len(results) != 1:
                raise ValueError("document_docling_invalid_output")
            output.append({
                "id": item["id"], "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": list(image.size), "boxes": results[0].json["res"]["boxes"],
            })
            image.close()
    finally:
        detector.close()
    return {"protocol": 1, "packages": versions, "images": output, "network_disabled": True}
