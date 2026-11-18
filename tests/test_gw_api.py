import unittest
from datetime import datetime

from gw2pvo.gw_api import GoodWeApi


def points(*values):
    ''' The answer of GetInverterDataByColumn: one point per time, in the order given. '''
    return {'column1': [{'date': '11/17/2026 {:02d}:00:00'.format(hour), 'column': value}
                        for hour, value in enumerate(values)]}


class GoodWeApiTest(unittest.TestCase):

    def api(self, data):
        ''' An api answering every call with the data, which remembers the calls. '''
        api = GoodWeApi('station', 'inverter', 'account', 'password')
        api.calls = []

        def call(url, payload):
            api.calls.append((url, payload))
            return data

        api.call = call
        return api

    def test_actual_kwh_is_the_highest_point_of_the_day(self):
        # the Eday series grows during the day and goes back to 0 after midnight
        api = self.api(points(0.0, 12.4, 25.2, 0.0))

        self.assertEqual(api.getActualKwh(datetime(2026, 11, 17)), 25.2)

    def test_actual_kwh_only_asks_for_the_inverter_data(self):
        # GetPowerStationPowerAndIncomeByDay does not answer anymore (2026-11-10): it must not be called
        api = self.api(points(1.0))

        api.getActualKwh(datetime(2026, 11, 17))

        self.assertEqual(len(api.calls), 1)
        url, payload = api.calls[0]
        self.assertEqual(url, 'v2/PowerStationMonitor/GetInverterDataByColumn')
        self.assertEqual(payload, {'id': 'inverter', 'date': '2026-11-17', 'column': 'Eday'})

    def test_actual_kwh_without_points(self):
        self.assertEqual(self.api(points()).getActualKwh(datetime(2026, 11, 17)), 0)

    def test_actual_kwh_ignores_points_without_value(self):
        api = self.api(points(None, 3.5, None))

        self.assertEqual(api.getActualKwh(datetime(2026, 11, 17)), 3.5)


if __name__ == '__main__':
    unittest.main()
