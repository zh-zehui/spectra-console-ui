from pathlib import Path
p=Path('/home/sribd/spectrum_agent/vendor/gr-wifi-modern/lib/demod_impl.cc')
text=p.read_text();marker='// Spectrum finite-stream scheduler fix'
if marker not in text:
    old='ninput_items_required[0] = noutput_items;'
    assert text.count(old)==1
    text=text.replace(old,'''// One OFDM input symbol can expand into hundreds of coded output bits.
      // Requesting noutput input samples deadlocks finite high-MCS recordings.
      if(d_sDemod==DEMOD_S_DEMOD) ninput_items_required[0]=d_m.nSymSamp+1;
      else if(d_sDemod==DEMOD_S_FORMAT) ninput_items_required[0]=160;
      else if(d_sDemod==DEMOD_S_VHT) ninput_items_required[0]=80+d_m.nLTF*80+80;
      else if(d_sDemod==DEMOD_S_HT) ninput_items_required[0]=80+d_m.nLTF*80;
      else ninput_items_required[0]=1;''')
    old='d_nProc = 0;'
    assert text.count(old)==1
    text=text.replace(old,'set_min_noutput_items(624);\n      '+old)
    p.write_text(marker+'\n'+text)
text=p.read_text();marker='// Spectrum short-GI FFT reference fix'
if marker not in text:
    old='demod_impl::nonLegacyChanUpdate(const gr_complex* sig1)\n    {\n      fftDemod(&sig1[C8P_SYM_SAMP_SHIFT], d_fftLtfOut1);'
    assert text.count(old)==1
    new='demod_impl::nonLegacyChanUpdate(const gr_complex* sig1)\n    {\n      '+marker+'\n      fftDemod(&sig1[d_m.nSymSamp==72 ? 0 : C8P_SYM_SAMP_SHIFT], d_fftLtfOut1);'
    p.write_text(text.replace(old,new))
