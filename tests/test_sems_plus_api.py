import base64
import hashlib
import unittest
from unittest import mock

from gw2pvo.sems_plus_api import SemsPlusApi


def factors(**values):
    return [{'code': 'group', 'factors': [{'code': code, 'data': str(value)} for code, value in values.items()]}]


def station(*inverters):
    ''' The device status response, with (serial number, status) per inverter. '''
    return {'deviceDetailList': [{'deviceType': 'INVERTER', 'statusDetailList': [
        {'status': status, 'snList': [sn]} for sn, status in inverters
    ]}]}


class SemsPlusApiTest(unittest.TestCase):

    def api(self, responses):
        ''' An api answering with the response for the last two parts of the path (device/all-status, SN1/telemetry). '''
        api = SemsPlusApi('station', 'account', 'password')
        api.call = lambda method, path, **kwargs: responses['/'.join(path.split('/')[-2:])]
        return api

    def test_signature(self):
        api = SemsPlusApi('station', 'account', 'password')
        digest, timestamp = base64.b64decode(api.signature({'uid': 'u', 'token': 't'})).decode().split('@')
        self.assertEqual(digest, hashlib.sha256('{}@u@t'.format(timestamp).encode()).hexdigest())

    def test_online_inverter(self):
        api = self.api({
            'device/all-status': station(('SN1', 5)),
            'SN1/telecounting': factors(proPvStatsToday=9.1, proPvStatsTotal=49119.4),
            'SN1/telemetry': factors(pAc=2.306, Vac=224.7, **{
                'MPPT-1:Ppv': 0.9902, 'MPPT-2:Ppv': 0.8039, 'MPPT-3:Ppv': 0.4063,
                'MPPT-1:Vpv': 391.5, 'MPPT-2:Vpv': 304.6, 'MPPT-3:Vpv': 115.7,
            }),
        })
        data = api.getCurrentReadings()
        self.assertEqual(data['status'], 'Normal')
        self.assertEqual(data['pgrid_w'], 2306.0)
        self.assertEqual(data['eday_kwh'], 9.1)
        self.assertEqual(data['etotal_kwh'], 49119.4)
        self.assertEqual(data['grid_voltage'], 224.7)
        self.assertEqual(data['pv_voltage'], 811.8)
        self.assertEqual(data['powers'], [990.2, 803.9, 406.3, 2200.4])

    def test_offline_inverter_still_reports_energy(self):
        api = self.api({
            'device/all-status': station(('SN1', -1)),
            'SN1/telecounting': factors(proPvStatsToday=0, proPvStatsTotal=49119.4),
        })
        data = api.getCurrentReadings()  # No telemetry is requested: it is not in the responses
        self.assertEqual(data['status'], 'Offline')
        self.assertEqual(data['pgrid_w'], 0)
        self.assertEqual(data['etotal_kwh'], 49119.4)
        self.assertEqual(data['powers'], [])

    def test_inverters_are_summed(self):
        api = self.api({
            'device/all-status': station(('SN1', 5), ('SN2', -1), ('SN3', 1)),
            'SN1/telecounting': factors(proPvStatsToday=1, proPvStatsTotal=10),
            'SN2/telecounting': factors(proPvStatsToday=2, proPvStatsTotal=20),
            'SN3/telecounting': factors(proPvStatsToday=3, proPvStatsTotal=30),
            'SN1/telemetry': factors(pAc=1, Vac=220, **{'MPPT-1:Ppv': 1.1}),
            'SN3/telemetry': factors(pAc=2, Vac=230, **{'MPPT-1:Ppv': 2.2}),
        })
        data = api.getCurrentReadings()
        self.assertEqual(data['status'], 'Normal')
        self.assertEqual(data['pgrid_w'], 3000)
        self.assertEqual(data['eday_kwh'], 6)
        self.assertEqual(data['etotal_kwh'], 60)
        self.assertEqual(data['grid_voltage'], 220)  # Of the first online inverter

    def test_login_again_when_session_expired(self):
        api = SemsPlusApi('station', 'account', 'password')
        api.token = {'api': 'https://gateway', 'uid': 'u', 'token': 'expired'}
        api.login = mock.Mock(side_effect=lambda: api.token.update(token='new'))
        with mock.patch.object(api, 'send', side_effect=[{'code': 'C0602'}, {'code': '00000', 'data': 'ok'}]) as send:
            self.assertEqual(api.call('GET', '/path'), 'ok')
        api.login.assert_called_once()
        self.assertEqual(send.call_count, 2)

    def test_login_as_client(self):
        for client, expected in ((None, 'semsPlusWeb'), ('web', 'web')):
            api = SemsPlusApi('station', 'account', 'password', *([client] if client else []))
            with mock.patch.object(api, 'send', return_value={'code': '00000', 'data': {'api': 'https://gateway'}}) as send:
                api.login()
            self.assertEqual(send.call_args[0][2]['client'], expected)

    def test_error_code(self):
        api = SemsPlusApi('station', 'account', 'password')
        with self.assertRaisesRegex(Exception, '100005: Email or password error'):
            api.data({'code': '100005', 'description': 'Email or password error.'})


if __name__ == '__main__':
    unittest.main()
