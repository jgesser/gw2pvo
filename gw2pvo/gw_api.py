import json
import logging
import time
from datetime import datetime, timedelta
import requests

__author__ = "Mark Ruys"
__copyright__ = "Copyright 2017, Mark Ruys"
__license__ = "MIT"
__email__ = "mark@paracas.nl"

class GoodWeApi:

    def __init__(self, system_id, inverter_id, account, password):
        self.system_id = system_id
        self.inverter_id = inverter_id
        self.account = account
        self.password = password
        self.token = '{"version":"v3.1","client":"ios","language":"en"}'
        self.global_url = 'https://semsportal.com/api/'
        self.base_url = self.global_url

    def statusText(self, status):
        labels = { -1 : 'Offline', 0 : 'Waiting', 1 : 'Normal', 2: 'Fault' }
        return labels[status] if status in labels else 'Unknown'

    def calcMPTTsPowerForDate(self, date, vpv1, vpv2, vpv3, ipv1, ipv2, ipv3):
        data = {
            'vpv1' : vpv1[date],
            'vpv2' : vpv2[date],
            'vpv3' : vpv3[date],
            'ipv1' : ipv1[date],
            'ipv2' : ipv2[date],
            'ipv3' : ipv3[date],
        }
        return self.calcMPTTsPower(data)

    def calcMPTTsPower(self, data):
        result = [
            data['vpv' + str(i)]
            for i in range(1, 4)
            if 'vpv' + str(i) in data
            if data['vpv' + str(i)]
            if data['vpv' + str(i)] < 6553
        ]

        for i in range(0, len(result)):
            if data['ipv' + str(i+1)] and data['ipv' + str(i+1)] < 6553:
                result[i] = result[i] * data['ipv' + str(i+1)]
            else:
                result[i] = 0

        result.append(sum(result))

        return [round(v, 1) for v in result]
        
    def calcPvVoltage(self, data):
        pv_voltages = [
            data['vpv' + str(i)]
            for i in range(1, 5)
            if 'vpv' + str(i) in data
            if data['vpv' + str(i)]
            if data['vpv' + str(i)] < 6553
        ]
        return round(sum(pv_voltages), 1)

    def getCurrentReadings(self):
        ''' Download the most recent readings from the GoodWe API. '''

        payload = {
            'powerStationId' : self.system_id
        }
        data = self.call("v2/PowerStation/GetMonitorDetailByPowerstationId", payload)

        result = {
            'status' : 'Unknown',
            'pgrid_w' : 0,
            'eday_kwh' : 0,
            'etotal_kwh' : 0,
            'grid_voltage' : 0,
            'pv_voltage' : 0,
            'powers' : [],
            'latitude' : data['info'].get('latitude'),
            'longitude' : data['info'].get('longitude')
        }

        count = 0
        for inverterData in data['inverter']:
            status = self.statusText(inverterData['status'])
            if status == 'Normal':
                result['status'] = status
                result['pgrid_w'] += inverterData['out_pac']
                result['grid_voltage'] += self.parseValue(inverterData['output_voltage'], 'V')
                result['pv_voltage'] += self.calcPvVoltage(inverterData['d'])
                result['powers'] = self.calcMPTTsPower(inverterData['d'])
                count += 1
            result['eday_kwh'] += inverterData['eday']
            result['etotal_kwh'] += inverterData['etotal']
        if count > 0:
            # These values should not be the sum, but the average
            result['grid_voltage'] /= count
            result['pv_voltage'] /= count
        elif len(data['inverter']) > 0:
            # We have no online inverters, then just pick the first
            inverterData = data['inverter'][0]
            result['status'] = self.statusText(inverterData['status'])
            result['pgrid_w'] = inverterData['out_pac']
            result['grid_voltage'] = self.parseValue(inverterData['output_voltage'], 'V')
            result['pv_voltage'] = self.calcPvVoltage(inverterData['d'])
            result['powers'] = self.calcMPTTsPower(inverterData['d'])

        message = "{status}, {pgrid_w} W now, {eday_kwh} kWh today, {etotal_kwh} kWh all time, {grid_voltage} V grid, {pv_voltage} V PV".format(**result)
        if result['status'] == 'Normal' or result['status'] == 'Offline':
            logging.info(message)
        else:
            logging.warning(message)

        return result

    def getActualKwh(self, date):
        payload = {
            'powerstation_id' : self.system_id,
            'count' : 1,
            'date' : date.strftime('%Y-%m-%d')
        }
        data = self.call("v2/PowerStationMonitor/GetPowerStationPowerAndIncomeByDay", payload)
        if not data:
            logging.warning("GetPowerStationPowerAndIncomeByDay missing data")
            return 0

        eday_kwh = 0
        for day in data:
            if day['d'] == date.strftime('%m/%d/%Y'):
                eday_kwh = day['p']

        return eday_kwh

    def getLocation(self):
        payload = {
            'powerStationId' : self.system_id
        }
        data = self.call("v2/PowerStation/GetMonitorDetailByPowerstationId", payload)
        if 'info' not in data:
            logging.warning("GetMonitorDetailByPowerstationId returned bad data: " + str(data))
            return {}

        return {
            'latitude' : data['info'].get('latitude'),
            'longitude' : data['info'].get('longitude'),
        }

    def getDayPac(self, date):
        payload = {
            'id' : self.system_id,
            'date' : date.strftime('%Y-%m-%d')
        }
        data = self.call("v2/PowerStationMonitor/GetPowerStationPacByDayForApp", payload)
        if 'pacs' not in data:
            logging.warning("GetPowerStationPacByDayForApp returned bad data: " + str(data))
            return []

        return data['pacs']

    def getColumnByDay(self, date, column):
        payload = {
            'id' : self.inverter_id,
            'date' : date.strftime('%Y-%m-%d'),
            'column' : column
        }
        data = self.call("v2/PowerStationMonitor/GetInverterDataByColumn", payload)
        
        mydict={}
        if 'column1' in data:
            for column in data['column1']:
                mydict[column['date']]=column['column']
        else:
            logging.warning("GetInverterDataByColumn returned bad data: " + str(data))
        return mydict

    def getDayReadings(self, date):
        result = self.getLocation()
        pacs = self.getDayPac(date)
        # vac1 = self.getColumnByDay(date, 'Vac1')
        vpv1 = self.getColumnByDay(date, 'Vpv1')
        vpv2 = self.getColumnByDay(date, 'Vpv2')
        vpv3 = self.getColumnByDay(date, 'Vpv3')
        ipv1 = self.getColumnByDay(date, 'Ipv1')
        ipv2 = self.getColumnByDay(date, 'Ipv2')
        ipv3 = self.getColumnByDay(date, 'Ipv3')

        hours = 0
        kwh = 0
        result['entries'] = []
        for sample in pacs:
            parsed_date = datetime.strptime(sample['date'], "%m/%d/%Y %H:%M:%S")
            next_hours = parsed_date.hour + parsed_date.minute / 60
            pgrid_w = sample['pac']
            if pgrid_w > 0:
                kwh += pgrid_w / 1000 * (next_hours - hours)
                powers = self.calcMPTTsPowerForDate(sample['date'], vpv1, vpv2, vpv3, ipv1, ipv2, ipv3)
                # if powers[3] > 0:
                #     correctionW = pgrid_w / powers[3]
                #     powers = [p * correctionW for p in powers]
                result['entries'].append({
                    'dt' : parsed_date,
                    'pgrid_w': pgrid_w,
                    'eday_kwh': round(kwh, 3),
                    # 'grid_voltage': vac1[sample['date']],
                    'powers': powers
                })
            hours = next_hours

        eday_kwh = self.getActualKwh(date)
        if eday_kwh > 0:
            correction = eday_kwh / kwh
            for sample in result['entries']:
                sample['eday_kwh'] *= correction

        return result

    def call(self, url, payload):
        for i in range(1, 4):
            try:
                headers = {
                    'User-Agent': 'SEMS Portal/3.1 (iPhone; iOS 13.5.1; Scale/2.00)',
                    'Token': self.token,
                }

                r = requests.post(self.base_url + url, headers=headers, data=payload, timeout=10)
                r.raise_for_status()
                data = r.json()
                logging.debug(data)

                try:
                    code = int(data['code'])
                except ValueError:
                    raise Exception("Failed to call GoodWe API (no code)")

                if code == 0 and data['data'] is not None:
                    return data['data']
                elif code == 100001:
                    loginPayload = {
                        'account': self.account,
                        'pwd': self.password,
                    }
                    r = requests.post(self.global_url + 'v2/Common/CrossLogin', headers=headers, data=loginPayload, timeout=10)
                    r.raise_for_status()
                    data = r.json()
                    if 'api' not in data:
                        raise Exception(data['msg'])
                    self.base_url = data['api']
                    self.token = json.dumps(data['data'])
                else:
                    raise Exception("Failed to call GoodWe API (code {})".format(code))
            except requests.exceptions.RequestException as exp:
                logging.warning(exp)
            time.sleep(i ** 3)
        else:
            raise Exception("Failed to call GoodWe API (too many retries)")

        return {}

    def parseValue(self, value, unit):
        try:
            return float(value.rstrip(unit))
        except ValueError as exp:
            logging.warning(exp)
            return 0
