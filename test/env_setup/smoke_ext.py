"""Smoke test: JIT-compile a trivial torch CUDA extension with the local toolchain."""
import os
import time

import torch
from torch.utils.cpp_extension import load_inline

cpp = "torch::Tensor add_one(torch::Tensor x);"
cu = r"""
#include <torch/extension.h>
#include <ATen/cuda/CUDAContext.h>
__global__ void k(float* x, int n){ int i = blockIdx.x*blockDim.x+threadIdx.x; if(i<n) x[i]+=1.f; }
torch::Tensor add_one(torch::Tensor x){
  auto y = x.clone();
  int n = y.numel();
  k<<<(n+255)/256,256,0,at::cuda::getCurrentCUDAStream()>>>(y.data_ptr<float>(), n);
  return y;
}
"""

t = time.time()
mod = load_inline(
    name="smoke_ext",
    cpp_sources=cpp,
    cuda_sources=cu,
    functions=["add_one"],
    build_directory=os.environ["SMOKE_BUILD_DIR"],
    extra_cuda_cflags=["-O2"],
    verbose=True,
)
x = torch.zeros(10, device="cuda")
print("RESULT", mod.add_one(x).tolist(), f"{time.time() - t:.1f}s")
