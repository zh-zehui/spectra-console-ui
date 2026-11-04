"""Expose CRC-valid HT/VHT headers without widening the 20MHz data decoder."""
from pathlib import Path
import os
root=Path(os.environ.get('SPECTRUM_ROOT','/home/sribd/spectrum_agent'))
p=root/'vendor/gr-wifi-modern/lib/demod_impl.cc';s=p.read_text()
if 'spectrum_headers' not in s:
    s=s.replace('message_port_register_out(pmt::mp("spectrum_status"));',
        'message_port_register_out(pmt::mp("spectrum_status"));\n      message_port_register_out(pmt::mp("spectrum_headers"));',1)
    anchor='            if(signalCheckVhtA(d_sigVhtABits))'
    insert='''            // Header observation is independent from payload-decoder support.
            auto publish_header = [&](const char* format, const uint8_t* bits) {
              pmt::pmt_t h=pmt::make_dict();
              h=pmt::dict_add(h,pmt::mp("format"),pmt::mp(format));
              h=pmt::dict_add(h,pmt::mp("bits"),pmt::init_u8vector(48,bits));
              h=pmt::dict_add(h,pmt::mp("sample_start"),pmt::from_uint64(spectrum_start));
              h=pmt::dict_add(h,pmt::mp("sample_stop"),pmt::from_uint64(spectrum_stop));
              h=pmt::dict_add(h,pmt::mp("lsig_mcs"),pmt::from_long(d_nSigLMcs));
              h=pmt::dict_add(h,pmt::mp("lsig_length"),pmt::from_long(d_nSigLLen));
              message_port_pub(pmt::mp("spectrum_headers"),h);
            };
            const bool wide_vht_valid = d_sigVhtABits[2]==1 && d_sigVhtABits[23]==1 && d_sigVhtABits[33]==1
              && checkBitCrc8(d_sigVhtABits,34,&d_sigVhtABits[34]);
            if(wide_vht_valid) publish_header("VHT",d_sigVhtABits);
'''
    assert anchor in s;s=s.replace(anchor,insert+anchor,1)
    anchor='              if(signalCheckHt(d_sigHtBits))'
    insert='''              if(!wide_vht_valid && d_sigHtBits[26]==1 && checkBitCrc8(d_sigHtBits,34,&d_sigHtBits[34]))
                publish_header("HT",d_sigHtBits);
'''
    assert anchor in s;s=s.replace(anchor,insert+anchor,1)
    p.write_text(s)
print('Wide HT/VHT header telemetry ready; original payload support guards retained.')
