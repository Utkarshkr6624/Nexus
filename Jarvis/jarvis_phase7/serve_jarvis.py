"""Jarvis generation server: Qwen/Qwen3-8B + one LoRA adapter, behind a tiny local HTTP API.

The NEXUS backend (app/jarvis/model_client.py) calls:
  GET  /health   -> {"status": "ok", "model_id": "Qwen/Qwen3-8B", "adapter_sha256": "<sha256>", "runtime": {...}}
  POST /generate -> {"text": "<raw generation>", "adapter_sha256": "<sha256>"}   body: {"messages": [...], "tools": [...]}

Generation matches the Phase 6 evaluation decoding: chat template with enable_thinking=False, greedy, no sampling.
The adapter's sha256 is computed at start-up and must equal --expect-sha256, so the server can never silently serve a
different checkpoint. Binds to 127.0.0.1 only.

Two runtimes:

* transformers (default): bitsandbytes 4-bit nf4, as in the Phase 6 evaluation. Needs a CUDA GPU with roughly 6+ GB
  free; it cannot run on the 4 GB RTX 3050 of the development machine.
* llama.cpp (``--llama-server``): the official Qwen/Qwen3-8B-GGUF Q4_K_M base with the same adapter converted to a GGUF
  LoRA (``runtime/convert_adapter.py``), partially offloaded to the GPU. Runs on the development machine. Its
  quantization differs from the evaluation's nf4, so its outputs are not the evaluated outputs. The GGUF LoRA is only
  accepted if ``runtime/LORA_GGUF.json`` records that it was converted from the verified adapter.

usage:
  python serve_jarvis.py --adapter E:/Nexo/Jarvis/jarvis_v31_remediation_train/runs/qwen3_8b_jarvis_v3_1_remediation/checkpoint-139/adapter \
                         --expect-sha256 d754a3c6b77fb80bf24e67527d5b32f04606d8496e9e7c2b9d7cccef48f48fa8 [--port 8765]
  python serve_jarvis.py --adapter ... --expect-sha256 ... --verify-only     # hash check without loading any weights
  python serve_jarvis.py --adapter ... --expect-sha256 ... --llama-server runtime/llama.cpp/llama-server.exe \
                         --gguf runtime/models/Qwen3-8B-Q4_K_M.gguf --lora-gguf runtime/models/jarvis_v31_139_lora.gguf
"""
import argparse
import atexit
import hashlib
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL_ID = "Qwen/Qwen3-8B"
MAX_BODY = 2_000_000
HERE = Path(__file__).resolve().parent
LORA_MANIFEST = HERE / "runtime" / "LORA_GGUF.json"


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def adapter_sha256(adapter_dir: Path) -> str:
    return file_sha256(adapter_dir / "adapter_model.safetensors")


def load(adapter_dir: Path):
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16,
                               bnb_4bit_use_double_quant=True)
    base = AutoModelForCausalLM.from_pretrained(MODEL_ID, quantization_config=quant, device_map="auto")
    model = PeftModel.from_pretrained(base, str(adapter_dir))
    model.eval()
    return tok, model


def prompt_for(tok, messages, tools) -> str:
    return tok.apply_chat_template(messages, tools=tools, add_generation_prompt=True, tokenize=False,
                                   enable_thinking=False)


