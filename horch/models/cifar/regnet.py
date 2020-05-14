import math

import torch
import torch.nn as nn

from horch.models.modules import get_activation, Conv2d
from horch.models.utils import profile


class SE(nn.Module):
    """Squeeze-and-Excitation (SE) block: AvgPool, FC, ReLU, FC, Sigmoid."""

    def __init__(self, channels, reduction):
        super().__init__()
        c = channels // reduction
        self.avg_pool = nn.AdaptiveAvgPool2d((1, 1))
        self.f_ex = nn.Sequential(
            Conv2d(channels, c, 1),
            get_activation(),
            Conv2d(c, channels, 1),
            nn.Sigmoid(),
        )

    def forward(self, x):
        return x * self.f_ex(self.avg_pool(x))


class Bottleneck(nn.Module):

    def __init__(self, in_channels, out_channels, stride, groups, use_se):
        super().__init__()
        self.use_se = use_se

        self.conv1 = Conv2d(in_channels, out_channels, kernel_size=1,
                            norm_layer='default', activation='default')
        self.conv2 = Conv2d(out_channels, out_channels, kernel_size=3, stride=stride, groups=groups,
                            norm_layer='default', activation='default')
        if self.use_se:
            self.se = SE(out_channels, 4)
        self.conv3 = Conv2d(out_channels, out_channels, kernel_size=1,
                            norm_layer='default')
        self.shortcut = Conv2d(in_channels, out_channels, kernel_size=1, stride=stride,
                               norm_layer='default') if stride != 1 or in_channels != out_channels else nn.Identity()
        self.relu = get_activation('default')

    def init_weights(self):
        self.conv3[1].weight.data.fill_(0.0)

    def forward(self, x):
        identity = self.shortcut(x)
        x = self.conv1(x)
        x = self.conv2(x)
        if self.use_se:
            x = self.se(x)
        x = self.conv3(x)
        x = x + identity
        x = self.relu(x)
        return x


class RegNet(nn.Module):

    def __init__(self, stem_channels=32, channels_per_stage=(96, 256, 640), units_per_stage=(4, 8, 2),
                 channels_per_group=16, use_se=True, num_classes=10):
        super().__init__()

        self.conv = Conv2d(3, stem_channels, kernel_size=3,
                           norm_layer='default', activation='default')

        cs = channels_per_stage
        gs = [c // channels_per_group for c in cs]
        us = units_per_stage

        self.layer1 = self._make_layer(
            stem_channels, cs[0], us[0], stride=1, groups=gs[0], use_se=use_se)
        self.layer2 = self._make_layer(
            cs[0], cs[1], us[1], stride=2, groups=gs[1], use_se=use_se)
        self.layer3 = self._make_layer(
            cs[1], cs[2], us[2], stride=2, groups=gs[2], use_se=use_se)

        self.avgpool = nn.AdaptiveAvgPool2d(1)
        self.fc = nn.Linear(cs[2], num_classes)

    def _make_layer(self, in_channels, out_channels, num_units, stride, groups, use_se):
        layers = [Bottleneck(in_channels, out_channels, stride=stride, groups=groups, use_se=use_se)]
        for i in range(1, num_units):
            layers.append(
                Bottleneck(out_channels, out_channels, stride=1, groups=groups, use_se=use_se))
        return nn.Sequential(*layers)

    def init_weights(self):
        for m in self.modules():
            if isinstance(m, Bottleneck):
                m.init_weights()

    def forward(self, x):
        x = self.conv(x)

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)

        x = self.avgpool(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x


def test_regnet():
    x = torch.randn(1, 3, 32, 32)

    net = RegNet(32, (96, 256, 640), [4, 8, 2], 16, use_se=True)
    assert profile(net, (x,))[1] == 3920266
