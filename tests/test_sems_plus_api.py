import base64
import hashlib
import unittest
from datetime import datetime
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

    def test_counters_are_requested_one_group_per_request(self):
        api = self.recording_api({
            'all-status': station(('SN1', -1)),
            'telecounting': factors(proPvStatsToday=0, proPvStatsTotal=49119.4),
        })
        api.getCurrentReadings()
        # The portal answers an error to some client types when no group, or more than one, is requested
        counting = [kwargs['params'] for method, path, kwargs in api.requests if path.endswith('/telecounting')]
        self.assertEqual([params.get('groups') for params in counting], ['telecounting_today', 'telecounting_lifetime'])
        for params in counting:
            self.assertEqual(params['deviceType'], 'INVERTER')

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

    def recording_api(self, responses):
        ''' An api answering by the last part of the path, which remembers the requests as (method, path, arguments). '''
        api = SemsPlusApi('station', 'account', 'password')
        api.requests = []

        def call(method, path, **kwargs):
            api.requests.append((method, path, kwargs))
            response = responses[path.split('/')[-1]]
            if isinstance(response, Exception):
                raise response
            return response

        api.call = call
        return api

    def day_responses(self, powers, total=None):
        ''' The responses for a day: the power by time of the station report, and the energy of the day. '''
        return {
            'userTableHeaders': [{'field': 'real_time'}],
            'real-time': {'realTimeData': [{'metricPointData': {'pSystem': powers}}] if powers is not None else []},
            'statistics': total if isinstance(total, Exception) else
            {'dataList': [{'item': 'proSystemTotalStats', 'statisticsList': [{'date': '2026-11-17', 'val': total}]}]},
        }

    def test_day_readings(self):
        api = self.recording_api(self.day_responses({
            '00:00': None, '05:20': 0.0, '05:25': 0.6, '12:00': 6.0, '12:05': 3.0, '23:55': 0.0}, total=1.6))

        data = api.getDayReadings(datetime(2026, 11, 17))

        # Only the points with production, at the time of the report, without voltage and without powers per string
        self.assertEqual([entry['dt'] for entry in data['entries']], [
            datetime(2026, 11, 17, 5, 25), datetime(2026, 11, 17, 12, 0), datetime(2026, 11, 17, 12, 5)])
        self.assertEqual([entry['pgrid_w'] for entry in data['entries']], [600.0, 6000.0, 3000.0])
        self.assertTrue(all(entry['powers'] is None and 'grid_voltage' not in entry for entry in data['entries']))
        # The points are instants of 5 minutes: 0.8 kWh in the area, which is adjusted to the 1.6 kWh of the day
        for entry, kwh in zip(data['entries'], [0.1, 1.1, 1.6]):
            self.assertAlmostEqual(entry['eday_kwh'], kwh)
        self.assertIsNone(data['latitude'])

    def test_day_readings_request(self):
        api = self.recording_api(self.day_responses({'12:00': 6.0}, total=1))

        api.getDayReadings(datetime(2026, 11, 17))

        (method, path, kwargs), (report_method, report_path, report), (stats_method, stats_path, stats) = api.requests
        self.assertEqual((method, path), ('GET', '/sems-plant/api/user/settings/userTableHeaders'))
        self.assertEqual(kwargs, {'params': {'resourceKey': 'resource_station_real_time_report'}})
        self.assertEqual((report_method, report_path), ('POST', '/sems-report/api/report/station/real-time'))
        self.assertEqual(report['json'], {
            'reportCategorize': 'STATION', 'reportType': 'REAL_TIME', 'reportingPeriod': 'day', 'timeScale': 5,
            'startTime': '2026-11-17 00:00:00', 'endTime': '2026-11-17 23:59:59', 'stationIds': ['station'],
            'reportIndex': {'userTableHeaders': [{'field': 'real_time'}]}})
        self.assertEqual((stats_method, stats_path), ('POST', '/sems-plant/api/stations/statistics'))
        self.assertEqual(stats['json']['dimension'], 'day')
        self.assertEqual(stats['json']['startTime'], '2026-11-17 00:00:00')

    def test_day_readings_without_the_energy_of_the_day(self):
        # The energy only corrects the area under the points: the power is imported without it
        api = self.recording_api(self.day_responses({'12:00': 6.0, '12:05': 3.0}, total=Exception('no energy')))

        data = api.getDayReadings(datetime(2026, 11, 17))

        self.assertEqual([entry['pgrid_w'] for entry in data['entries']], [6000.0, 3000.0])
        self.assertAlmostEqual(data['entries'][-1]['eday_kwh'], 0.75)

    def test_day_readings_without_points(self):
        for powers in (None, {}, {'12:00': None, '12:05': 0.0}):
            api = self.recording_api(self.day_responses(powers, total=0))
            self.assertEqual(api.getDayReadings(datetime(2026, 11, 17))['entries'], [])

    def test_day_total(self):
        api = self.recording_api(self.day_responses({}, total=25.2))
        self.assertEqual(api.getDayTotal(datetime(2026, 11, 17)), 25.2)
        # A statistics list of another day, or without value, is no energy
        for points in ([{'date': '2026-11-16', 'val': 9.0}], [{'date': '2026-11-17', 'val': None}], []):
            api = self.recording_api({'statistics': {'dataList': [{'statisticsList': points}]}})
            self.assertEqual(api.getDayTotal(datetime(2026, 11, 17)), 0)

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
