from __future__ import annotations

from shagteampro.application.captcha.solvers.base import CaptchaSolver
from shagteampro.application.captcha.solvers.manual import ManualCaptchaSolver
from shagteampro.application.captcha.solvers.capsola import CapsolaCaptchaSolver

__all__ = [
    "CaptchaSolver",
    "ManualCaptchaSolver",
    "CapsolaCaptchaSolver",
]
