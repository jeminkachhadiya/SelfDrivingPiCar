import cv2
import logging
import datetime
import picar
import time
import edgetpu.detection.engine
from PIL import Image
from traffic_objects import *

_SHOW_IMAGE = True


class ObjectsOnRoadProcessor(object):
    """
    This class 1) detects what objects (namely traffic signs and people) are on the road
    and 2) controls the car navigation (speed/steering) accordingly
    """
    __INITIAL_SPEED = 0
    __SCREEN_WIDTH = 320
    __SCREEN_HEIGHT = 240

    def __init__(self,
                 car=None,
                 speed_limit=40,
                 model='/home/pi/DeepPiCar/models/object_detection/data/model_result/road_signs_quantized_edgetpu.tflite',
                 label='/home/pi/DeepPiCar/models/object_detection/data/model_result/road_sign_labels.txt',
                 width=640,
                 height=480):
        # model: This MUST be a tflite model that was specifically compiled for Edge TPU.
        # https://coral.withgoogle.com/web-compiler/
        logging.info('Creating a ObjectsOnRoadProcessor...')
        self.width = width
        self.height = height

        # initialize car
        self.car = car
        self.speed_limit = speed_limit
        # self.speed = 0
        """ Init camera and wheels"""
        logging.info('Creating a DeepPiCar...')

        picar.setup()

        logging.debug('Set up camera')
        self.camera = cv2.VideoCapture(-1)
        self.camera.set(3, self.__SCREEN_WIDTH)
        self.camera.set(4, self.__SCREEN_HEIGHT)

        self.pan_servo = picar.Servo.Servo(1)
        self.pan_servo.offset = -30  # calibrate servo to center
        self.pan_servo.write(90)

        self.tilt_servo = picar.Servo.Servo(2)
        self.tilt_servo.offset = 20  # calibrate servo to center
        self.tilt_servo.write(90)

        logging.debug('Set up back wheels')
        self.back_wheels = picar.back_wheels.Back_Wheels()
        self.back_wheels.speed = 0  # Speed Range is 0 (stop) - 100 (fastest)

        logging.debug('Set up front wheels')
        self.front_wheels = picar.front_wheels.Front_Wheels()
        self.front_wheels.turning_offset = 0  # calibrate servo to center (default = -25)
        self.front_wheels.turn(90)  # Steering Range is 45 (left) - 90 (center) - 135 (right)

        self.fourcc = cv2.VideoWriter_fourcc(*'XVID')
        datestr = datetime.datetime.now().strftime("%y%m%d_%H%M%S")
        self.video_orig = self.create_video_recorder('../data/tmp/car_video%s.avi' % datestr)
        self.video_lane = self.create_video_recorder('../data/tmp/car_video_lane%s.avi' % datestr)
        self.video_objs = self.create_video_recorder('../data/tmp/car_video_objs%s.avi' % datestr)

        logging.info('Created a DeepPiCar')

        # initialize TensorFlow models
        with open(label, 'r') as f:
            pairs = (l.strip().split(maxsplit=1) for l in f.readlines())
            self.labels = dict((int(k), v) for k, v in pairs)

        # initial edge TPU engine
        logging.info('Initialize Edge TPU with model %s...' % model)
        self.engine = edgetpu.detection.engine.DetectionEngine(model)
        self.min_confidence = 0.30
        self.num_of_objects = 3
        logging.info('Initialize Edge TPU with model done.')

        # initialize open cv for drawing boxes
        self.font = cv2.FONT_HERSHEY_SIMPLEX
        self.bottomLeftCornerOfText = (10, height - 10)
        self.fontScale = 1
        self.fontColor = (255, 255, 255)  # white
        self.boxColor = (0, 0, 255)  # RED
        self.boxLineWidth = 1
        self.lineType = 2
        self.annotate_text = ""
        self.annotate_text_time = time.time()
        self.time_to_show_prediction = 1.0  # ms

        #
        self.traffic_objects = {0: GreenTrafficLight(),
                                1: Person(),
                                2: RedTrafficLight(),
                                3: SpeedLimit(25),
                                4: SpeedLimit(40),
                                5: StopSign()}
                                
    def create_video_recorder(self, path):
        return cv2.VideoWriter(path, self.fourcc, 20.0, (self.__SCREEN_WIDTH, self.__SCREEN_HEIGHT))

    def __enter__(self):
        """ Entering a with statement """
        return self

    def __exit__(self, _type, value, traceback):
        """ Exit a with statement"""
        if traceback is not None:
            # Exception occurred:
            logging.error('Exiting with statement with exception %s' % traceback)

        self.cleanup()

    def cleanup(self):
        """ Reset the hardware"""
        logging.info('Stopping the car, resetting hardware.')
        self.back_wheels.speed = 0
        self.front_wheels.turn(90)
        self.camera.release()
        self.video_orig.release()
        self.video_lane.release()
        self.video_objs.release()
        cv2.destroyAllWindows()

    def process_objects_on_road(self, frame):
        # Main entry point of the Road Object Handler
        logging.debug('Processing objects.................................')
        objects, final_frame = self.detect_objects(frame)
        self.control_car(objects)
        logging.debug('Processing objects END..............................')

        return final_frame

    def control_car(self, objects):
        logging.debug('Control car...')
        car_state = {"speed": self.speed_limit, "speed_limit": self.speed_limit}

        if len(objects) == 0:
            logging.debug('No objects detected, drive at speed limit of %s.' % self.speed_limit)

        contain_stop_sign = False
        for obj in objects:
            obj_label = self.labels[obj.label_id]
            processor = self.traffic_objects[obj.label_id]
            if processor.is_close_by(obj, self.height):
                processor.set_car_state(car_state)
            else:
                logging.debug("[%s] object detected, but it is too far, ignoring. " % obj_label)
            if obj_label == 'Stop':
                contain_stop_sign = True

        if not contain_stop_sign:
            self.traffic_objects[5].clear()

        self.resume_driving(car_state)

    def resume_driving(self, car_state):
        # old_speed = self.speed
        self.speed_limit = car_state['speed_limit']
        self.speed = car_state['speed']

        if self.speed == 0:
            self.set_speed(0)
        else:
            self.set_speed(self.speed_limit)
        # logging.debug('Current Speed = %d, New Speed = %d' % (old_speed, self.speed))

        if self.speed == 0:
            logging.debug('full stop for 1 seconds')
            time.sleep(1)

    def set_speed(self, speed):
        # Use this setter, so we can test this class without a car attached
        self.speed = speed
        self.back_wheels.speed = speed
        if self.car is not None:
            logging.debug("Actually setting car speed to %d" % speed)
            self.car.back_wheels.speed = speed



    ############################
    # Frame processing steps
    ############################
    def detect_objects(self, frame):
        logging.debug('Detecting objects...')

        # call tpu for inference
        start_ms = time.time()
        frame_RGB = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img_pil = Image.fromarray(frame_RGB)
        objects = self.engine.DetectWithImage(img_pil, threshold=self.min_confidence, keep_aspect_ratio=True,
                                         relative_coord=False, top_k=self.num_of_objects)
        if objects:
            for obj in objects:
                height = obj.bounding_box[1][1]-obj.bounding_box[0][1]
                width = obj.bounding_box[1][0]-obj.bounding_box[0][0]
                logging.debug("%s, %.0f%% w=%.0f h=%.0f" % (self.labels[obj.label_id], obj.score * 100, width, height))
                box = obj.bounding_box
                coord_top_left = (int(box[0][0]), int(box[0][1]))
                coord_bottom_right = (int(box[1][0]), int(box[1][1]))
                cv2.rectangle(frame, coord_top_left, coord_bottom_right, self.boxColor, self.boxLineWidth)
                annotate_text = "%s %.0f%%" % (self.labels[obj.label_id], obj.score * 100)
                coord_top_left = (coord_top_left[0], coord_top_left[1] + 15)
                cv2.putText(frame, annotate_text, coord_top_left, self.font, self.fontScale, self.boxColor, self.lineType)
        else:
            logging.debug('No object detected')

        elapsed_ms = time.time() - start_ms

        annotate_summary = "%.1f FPS" % (1.0/elapsed_ms)
        logging.debug(annotate_summary)
        cv2.putText(frame, annotate_summary, self.bottomLeftCornerOfText, self.font, self.fontScale, self.fontColor, self.lineType)
        #cv2.imshow('Detected Objects', frame)

        return objects, frame
        
    def drive(self, speed=__INITIAL_SPEED):
        """ Main entry point of the car, and put it in drive mode

        Keyword arguments:
        speed -- speed of back wheel, range is 0 (stop) - 100 (fastest)
        """

        logging.info('Starting to drive at speed %s...' % speed)
        self.back_wheels.speed = speed
        i = 0
        while self.camera.isOpened():
            _, image_lane = self.camera.read()
            image_objs = image_lane.copy()
            i += 1
            self.video_orig.write(image_lane)

            image_objs = self.process_objects_on_road(image_objs)
            self.video_objs.write(image_objs)
            self.create_video_recorder('../data/car_video_objs%s.avi' % datestr).write(image_objs)
            show_image('Detected Objects', image_objs)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                self.cleanup()
                break

