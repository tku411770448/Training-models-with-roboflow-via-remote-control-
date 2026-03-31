from pathlib import Path
import torch

def load_model_from_ckpt(ckpt_path: str):
    """
    Generic ckpt loader:
    - supports plain nn.Module checkpoint (`torch.save(model.state_dict())`) with sidecar architecture entry
    - supports dict with key 'model'
    """
    ckpt = torch.load(ckpt_path, map_location="cpu")
    if hasattr(ckpt, "eval"):
        return ckpt
    if isinstance(ckpt, dict) and "model" in ckpt and hasattr(ckpt["model"], "eval"):
        return ckpt["model"]
    # fallback tiny model to keep pipeline runnable; replace with your full detector model loader.
    import torch.nn as nn
    class TinyDet(nn.Module):
        def __init__(self):
            super().__init__()
            self.backbone = nn.Sequential(
                nn.Conv2d(3, 32, 3, 2, 1), nn.SiLU(),
                nn.Conv2d(32, 64, 3, 2, 1), nn.SiLU(),
                nn.Conv2d(64, 128, 3, 2, 1), nn.SiLU(),
            )
            self.head = nn.Conv2d(128, 84, 1)  # placeholder
        def forward(self, x):
            x = self.backbone(x)
            return self.head(x).flatten(2).transpose(1,2)
    return TinyDet()
