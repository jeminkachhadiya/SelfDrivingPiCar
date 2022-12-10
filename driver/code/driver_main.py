from selfdriving_pi_car import SelfDrivingCar
import logging
import sys

def main():
    # print system info
    logging.info('Starting SelfDrivingCar, system info: ' + sys.version)
    
    with SelfDrivingCar() as car:
        car.drive(40)
    
if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG)
    main()