############################
# Utility Functions
############################
def show_image(title, frame, show=_SHOW_IMAGE):
    if show:
        cv2.imshow(title, frame)


############################
# Test Functions
############################
def test_photo(file):
    object_processor = ObjectsOnRoadProcessor()
    frame = cv2.imread(file)
    combo_image = object_processor.process_objects_on_road(frame)
    show_image('Detected Objects', combo_image)

    cv2.waitKey(0)
    cv2.destroyAllWindows()

def test_stop_sign():
    # this simulates a car at stop sign
    object_processor = ObjectsOnRoadProcessor()
    frame = cv2.imread('/home/pi/DeepPiCar/driver/data/objects/stop_sign.jpg')
    combo_image = object_processor.process_objects_on_road(frame)
    show_image('Stop 1', combo_image)
    time.sleep(1)
    frame = cv2.imread('/home/pi/DeepPiCar/driver/data/objects/stop_sign.jpg')
    combo_image = object_processor.process_objects_on_road(frame)
    show_image('Stop 2', combo_image)
    time.sleep(2)
    frame = cv2.imread('/home/pi/DeepPiCar/driver/data/objects/stop_sign.jpg')
    combo_image = object_processor.process_objects_on_road(frame)
    show_image('Stop 3', combo_image)
    time.sleep(1)
    frame = cv2.imread('/home/pi/DeepPiCar/driver/data/objects/green_light.jpg')
    combo_image = object_processor.process_objects_on_road(frame)
    show_image('Stop 4', combo_image)

    cv2.waitKey(0)
    cv2.destroyAllWindows()

