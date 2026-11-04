"""Idempotent project-local timestamp propagation patch for the pinned Wi-Fi source."""
from pathlib import Path

base=Path('/home/sribd/spectrum_agent/vendor/gr-ieee802-11/lib')


def patch(name,edits):
    path=base/name;s=path.read_text()
    if 'SPECTRUM_TIMESTAMP_V1' in s: return
    for old,new in edits:
        if old not in s: raise RuntimeError('Pinned source anchor missing: '+name+' '+old[:50])
        s=s.replace(old,new,1)
    path.write_text('// SPECTRUM_TIMESTAMP_V1 project-local receive provenance\n'+s)


patch('sync_short.cc',[
 ('add_item_tag(0, item, key, value, srcid);','add_item_tag(0, item, key, value, srcid);\n        add_item_tag(0, item, pmt::mp("spectrum_source_sample"), pmt::from_uint64(input_item), srcid);')])
patch('sync_long.cc',[
 ('get_tags_in_range(d_tags, 0, nread, nread + ninput);','get_tags_in_range(d_tags, 0, nread, nread + ninput, pmt::mp("wifi_start"));'),
 ('d_freq_offset_short = pmt::to_double(d_tags.front().value);','d_freq_offset_short = pmt::to_double(d_tags.front().value);\n                std::vector<gr::tag_t> source_tags;\n                get_tags_in_range(source_tags, 0, nread, nread + 1, pmt::mp("spectrum_source_sample"));\n                d_source_valid = !source_tags.empty();\n                if (d_source_valid) d_source_sample = pmt::to_uint64(source_tags.front().value);'),
 ('if (!rel) {','if (!rel) {\n                    if (d_source_valid) add_item_tag(0, nitems_written(0), pmt::mp("spectrum_ltf_sample"),\n                        pmt::from_uint64(d_source_sample + d_frame_start - 16), pmt::mp(name()));'),
 ('int d_frame_start;','int d_frame_start;\n    uint64_t d_source_sample = 0;\n    bool d_source_valid = false;')])
patch('frame_equalizer_impl.h',[
 ('int d_frame_bytes;','int d_frame_bytes;\n    uint64_t d_source_ltf_sample = 0;\n    bool d_source_timestamp_valid = false;')])
patch('frame_equalizer_impl.cc',[
 ('d_current_symbol = 0;','d_current_symbol = 0;\n            std::vector<gr::tag_t> source_tags;\n            get_tags_in_window(source_tags, 0, i, i + 1, pmt::mp("spectrum_ltf_sample"));\n            d_source_timestamp_valid = !source_tags.empty();\n            if (d_source_timestamp_valid) d_source_ltf_sample = pmt::to_uint64(source_tags.front().value);'),
 ('pmt::pmt_t dict = pmt::make_dict();','pmt::pmt_t dict = pmt::make_dict();\n                if (d_source_timestamp_valid) dict = pmt::dict_add(dict, pmt::mp("spectrum_ltf_sample"), pmt::from_uint64(d_source_ltf_sample));')])
print('Project-local Wi-Fi timestamps patched')
