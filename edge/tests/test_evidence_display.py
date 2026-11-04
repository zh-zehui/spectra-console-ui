import unittest
from spectrum_agent.evidence_display import stage_notes


class StageDisplayTests(unittest.TestCase):
    def test_crafter_diagnostics_do_not_invent_lsig_counts(self):
        text=' '.join(stage_notes(dict(verification_profile='wifi_crafter_all',
            verification_stages=dict(strongest_stage='no_valid_frame',counts=dict(invalid_fcs=3,fcs_pass=0)))))
        self.assertIn('MAC FCS失败诊断 3',text)
        self.assertIn('不是所有同步尝试',text)
        self.assertNotIn('L-SIG',text)
    def test_modern_wifi_native_stages_are_not_ble_trial_counts(self):
        text=' '.join(stage_notes(dict(verification_profile='wifi_gr20_siso',
            verification_stages=dict(strongest_stage='phy_header_pass',
                counts=dict(lsig_pass=3,ht_sig_pass=1,fcs_failed=1)))))
        self.assertIn('HT-SIG通过 1',text);self.assertIn('MAC FCS失败 1',text)
        self.assertNotIn('定时相位',text);self.assertNotIn('接入地址候选',text)
    def test_missing_wifi_telemetry_is_unknown_not_header_failure(self):
        text=' '.join(stage_notes(dict(protocol='wifi_legacy_ofdm',valid_frames=0)))
        self.assertIn('失败阶段未知',text)
        self.assertNotIn('头部失败',text)

    def test_ble_trial_counts_and_incomplete_coverage_are_explicit(self):
        text=' '.join(stage_notes(dict(verification_stages=dict(strongest_stage='header_plausible',
            counts=dict(crc_failed=12),unique_valid_frames=0),
            analysis_coverage=dict(input_samples=1000,processed_samples=500,complete=False))))
        self.assertIn('非头部CRC',text);self.assertIn('CRC失败 12',text)
        self.assertIn('定时相位',text);self.assertIn('处理不完整',text)
