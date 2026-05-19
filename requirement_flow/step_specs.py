
import json
from typing import List

from .config import STEP_SPECS_PATH
from .schemas import StepSpec


def load_step_specs() -> List[StepSpec]:
    raw = json.loads(STEP_SPECS_PATH.read_text(encoding="utf-8"))
    return [StepSpec(**item) for item in raw]

