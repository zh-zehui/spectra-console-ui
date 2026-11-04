/* File-only FR1 SSB/PBCH adapter, linked to pinned AGPL-3.0-or-later srsRAN PHY. */
#include "srsran/srsran.h"
#include "srsran/phy/sync/ssb.h"
#include "srsran/phy/phch/pbch_msg_nr.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
int main(int argc,char**argv) {
  if(argc!=5)return 2;
  int scs=atoi(argv[2]);double rate=scs==15?7680000:15360000,center=strtod(argv[4],NULL);
  if((scs!=15&&scs!=30)||!isfinite(center)||center<1e6||center>6e9)return 2;
  srsran_ssb_pattern_t pattern=srsran_ssb_pattern_fom_str(argv[3]);
  if((scs==15&&pattern!=SRSRAN_SSB_PATTERN_A)||(scs==30&&pattern!=SRSRAN_SSB_PATTERN_B&&pattern!=SRSRAN_SSB_PATTERN_C))return 2;
  FILE*f=fopen(argv[1],"rb");if(!f)return 2;fseek(f,0,SEEK_END);long bytes=ftell(f);rewind(f);
  if(bytes<(long)(rate*.005*8)||bytes>(long)(rate*.5*8)||bytes%8){fclose(f);return 2;}
  uint32_t n=bytes/8;cf_t*data=srsran_vec_cf_malloc(n);if(!data||fread(data,8,n,f)!=n){fclose(f);free(data);return 2;}fclose(f);
  srsran_ssb_t q={0};srsran_ssb_args_t args={.max_srate_hz=rate,.min_scs=srsran_subcarrier_spacing_15kHz,
    .enable_search=true,.enable_measure=true,.enable_decode=true,.disable_polar_simd=true};
  if(srsran_ssb_init(&q,&args))return 3;
  srsran_ssb_cfg_t cfg={.srate_hz=rate,.center_freq_hz=center,.ssb_freq_hz=center,
    .scs=scs==15?srsran_subcarrier_spacing_15kHz:srsran_subcarrier_spacing_30kHz,.pattern=pattern,
    .duplex_mode=pattern==SRSRAN_SSB_PATTERN_C?SRSRAN_DUPLEX_MODE_TDD:SRSRAN_DUPLEX_MODE_FDD,.periodicity_ms=20};
  if(srsran_ssb_set_cfg(&q,&cfg)){srsran_ssb_free(&q);free(data);return 3;}
  uint32_t step=(uint32_t)(rate*.005),trials=0,evaluated=0,last_start=UINT32_MAX,last_pci=UINT32_MAX,valid=0;
  printf("{\"input_samples\":%u,\"sample_rate_hz\":%.0f,\"scs_khz\":%d,\"pattern\":\"%s\",\"blocks\":[",n,rate,scs,argv[3]);
  for(uint32_t start=0;start+q.ssb_sz<n;start+=step) {
    uint32_t count=n-start;if(count>step+q.ssb_sz)count=step+q.ssb_sz;
    srsran_ssb_search_res_t result={0};trials++;
    if(srsran_ssb_search(&q,data+start,count,&result)){srsran_ssb_free(&q);free(data);return 4;}
    evaluated=start+count;
    if(!result.pbch_msg.crc)continue;
    uint32_t position=start+result.t_offset;
    if(result.N_id==last_pci&&last_start!=UINT32_MAX&&abs((int)position-(int)last_start)<8)continue;
    srsran_mib_nr_t mib={0};
    if(!srsran_pbch_msg_nr_is_mib(&result.pbch_msg)||srsran_pbch_msg_nr_mib_unpack(&result.pbch_msg,&mib))continue;
    uint32_t payload=0;for(unsigned i=0;i<24;i++)payload=(payload<<1)|result.pbch_msg.payload[i];
    printf("%s{\"cell_id\":%u,\"ssb_sample_start\":%u,\"ssb_sample_stop\":%u,\"pbch_crc_pass\":true,"
      "\"ssb_index\":%u,\"half_frame\":%s,\"sfn\":%u,\"scs_common_khz\":%u,\"ssb_offset\":%u,"
      "\"coreset0_index\":%u,\"search_space0_index\":%u,\"cell_barred\":%s,\"mib_payload_hex\":\"%06x\",\"cfo_hz\":%.7g}",
      valid++?",":"",result.N_id,position,position+q.ssb_sz,result.pbch_msg.ssb_idx,result.pbch_msg.hrf?"true":"false",
      mib.sfn,(unsigned)(SRSRAN_SUBC_SPACING_NR(mib.scs_common)/1000),mib.ssb_offset,mib.coreset0_idx,mib.ss0_idx,
      mib.cell_barred?"true":"false",payload,isfinite(result.measurements.cfo_hz)?result.measurements.cfo_hz:0);
    last_start=position;last_pci=result.N_id;
    if(valid>=64)break;
  }
  printf("],\"search_windows\":%u,\"evaluated_prefix_samples\":%u,\"valid_blocks\":%u,\"hit_limit_reached\":%s}\n",trials,evaluated,valid,valid>=64?"true":"false");
  srsran_ssb_free(&q);free(data);return 0;
}