def test_video(video_file):
    object_processor = ObjectsOnRoadProcessor()
    cap = cv2.VideoCapture(video_file + '.avi')

    # skip first second of video.
    for i in range(3):
        _, frame = cap.read()

    video_type = cv2.VideoWriter_fourcc(*'XVID')
    date_str = datetime.datetime.now().strftime("%y%m%d_%H%M%S")
    video_overlay = cv2.VideoWriter("%s_overlay_%s.avi" % (video_file, date_str), video_type, 20.0, (320, 240))
    try:
        i = 0
        while cap.isOpened():
            _, frame = cap.read()
            cv2.imwrite("%s_%03d.png" % (video_file, i), frame)

            combo_image = object_processor.process_objects_on_road(frame)
            cv2.imwrite("%s_overlay_%03d.png" % (video_file, i), combo_image)
            video_overlay.write(combo_image)

            cv2.imshow("Detected Objects", combo_image)

            i += 1
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    finally:
        cap.release()
        video_overlay.release()
        cv2.destroyAllWindows()

def main():
    with ObjectsOnRoadProcessor() as car:
        car.drive(40)

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format='%(levelname)-5s:%(asctime)s: %(message)s')

    # These processors contains no state
    #test_photo('/home/pi/DeepPiCar/driver/data/objects/red_light.jpg')
    #test_photo('/home/pi/DeepPiCar/driver/data/objects/person.jpg')
    #test_photo('/home/pi/DeepPiCar/driver/data/objects/limit_40.jpg')
    #test_photo('/home/pi/DeepPiCar/driver/data/objects/limit_25.jpg')
    #test_photo('/home/pi/DeepPiCar/driver/data/objects/green_light.jpg')
    #test_photo('/home/pi/DeepPiCar/driver/data/objects/no_obj.jpg')
    # main()
    roadprocessor = ObjectsOnRoadProcessor()
    roadprocessor.drive(40)
    # test stop sign, which carries state
    #test_stop_sign()
