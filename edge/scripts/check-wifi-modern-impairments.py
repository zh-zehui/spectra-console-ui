import json
from tests.test_wifi_modern import fixture
from spectrum_agent.wifi_modern import receive_iq
for fmt in ('HT','VHT'):
    for short in (False,True):
        for cfo in (0.,30000.):
            frames,stages=receive_iq(fixture(fmt,mcs=3,short_gi=short,cfo=cfo),True)
            print(json.dumps(dict(format=fmt,short_gi=short,cfo=cfo,frames=len(frames),stages=stages)),flush=True)
