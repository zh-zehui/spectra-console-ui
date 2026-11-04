/* Offline NR SSB fixture only; no RF interface is linked. */
#include "srsran/srsran.h"
#include "srsran/phy/sync/ssb.h"
#include "srsran/phy/phch/pbch_msg_nr.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(int argc,char**argv) {
  if(argc<4||argc>5)return 2;int scs=atoi(argv[2]);double rate=scs==15?7680000:15360000;
  if(scs!=15&&scs!=30)return 2;
  srsran_ssb_t q={0};srsran_ssb_args_t a={.max_srate_hz=rate,.min_scs=srsran_subcarrier_spacing_15kHz,.enable_encode=true,.disable_polar_simd=true};
  if(srsran_ssb_init(&q,&a))return 3;
  srsran_ssb_cfg_t cfg={.srate_hz=rate,.center_freq_hz=2370e6,.ssb_freq_hz=2370e6,
    .scs=scs==15?srsran_subcarrier_spacing_15kHz:srsran_subcarrier_spacing_30kHz,
    .pattern=srsran_ssb_pattern_fom_str(argv[3]),.duplex_mode=!strcmp(argv[3],"C")?SRSRAN_DUPLEX_MODE_TDD:SRSRAN_DUPLEX_MODE_FDD,.periodicity_ms=20};
  if(srsran_ssb_set_cfg(&q,&cfg))return 3;
  uint32_t half=(uint32_t)(rate*.005);cf_t*buffer=srsran_vec_cf_malloc(half);
  FILE*f=fopen(argv[1],"wb");if(!f)return 2;
  for(unsigned index=0;index<8;index++){
    srsran_vec_cf_zero(buffer,half);
    if(index%4==0){
      srsran_mib_nr_t mib={.sfn=513+index/2,.ssb_idx=0,.hrf=false,.scs_common=scs==15?srsran_subcarrier_spacing_15kHz:srsran_subcarrier_spacing_30kHz,
        .ssb_offset=2,.dmrs_typeA_pos=srsran_dmrs_sch_typeA_pos_2,.coreset0_idx=3,.ss0_idx=4,.cell_barred=false,.intra_freq_reselection=true};
      srsran_pbch_msg_nr_t msg={0};if(srsran_pbch_msg_nr_mib_pack(&mib,&msg)||srsran_ssb_add(&q,123,&msg,buffer,buffer))return 3;
      if(argc==5 && !strcmp(argv[4],"no_pbch")){
        uint32_t symbol=q.symbol_sz+q.cp_sz,start=q.l_first[0]*symbol;
        /* Keep only PSS(symbol0). No SSS/PBCH may become a confirmation. */
        memset(buffer+start+symbol,0,3*symbol*sizeof(cf_t));
      }
    }
    fwrite(buffer,8,half,f);
  }
  fclose(f);free(buffer);srsran_ssb_free(&q);return 0;
}
