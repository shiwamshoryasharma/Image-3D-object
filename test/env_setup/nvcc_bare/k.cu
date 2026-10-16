__global__ void k(float* x){ x[threadIdx.x] += 1.f; }
int main(){ return 0; }
