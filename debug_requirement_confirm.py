import json

from requirement_flow.runtime import rerun_step


THREAD_ID = "TAPDNEW5_ff"
NOTE = "https://www.tapd.cn/tapd_fe/20848741/story/detail/1120848741001823055 获取需求"


def main() -> None:
    """
    直接走正式 requirement_confirm 流程，不经过 dashboard / argparse。

    IDEA 里建议打断点的位置:
    1. requirement_flow.runtime.rerun_step
    2. requirement_flow.node_executors.requirement_confirm_prepare
    3. requirement_flow.graph._make_node
    4. requirement_flow.runtime._write_runtime_outputs
    """
    # BREAKPOINT HERE:
    # 从这里启动整条正式链路。适合先在这里停住，确认 thread_id / note 是否正确。
    result = rerun_step(THREAD_ID, "requirement_confirm", NOTE)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
