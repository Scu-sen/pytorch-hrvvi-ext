import torch
import torch.nn as nn

from horch.models.modules import Sequential
from horch.models.detection.ssd import SSD
from horch.models.detection.retinanet import RetinaNet

from horch.common import tuplify
from horch.train.trainer import set_training


class OneStageDetector(Sequential):
    r"""
    A simple composation of backbone, head, inference and optional fpn.

    Parameters
    ----------
    backbone : nn.Module
        Backbone network from `horch.models.detection.backbone`.
    head : nn.Module
        Head of the detector from `horch.models.detection.head`.
    inference
        A function or callable to inference on the outputs of the `head`.
        For most cases, use `horch.detection.one.AnchorBasedInference`.
    fpn : nn.Module
        Optional feature enhance module from `horch.models.detection.enhance`.
    """

    def __init__(self, backbone, fpn, head, inference=None):
        super().__init__()
        self.backbone = backbone
        self.fpn = fpn
        self.head = head
        self._inference = inference

    def forward(self, inputs, targets=None):
        cs = self.backbone(inputs)
        features = self.fpn(*tuplify(cs))
        outputs = self.head(*tuplify(features))
        return outputs

    def inference(self, inputs):
        self.eval()
        with torch.no_grad():
            cs = self.backbone(inputs)
            features = self.fpn(*tuplify(cs))
            preds = self.head(*tuplify(features))
        if self._inference:
            preds = self._inference(*tuplify(preds))
        set_training(self)
        return preds


def split_levels(levels, split_at=5):
    levels = tuplify(levels)
    lo = levels[0]
    hi = levels[-1]
    assert levels == tuple(range(lo, hi + 1))
    basic_levels = tuple(range(lo, min(hi, split_at) + 1))
    extra_levels = tuple(range(max(lo, split_at + 1), hi + 1))
    return basic_levels, extra_levels
