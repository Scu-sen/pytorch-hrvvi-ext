import torch
import torch.nn as nn
import torch.nn.functional as F
from horch.models.gan.common import ResBlock
from torch.nn.utils import spectral_norm


class ResNetGenerator(nn.Module):

    def __init__(self, in_channels, channels, out_channels, use_sn=True):
        super().__init__()
        self.in_channels = in_channels
        self.dense = nn.Linear(in_channels, 6 * 6 * channels * 8)
        self.conv = nn.Sequential(
            ResBlock(channels * 8, channels * 4, 'up', use_sn=use_sn),
            ResBlock(channels * 4, channels * 2, 'up', use_sn=use_sn),
            ResBlock(channels * 2, channels * 1, 'up', use_sn=use_sn),
            nn.BatchNorm2d(channels * 1),
            nn.ReLU(True),
            nn.Conv2d(channels * 1, out_channels, kernel_size=3, padding=1),
            nn.Tanh(),
        )

        if use_sn:
            spectral_norm(self.dense)
            spectral_norm(self.conv[-2])

    def forward(self, x):
        x = self.dense(x).view(x.size(0), -1, 6, 6)
        x = self.conv(x)
        return x


class ResNetDiscriminator(nn.Module):

    def __init__(self, in_channels, channels, out_channels, use_sn=True):
        super().__init__()
        self.out_channels = out_channels
        self.conv = nn.Sequential(
            ResBlock(in_channels, channels * 1, 'down', use_sn=use_sn),
            ResBlock(channels * 1, channels * 2, 'down', use_sn=use_sn),
            ResBlock(channels * 2, channels * 4, 'down', use_sn=use_sn),
            ResBlock(channels * 4, channels * 8, 'down', use_sn=use_sn),
            ResBlock(channels * 8, channels * 16, None),
            nn.ReLU(True),
            nn.AdaptiveAvgPool2d(1),
            nn.Conv2d(channels * 16, out_channels, kernel_size=1),
        )

        if use_sn:
            spectral_norm(self.conv[-1])

    def forward(self, x):
        x = self.conv(x).view(x.size(0), -1)
        return x
