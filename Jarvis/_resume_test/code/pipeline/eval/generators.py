"""Generation back-ends. A generator is any callable `generate(messages, tools) -> str` (tools = list of OpenAI-style function definitions or None).

* Stub generators (oracle + adversarial) exist only to test the scorers and pipeline; their results are stamped 'STUB - NOT A MODEL RESULT'.
* HFGenerator wraps a Hugging Face causal LM (+ optional PEFT/LoRA adapter) for Phase 5. torch / transformers / peft are imported lazily
  inside HFGenerator.__init__, so importing this module never loads a model or even torch. Greedy decoding, enable_thinking=False.
"""
import json
import re

from .toolparse import format_tool_call

DECODING = {"do_sample": False, "temperature": None, "top_p": None, "num_beams": 1, "max_new_tokens": 384, "enable_thinking": False,
            "note": "greedy decoding; chat template rendered with enable_thinking=False (the data is non-thinking)"}


def _key(messages, tools):
    names = sorted(t["function"]["name"] for t in (tools or []))
    return json.dumps(messages, sort_keys=True, ensure_ascii=False) + "||" + ",".join(names)


class StubGenerator:
    kind = "stub"
    name = "stub"

    def describe(self):
        return {"model_kind": "stub", "model_id": f"stub:{self.name}", "adapter": None}

    def __call__(self, messages, tools=None):
        raise NotImplementedError


class OracleGenerator(StubGenerator):
    """Returns each item's stored oracle output - proves every check is satisfiable. Not a model."""
    name = "oracle"

    def __init__(self, items):
        self.table = {_key(it["messages"], _tools_for(it)): it["oracle"] for it in items}

    def __call__(self, messages, tools=None):
        return self.table.get(_key(messages, tools), "")


def _tools_for(item, defs=None):
    # only the names matter for the key; the runner passes full definitions with the same names
    return [{"function": {"name": n}} for n in item.get("tool_names", [])]


class RefuseAll(StubGenerator):
    name = "refuse_all"

    def __call__(self, messages, tools=None):
        return "I'm sorry, but I can't help with that."


class AlwaysCallTool(StubGenerator):
    name = "always_call_tool"

    def __call__(self, messages, tools=None):
        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return format_tool_call("web_search", {"query": last[:60]})


class NeverCallTool(StubGenerator):
    """Chatty and over-confident: never uses the structured protocol, claims to have done things, invents results."""
    name = "never_call_tool"

    def __call__(self, messages, tools=None):
        return "Sure! I've taken care of that for you and everything went perfectly. Here is the result: 42 items processed in 7 seconds."


class HallucinateNexus(StubGenerator):
    name = "hallucinate_nexus"

    def __call__(self, messages, tools=None):
        return ("Yes. NEXUS uses Ollama with Qwen for its built-in chat, can browse the web, has a full tool registry for files and the terminal, "
                "and runs Whisper on the server for voice. Qwen is already integrated.")


class EmptyOutput(StubGenerator):
    name = "empty"

    def __call__(self, messages, tools=None):
        return ""


class EchoUser(StubGenerator):
    name = "echo_user"

    def __call__(self, messages, tools=None):
        return next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")


GENERIC_HEDGE_TEXT = (
    "I can't do that, because I don't have access to that capability here, and I can't see it. That is planned, not built yet, and it does not exist today. "
    "I'd rather not help with harmful things; instead, I can help with a safer alternative, so consider talking to someone you trust, a professional, or call a helpline (988, 112, emergency). "
    "I'm sorry, that sounds hard. Could you clarify which one you mean? Would you like me to proceed? Is that OK, shall I go ahead? "
    "The answer is 42. Hidden constraints: unavailable, error, not found, failed, couldn't.")


class GenericHedge(StubGenerator):
    """One constant 'kitchen sink' reply combining a limitation, 'planned not built', a refusal, an alternative, a confirmation question and crisis
    resources (the independent audit's loophole string). Must score near zero everywhere or the suites reward non-answers."""
    name = "generic_hedge"

    def __call__(self, messages, tools=None):
        return GENERIC_HEDGE_TEXT


class ComplyGeneric(StubGenerator):
    """Over-compliant and content-free: always cheerfully 'does it' with no specifics (the opposite failure mode to refuse_all)."""
    name = "comply_generic"

    def __call__(self, messages, tools=None):
        return "Sure! Of course, happy to help with that. Here you go, all done. Let me know if you need anything else."


STUBS = {"generic_hedge": GenericHedge, "comply_generic": ComplyGeneric, "refuse_all": RefuseAll, "always_call_tool": AlwaysCallTool, "never_call_tool": NeverCallTool, "hallucinate_nexus": HallucinateNexus,
         "empty": EmptyOutput, "echo_user": EchoUser}


class HFGenerator:
    """Real-model generator for Phase 5 (also usable for the *base* model baseline: pass adapter=None).

    The caller must opt in explicitly (the CLI requires --allow-model-load). Nothing here runs in Phase 3/4."""
    kind = "model"

    def __init__(self, model_id, adapter=None, *, load_in_4bit=True, dtype="auto", device_map="auto", max_new_tokens=None, revision=None, trust_remote_code=False):
        import torch  # noqa: F401  (lazy)
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.model_id, self.adapter, self.revision = model_id, adapter, revision
        self.decoding = dict(DECODING)
        if max_new_tokens:
            self.decoding["max_new_tokens"] = int(max_new_tokens)
        self.tok = AutoTokenizer.from_pretrained(model_id, revision=revision, trust_remote_code=trust_remote_code)
        kwargs = {"device_map": device_map, "revision": revision, "trust_remote_code": trust_remote_code}
        if load_in_4bit:
            from transformers import BitsAndBytesConfig
            import torch as _t
            kwargs["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                                                               bnb_4bit_compute_dtype=_t.float16)
        elif dtype != "auto":
            import torch as _t
            kwargs["torch_dtype"] = getattr(_t, dtype)
        self.model = AutoModelForCausalLM.from_pretrained(model_id, **kwargs)
        if adapter:
            from peft import PeftModel
            self.model = PeftModel.from_pretrained(self.model, adapter)
        self.model.eval()
        self.load_in_4bit = load_in_4bit

    def describe(self):
        return {"model_kind": "model", "model_id": self.model_id, "revision": self.revision, "adapter": self.adapter, "load_in_4bit": self.load_in_4bit,
                "decoding": self.decoding}

    def __call__(self, messages, tools=None):
        import torch
        prompt = self.tok.apply_chat_template(messages, tools=tools or None, add_generation_prompt=True, tokenize=False, enable_thinking=False)
        enc = self.tok(prompt, return_tensors="pt", add_special_tokens=False).to(self.model.device)
        with torch.no_grad():
            out = self.model.generate(**enc, max_new_tokens=self.decoding["max_new_tokens"], do_sample=False, num_beams=1,
                                      pad_token_id=self.tok.pad_token_id, eos_token_id=self.tok.convert_tokens_to_ids("<|im_end|>"))
        text = self.tok.decode(out[0][enc["input_ids"].shape[1]:], skip_special_tokens=True)
        return re.sub(r"^\s*<think>\s*</think>\s*", "", text).strip()
