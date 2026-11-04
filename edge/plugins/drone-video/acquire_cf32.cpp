// Receive-only FFT overlap-save matched filter. Own FFTW plans,independent of Wi-Fi.
#include <fftw3.h>
#include <complex>
#include <vector>
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>

struct Acquirer {
    int n=2048,k;double ref_energy=0;
    fftwf_complex *in,*freq,*out,*kernel;
    fftwf_plan forward,backward;
    explicit Acquirer(const float* ref,int length):k(length){
        in=fftwf_alloc_complex(n);freq=fftwf_alloc_complex(n);out=fftwf_alloc_complex(n);kernel=fftwf_alloc_complex(n);
        forward=fftwf_plan_dft_1d(n,in,freq,FFTW_FORWARD,FFTW_ESTIMATE);
        backward=fftwf_plan_dft_1d(n,freq,out,FFTW_BACKWARD,FFTW_ESTIMATE);
        std::memset(in,0,n*sizeof(fftwf_complex));
        for(int j=0;j<k;++j){in[j][0]=ref[2*(k-1-j)];in[j][1]=-ref[2*(k-1-j)+1];ref_energy+=ref[2*j]*ref[2*j]+ref[2*j+1]*ref[2*j+1];}
        fftwf_execute(forward);std::memcpy(kernel,freq,n*sizeof(fftwf_complex));
    }
    ~Acquirer(){fftwf_destroy_plan(forward);fftwf_destroy_plan(backward);fftwf_free(in);fftwf_free(freq);fftwf_free(out);fftwf_free(kernel);}
};
extern "C" void* spectrum_zc_acquire_create(const float* ref,int length){
    if(!ref||length<8||length>512)return nullptr;
    return new Acquirer(ref,length);
}
extern "C" void spectrum_zc_acquire_destroy(void* handle){delete static_cast<Acquirer*>(handle);}
extern "C" int spectrum_zc_acquire_run(void* handle,const float* input,int count,float threshold,int distance,
        int64_t* indices,float* scores,int capacity){
    if(!handle||!input||!indices||!scores||count<1||distance<1)return -1;
    auto& a=*static_cast<Acquirer*>(handle);const int valid=count-a.k+1;if(valid<1)return 0;
    std::vector<float> rho(valid);double energy=0;
    auto power=[&](int i){return double(input[2*i])*input[2*i]+double(input[2*i+1])*input[2*i+1];};
    for(int j=0;j<a.k;++j)energy+=power(j);
    for(int begin=0;begin<valid;begin+=a.n-a.k+1){
        const int copied=std::min(a.n,count-begin);
        std::memcpy(a.in,input+2*begin,copied*sizeof(fftwf_complex));
        std::memset(a.in+copied,0,(a.n-copied)*sizeof(fftwf_complex));fftwf_execute(a.forward);
        for(int j=0;j<a.n;++j){float re=a.freq[j][0]*a.kernel[j][0]-a.freq[j][1]*a.kernel[j][1];
            float im=a.freq[j][0]*a.kernel[j][1]+a.freq[j][1]*a.kernel[j][0];a.freq[j][0]=re;a.freq[j][1]=im;}
        fftwf_execute(a.backward);const int amount=std::min(a.n-a.k+1,valid-begin);
        for(int j=0;j<amount;++j){int i=begin+j;if(i)energy+=power(i+a.k-1)-power(i-1);
            float re=a.out[j+a.k-1][0],im=a.out[j+a.k-1][1];
            rho[i]=float(std::sqrt((double(re)*re+double(im)*im)/std::max(energy*a.ref_energy,1e-30))/a.n);}
    }
    std::vector<int> peaks;
    for(int i=1;i+1<valid;++i)if(rho[i]>=threshold&&rho[i]>rho[i-1]&&rho[i]>=rho[i+1])peaks.push_back(i);
    std::sort(peaks.begin(),peaks.end(),[&](int x,int y){return rho[x]>rho[y];});
    std::vector<int> accepted;
    for(int p:peaks){bool near=false;for(int q:accepted)if(std::abs(p-q)<distance){near=true;break;}
        if(!near)accepted.push_back(p);}
    if(int(accepted.size())>capacity)return -2;
    std::sort(accepted.begin(),accepted.end());
    for(int i=0;i<int(accepted.size());++i){indices[i]=accepted[i];scores[i]=rho[accepted[i]];}
    return accepted.size();
}
