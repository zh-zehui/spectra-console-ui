import unittest
import uuid
from spectrum_agent.common import Context,dump
from spectrum_agent.service import normalize
from spectrum_agent.knowledge import query


class SiteContextTests(unittest.TestCase):
    def test_rf_request_uses_user_context_but_priors_remain_unobserved(self):
        request=normalize(dict(prompt='2.4GHz100MHz',source='usrp'))
        context=request['constraints']['capture_context']
        self.assertEqual(context['environment'],'indoor')
        self.assertEqual(context['provenance'],'user_reported')
        self.assertFalse(context['sensor_verified'])
        ctx=Context('site_test_'+uuid.uuid4().hex[:10]);dump(ctx.run/'request.json',request)
        priors=query(ctx,2.4e9,100e6)
        self.assertFalse(priors['observed'])
        self.assertEqual(priors['region'],context['region'])
        self.assertTrue(any(p['id']=='cellular_tdd_2300_2400' for p in priors['candidates']))

    def test_synthetic_origin_is_not_tagged_as_campus_rf_and_explicit_context_survives(self):
        synthetic=normalize(dict(prompt='2.4GHz100MHz',source='synthetic'))
        self.assertNotIn('capture_context',synthetic['constraints'])
        explicit=dict(region='user-chosen',provenance='user_reported')
        request=normalize(dict(prompt='2.4GHz100MHz',constraints=dict(capture_context=explicit)))
        self.assertEqual(request['constraints']['capture_context'],explicit)
