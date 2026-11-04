/* Offline sample generator for decoder tests. Never linked to an RF device. */
#include "srsran/srsran.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(int argc,char** argv) {
  if(argc!=3 && argc!=4)return 2;
  FILE* file=fopen(argv[1],"wb");if(!file)return 2;
  cf_t* output[SRSRAN_MAX_PORTS];for(int i=0;i<SRSRAN_MAX_PORTS;i++)output[i]=srsran_vec_cf_malloc(1920);
  srsran_enb_dl_t enb={0};srsran_cell_t cell={.id=42,.nof_prb=6,.nof_ports=1,.cp=SRSRAN_CP_NORM,
    .frame_type=!strcmp(argv[2],"tdd")?SRSRAN_TDD:SRSRAN_FDD,.phich_length=SRSRAN_PHICH_NORM,.phich_resources=SRSRAN_PHICH_R_1};
  if(srsran_enb_dl_init(&enb,output,6)||srsran_enb_dl_set_cell(&enb,cell))return 3;
  for(unsigned tti=0;tti<120;tti++) {
    srsran_dl_sf_cfg_t sf={.tti=tti,.cfi=2,.sf_type=SRSRAN_SF_NORM};
    sf.tdd_config.configured=true;sf.tdd_config.sf_config=2;sf.tdd_config.ss_config=7;
    srsran_enb_dl_put_base(&enb,&sf);
    /* enb_dl_put_base's helper inserts FDD sync positions even for a TDD cell.
     * Place TDD SSS at the end of subframes0/5, PSS in symbol2 of1/6. */
    if(cell.frame_type==SRSRAN_TDD) {
      unsigned index=tti%10,sc=6*12,first=sc/2-31;
      if(index==0 || index==5) {
        memset(enb.sf_symbols[0]+5*sc+first,0,62*sizeof(cf_t));
        memset(enb.sf_symbols[0]+6*sc+first,0,62*sizeof(cf_t));
        for(unsigned k=0;k<62;k++)enb.sf_symbols[0][13*sc+first+k]=index?enb.sss_signal5[k]:enb.sss_signal0[k];
      }
      if(index==1 || index==6)memcpy(enb.sf_symbols[0]+2*sc+first,enb.pss_signal,62*sizeof(cf_t));
    }
    if(argc==4 && !strcmp(argv[3],"no_pbch") && tti%10==0) {
      for(unsigned symbol=7;symbol<11;symbol++)memset(enb.sf_symbols[0]+symbol*72,0,72*sizeof(cf_t));
    }
    srsran_enb_dl_gen_signal(&enb);
    fwrite(output[0],sizeof(cf_t),1920,file);
  }
  srsran_enb_dl_free(&enb);for(int i=0;i<SRSRAN_MAX_PORTS;i++)free(output[i]);fclose(file);return 0;
}
