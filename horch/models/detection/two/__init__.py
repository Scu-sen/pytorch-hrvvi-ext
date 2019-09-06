from math import log

import torch
import torch.nn as nn
import torch.nn.functional as F

from horch.common import detach, tuplify
from horch.ops import _concat, inverse_sigmoid
from horch.models.utils import bias_init_constant, weight_init_normal
from horch.models.modules import Sequential, Conv2d
from horch.models.detection.head import to_pred
from horch.train.trainer import set_training


class RPNHead(nn.Module):
    r"""
    RPN Head of Faster R-CNN.

    Parameters
    ----------
    num_anchors : int or tuple of ints
        Number of anchors of every level, e.g., ``(4,6,6,6,6,4)`` or ``6``
    in_channels : int
        Number of input channels.
    f_channels : int
        Number of feature channels.
    lite : bool
        Whether to replace conv3x3 with depthwise seperable conv.
        Default: False
    """

    def __init__(self, num_anchors, in_channels, f_channels=256, lite=False):
        super().__init__()
        kernel_size = 5 if lite else 3
        self.conv = Conv2d(
            in_channels, f_channels, kernel_size=kernel_size,
            norm_layer='default', activation='default', depthwise_separable=lite)
        self.loc_conv = Conv2d(f_channels, num_anchors * 4, kernel_size=1)
        self.cls_conv = Conv2d(f_channels, num_anchors * 2, kernel_size=1)

        bias_init_constant(self.cls_conv, inverse_sigmoid(0.01))

    def forward(self, *ps):
        loc_preds = []
        cls_preds = []
        for p in ps:
            p = self.conv(p)
            loc_p = to_pred(self.loc_conv(p), 4)
            loc_preds.append(loc_p)

            cls_p = to_pred(self.cls_conv(p), 2)
            cls_preds.append(cls_p)
        loc_p = _concat(loc_preds, dim=1)
        cls_p = _concat(cls_preds, dim=1)
        return loc_p, cls_p


class Box2FCHead(nn.Module):

    def __init__(self, num_classes, in_channels, f_channels=256):
        super().__init__()
        self.fc1 = Conv2d(in_channels, f_channels, kernel_size=1,
                          norm_layer='default', activation='default')
        self.fc2 = Conv2d(f_channels, f_channels, kernel_size=1,
                          norm_layer='default', activation='default')
        self.loc_fc = Conv2d(f_channels, 4, kernel_size=1)
        weight_init_normal(self.loc_fc, 0, 0.001)
        self.cls_fc = Conv2d(f_channels, num_classes, kernel_size=1)
        weight_init_normal(self.cls_fc, 0, 0.01)

    def forward(self, *ps):
        r"""
        p : torch.Tensor
            (batch_size * num_rois, C, 14, 14)
        """
        ps = [self.fc1(p.view(p.size(0), -1, 1, 1)) for p in ps]
        if len(ps) != 1:
            p = torch.stack(ps).max(dim=0)[0]
        else:
            p = ps[0]
        p = self.fc2(p)
        loc_p = self.loc_fc(p).squeeze()
        cls_p = self.cls_fc(p).squeeze()
        return loc_p, cls_p


class RPN(Sequential):
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

    def __init__(self, backbone, fpn, head, matcher=None, inference=None):
        super().__init__(inference=inference)
        self.backbone = backbone
        self.fpn = fpn
        self.head = head
        self.matcher = matcher
        self._inference = inference
        self._e2e = True

    def forward(self, inputs, targets=None):
        cs = self.backbone(inputs)
        features = self.fpn(*tuplify(cs))
        outputs = self.head(*tuplify(features))
        if self.training and self.matcher:
            assert targets is not None, "Targets must be provided in training."
            targets = self.matcher(features, targets)
            return (*outputs, *targets)
        else:
            return outputs

    def region_proposal(self, inptus, targets):
        cs = self.backbone(inptus)
        features = self.fpn(*tuplify(cs))
        features = tuplify(features)
        loc_p, cls_p = self.head(*features)
        rois = self._inference(detach(loc_p), detach(cls_p))
        if self.training:
            if self.matcher:
                assert targets is not None, "Targets must be provided in training."
                targets = self.matcher(features, targets)
                return (features, rois, loc_p, cls_p) + targets
            else:
                if self._e2e:
                    return features, rois, loc_p, cls_p
                else:
                    return features, rois
        else:
            return features, rois


