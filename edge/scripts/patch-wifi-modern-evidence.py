"""Project-local native telemetry and source timestamp propagation, pinned v1.0."""
from pathlib import Path
root=Path('/home/sribd/spectrum_agent/vendor/gr-wifi-modern/lib')
marker='// Spectrum evidence v1'
def edit(name,changes):
    p=root/name;text=p.read_text()
    if marker in text:return
    for old,new,count in changes:
        assert text.count(old)==count,(name,old,text.count(old),count)
        text=text.replace(old,new)
    p.write_text(marker+'\n'+text)

fields='\n      uint64_t spectrum_start = 0;\n      uint64_t spectrum_stop = 0;\n'
for name in ('demod_impl.h','decode_impl.h'):
    edit(name,[('// block','// block'+fields,1)])
edit('signal_impl.cc',[
 ('d_nProc = 0;','message_port_register_out(pmt::mp("spectrum_status"));\n      d_nProc = 0;',1),
 ('if(signalCheckLegacy(d_sigLegacyBits, &d_nSigMcs, &d_nSigLen, &d_nSigDBPS))',
  'const bool spectrum_lsig_ok=signalCheckLegacy(d_sigLegacyBits, &d_nSigMcs, &d_nSigLen, &d_nSigDBPS);\n          message_port_pub(pmt::mp("spectrum_status"),pmt::mp(spectrum_lsig_ok ? "lsig_pass" : "lsig_failed"));\n          if(spectrum_lsig_ok)',1),
 ('dict = pmt::dict_add(dict, pmt::mp("seq"), pmt::from_long(d_nSigPktSeq));',
  'dict = pmt::dict_add(dict, pmt::mp("spectrum_start"), pmt::from_uint64(nitems_read(1)+d_nUsed>176 ? nitems_read(1)+d_nUsed-176 : 0));\n            dict = pmt::dict_add(dict,pmt::mp("spectrum_stop"),pmt::from_uint64(nitems_read(1)+d_nUsed+224+d_nSample));\n            dict = pmt::dict_add(dict, pmt::mp("seq"), pmt::from_long(d_nSigPktSeq));',1)])
read_fields='''spectrum_start=pmt::to_uint64(pmt::dict_ref(d_meta,pmt::mp("spectrum_start"),pmt::from_uint64(0)));
          spectrum_stop=pmt::to_uint64(pmt::dict_ref(d_meta,pmt::mp("spectrum_stop"),pmt::from_uint64(0)));
          '''
add_fields='''dict = pmt::dict_add(dict,pmt::mp("spectrum_start"),pmt::from_uint64(spectrum_start));
          dict = pmt::dict_add(dict,pmt::mp("spectrum_stop"),pmt::from_uint64(spectrum_stop));
          '''
edit('demod_impl.cc',[
 ('d_nProc = 0;','message_port_register_out(pmt::mp("spectrum_status"));\n      d_nProc = 0;',1),
 ('int tmpPktSeq =',read_fields+'int tmpPktSeq =',1),
 ('pmt::pmt_t dict = pmt::make_dict();','pmt::pmt_t dict = pmt::make_dict();\n          '+add_fields,1),
 ('// go to vht','message_port_pub(pmt::mp("spectrum_status"),pmt::mp("vht_sig_a_pass"));\n              // go to vht',1),
 ('// go to ht','message_port_pub(pmt::mp("spectrum_status"),pmt::mp("ht_sig_pass"));\n                // go to ht',1)])
edit('decode_impl.cc',[
 ('message_port_register_out(pmt::mp("out"));','message_port_register_out(pmt::mp("out"));\n      message_port_register_out(pmt::mp("spectrum_status"));',1),
 ('t_format = pmt::to_long',read_fields+'t_format = pmt::to_long',1),
 ('pmt::pmt_t tmpMeta = pmt::make_dict();','pmt::pmt_t tmpMeta = pmt::make_dict();\n              tmpMeta=pmt::dict_add(tmpMeta,pmt::mp("spectrum_start"),pmt::from_uint64(spectrum_start));\n              tmpMeta=pmt::dict_add(tmpMeta,pmt::mp("spectrum_stop"),pmt::from_uint64(spectrum_stop));',3),
 ('if (d_crc32.checksum() != 558161692) {','const bool spectrum_crc_ok=d_crc32.checksum()==558161692;\n            message_port_pub(pmt::mp("spectrum_status"),pmt::mp(spectrum_crc_ok ? "fcs_pass" : "fcs_failed"));\n            if (!spectrum_crc_ok) {',2),
 ('tmpEof = tmpBitP[0];','tmpLen=0; // Reset each A-MPDU delimiter, upstream otherwise ORs lengths.\n            tmpEof = tmpBitP[0];',1)])
