
#!/usr/bin/env python3
import argparse, os, json, math
from typing import Dict, List, Tuple
import torch
import torch.nn as nn
import torch.nn.functional as F

# -------- utilities --------
def seed_all(seed: int):
    import random, numpy as np
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed); torch.cuda.manual_seed_all(seed)

def load_model(path: str, device: torch.device):
    obj = torch.load(path, map_location=device)
    if isinstance(obj, nn.Module):
        return obj.to(device)
    if isinstance(obj, dict):
        for k in ("model","ema","net","student","teacher"):
            if isinstance(obj.get(k), nn.Module):
                return obj[k].to(device)
    raise ValueError(f"Unsupported checkpoint format: {path}")

class IdentityHeadAdapter:
    """Fallback adapter to normalize outputs into list[Tensor] format.
    If model returns Tensor -> [Tensor]
    If returns list/tuple -> flatten tensors only
    If returns dict -> use common keys
    """
    def __call__(self, out):
        if torch.is_tensor(out): return [out]
        if isinstance(out, (list,tuple)):
            ts=[]
            for x in out:
                if torch.is_tensor(x): ts.append(x)
                elif isinstance(x,(list,tuple)):
                    ts.extend([t for t in x if torch.is_tensor(t)])
            return ts
        if isinstance(out, dict):
            for k in ("preds","output","outputs","logits","det"):
                if k in out:
                    return self(out[k])
        return []

def _match_shapes(s: torch.Tensor, t: torch.Tensor):
    if s.shape == t.shape: return s, t
    if s.ndim == t.ndim == 4:
        # match spatial size and channels with interpolation/slice
        if s.shape[-2:] != t.shape[-2:]:
            s = F.interpolate(s, size=t.shape[-2:], mode="bilinear", align_corners=False)
        c = min(s.shape[1], t.shape[1])
        return s[:, :c], t[:, :c]
    # fallback flatten min length
    sf, tf = s.reshape(s.shape[0], -1), t.reshape(t.shape[0], -1)
    m = min(sf.shape[1], tf.shape[1])
    return sf[:, :m], tf[:, :m]

def kd_logits_loss(s_outs: List[torch.Tensor], t_outs: List[torch.Tensor], T: float):
    n = min(len(s_outs), len(t_outs))
    if n == 0: return torch.tensor(0.0, device=s_outs[0].device if s_outs else "cpu")
    loss = 0.0
    for i in range(n):
        s, t = _match_shapes(s_outs[i], t_outs[i].detach())
        if s.ndim > 2:
            s = s.flatten(2).transpose(1,2).reshape(-1, s.shape[1])
            t = t.flatten(2).transpose(1,2).reshape(-1, t.shape[1])
        log_p = F.log_softmax(s / T, dim=-1)
        q = F.softmax(t / T, dim=-1)
        loss = loss + F.kl_div(log_p, q, reduction="batchmean") * (T * T)
    return loss / n

def feature_loss(s_feats: List[torch.Tensor], t_feats: List[torch.Tensor]):
    n = min(len(s_feats), len(t_feats))
    if n == 0: return torch.tensor(0.0, device=s_feats[0].device if s_feats else "cpu")
    l=0.0
    for i in range(n):
        s,t=_match_shapes(s_feats[i], t_feats[i].detach())
        l = l + F.smooth_l1_loss(s, t)
    return l / n

def gt_loss_stub(student_outs: List[torch.Tensor], targets):
    # Replace with detector-specific criterion in TRAIN_ENTRY/QAT_KD_ENTRY custom script.
    if not student_outs: 
        return torch.tensor(0.0, requires_grad=True)
    return sum([x.float().mean()*0 for x in student_outs])

def build_qat_model(student: nn.Module):
    student.train()
    # backend choice
    backend = "fbgemm"
    torch.backends.quantized.engine = backend
    student.qconfig = torch.ao.quantization.get_default_qat_qconfig(backend)
    return torch.ao.quantization.prepare_qat(student, inplace=False)

def main():
    ap = argparse.ArgumentParser("QAT+KD runner (detector-agnostic scaffold)")
    ap.add_argument("--teacher", required=True)
    ap.add_argument("--student", required=True)
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--temperature", type=float, default=3.0)
    ap.add_argument("--alpha-kd", type=float, default=0.6)
    ap.add_argument("--alpha-feat", type=float, default=0.2)
    ap.add_argument("--alpha-gt", type=float, default=0.2)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--output", required=True)
    ap.add_argument("--steps-per-epoch", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    seed_all(args.seed)
    device = torch.device(args.device)

    teacher = load_model(args.teacher, device).eval()
    student_fp = load_model(args.student, device).train()
    student = build_qat_model(student_fp).to(device).train()

    opt = torch.optim.AdamW(student.parameters(), lr=args.lr)
    adapter = IdentityHeadAdapter()

    for ep in range(args.epochs):
        running = 0.0
        for _ in range(args.steps_per_epoch):
            # Placeholder input; in production, replace with real dataloader via custom QAT_KD_ENTRY.
            x = torch.randn(args.batch_size, 3, 640, 640, device=device)
            targets = None
            with torch.no_grad():
                t_raw = teacher(x)
            s_raw = student(x)

            t_outs = adapter(t_raw)
            s_outs = adapter(s_raw)
            # heuristics: reuse outputs as pseudo-features when no hook features are provided
            t_feats, s_feats = t_outs, s_outs

            l_kd = kd_logits_loss(s_outs, t_outs, args.temperature)
            l_feat = feature_loss(s_feats, t_feats)
            l_gt = gt_loss_stub(s_outs, targets)

            loss = args.alpha_kd*l_kd + args.alpha_feat*l_feat + args.alpha_gt*l_gt
            opt.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(student.parameters(), 10.0)
            opt.step()
            running += float(loss.detach().cpu().item())

        print(f"[QAT-KD] epoch={ep+1}/{args.epochs} loss={running/args.steps_per_epoch:.6f}")

    # Save fake-quant and int8-converted checkpoint
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    torch.save({"model": student_fp.state_dict()}, args.output.replace(".pt","_fakequant.pt"))
    student.eval()
    int8_model = torch.ao.quantization.convert(student.cpu().eval(), inplace=False)
    torch.save({"model": int8_model.state_dict()}, args.output.replace(".pt","_int8.pt"))
    print(json.dumps({
        "fakequant_ckpt": args.output.replace(".pt","_fakequant.pt"),
        "int8_ckpt": args.output.replace(".pt","_int8.pt")
    }))

if __name__ == "__main__":
    main()
