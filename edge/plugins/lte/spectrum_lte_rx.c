/* Receive-only file adapter for the pinned AGPL-3.0-or-later srsRAN PHY library.
 * No RF backend is linked. JSON reports separate synchronization from PBCH CRC. */
#include "srsran/srsran.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

typedef struct { cf_t* data; size_t count, position, padding; unsigned calls; } recording_t;

static int receive_file(void* handle, cf_t* buffers[SRSRAN_MAX_CHANNELS], uint32_t count, srsran_timestamp_t* timestamp)
{
  recording_t* r=(recording_t*)handle;
  if (++r->calls>200) return -1;
  size_t available=r->count-r->position;
  size_t copied=available<count?available:count;
  if (!copied) return -1;
  memcpy(buffers[0],r->data+r->position,copied*sizeof(cf_t));
  if (copied<count) { memset(buffers[0]+copied,0,(count-copied)*sizeof(cf_t)); r->padding+=count-copied; }
  if (timestamp) srsran_timestamp_init(timestamp,r->position/1920000,(double)(r->position%1920000)/1920000.0);
  r->position+=copied;
  return count;
}

int main(int argc,char** argv)
{
  if(argc!=3 || (strcmp(argv[2],"auto") && strcmp(argv[2],"tdd") && strcmp(argv[2],"fdd"))) return 2;
  FILE* f=fopen(argv[1],"rb"); if(!f) return 2;
  fseek(f,0,SEEK_END);long bytes=ftell(f);rewind(f);
  if(bytes<76800*8 || bytes>1920000*8 || bytes%8) { fclose(f);return 2; }
  size_t n=bytes/sizeof(cf_t);cf_t* data=malloc(bytes);
  if(!data || fread(data,sizeof(cf_t),n,f)!=n) { fclose(f);free(data);return 2; } fclose(f);
  unsigned scans=(unsigned)(n/9600);scans=scans>2?scans-2:1;if(scans>80)scans=80;
  printf("{\"input_samples\":%zu,\"sample_rate_hz\":1920000,\"cells\":[",n);
  int emitted=0;size_t consumed[3]={0},search_padding[3]={0};int statuses[3]={0};
  for(int root=0;root<3;root++) {
    recording_t stream={.data=data,.count=n};srsran_ue_cellsearch_t search={0};
    srsran_ue_cellsearch_result_t found={0};
    if(srsran_ue_cellsearch_init_multi(&search,scans,receive_file,1,&stream)) {free(data);return 3;}
    srsran_ue_cellsearch_set_nof_valid_frames(&search,3);
    if(!strcmp(argv[2],"tdd"))srsran_ue_sync_set_frame_type(&search.ue_sync,SRSRAN_TDD);
    if(!strcmp(argv[2],"fdd"))srsran_ue_sync_set_frame_type(&search.ue_sync,SRSRAN_FDD);
    int status=srsran_ue_cellsearch_scan_N_id_2(&search,root,&found);
    consumed[root]=stream.position;search_padding[root]=stream.padding;statuses[root]=status;
    unsigned detections=0,pci_counts[504]={0};
    for(unsigned i=0;i<scans;i++)if(search.candidates[i].peak>0){
      detections++;
      if(search.candidates[i].cell_id<504)pci_counts[search.candidates[i].cell_id]++;
    }
    srsran_ue_cellsearch_free(&search);
    if(status<=0)continue;
    size_t searched=stream.position;stream.position=stream.padding=stream.calls=0;
    srsran_cell_t cell={.id=found.cell_id,.cp=found.cp,.frame_type=found.frame_type,.nof_prb=6,.nof_ports=0,
                        .phich_length=SRSRAN_PHICH_NORM,.phich_resources=SRSRAN_PHICH_R_1};
    srsran_ue_mib_sync_t mib={0};uint8_t payload[SRSRAN_BCH_PAYLOAD_LEN]={0};uint32_t ports=0,sfn=0;int offset=0;
    int decoded=0;
    if(!srsran_ue_mib_sync_init_multi(&mib,receive_file,1,&stream)) {
      if(!srsran_ue_mib_sync_set_cell(&mib,cell)) {
        mib.ue_sync.cfo_current_value=found.cfo/15000;
        mib.ue_sync.cfo_is_copied=true;mib.ue_sync.cfo_correct_enable_find=true;
        srsran_sync_set_cfo_cp_enable(&mib.ue_sync.sfind,false,0);
        decoded=srsran_ue_mib_sync_decode(&mib,(uint32_t)(n/19200),payload,&ports,&offset);
      }
      srsran_ue_mib_sync_free(&mib);
    }
    if(decoded==SRSRAN_UE_MIB_FOUND) { srsran_pbch_mib_unpack(payload,&cell,&sfn);cell.nof_ports=ports; }
    printf("%s{\"cell_id\":%u,\"n_id_2\":%d,\"duplex_mode\":\"%s\",\"cp\":\"%s\","
           "\"sync_detections\":%u,\"matching_cell_detections\":%u,\"sync_mode_fraction\":%.7g,\"psr\":%.7g,\"cfo_hz\":%.7g,\"sync_samples_consumed\":%zu,"
           "\"pbch_crc_pass\":%s,\"mib_samples_consumed\":%zu,\"zero_padding_samples\":%zu",
           emitted++?",":"",found.cell_id,root,found.frame_type==SRSRAN_TDD?"tdd":"fdd",
           found.cp==SRSRAN_CP_EXT?"extended":"normal",detections,found.cell_id<504?pci_counts[found.cell_id]:0,
           isfinite(found.mode)?found.mode:0,isfinite(found.psr)?found.psr:0,
           isfinite(found.cfo)?found.cfo:0,searched,decoded==SRSRAN_UE_MIB_FOUND?"true":"false",stream.position,stream.padding);
    if(decoded==SRSRAN_UE_MIB_FOUND) {
      unsigned bits=0;for(unsigned i=0;i<SRSRAN_BCH_PAYLOAD_LEN;i++)bits=(bits<<1)|payload[i];
      printf(",\"nof_prb\":%u,\"antenna_ports\":%u,\"sfn_base\":%u,\"sfn_offset\":%d,\"mib_payload_hex\":\"%06x\"",cell.nof_prb,ports,sfn,offset,bits);
    }
    printf("}");
  }
  printf("],\"synchronization_trials\":[");
  for(int root=0;root<3;root++)printf("%s{\"n_id_2\":%d,\"samples_consumed\":%zu,\"zero_padding_samples\":%zu,\"search_status\":%d}",
      root?",":"",root,consumed[root],search_padding[root],statuses[root]);
  printf("]}\n");free(data);return 0;
}
