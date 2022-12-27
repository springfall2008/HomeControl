import datetime
import time
import signal
import sys
import os

from os.path import dirname
from datetime import datetime

from givenergy_modbus.client import GivEnergyClient
from givenergy_modbus.model.battery import Battery
from givenergy_modbus.model.inverter import Inverter
from givenergy_modbus.model.plant import Plant

START_HOUR = 10
STOP_HOUR = 18
HOME_BATTERY_THRESHOLD_HIGH = 97
HOME_BATTERY_THRESHOLD_LOW = 87
CAR_BATTERY_MAX = 90
CAR_CHARGE_AMPS = 8
DEFAULT_CHARGE_AMPS = 64

WAIT_TIME = 60
def signal_handler(sig, frame):
    print('You pressed Ctrl+C!')
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)

import teslapy
with teslapy.Tesla('tdlj@tdlj.net', retry=3, timeout=15) as tesla:
    vehicles = tesla.vehicle_list()
    # print(vehicles)
    my_car = vehicles[0]

    client = GivEnergyClient(host="192.168.0.141")
    p = Plant(number_batteries=1)
    #client.set_battery_power_reserve(5)
    client.refresh_plant(p, full_refresh=True)
    
    # Initial state
    car_charging = False
    charging_amps = CAR_CHARGE_AMPS

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

         
        if (car_charging_state == 'NoPower'):
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
            print("   === Starting the car charging ===")
            # Start now        
            my_car.sync_wake_up()
            my_car.command('CHARGING_AMPS', charging_amps=charging_amps)
            my_car.command('CHANGE_CHARGE_LIMIT', percent=CAR_BATTERY_MAX)
            my_car.command('START_CHARGE')
            car_charging = True
        elif car_charging and (battery_under_threshold or not in_window):
            print("   === Stopping car the charging ===")
            # Stop now
            my_car.sync_wake_up()
            my_car.command('STOP_CHARGE')
            my_car.command('CHARGING_AMPS', charging_amps=DEFAULT_CHARGE_AMPS)
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



    