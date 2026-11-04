"""Offline frequency-scope regressions. No UHD, GNU Radio or network access."""
import json,unittest,uuid,contextlib,types
from pathlib import Path
from unittest.mock import patch,MagicMock
import numpy as np
from spectrum_agent.common import ROOT,Context,DomainError,dump
from spectrum_agent.frequency_scope import describe,bounds
from spectrum_agent import coarse,dsp
from spectrum_agent.service import normalize
from spectrum_agent.continuation import attach_replay


class FrequencyScopeTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context('scope_sim_'+uuid.uuid4().hex[:10])
        self.meta=dict(center_hz=2400e6,sample_rate_hz=200e6,requested_span_hz=100e6,
            analog_bandwidth_hz=120e6,device_info=dict(rx_id='UBX-160 v1 (0x007a)'),
            sample_count=1000000,duration_s=.005,simulated=True,complete=True,gaps=[],
            segments=[dict(file_start=0,count=1000000,hardware_start_s=10)])

    def fixture(self):
        n=self.meta['sample_count'];fs=self.meta['sample_rate_hz'];rng=np.random.default_rng(1901)
        t=np.arange(n)/fs;x=(rng.normal(size=n)+1j*rng.normal(size=n))*.0001
        for index,f in enumerate((2325e6,2375e6,2425e6,2475e6)):
            start=.0005+index*.001;gate=(t>start)&(t<start+.0006)
            x+=.1*gate*np.exp(2j*np.pi*(f-2400e6)*t)
        aid,path=self.ctx.artifact('capture','cf32');x.astype('<c8').tofile(path)
        dump(path.with_suffix('.json'),self.meta)
        self.ctx.register(aid,path,kind='capture',metadata_path=str(path.with_suffix('.json')))
        return aid

    def test_three_ranges_and_legacy_bandwidth_echo(self):
        s=describe(self.meta,dict(center_hz=2400e6,span_hz=160e6))
        self.assertEqual((s['digital_low_hz'],s['digital_high_hz']),(2300e6,2500e6))
        self.assertEqual((s['frontend_low_hz'],s['frontend_high_hz']),(2320e6,2480e6))
        self.assertEqual((s['analysis_low_hz'],s['analysis_high_hz']),(2320e6,2480e6))
        self.assertFalse(s['frontend_passband_calibrated'])
        with self.assertRaises(DomainError):describe(self.meta,dict(center_hz=2380e6,span_hz=160e6))
        with self.assertRaises(DomainError):describe(self.meta,dict(center_hz=2400e6,span_hz=200e6))

    def test_prompt_numbers_remain_provisional_until_model_resolution(self):
        req=normalize(dict(prompt='analysis range2320-2480,target10MHz',constraints=dict(center_hz=2400e6,span_hz=100e6)))
        self.assertEqual(req['constraints']['span_hz'],100e6)
        self.assertEqual(req['task_intent']['status'],'pending_model')

    def test_numeric_resolved_constraints_still_validate_hardware(self):
        req=normalize(dict(prompt='model output',constraints=dict(center_hz=2400e6,span_hz=160e6,sample_rate_hz=200e6)),model_resolved=True)
        self.assertEqual(req['constraints']['span_hz'],160e6)
        with self.assertRaises(DomainError):
            normalize(dict(prompt='invalid numeric output',constraints=dict(center_hz=2400e6,span_hz=160e6,sample_rate_hz=160e6)),model_resolved=True)

    def test_full_stft_replay_expands_without_changing_iq_or_lo(self):
        aid=self.fixture();original=self.ctx.resolve(aid);original_meta=Path(original['metadata_path']).read_bytes()
        dump(self.ctx.run/'outcome.json',dict(status='succeeded'))
        request=normalize(dict(prompt='Offline full160MHz analysis',source='synthetic',
            replay_from_task_id=self.ctx.task_id,constraints=dict(center_hz=2400e6,span_hz=160e6)))
        target=Context('scope_replay_'+uuid.uuid4().hex[:10]);dump(target.run/'request.json',request)
        attach_replay(target,request)
        _,effective=dsp.load_iq(target,aid)
        self.assertEqual(effective['center_hz'],2400e6)
        self.assertEqual(effective['requested_span_hz'],100e6)
        self.assertEqual(bounds(effective),(2320e6,2480e6))
        result=coarse.segment(target,aid,fft_size=2048,time_bin_s=.00005,frequency_bin_hz=1e6)
        seg=json.loads(Path(target.resolve(result['segmentation_id'])['path']).read_text())
        self.assertEqual(seg['coverage']['requested_range_hz'],[2320e6,2480e6])
        for f in (2325e6,2375e6,2425e6,2475e6):
            self.assertTrue(any(g['freq_low_hz']<=f<=g['freq_high_hz'] for g in seg['signal_groups']),f)
        self.assertEqual(Path(original['metadata_path']).read_bytes(),original_meta)
        self.assertEqual(Path(target.resolve(aid)['path']).stat().st_ino,Path(original['path']).stat().st_ino)
        dump(ROOT/'docs/testing/frequency-scope-offline-result.json',dict(simulated=True,hardware_access=False,
            fixture_task_id=target.task_id,capture_id=aid,
            frequency_scope=effective['frequency_scope'],detected_regions=seg['signal_groups'],
            all_four_tones_detected=True,original_metadata_unchanged=True))

    def test_offset_analysis_uses_original_lo_for_fft_axis(self):
        aid=self.fixture();dump(self.ctx.run/'request.json',dict(constraints=dict(center_hz=2380e6,span_hz=100e6)))
        result=coarse.segment(self.ctx,aid,fft_size=2048,time_bin_s=.00005,frequency_bin_hz=1e6)
        seg=json.loads(Path(self.ctx.resolve(result['segmentation_id'])['path']).read_text())
        self.assertEqual(seg['coverage']['requested_range_hz'],[2330e6,2430e6])
        self.assertTrue(all(g['freq_low_hz']>=2330e6 and g['freq_high_hz']<=2430e6 for g in seg['signal_groups']))
        for f in (2375e6,2425e6):self.assertTrue(any(g['freq_low_hz']<=f<=g['freq_high_hz'] for g in seg['signal_groups']))

    def test_outside_analysis_is_rejected_before_any_filter_backend_import(self):
        aid=self.fixture();dump(self.ctx.run/'request.json',dict(constraints=dict(center_hz=2400e6,span_hz=100e6)))
        with self.assertRaisesRegex(DomainError,'analysis range'):dsp.extract(self.ctx,aid,2325e6,1e6)

    def test_fixed_frontend160_capture_configuration_passes_without_tuning_filter(self):
        from spectrum_agent import acquisition
        class Range:
            def __init__(self,lo,hi):self.lo,self.hi=lo,hi
            def start(self):return self.lo
            def stop(self):return self.hi
        radio=MagicMock();radio.get_rx_freq_range.return_value=Range(10e6,6e9)
        radio.get_rx_bandwidth_range.return_value=Range(160e6,160e6)
        radio.get_rx_gain_range.return_value=Range(0,31.5)
        radio.get_usrp_rx_info.return_value=dict(rx_id='UBX-160')
        radio.get_rx_rate.return_value=200e6;radio.get_rx_freq.return_value=2400e6;radio.get_rx_gain.return_value=20
        radio.set_rx_freq.return_value=types.SimpleNamespace()
        radio.get_rx_stream.side_effect=RuntimeError('SIMULATION_STOP_BEFORE_RF')
        @contextlib.contextmanager
        def fake_device():yield radio
        uhd=types.SimpleNamespace(types=types.SimpleNamespace(TuneRequest=lambda value:value),usrp=types.SimpleNamespace(StreamArgs=lambda *args:types.SimpleNamespace()))
        dump(self.ctx.run/'request.json',normalize(dict(prompt='160MHz capture config only',constraints=dict(center_hz=2400e6,span_hz=160e6))))
        with patch.dict('sys.modules',{'uhd':uhd}),patch.object(acquisition,'device',fake_device),patch.object(acquisition.time,'sleep'):
            with self.assertRaisesRegex(RuntimeError,'STOP_BEFORE_RF'):acquisition.capture(self.ctx,2400e6,160e6,.005,20,200e6)
        radio.set_rx_bandwidth.assert_not_called()
        radio.set_rx_rate.assert_called_once_with(200e6)


if __name__=='__main__':unittest.main()