def transformers_generator(tok, model, max_new_tokens):
    def generate(messages, tools):
        import torch

        ids = tok(prompt_for(tok, messages, tools), return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(**ids, max_new_tokens=max_new_tokens, do_sample=False, pad_token_id=tok.pad_token_id)
        text = tok.decode(out[0][ids["input_ids"].shape[1]:], skip_special_tokens=False)
        return text.split("<|im_end|>")[0]

    return generate


def _post(url, body, timeout):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def verified_lora(lora: Path, adapter_sha: str) -> str:
    """The GGUF LoRA's sha256, if the conversion manifest ties it to the verified adapter; else raise."""
    rec = json.loads(LORA_MANIFEST.read_text(encoding="utf-8"))
    if rec.get("adapter_sha256") != adapter_sha:
        raise SystemExit(f"REFUSING: {LORA_MANIFEST.name} was converted from {rec.get('adapter_sha256')}, not {adapter_sha}")
    sha = file_sha256(lora)
    if sha != rec.get("lora_gguf_sha256"):
        raise SystemExit(f"REFUSING: {lora.name} sha256 {sha} does not match {LORA_MANIFEST.name}")
    return sha


def llama_generator(a, adapter_sha):
    """Start llama-server (base GGUF + verified LoRA) as a child process and generate through /completion.

    The prompt is rendered by llama-server's /apply-template from the template embedded in the official GGUF.
    evidence/template_parity_result.json shows it byte-identical to the HF tokenizer's apply_chat_template
    (enable_thinking=False) for plain, tool-bearing and tool-round-trip conversations; rendering it here instead of
    importing transformers saves about 1 GB of RAM, which is what lets the model and NEXUS run together on 15.6 GB.
    """
    lora_sha = verified_lora(a.lora_gguf, adapter_sha)
    port = a.port + 1
    proc = subprocess.Popen(
        [str(a.llama_server), "-m", str(a.gguf), "--lora", str(a.lora_gguf), "-ngl", str(a.gpu_layers),
         "-c", str(a.ctx), "-np", "1", "-fa", "on", "-ctk", "q8_0", "-ctv", "q8_0", "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=open(HERE / "runtime" / "llama-server.log", "w"),
    )
    atexit.register(proc.terminate)
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + a.load_timeout
    while True:
        if proc.poll() is not None:
            raise SystemExit(f"llama-server exited with code {proc.returncode}; see runtime/llama-server.log")
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=5) as r:
                if r.status == 200:
                    break
        except (urllib.error.URLError, OSError):
            pass
        if time.monotonic() > deadline:
            proc.terminate()
            raise SystemExit("llama-server did not become healthy in time; see runtime/llama-server.log")
        time.sleep(2)

    def generate(messages, tools):
        body = {"messages": messages, "chat_template_kwargs": {"enable_thinking": False}}
        if tools:
            body["tools"] = tools
        prompt = _post(f"{base}/apply-template", body, timeout=30)["prompt"]
        out = _post(f"{base}/completion", {
            "prompt": prompt, "n_predict": a.max_new_tokens, "temperature": 0,
            "top_k": 1, "cache_prompt": True, "stop": ["<|im_end|>"],
            # DRY penalises repeated *sequences* ("draft draft draft"), which greedy Q4 decoding fell into
            # until the token limit; its default sequence breakers (quotes, colons, newlines) keep JSON intact.
            "dry_multiplier": a.dry_multiplier,
        }, timeout=a.generate_timeout)
        return out["content"]

    runtime = {"engine": "llama.cpp", "base_gguf": a.gguf.name, "base_gguf_sha256": a.gguf_sha256,
               "lora_gguf_sha256": lora_sha, "gpu_layers": a.gpu_layers, "ctx": a.ctx, "kv_cache": "q8_0",
               "dry_multiplier": a.dry_multiplier,
               "note": "Q4_K_M base, q8_0 KV cache, DRY repetition penalty; differs from the Phase 6 nf4 greedy evaluation"}
    return generate, runtime


def make_handler(generate, sha, runtime):
    lock = threading.Lock()  # one generation at a time on one GPU

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):  # never log prompts
            sys.stderr.write(f"{self.command} {self.path} {args[1] if len(args) > 1 else ''}\n")

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"status": "ok", "model_id": MODEL_ID, "adapter_sha256": sha,
                                        "runtime": runtime})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/generate":
                return self._send(404, {"error": "not found"})
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                return self._send(413, {"error": "bad body size"})
            try:
                req = json.loads(self.rfile.read(n))
                messages, tools = req["messages"], req.get("tools") or None
            except (ValueError, KeyError):
                return self._send(400, {"error": "invalid request"})
            try:
                with lock:
                    text = generate(messages, tools)
            except Exception as exc:  # report, never crash the server
                return self._send(502, {"error": f"generation failed: {type(exc).__name__}"})
            self._send(200, {"text": text, "adapter_sha256": sha})

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True, type=Path)
    ap.add_argument("--expect-sha256", required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--max-new-tokens", type=int, default=512)  # about 55 s at 9.6 tokens/s, inside --generate-timeout
    ap.add_argument("--dry-multiplier", type=float, default=0.8, help="llama.cpp DRY anti-repetition; 0 disables")
    ap.add_argument("--verify-only", action="store_true")
    ap.add_argument("--llama-server", type=Path, help="use llama.cpp instead of transformers+bitsandbytes")
    ap.add_argument("--gguf", type=Path)
    ap.add_argument("--gguf-sha256", help="expected sha256 of --gguf (checked at start-up)")
    ap.add_argument("--lora-gguf", type=Path)
    ap.add_argument("--gpu-layers", type=int, default=16)  # measured on the 4 GB RTX 3050 with an 8k q8_0 KV cache
    ap.add_argument("--ctx", type=int, default=8192)
    ap.add_argument("--load-timeout", type=int, default=600)
    ap.add_argument("--generate-timeout", type=int, default=115)
    a = ap.parse_args(argv)
    sha = adapter_sha256(a.adapter)
    if sha != a.expect_sha256:
        print(f"REFUSING: adapter sha256 {sha} != expected {a.expect_sha256}", file=sys.stderr)
        return 2
    print(f"adapter verified: {sha}", flush=True)
    if a.verify_only:
        return 0
    if a.llama_server:
        if not (a.gguf and a.lora_gguf):
            ap.error("--llama-server needs --gguf and --lora-gguf")
        if a.gguf_sha256 and file_sha256(a.gguf) != a.gguf_sha256:
            print(f"REFUSING: {a.gguf.name} does not match --gguf-sha256", file=sys.stderr)
            return 2
        generate, runtime = llama_generator(a, sha)
    else:
        tok, model = load(a.adapter)
        generate, runtime = transformers_generator(tok, model, a.max_new_tokens), {"engine": "transformers-nf4"}
    server = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(generate, sha, runtime))
    print(f"serving {MODEL_ID} + adapter {sha[:12]} ({runtime['engine']}) on http://127.0.0.1:{a.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
