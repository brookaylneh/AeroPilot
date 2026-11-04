"""The original deterministic meadow heights, which the valley generator reshapes."""

import numpy as np

# The original release is the immutable reference for calibration geometry.
ORIGINAL_WIDTH_M = 1609.344
ORIGINAL_SAMPLES = 2017


def hill(x, y):
    """Northern hill term in metres north/east; the valley reshapes it into Granite Dome."""
    return 36 * np.exp(-(((x * 2 - 1200) / 650) ** 2) - ((y * 2 - 600) / 450) ** 2)


def height(x, y):
    northern_hill = hill(x, y)
    x, y = x * 2, y * 2
    return (
        8
        + 3 * np.sin(x / 470) * np.cos(y / 590)
        + 1.3 * np.sin(y / 95 + x / 180)
        + 24 * np.exp(-(((x + 1100) / 500) ** 2) - ((y - 700) / 650) ** 2)
        + northern_hill
        + 26 * np.exp(-(((x - 600) / 550) ** 2) - ((y + 1400) / 600) ** 2)
        + 0.06 * np.sin(x / 6) * np.sin(y / 7)
    )
