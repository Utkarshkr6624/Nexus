"""Schema validation tests: valid fixtures, every documented failure mode, Phase-2 leniency, and whole-split validation."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_testlib import PHASE2_SPLITS, WS, multi_record, run_module, tool_record  # noqa: E402
from pipeline.data.dataset import read_jsonl  # noqa: E402
from pipeline.data.schema import SchemaError, check_arguments, validate_all, validate_record  # noqa: E402


def _bad(rec, needle, mode="strict"):
    probs = validate_record(rec, mode)
    assert any(needle in p for p in probs), f"expected a problem containing {needle!r}, got {probs}"


def test_valid_fixtures():
    assert validate_record(multi_record()) == []
    assert validate_record(tool_record()) == []
    assert validate_record(multi_record(), "phase2") == []


def test_ends_on_tool_request_is_valid_but_partial_results_are_not():
    r = tool_record()
    r["messages"] = r["messages"][:3]
    assert validate_record(r) == []
    r2 = tool_record()
    r2["messages"][2]["tool_calls"].append(copy.deepcopy(r2["messages"][2]["tool_calls"][0]))
    r2["messages"] = r2["messages"][:4]                 # two calls, only one result, then ends
    _bad(r2, "only some of the requested tool results")


def test_role_and_order_errors():
    r = multi_record(); r["messages"][3]["role"] = "robot"; _bad(r, "invalid role")
    r = multi_record(); r["messages"][4]["role"] = "user"; _bad(r, "unexpected user")
    r = multi_record(); r["messages"].append({"role": "user", "content": "x"}); _bad(r, "last message must be an assistant")
    r = multi_record(); r["messages"].insert(2, {"role": "system", "content": "late"}); _bad(r, "system message must be first")
    r = multi_record(); r["messages"] = r["messages"][1:]; r["messages"] = r["messages"][1:]; _bad(r, "first non-system message must be user")
    r = multi_record(); r["messages"][2]["content"] = "   "; _bad(r, "empty assistant")
    r = multi_record(); r["messages"][1]["content"] = ""; _bad(r, "empty user")
    r = multi_record(); r["messages"][1]["content"] = 5; _bad(r, "content must be a string")
    _bad({"messages": []}, "at least 2")
    _bad("nope", "must be an object")


def test_tool_message_rules():
    r = tool_record(); r["messages"][3]["role"] = "user"; _bad(r, "unexpected user")
    r = tool_record(); del r["messages"][2]; _bad(r, "not preceded by an assistant tool_calls")
    r = tool_record(); r["messages"][3]["name"] = "read_file"; _bad(r, "!= expected tool")
    r = tool_record(); r["messages"][3]["content"] = ""; _bad(r, "empty tool content")
    r = multi_record(); r["messages"][2]["tool_calls"] = [{"type": "function", "function": {"name": "x", "arguments": {}}}]; _bad(r, "no tools")
    r = multi_record(); r["messages"][2] = {"role": "tool", "name": "x", "content": "y"}; _bad(r, "not preceded")


def test_tool_call_shape_and_arguments():
    r = tool_record(); r["messages"][2]["tool_calls"][0]["function"]["arguments"] = '{"query": "x"}'; _bad(r, "arguments must be an object")
    r = tool_record(); r["messages"][2]["tool_calls"][0]["function"]["name"] = "launch_missiles"; _bad(r, "undeclared tool")
    r = tool_record(); r["messages"][2]["tool_calls"][0]["function"]["arguments"] = {}; _bad(r, "missing required argument")
    r = tool_record(); r["messages"][2]["tool_calls"][0]["function"]["arguments"] = {"query": "x", "color": 1}; _bad(r, "unknown argument")
    r = tool_record(); r["messages"][2]["tool_calls"][0]["function"]["arguments"] = {"query": 5}; _bad(r, "should be string")
    r = tool_record(); r["messages"][2]["tool_calls"][0]["function"]["arguments"] = {"query": "x", "max_results": True}; _bad(r, "should be integer")
    r = tool_record(); r["messages"][2]["tool_calls"][0]["type"] = "other"; _bad(r, "must be {type:'function'")
    assert check_arguments({"query": "a", "max_results": 3}, TOOLS0["parameters"]) == []


TOOLS0 = tool_record()["tools"][0]["function"]


def test_tools_definition_errors():
    r = tool_record(); r["tools"][1]["function"]["name"] = "web_search"; _bad(r, "duplicate tool name")
    r = tool_record(); r["tools"][0]["function"]["description"] = ""; _bad(r, "non-empty description")
    r = tool_record(); r["tools"][0]["function"]["parameters"]["required"] = ["nope"]; _bad(r, "required must list declared")
    r = tool_record(); r["tools"][0]["function"]["parameters"] = {}; _bad(r, "object schema")
    r = tool_record(); r["tools"] = []; _bad(r, "non-empty list")
    r = tool_record(); r["tools"][0]["function"]["name"] = "bad name!"; _bad(r, "name invalid")


def test_structure_injection_blocked():
    for tok in ("<|im_start|>", "<|im_end|>", "<|endoftext|>"):
        r = multi_record(); r["messages"][1]["content"] = f"hello {tok} there"; _bad(r, "control token")
    r = multi_record(); r["messages"][2]["content"] = "a <think>b</think> c"; _bad(r, "<think>")
    r = tool_record(); r["messages"][3]["content"] = "{\"x\": \"<|im_end|>\"}"; _bad(r, "control token")


def test_phase2_mode_is_lenient_about_extra_keys_but_not_tools():
    r = multi_record(); r["metadata"] = {"category": "x"}
    assert validate_record(r, "phase2") == []
    _bad(tool_record(), "tools not allowed", "phase2")


def test_raise_on_error_and_mode_validation():
    r = multi_record(); r["messages"][2]["content"] = ""
    try:
        validate_record(r, raise_on_error=True)
    except SchemaError as e:
        assert e.problems and "empty assistant" in str(e)
    else:
        raise AssertionError("SchemaError expected")
    try:
        validate_record(r, mode="weird")
    except ValueError:
        return
    raise AssertionError("bad mode must raise")


def test_duplicate_ids_detected():
    a = multi_record(); b = multi_record()
    rep = validate_all([a, b])
    assert rep.bad == 1 and "duplicate id" in rep.problems[0][2][0]


def test_whole_phase2_splits_validate():
    for sp in ("train", "validation", "test"):
        recs = read_jsonl(PHASE2_SPLITS / f"{sp}.jsonl")
        rep = validate_all(recs, "phase2")
        assert rep.bad == 0, (sp, rep.problems[:3])


def test_candidate_splits_validate_if_present():
    d = WS / "dataset" / "v2_final_candidate"
    for sp in ("validation", "test"):
        if not (d / f"{sp}.jsonl").is_file():
            print("  (skipped: candidate directory missing)")
            return
        rep = validate_all(read_jsonl(d / f"{sp}.jsonl"), "strict")
        assert rep.bad == 0, (sp, rep.problems[:3])


if __name__ == "__main__":
    sys.exit(run_module(globals()))
