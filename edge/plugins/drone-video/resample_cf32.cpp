// Standard polyphase FIR resampling,zero-phase alignment matching resample_poly.
// This independent receive-only kernel does not link to any Wi-Fi receiver.
#include <complex>
#include <cstdint>
#include <vector>
#include <algorithm>
#if defined(__aarch64__)
#include <arm_neon.h>
#endif

static std::complex<float> dot(const std::complex<float>* x,const float* h,int n){
    int j=0;float re=0.f,im=0.f;
#if defined(__aarch64__)
    float32x4_t ai0=vdupq_n_f32(0),aq0=ai0,ai1=ai0,aq1=ai0;
    for(;j+8<=n;j+=8){
        auto a=vld2q_f32(reinterpret_cast<const float*>(x+j));
        auto b=vld2q_f32(reinterpret_cast<const float*>(x+j+4));
        auto ha=vld1q_f32(h+j),hb=vld1q_f32(h+j+4);
        ai0=vfmaq_f32(ai0,a.val[0],ha);aq0=vfmaq_f32(aq0,a.val[1],ha);
        ai1=vfmaq_f32(ai1,b.val[0],hb);aq1=vfmaq_f32(aq1,b.val[1],hb);
    }
    re=vaddvq_f32(vaddq_f32(ai0,ai1));im=vaddvq_f32(vaddq_f32(aq0,aq1));
#endif
    for(;j<n;++j){re+=x[j].real()*h[j];im+=x[j].imag()*h[j];}
    return {re,im};
}

extern "C" int64_t spectrum_resample_cf32(const float* input,int64_t count,
        int up,int down,const float* taps,int ntaps,float* output,int64_t capacity) {
    if(!input||!output||!taps||count<1||up<1||down<1||ntaps<1)return -1;
    const int64_t nout=(count*up+down-1)/down;
    if(nout>capacity)return -2;
    const auto* x=reinterpret_cast<const std::complex<float>*>(input);
    auto* y=reinterpret_cast<std::complex<float>*>(output);
    std::vector<std::vector<float>> phases(up);
    for(int phase=0;phase<up;++phase){
        for(int k=phase;k<ntaps;k+=up)phases[phase].push_back(taps[k]*up);
        std::reverse(phases[phase].begin(),phases[phase].end());
    }
    const int64_t half=(ntaps-1)/2;
    for(int64_t m=0;m<nout;++m){
        const int64_t t=m*down+half,base=t/up;
        const auto& h=phases[t%up];const int k=h.size();const int64_t start=base-k+1;
        if(start>=0&&base<count){
            y[m]=dot(x+start,h.data(),k);
        }else{
            std::complex<float> sum(0.f,0.f);
            for(int j=0;j<k;++j)if(start+j>=0&&start+j<count)sum+=x[start+j]*h[j];
            y[m]=sum;
        }
    }
    return nout;
}
