
import os
from pathlib import Path
from typing import Dict, List


DEFAULT_SKILL_ROOTS = [
    Path.home() / ".codex" / "skills",
    Path("/Users/zz/IdeaProjects/dw-skills/skills"),
]


def configured_skill_roots() -> List[Path]:
    roots: List[Path] = []
    env_value = os.getenv("CODEX_SKILL_ROOTS", "").strip()
    if env_value:
        for raw in env_value.split(":"):
            path = Path(raw).expanduser()
            if path.exists():
                roots.append(path)
    for root in DEFAULT_SKILL_ROOTS:
        if root.exists() and root not in roots:
            roots.append(root)
    return roots


def discover_skills() -> Dict[str, Dict[str, str]]:
    catalog: Dict[str, Dict[str, str]] = {}
    for root in configured_skill_roots():
        for skill_md in root.rglob("SKILL.md"):
            skill_dir = skill_md.parent
            name = skill_dir.name
            if name in catalog:
                continue
            catalog[name] = {
                "name": name,
                "path": str(skill_md),
                "root": str(root),
            }
    return catalog


def skill_excerpt(skill_md_path: str, limit: int = 2400) -> str:
    text = Path(skill_md_path).read_text(encoding="utf-8")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n\n[truncated]"


def match_preferred_skills(preferred: List[str]) -> Dict[str, List[Dict[str, str]]]:
    catalog = discover_skills()
    available: List[Dict[str, str]] = []
    missing: List[Dict[str, str]] = []
    for name in preferred:
        found = catalog.get(name)
        if found:
            available.append(found)
        else:
            missing.append({"name": name})
    return {
        "available": available,
        "missing": missing,
    }
