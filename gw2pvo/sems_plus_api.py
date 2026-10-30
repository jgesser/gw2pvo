import base64
import hashlib
import json
import logging
import time

import requests


class SemsPlusApi:
    ''' SEMS+ (https://semsplus.goodwe.com) has no public API. This is what its web app does, as also
    implemented by https://github.com/TimSoethout/goodwe-sems-home-assistant. Logging in gives a token
    and the regional gateway to send every other request to. '''

    LOGIN_URL = 'https://semsplus.goodwe.com/web/sems/sems-user/api/v1/auth/cross-login'
    # Without browser-like headers the login is rejected (code 100004)
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Origin': 'https://semsplus.goodwe.com',
        'Referer': 'https://semsplus.goodwe.com/',
        'Accept': 'application/json, text/plain, */*',
    }
    ANONYMOUS_TOKEN = {'uid': '', 'timestamp': 0, 'token': '', 'client': 'semsPlusWeb', 'version': '', 'language': 'en'}

    OK = '00000'
    # The token expired, or a newer login (of this account) replaced it
    SESSION_EXPIRED = ('C0602', '100002')

    # Inverter status codes
    STATUS_LABELS = {-1: 'Offline', 0: 'Waiting', 1: 'Normal', 2: 'Fault', 3: 'Waiting', 5: 'Normal'}

    def __init__(self, system_id, account, password):
        self.system_id = system_id
        self.account = account
        self.password = password
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.token = None

    def signature(self, token):
        timestamp = str(int(time.time() * 1000))
        digest = hashlib.sha256('{}@{}@{}'.format(timestamp, token['uid'], token['token']).encode()).hexdigest()
        return base64.b64encode('{}@{}'.format(digest, timestamp).encode()).decode()

    def send(self, method, url, token, **kwargs):
        headers = {'token': json.dumps(token), 'X-Signature': self.signature(token)}
        r = self.session.request(method, url, headers=headers, timeout=15, **kwargs)
        r.raise_for_status()
        return r.json()

    def data(self, body):
        if body.get('code') != self.OK:
            raise Exception("Failed to call SEMS+ API (code {}: {})".format(body.get('code'), body.get('description')))
        return body['data']

    def login(self):
        payload = {
            'account': self.account,
            'pwd': base64.b64encode(hashlib.md5(self.password.encode()).hexdigest().encode()).decode(),
            'agreement': 1,
            'isChinese': False,
            'isLocal': False,
        }
        token = self.data(self.send('POST', self.LOGIN_URL, self.ANONYMOUS_TOKEN, json=payload))
        token['client'] = 'semsPlusWeb'
        self.token = token

    def call(self, method, path, **kwargs):
        if self.token is None:
            self.login()
        body = self.send(method, self.token['api'] + path, self.token, **kwargs)
        if body.get('code') in self.SESSION_EXPIRED:
            logging.debug("SEMS+ session expired, logging in again")
            self.login()
            body = self.send(method, self.token['api'] + path, self.token, **kwargs)
        return self.data(body)

    def statusText(self, status):
        if status not in self.STATUS_LABELS:
            logging.warning("Unknown SEMS+ inverter status {}".format(status))
        return self.STATUS_LABELS.get(status, 'Unknown')

    def getInverters(self):
        ''' Returns the (serial number, status) of each inverter of the station. '''
        data = self.call('GET', '/sems-plant/api/stations/device/all-status', params={'stationId': self.system_id})
        return [
            (sn, group['status'])
            for devices in data['deviceDetailList'] if devices['deviceType'] == 'INVERTER'
            for group in devices['statusDetailList']
            for sn in group['snList']
        ]

    def getFactors(self, endpoint, sn):
        ''' Returns the numeric values of an inverter endpoint (telemetry or telecounting) by factor code. '''
        groups = self.call('GET', '/sems-plant/api/equipments/{}/{}'.format(sn, endpoint),
                           params={'deviceType': 'INVERTER', 'pwId': self.system_id})
        factors = {}
        for group in groups:
            for factor in group['factors']:
                try:
                    factors[factor['code']] = float(factor.get('data'))
                except (TypeError, ValueError):
                    pass  # No value, or not a number (like the serial number)
        return factors

    def getCurrentReadings(self):
        ''' Download the most recent readings from the SEMS+ API. '''

        result = {
            'status' : 'Unknown',
            'pgrid_w' : 0,
            'eday_kwh' : 0,
            'etotal_kwh' : 0,
            'grid_voltage' : 0,
            'pv_voltage' : 0,
            'powers' : [],
            'latitude' : None,
            'longitude' : None
        }

        for sn, status in self.getInverters():
            status = self.statusText(status)
            if result['status'] != 'Normal':
                result['status'] = status

            # The energy counters are also available while the inverter is not producing
            counters = self.getFactors('telecounting', sn)
            result['eday_kwh'] += counters.get('proPvStatsToday', 0)
            result['etotal_kwh'] += counters.get('proPvStatsTotal', 0)

            if status == 'Normal':
                telemetry = self.getFactors('telemetry', sn)
                result['pgrid_w'] += round(telemetry.get('pAc', 0) * 1000, 1)
                if not result['powers']:
                    # The voltages and strings are not summed over inverters: only the first online one is reported
                    mppts = [i for i in range(1, 5) if 'MPPT-{}:Ppv'.format(i) in telemetry]
                    powers = [round(telemetry['MPPT-{}:Ppv'.format(i)] * 1000, 1) for i in mppts]
                    result['powers'] = powers + [round(sum(powers), 1)]
                    result['pv_voltage'] = round(sum(telemetry.get('MPPT-{}:Vpv'.format(i), 0) for i in mppts), 1)
                    result['grid_voltage'] = telemetry.get('Vac', 0)

        message = "{status}, {pgrid_w} W now, {eday_kwh} kWh today, {etotal_kwh} kWh all time, {grid_voltage} V grid, {pv_voltage} V PV".format(**result)
        if result['status'] == 'Normal' or result['status'] == 'Offline':
            logging.info(message)
        else:
            logging.warning(message)

        return result
