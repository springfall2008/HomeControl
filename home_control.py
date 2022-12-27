import datetime
import time
import signal
import sys
import os
import argparse

from os.path import dirname
from datetime import datetime

from givenergy_modbus.client import GivEnergyClient
from givenergy_modbus.model.battery import Battery
from givenergy_modbus.model.inverter import Inverter
from givenergy_modbus.model.plant import Plant
import teslapy

START_HOUR = 10
STOP_HOUR = 18
HOME_BATTERY_THRESHOLD_HIGH = 97
HOME_BATTERY_THRESHOLD_LOW = 87
CAR_BATTERY_MAX = 90
CAR_CHARGE_AMPS = 8
DEFAULT_CHARGE_AMPS = 64
HOME_LAT = 0
HOME_LONG = 0

WAIT_TIME = 60
def signal_handler(sig, frame):
    print('You pressed Ctrl+C!')
    sys.exit(0)

def is_at_home(lat, long):
    dif = abs(HOME_LAT - lat) + abs(HOME_LONG - long)
    if (dif < 0.01):
        return True
    else:
        return False

signal.signal(signal.SIGINT, signal_handler)

def main():
    global HOME_LAT, HOME_LONG

    parser = argparse.ArgumentParser(description='Home battery controller')
    parser.add_argument('longitude', help='Home longitude')
    parser.add_argument('latitude', help='Home latitude')
    args = parser.parse_args()
    HOME_LAT=float(args.latitude)
    HOME_LONG=float(args.longitude)

    with teslapy.Tesla('tdlj@tdlj.net', retry=3, timeout=15) as tesla:

        # Wake the car up to find out it's location and other-data on the first iteration
        vehicles = tesla.vehicle_list()
        my_car = vehicles[0]
        my_car.sync_wake_up()
        car_data = my_car.get_vehicle_data()

        client = GivEnergyClient(host="192.168.0.141")
        p = Plant(number_batteries=1)
        #client.set_battery_power_reserve(5)
        client.refresh_plant(p, full_refresh=True)
    
        # Initial state
        car_charging = False
        charging_amps = CAR_CHARGE_AMPS
        is_home = False

        # Loop polling the battery
        while (True):
            time_now = datetime.now()
            print("%s:" % time_now.strftime("%m/%d/%Y, %H:%M:%S"))

            # Access the Givenergy system
            client.refresh_plant(p, full_refresh=False)
            battery_per = p.inverter.battery_percent
            battery_power = p.inverter.p_battery
            load = p.inverter.p_load_demand
            solar = p.inverter.p_pv1 + p.inverter.p_pv2 
            grid_export = p.inverter.p_grid_out
            spare_power = grid_export - battery_power

            print("   Battery is at %2.0f%% - battery power %d solar %d house %d grid %d - spare power %d" % (battery_per, battery_power, solar, load, grid_export, spare_power))

            # Get the car's status
            my_car.get_vehicle_summary()
            car_data = my_car.get_vehicle_data()
            car_battery = my_car['charge_state']['battery_level']
            car_charging_state = my_car['charge_state']['charging_state']

            # Update car location if the data is available
            if 'latitude' in car_data['drive_state']:
                is_home = is_at_home(car_data['drive_state']['latitude'], car_data['drive_state']['longitude'])

            print("   Car: " + my_car['display_name'] + ' last seen ' + my_car.last_seen() + ' at ' + str(car_battery) + '% SoC' + ' Charge state ' + car_charging_state)

            if time_now.hour >= START_HOUR and time_now.hour < STOP_HOUR:
                print("   Within the time window %d-%d" % (START_HOUR, STOP_HOUR))
                in_window = True
            else:
                print("   Outside the time window %d-%d hrs" % (START_HOUR, STOP_HOUR))
                in_window = False
        
            if battery_power < 0:
                print("   Home battery is charging")
                battery_charging = True
            else:
                print("   Home battery is discharging")
                battery_charging = False

            if battery_per >= HOME_BATTERY_THRESHOLD_HIGH:
                print("   Home battery is above the upper threshold")
                battery_over_threshold = True
                battery_under_threshold = False
            elif battery_per <= HOME_BATTERY_THRESHOLD_LOW:
                print("   Home battery is below the lower threshold")
                battery_over_threshold = False
                battery_under_threshold = True
            else:
                print("   Home battery is between the thresholds")
                battery_over_threshold = False
                battery_under_threshold = False

         
            if (not is_home):
              print("   Car does not appear to be at home, won't manage it")
            elif (car_charging_state == 'NoPower'):
                print("   Car charger doesn't have power, please enable it")
                car_charging = False
            elif (car_charging_state == 'Disconnected'):
                print("   Car charger is not connected, please connect it")
                car_charging = False
            elif battery_charging and (car_charging_state == 'Complete' or car_charging_state == 'Stopped'):
                car_charging = False
                print("   Car has stopped charging, likely hit the charging limit or stopped by the app")
                my_car.sync_wake_up()
                my_car.command('CHARGING_AMPS', charging_amps=DEFAULT_CHARGE_AMPS)
            elif not battery_charging and (car_charging_state == 'Charging'):
                if (in_window):
                    battery_charging = True
                    print("   The car is already charging in the window, starting to manage it now")
                else:
                    print("   The car is already charging outside the window and we didn't enable it, ignoring it")
            elif not car_charging and in_window and (spare_power > 0) and battery_over_threshold and (car_battery < CAR_BATTERY_MAX):
                # Start now        
                my_car.sync_wake_up()
                is_home = is_at_home(car_data['drive_state']['latitude'], car_data['drive_state']['longitude'])
                if (is_home):
                    print("   === Starting the car charging ===")
                    my_car.command('CHARGING_AMPS', charging_amps=charging_amps)
                    my_car.command('CHANGE_CHARGE_LIMIT', percent=CAR_BATTERY_MAX)
                    my_car.command('START_CHARGE')
                    car_charging = True
                else:
                    print("   Car has moved away from home, wont't start charge")
            elif car_charging and (battery_under_threshold or not in_window):
                # Stop now
                my_car.sync_wake_up()
                is_home = is_at_home(car_data['drive_state']['latitude'], car_data['drive_state']['longitude'])
                if (is_home):
                    print("   === Stopping car the charging ===")
                    my_car.command('STOP_CHARGE')
                    my_car.command('CHARGING_AMPS', charging_amps=DEFAULT_CHARGE_AMPS)
                else:
                    print("   Car has moved away from home, won't stop charge")
                car_charging = False
            elif car_charging:
                # Adjust car charging amps
                if (spare_power < 0 and charging_amps > 1):
                    charging_amps -= 1
                    my_car.sync_wake_up()
                    my_car.command('CHARGING_AMPS', charging_amps=charging_amps)
                    print("   Adjusting down car charging amps to %d based on spare_power" % charging_amps)
                elif (spare_power > 0 and charging_amps < 32):
                    print("   Adjusting up car charging amps to %d based on spare_power" % charging_amps)
                    charging_amps += 1
                    my_car.sync_wake_up()
                    my_car.command('CHARGING_AMPS', charging_amps=charging_amps)

            time.sleep(WAIT_TIME)

if __name__ == "__main__":
    exit(main())

    