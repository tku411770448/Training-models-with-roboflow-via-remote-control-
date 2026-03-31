
import torch
import torch.nn.functional as F

def normalize_outputs(out):
    if torch.is_tensor(out): return [out]
    if isinstance(out,(list,tuple)):
        r=[]
        for x in out:
            if torch.is_tensor(x): r.append(x)
            elif isinstance(x,(list,tuple)):
                r.extend([t for t in x if torch.is_tensor(t)])
        return r
    if isinstance(out,dict):
        for k in ("preds","output","outputs","logits","det"):
            if k in out:
                return normalize_outputs(out[k])
    return []

def match_tensor(a: torch.Tensor, b: torch.Tensor):
    if a.shape == b.shape: return a,b
    if a.ndim==b.ndim==4:
        if a.shape[-2:] != b.shape[-2:]:
            a = F.interpolate(a, size=b.shape[-2:], mode="bilinear", align_corners=False)
        c=min(a.shape[1], b.shape[1])
        return a[:,:c], b[:,:c]
    af=a.reshape(a.shape[0],-1); bf=b.reshape(b.shape[0],-1); m=min(af.shape[1],bf.shape[1])
    return af[:,:m], bf[:,:m]
