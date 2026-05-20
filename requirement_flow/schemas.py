
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Annotated, Dict, List, Optional, TypedDict


def utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def clean_string_list(items: Optional[List]) -> List[str]:
    cleaned: List[str] = []
    for item in items or []:
        text = str(item).strip()
        if text:
            cleaned.append(text)
    return cleaned


def dedupe_list_preserve_order(items: Optional[List]) -> List:
    seen = set()
    result: List = []
    for item in items or []:
        marker = item.strip() if isinstance(item, str) else item if isinstance(item, (int, float, bool, type(None))) else repr(item)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(item)
    return result


def merge_nested_dicts(left: Optional[Dict], right: Optional[Dict]) -> Dict:
    base: Dict = dict(left or {})
    for key, value in (right or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merged = dict(base[key])
            merged.update(value)
            base[key] = merged
        elif isinstance(value, list) and isinstance(base.get(key), list):
            base[key] = [*base[key], *value]
        else:
            base[key] = value
    return base


def merge_node_input_dicts(left: Optional[Dict[str, List]], right: Optional[Dict[str, List]]) -> Dict[str, List]:
    base: Dict[str, List] = {
        str(key): dedupe_list_preserve_order(clean_string_list(value))
        for key, value in (left or {}).items()
    }
    for key, value in (right or {}).items():
        key = str(key)
        if isinstance(value, list):
            base[key] = dedupe_list_preserve_order([*base.get(key, []), *clean_string_list(value)])
        else:
            base[key] = value
    return base


def take_latest(left, right):
    return left if right is None else right


def append_lists(left: Optional[List], right: Optional[List]) -> List:
    return [*(left or []), *(right or [])]


@dataclass
class StepSpec:
    id: str
    label: str
    target_file: str
    review_required: bool
    instructions: str

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class NodeStatus(TypedDict, total=False):
    label: str
    status: str
    updated_at: str
    note: str


class RequirementState(TypedDict, total=False):
    workflow_type: str
    thread_id: str
    requirement_dir: str
    template_dir: str
    title: str
    requirement_name: str
    tapd_id: str
    tapd_url: str
    predecessor_requirements: List[str]
    brief: str
    interactive_review: bool
    external_contexts: Annotated[Dict[str, str], merge_nested_dicts]
    node_inputs: Annotated[Dict[str, List[str]], merge_node_input_dicts]
    node_statuses: Annotated[Dict[str, NodeStatus], merge_nested_dicts]
    artifacts: Annotated[Dict[str, str], merge_nested_dicts]
    source_script_info: Annotated[Dict[str, str], merge_nested_dicts]
    local_sql_path: Annotated[str, take_latest]
    modified_sql_path: Annotated[str, take_latest]
    self_test_report_path: Annotated[str, take_latest]
    latest_interrupt: Annotated[Dict[str, object], merge_nested_dicts]
    current_step: Annotated[str, take_latest]
    status: Annotated[str, take_latest]
    last_error: Annotated[Optional[str], take_latest]
