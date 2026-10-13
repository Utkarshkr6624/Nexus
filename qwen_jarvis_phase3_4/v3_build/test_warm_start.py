"""Self-check for train.warm_start on the tiny smoke model (CPU): weights are copied exactly; a wrong hash is refused."""
import hashlib, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import torch
from pipeline.train import config as C, train as T
from pipeline.train.checkpoint import CheckpointError

cfg = C.load_config(str(Path(__file__).resolve().parent.parent / "configs" / "smoke_test.yaml")) if hasattr(C, "load_config") else C.load(str(Path(__file__).resolve().parent.parent / "configs" / "smoke_test.yaml"))
torch.manual_seed(0)
a = T.attach_lora(T.build_tiny_model(cfg), cfg)
for n, p in a.named_parameters():
    if "lora_B" in n:
        p.data.normal_()
with tempfile.TemporaryDirectory() as d:
    a.save_pretrained(d)
    h = hashlib.sha256((Path(d) / "adapter_model.safetensors").read_bytes()).hexdigest()
    torch.manual_seed(1)
    b = T.attach_lora(T.build_tiny_model(cfg), cfg)
    try:
        T.warm_start(b, d, "0" * 64)
        raise SystemExit("FAIL: wrong hash accepted")
    except CheckpointError:
        pass
    info = T.warm_start(b, d, h)
    sa, sb = {n: p for n, p in a.named_parameters() if "lora" in n}, {n: p for n, p in b.named_parameters() if "lora" in n}
    assert sa and all(torch.equal(sa[n], sb[n]) for n in sa), "LoRA weights differ after warm start"
    assert info["sha256"] == h
print("WARM START SELF-CHECK PASSED", len(sa), "LoRA tensors copied")