class FasterRCNN(nn.Module):
    def __init__(self, rpn, roi_matcher, roi_pool, box_head, inference):
        super().__init__()
        self.rpn = rpn
        self.roi_matcher = roi_matcher
        self.roi_pool = roi_pool
        self.box_head = box_head
        self._inference = inference
        # self._position_sensitive = "PS" in type(self.roi_pool).__name__

    def forward(self, x, image_gts=None):
        ps, rois, rpn_loc_p, rpn_cls_p, rpn_loc_t, rpn_cls_t, ignore = \
            self.rpn.region_proposal(x, image_gts)

        loc_t, cls_t, rois = self.roi_matcher(rois, image_gts)

        ps = [self.roi_pool(p, rois) for p in ps]
        # if self._position_sensitive:
        #     ps = [p.view(p.size(0), -1, 1, 1) for p in ps]
        preds = self.box_head(*ps)
        return preds + (loc_t, cls_t, rpn_loc_p, rpn_cls_p, rpn_loc_t, rpn_cls_t, ignore)

    def inference(self, x):
        self.eval()
        with torch.no_grad():
            ps, rois = self.rpn.region_proposal(x)
            ps = [self.roi_pool(p, rois) for p in ps]
            # if self._position_sensitive:
            #     ps = [p.view(p.size(0), -1, 1, 1) for p in ps]
            preds = self.box_head(*ps)
        image_dets = self._inference(rois[..., 1:], *preds)
        set_training(self)
        return image_dets


class MaskHead(nn.Module):
    r"""
    Light head only for R-CNN, not for one-stage detector.
    """

    def __init__(self, num_classes, f_channels=256, lite=False):
        super().__init__()
        self.conv1 = Conv2d(f_channels, f_channels, kernel_size=3,
                            norm_layer='default', activation='default', depthwise_separable=lite)
        self.conv2 = Conv2d(f_channels, f_channels, kernel_size=3,
                            norm_layer='default', activation='default', depthwise_separable=lite)
        self.conv3 = Conv2d(f_channels, f_channels, kernel_size=3,
                            norm_layer='default', activation='default', depthwise_separable=lite)
        self.conv4 = Conv2d(f_channels, f_channels, kernel_size=3,
                            norm_layer='default', activation='default', depthwise_separable=lite)
        self.deconv = Conv2d(f_channels, f_channels, kernel_size=2, stride=2,
                             norm_layer='default', activation='default', depthwise_separable=lite, transposed=True)
        self.mask_fc = Conv2d(f_channels, num_classes, kernel_size=1)

    def forward(self, ps):
        r"""
        p : torch.Tensor
            (batch_size * num_rois, C, 7, 7)
        """
        ps = [self.conv1(p) for p in ps]
        p = torch.stack(ps).max(dim=0)[0]
        p = self.conv2(p)
        p = self.conv3(p)
        p = self.conv4(p)
        p = self.deconv(p)
        mask_p = self.mask_fc(p)
        return mask_p


class MaskRCNN(nn.Module):
    def __init__(self, match_anchors, rpn, roi_match, roi_pool, box_head, mask_head, inference):
        super().__init__()
        self.match_anchors = match_anchors
        self.rpn = rpn
        self.roi_match = roi_match
        self.roi_pool = roi_pool
        self.box_head = box_head
        self.mask_head = mask_head
        self._inference = inference
        self._position_sensitive = "PS" in type(self.roi_pool).__name__

    def forward(self, x, image_gts=None):
        rpn_loc_t, rpn_cls_t, ignore = self.match_anchors(image_gts)

        ps, rois, rpn_loc_p, rpn_cls_p = self.rpn.region_proposal(x)

        loc_t, cls_t, mask_t, rois = self.roi_match(rois, image_gts)

        ps = [self.roi_pool(p, rois) for p in ps]
        if self._position_sensitive:
            ps = [p.view(p.size(0), -1, 1, 1) for p in ps]
        loc_p, cls_p = self.box_head(ps)

        pos = cls_t != 0
        mask_p = self.mask_head([p[pos] for p in ps])

        return loc_p, cls_p, mask_p, loc_t, cls_t, mask_t, rpn_loc_p, rpn_cls_p, rpn_loc_t, rpn_cls_t, ignore

    def inference(self, x):
        b = x.size(0)
        self.eval()
        with torch.no_grad():
            ps, rois = self.rpn.region_proposal(x)
            ps = [self.roi_pool(p, rois) for p in ps]
            if self._position_sensitive:
                ps = [p.view(p.size(0), -1, 1, 1) for p in ps]
            loc_p, cls_p = self.box_head(ps)

            ps = [p.view(b, -1, *p.size()[1:]) for p in ps]

            def predict_mask(i, indices):
                return self.mask_head([p[i][indices] for p in ps])

        image_dets = self._inference(rois[..., 1:], loc_p, cls_p, predict_mask)
        self.train()
        return image_dets
