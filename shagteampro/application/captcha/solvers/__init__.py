from __future__ import annotations

from shagteampro.application.captcha.solvers.base import CaptchaSolver
from shagteampro.application.captcha.solvers.manual import ManualCaptchaSolver
from shagteampro.application.captcha.solvers.image_coordinate import ImageCoordinateCaptchaSolver
from shagteampro.application.captcha.solvers.capsola import CapsolaCaptchaSolver
from shagteampro.application.captcha.solvers.botlab import BotlabCaptchaSolver

__all__ = [
    "CaptchaSolver",
    "ManualCaptchaSolver",
    "ImageCoordinateCaptchaSolver",
    "CapsolaCaptchaSolver",
    "BotlabCaptchaSolver",
]
