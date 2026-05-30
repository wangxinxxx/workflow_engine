"""Compatibility package for the old requirement_flow import path.

New code should import workflow modules from app.workflow.
"""

from pathlib import Path


__path__ = [str(Path(__file__).resolve().parents[1] / "backend" / "app" / "workflow")]
