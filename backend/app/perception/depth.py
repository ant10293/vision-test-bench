"""Relative monocular depth. The values are not meters.

MiDaS small is loaded only when a fixed-camera frame has two overlapping boxes.
Its output is inverse relative depth: a larger value is nearer the camera, and
the scale and offset change from frame to frame.
"""

from __future__ import annotations

import cv2
import numpy as np

from app.perception.types import DepthUnavailable

MIDAS_SMALL_ID = "midas_small"
MIDAS_SMALL_VERSION = "intel-isl/MiDaS:MiDaS_small"
MIDAS_BACKBONE_REPO = "rwightman/gen-efficientnet-pytorch"
# The long side of the frame given to the depth model.
DEPTH_EDGE = 384


class MidasSmallDepth:
    """Intel ISL MiDaS small. Weights download on the first predict call."""

    depth_id = MIDAS_SMALL_ID
    depth_version = MIDAS_SMALL_VERSION

    def __init__(self):
        self._torch = None
        self._model = None
        self._transform = None
        self.device = "cpu"

    def predict(self, image: np.ndarray) -> np.ndarray:
        torch, model, transform = self._load()
        height, width = image.shape[:2]
        scale = min(1.0, DEPTH_EDGE / max(height, width))
        view = image
        if scale < 1.0:
            view = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
        rgb = cv2.cvtColor(view, cv2.COLOR_BGR2RGB)
        batch = transform(rgb).to(self.device)
        with torch.no_grad():
            output = model(batch)
            output = torch.nn.functional.interpolate(
                output.unsqueeze(1), size=(height, width), mode="bicubic", align_corners=False
            ).squeeze()
        return output.detach().float().cpu().numpy()

    def _load(self):
        if self._model is not None:
            return self._torch, self._model, self._transform
        status = probe_depth(MIDAS_SMALL_ID)
        if not status["available"]:
            raise DepthUnavailable(status["detail"])
        import torch

        try:
            # MiDaS small loads this backbone repo without trust_repo, which would wait on a
            # terminal prompt. Trusting it here first lets the nested load run unattended.
            torch.hub.help(MIDAS_BACKBONE_REPO, "tf_efficientnet_lite3", trust_repo=True)
            model = torch.hub.load("intel-isl/MiDaS", "MiDaS_small", trust_repo=True)
            transforms = torch.hub.load("intel-isl/MiDaS", "transforms", trust_repo=True)
        except Exception as exc:
            raise DepthUnavailable(
                f"MiDaS small weights could not be loaded ({exc}). No other depth source was substituted."
            ) from exc
        device = status.get("device") or "cpu"
        model.eval()
        model.to(device)
        self._torch = torch
        self._model = model
        self._transform = transforms.small_transform
        self.device = device
        return torch, model, self._transform


def probe_depth(depth_id: str | None) -> dict:
    """Report availability without downloading weights."""
    if not depth_id:
        return {"depth_id": None, "available": False, "detail": "No depth model was requested."}
    if depth_id != MIDAS_SMALL_ID:
        return {
            "depth_id": depth_id,
            "available": False,
            "detail": f"Depth model '{depth_id}' is not registered. No substitute was selected.",
        }
    try:
        import timm  # noqa: F401
        import torch
    except ImportError:
        return {
            "depth_id": depth_id,
            "available": False,
            "device": None,
            "detail": "PyTorch and timm are needed for MiDaS small. Depth order was not estimated.",
        }
    device = "cpu"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        device = "mps"
    elif torch.cuda.is_available():
        device = "cuda"
    return {
        "depth_id": depth_id,
        "available": True,
        "device": device,
        "detail": "MiDaS small can be loaded on first use. Weights download when two boxes overlap.",
    }


def load_depth(depth_id: str | None):
    if not depth_id:
        return None
    status = probe_depth(depth_id)
    if not status["available"]:
        raise DepthUnavailable(status["detail"])
    return MidasSmallDepth()
