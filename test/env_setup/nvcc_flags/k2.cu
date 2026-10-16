#include <cuda_runtime.h>
#include <vector>
#include <string>
__global__ void k(float* x){ x[threadIdx.x] += 1.f; }
int main(){ std::vector<int> v(3); std::string s="a"; return (int)v.size(); }
