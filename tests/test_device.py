import pytest
import torch

from app.services.device import select_device


def test_cpu_is_selected_explicitly():
    assert select_device("cpu").type == "cpu"


def test_auto_falls_back_to_cpu(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    assert select_device("auto").type == "cpu"


def test_cuda_fails_fast_when_unavailable(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    with pytest.raises(RuntimeError, match="CUDA"):
        select_device("cuda")
