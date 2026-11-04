import json
from pathlib import Path
import time
import unittest
import uuid
import numpy as np
from scapy.layers.bluetooth4LE import BTLE
from spectrum_agent.common import ROOT,Context,DomainError,dump
from spectrum_agent.worker import execute
from spectrum_agent import dsp,protocols


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.ctx=Context("test_"+uuid.uuid4().hex[:12])

    def test_center_span_and_unknown_priors(self):
        result=execute(self.ctx.task_id,"query_band_priors",dict(center_hz=2.4e9,span_hz=100e6))
        self.assertEqual(result["result"]["range_hz"],[2.35e9,2.45e9])
        self.assertFalse(result["result"]["observed"])
        result=execute(self.ctx.task_id,"query_band_priors",dict(center_hz=900e6,span_hz=10e6))
        self.assertEqual(result["result"]["candidates"],[])

    def test_artifact_isolation(self):
        aid=self.ctx.json_artifact("test",{"secret":"not shared"})
        other=Context("test_"+uuid.uuid4().hex[:12])
        with self.assertRaises(DomainError):
            other.resolve(aid)
        with self.assertRaises(DomainError):
            self.ctx.resolve("../../etc/passwd")

    def test_cancel_and_deadline(self):
        (self.ctx.run/"cancel").touch()
        with self.assertRaisesRegex(DomainError,"cancellation"):
            execute(self.ctx.task_id,"query_band_priors",dict(center_hz=2.4e9,span_hz=10e6))
        (self.ctx.run/"cancel").unlink()
        dump(self.ctx.run/"request.json",dict(deadline_unix=time.time()-1))
        with self.assertRaisesRegex(DomainError,"deadline"):
            self.ctx.check()

    def test_segmentation_maps_known_tone_and_burst(self):
        result=execute(self.ctx.task_id,"make_synthetic_capture",dict())
        aid=result["result"]["capture_id"]
        duplicate=execute(self.ctx.task_id,"make_synthetic_capture",dict())
        self.assertEqual(aid,duplicate["result"]["capture_id"])
        self.assertTrue(duplicate["cached"])
        segmented=execute(self.ctx.task_id,"segment_spectrum",dict(capture_id=aid))["result"]
        groups=segmented["signal_groups"]
        self.assertGreaterEqual(len(groups),2)
        self.assertTrue(any(g["freq_low_hz"]<2.4e9-.22*4e6<g["freq_high_hz"] for g in groups))
        self.assertTrue(all(g["protocol"]=="unknown" for g in groups))
        self.assertGreater(segmented["coverage"]["occupancy_fraction"],0)
        self.assertLessEqual(segmented["coverage"]["occupancy_fraction"],1)

    def test_gnuradio_frequency_translation(self):
        aid=dsp.synthetic(self.ctx)["capture_id"]
        sub=dsp.extract(self.ctx,aid,2.4e9-.22*4e6,200e3)
        x,m=dsp.load_iq(self.ctx,sub["artifact_id"])
        f=np.fft.fftfreq(len(x),1/m["sample_rate_hz"])
        peak=f[np.argmax(abs(np.fft.fft(x)))]
        self.assertLess(abs(peak),2000)
        self.assertEqual(sub["parent_artifact_id"],aid)

    def ble_iq(self,corrupt=False,noise=False):
        # Scapy provides independent packet CRC; experimental modulator only exercises the receive path.
        payload=bytes.fromhex("010203040506")+b"test"
        pdu=bytes([0x02,len(payload)])+payload
        crc=bytearray(BTLE.compute_crc(pdu,init=0x555555))
        if corrupt: crc[0]^=1
        packet_bits=np.unpackbits(np.frombuffer(pdu+crc,dtype=np.uint8),bitorder="little")
        onair=protocols.whitening(packet_bits,37)
        aa=np.unpackbits(np.frombuffer(bytes.fromhex("d6be898e"),dtype=np.uint8),bitorder="little")
        bits=np.concatenate([np.array([0,1]*4,dtype=np.uint8),aa,onair])
        frequency=np.repeat(bits.astype(float)*2-1,4)*250e3
        iq=np.exp(1j*np.cumsum(2*np.pi*frequency/4e6)).astype(np.complex64)
        iq=np.concatenate([np.zeros(512,np.complex64),iq,np.zeros(512,np.complex64)])
        if noise:
            rng=np.random.default_rng(41)
            iq=(rng.normal(size=20000)+1j*rng.normal(size=20000)).astype(np.complex64)
        aid,path=self.ctx.artifact("ble_test","cf32");iq.tofile(path)
        dump(path.with_suffix(".json"),dict(center_hz=2402e6,sample_rate_hz=4e6,
            simulated=True,segments=[dict(file_start=0,count=len(iq),hardware_start_s=None)]))
        self.ctx.register(aid,path,kind="subband",metadata_path=str(path.with_suffix(".json")))
        return aid

    def test_ble_integrity_and_negative_controls(self):
        valid=protocols.decode_ble(self.ctx,self.ble_iq(),37)
        self.assertGreater(valid["valid_frames"],0)
        self.assertEqual(valid['verification_stages']['strongest_stage'],'crc_pass')
        self.assertEqual(valid['verification_stages']['unique_valid_frames'],valid['valid_frames'])
        self.assertTrue(valid['analysis_coverage']['complete'])
        invalid=protocols.decode_ble(self.ctx,self.ble_iq(corrupt=True),37)
        self.assertEqual(invalid["valid_frames"],0)
        self.assertGreater(invalid['verification_stages']['counts']['crc_failed'],0)
        self.assertEqual(invalid['verification_stages']['strongest_stage'],'header_plausible')
        noise=protocols.decode_ble(self.ctx,self.ble_iq(noise=True),37)
        self.assertEqual(noise["valid_frames"],0)
        self.assertEqual(noise["evidence_level"],"unknown")
        self.assertNotEqual(noise['verification_stages']['strongest_stage'],'crc_pass')

    def test_gap_cannot_be_filtered_as_contiguous(self):
        aid=dsp.synthetic(self.ctx)["capture_id"]
        rec=self.ctx.resolve(aid)
        meta=json.loads(Path(rec["metadata_path"]).read_text())
        n=meta["sample_count"]
        meta["segments"]=[dict(file_start=0,count=n//2,hardware_start_s=0),
                          dict(file_start=n//2,count=n-n//2,hardware_start_s=1)]
        dump(rec["metadata_path"],meta)
        with self.assertRaisesRegex(DomainError,"hardware gap"):
            dsp.extract(self.ctx,aid,2.4e9,1e6)

    def test_synthetic_tasks_cannot_capture_hardware(self):
        dump(self.ctx.run/"request.json",dict(source="synthetic"))
        with self.assertRaisesRegex(DomainError,"cannot start RF"):
            execute(self.ctx.task_id,"capture_iq",dict(center_hz=2.4e9,span_hz=100e6,duration_s=.01))

    def test_noise_is_not_protocol_confirmation(self):
        rng=np.random.default_rng(31)
        iq=(rng.normal(size=200000)+1j*rng.normal(size=200000)).astype(np.complex64)*.02
        aid,path=self.ctx.artifact("noise","cf32");iq.tofile(path)
        dump(path.with_suffix(".json"),dict(center_hz=2.4e9,sample_rate_hz=4e6,
            requested_span_hz=3.2e6,simulated=True,segments=[dict(file_start=0,count=len(iq),hardware_start_s=None)]))
        self.ctx.register(aid,path,kind="capture",metadata_path=str(path.with_suffix(".json")))
        result=dsp.segment(self.ctx,aid)
        self.assertLess(len(result["signal_groups"]),5)
        self.assertTrue(all(g["protocol"]=="unknown" for g in result["signal_groups"]))

    def feature_iq(self,iq,fs):
        aid,path=self.ctx.artifact("feature_fixture","cf32")
        np.asarray(iq,dtype=np.complex64).tofile(path)
        dump(path.with_suffix(".json"),dict(center_hz=2.4e9,sample_rate_hz=fs,simulated=True,
            segments=[dict(file_start=0,count=len(iq),hardware_start_s=None)]))
        self.ctx.register(aid,path,kind="subband",metadata_path=str(path.with_suffix(".json")))
        return dsp.features(self.ctx,aid)

    def test_generic_modulation_features_remain_candidates(self):
        rng=np.random.default_rng(112)
        carriers=(rng.choice([-1,1],size=(100,64))+1j*rng.choice([-1,1],size=(100,64)))
        symbols=np.fft.ifft(carriers,axis=1)*4
        ofdm=np.concatenate([symbols[:,-16:],symbols],axis=1).ravel()
        result=self.feature_iq(ofdm,20e6)
        self.assertIn("OFDM-like",[x["label"] for x in result["waveform_hypotheses"]])
        self.assertEqual(result["protocol"],"unknown")
        bits=rng.choice([-1.,1.],size=2000)
        phase=np.cumsum(np.repeat(bits,4)*2*np.pi*250e3/4e6)
        result=self.feature_iq(.4*np.exp(1j*phase),4e6)
        self.assertIn("FSK-like",[x["label"] for x in result["waveform_hypotheses"]])
        self.assertEqual(result["evidence_level"],"candidate")


if __name__=="__main__":
    unittest.main(verbosity=2)
