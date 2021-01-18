#!/usr/bin/env python
# Software License Agreement (BSD License)
#
# Copyright (c) 2008, Willow Garage, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of Willow Garage, Inc. nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#

import rospy

from std_msgs.msg import UInt8
from std_msgs.msg import String
from geometry_msgs.msg import Twist 
from std_msgs.msg import Float32MultiArray
from std_msgs.msg import MultiArrayDimension
from sensor_msgs.msg import Imu
from sensor_msgs.msg import NavSatFix
from sensor_msgs.msg import NavSatStatus
from sensor_msgs.msg import MagneticField
from geometry_msgs.msg import Vector3
from geometry_msgs.msg import Quaternion


from threading import Thread, Lock
import time
import can
import os
import struct
import codecs
from ctypes import *

VALPI = 3.142
VALG = 9.807

CMC = 0x010
MS = 0x100
US1 = 0x000
US2 = 0x001
OM1 = 0x101
OM2 = 0x102
GPS_ID = 0x201
IMU_ACCELXY = 0x202
IMU_MAGNETOXY = 0x203
IMU_ROTATIONXY = 0x204
IMU_ACCELMAGNETOZ = 0x205
IMU_ROTATIONZ = 0x206

############### SHARED VARIABLES (mutex) ##################
class CMC_ROS:
    def __init__(self):
        self.speed_cmd = 0
        self.steering_cmd = 0
        self.steering_enabled = 0
        self.drive_enabled = 1
        self.MUT = Lock()

class MS_ROS:
    def __init__(self):
        self.steering_angle = 0  #Bytes 0-1 / Steering Wheel Angle
        self.steering_center = 0
        self.batt_level = 0      #Bytes 2-3 / Battery Level
        self.motor_speed_L = 0   #Bytes 4-5 / Left Motor Speed
        self.motor_speed_R = 0   #Bytes 6-7 / Right Motor Speed
        self.MUT = Lock()

class US1_ROS:
    def __init__(self):
        self.frontLeftUltr = 0  #Bytes 0-1 / Front Left Ultrasonic US_AVG
        self.frontRightUltr = 0      #Bytes 2-3 / Front Right Ultrasonic US_AVD
        self.rearCentralUltr = 0   #Bytes 4-5 / Central Rear Ultrasonic US_ARCa
        self.MUT = Lock()

class US2_ROS:
    def __init__(self):
        self.rearLeftUltr= 0  #Bytes 0-1 / Left Rear Ultrasonic US_ARG
        self.rearRightUltr = 0      #Bytes 2-3 / Right Rear Ultrasonic US_ARD
        self.frontCentralUltr = 0   #Bytes 4-5 / Central Front Ultrasonic US_AVC
        self.MUT = Lock()

class GPS_ROS:
    def __init__(self):
        self.latitude = 0       #bytes 0..3 / Latitude
        self.longitude = 0      #bytes 4..7 / Longitude
        self.MUT = Lock()

class IMU_ROS:
    def __init__(self):
        self.x_acceleration = 0      #id 202 / bytes 0..3 / x linear velocity
        self.y_acceleration = 0      #id 202 / bytes 4..7 / y linear velocity
        self.z_acceleration = 0      #id 205 / bytes 0..3 / z linear velocity

        self.x_rotation = 0      #id 204 / bytes 0..3 / x rotation
        self.y_rotation = 0      #id 204 / bytes 4..7 / y rotation
        self.z_rotation = 0      #id 206 / bytes 0..3 / z rotation

        self.x_magneto = 0      #id 203 / bytes 0..3 / x magnetic field
        self.y_magneto = 0      #id 203 / bytes 4..7 / y magnetic field
        self.z_magneto = 0      #id 205 / bytes 4..7 / z magnetic field
        self.MUT = Lock()

MOTOR_COMMANDS = CMC_ROS()
MOTOR_SENSORS = MS_ROS()
ULTRASONIC_SENSORS1 = US1_ROS()
ULTRASONIC_SENSORS2 = US2_ROS()
GPS = GPS_ROS()
IMU = IMU_ROS()

###########################################################
def twos_complement(hexstr,bits):
    value = int(hexstr,16)
    if value & (1<< (bits-1)):
        value -= 1 << bits
    return value

class MyReceive(Thread):
    def __init__(self, bus):
        Thread.__init__(self)
        #self.conn = conn
        self.bus  = can.interface.Bus(channel='can0', bustype='socketcan_native')

        self.speed_cmd = 0
        self.steering_cmd = 0
        self.steering_enable = 0
        self.enable_speed = 0
        self.motor_cmd_R = 0
        self.motor_cmd_L = 0

    def run(self):
        self.speed_cmd = 0
        self.steering_cmd = 0
        self.steering_enable = 0
        self.enable_speed = 1
        self.motor_cmd_R = 0
        self.motor_cmd_L = 0

        while True :

            global MOTOR_COMMANDS
            MOTOR_COMMANDS.MUT.acquire()
            self.speed_cmd = MOTOR_COMMANDS.speed_cmd
            self.steering_cmd = MOTOR_COMMANDS.steering_cmd

            self.steering_enable = MOTOR_COMMANDS.steering_enabled
            self.enable_speed = MOTOR_COMMANDS.drive_enabled

            MOTOR_COMMANDS.MUT.release()
            #self.steering_cmd = steering_PID(self.steering_cmd)
            ########## with PID ###########
            self.motor_cmd_R, self.motor_cmd_L = speed_PID(self.speed_cmd, self.speed_cmd)
            if self.enable_speed:
                pwm_motor_R = (50 + self.motor_cmd_R) | 0x80
                pwm_motor_L = (50 + self.motor_cmd_L) | 0x80
            ########################################
            
            ########## without PID ###########
            #if self.enable_speed:
            #    pwm_motor_R = (50 + self.speed_cmd) | 0x80
            #    pwm_motor_L = (50 + self.speed_cmd) | 0x80
            ########################################
            else:
                pwm_motor_R = (50 + self.motor_cmd_R) & ~0x80
                pwm_motor_L = (50 + self.motor_cmd_L) & ~0x80
            
            self.steering_cmd = steering_PID(self.steering_cmd)
            if self.steering_enable:
                pwm_steering = (50 + self.steering_cmd) | 0x80
            else:
                pwm_steering = (50 + self.steering_cmd) & ~0x80               
                global MOTOR_SENSORS
                MOTOR_SENSORS.MUT.acquire()
                MOTOR_SENSORS.steering_center = MOTOR_SENSORS.steering_angle
                #print("center update")
                if  (MOTOR_SENSORS.steering_center != 0):              
                    MOTOR_COMMANDS.MUT.acquire()            
                    MOTOR_COMMANDS.steering_enabled = 1
                    MOTOR_COMMANDS.MUT.release()
                MOTOR_SENSORS.MUT.release()
            
            #print("speed_pwm: ",(50 + self.speed_cmd),"steering_pwm: ", (50 + self.steering_cmd) )

            msg = can.Message(arbitration_id=CMC,data=[pwm_motor_L, pwm_motor_R, pwm_steering,0,0,0,0,0],extended_id=False)

            try:
                self.bus.send(msg)
                print("Message sent")
            except can.CanError:
                print("Message NOT sent")
            
            time.sleep(0.1)


class MySend(Thread):

    def __init__(self, bus):
        Thread.__init__(self)
        self.bus = bus
    
        self.batt_level = 0.0
        self.steering_angle = 0.0
        self.steering_center = 0.0
        self.motor_speed_L = 0.0
        self.motor_speed_R = 0.0
        self.frontLeftUltr = 0.0
        self.frontRightUltr = 0.0 
        self.rearCentralUltr = 0.0
        self.rearLeftUltr= 0.0
        self.rearRightUltr = 0.0
        self.frontCentralUltr = 0.0
        self.latitude = 0.0
        self.longitude = 0.0
        self.x_acceleration = 0.0
        self.y_acceleration = 0.0
        self.z_acceleration = 0.0
        self.x_rotation = 0.0
        self.y_rotation = 0.0
        self.z_rotation = 0.0
        self.x_magneto = 0.0
        self.y_magneto = 0.0
        self.z_magneto = 0.0
        

    def run(self):
        while True :
            msg = self.bus.recv()
            
            global MOTOR_SENSORS
            MOTOR_SENSORS.MUT.acquire()
            self.steering_center = MOTOR_SENSORS.steering_center
            MOTOR_SENSORS.MUT.release()
            
            #print(msg.arbitration_id, msg.data)
            st = ""

            if msg.arbitration_id == MS:
                # Steering wheel angle
                if (self.steering_center == 0):
                    self.steering_angle = (int(codecs.encode(msg.data[0:2],'hex'), 16))
                else:
                    self.steering_angle = (int(codecs.encode(msg.data[0:2],'hex'), 16)-(self.steering_center+60))/19.45
                    #print(self.steering_angle)
                # Battery level
                self.batt_level = ((int(codecs.encode(msg.data[2:4],'hex'), 16)*(3.3/0.20408))/4095)
                # Left wheel speed
                self.motor_speed_L = int(codecs.encode(msg.data[4:6],'hex'), 16)*0.01
                print(self.motor_speed_L)
                # Right wheel speed
                # header : SWR payload : integer, *0.01rpm
                self.motor_speed_R= int(codecs.encode(msg.data[6:8],'hex'), 16)*0.01
                
                #print("steering_angle: ",self.steering_angle,"; batt_level: ",self.batt_level,"; left_speed: ",self.motor_speed_L,"; right_speed: ",self.motor_speed_R)
                         
            MOTOR_SENSORS.MUT.acquire()
            MOTOR_SENSORS.batt_level = self.batt_level
            MOTOR_SENSORS.steering_angle = self.steering_angle
            MOTOR_SENSORS.motor_speed_L = self.motor_speed_L
            MOTOR_SENSORS.motor_speed_R = self.motor_speed_R
            MOTOR_SENSORS.MUT.release()
            
            if msg.arbitration_id == US1:
                # Front left ultrasonic sensor
                self.frontLeftUltr = int(codecs.encode(msg.data[0:2],'hex'), 16)
                # Front right ultrasonic sensor
                self.frontRightUltr = int(codecs.encode(msg.data[2:4],'hex'), 16)
                # Central rear ultrasonic sensor
                self.rearCentralUltr = int(codecs.encode(msg.data[4:6],'hex'), 16)
                
                #print("ULTRASONIC1 --- frontLeftUltr: ",self.frontLeftUltr, " frontRightUltr: ",self.frontRightUltr, " rearCentralUltr: ",self.rearCentralUltr)

            global ULTRASONIC_SENSORS1
            ULTRASONIC_SENSORS1.MUT.acquire()
            ULTRASONIC_SENSORS1.frontLeftUltr = self.frontLeftUltr
            ULTRASONIC_SENSORS1.frontRightUltr = self.frontRightUltr
            ULTRASONIC_SENSORS1.rearCentralUltr = self.rearCentralUltr 
            ULTRASONIC_SENSORS1.MUT.release()

            if msg.arbitration_id == US2:
                # Left rear ultrasonic sensor
                self.rearLeftUltr = int(codecs.encode(msg.data[0:2],'hex'), 16)
                # Right rear ultrasonic sensor
                self.rearRightUltr = int(codecs.encode(msg.data[2:4],'hex'), 16)
                # Central frontal ultrasonic sensor
                self.frontCentralUltr = int(codecs.encode(msg.data[4:6],'hex'), 16)
                
                #print("ULTRASONIC2 --- rearLeftUltr: ",self.rearLeftUltr, " rearRightUltr: ",self.rearRightUltr, " frontCentralUltr: ",self.frontCentralUltr)
            
            global ULTRASONIC_SENSORS2
            ULTRASONIC_SENSORS2.MUT.acquire()
            self.rearLeftUltr = ULTRASONIC_SENSORS2.rearLeftUltr
            self.rearRightUltr = ULTRASONIC_SENSORS2.rearRightUltr
            self.frontCentralUltr = ULTRASONIC_SENSORS2.frontCentralUltr
            ULTRASONIC_SENSORS2.MUT.release()

            if msg.arbitration_id == GPS_ID:
                self.latitude = (int(codecs.encode(msg.data[0:4],'hex'), 16))*0.0000001
                self.longitude = (int(codecs.encode(msg.data[4:8],'hex'), 16))*0.0000001
                #print("latitude =", self.latitude, "longitude =", self.longitude)
                
            global GPS
            GPS.MUT.acquire()
            GPS.latitude = self.latitude
            GPS.longitude = self.longitude
            GPS.MUT.release()

            #------IMU frames------
            if msg.arbitration_id == IMU_ACCELXY:
                # x acceleration, converted from mg to m/s**2
                self.x_acceleration = twos_complement(codecs.encode(msg.data[0:4],'hex'),32)*0.001*VALG
                # y acceleration, converted from mg to m/s**2
                self.y_acceleration = twos_complement(codecs.encode(msg.data[4:8],'hex'),32)*0.001*VALG

            global IMU
            IMU.MUT.acquire()
            IMU.x_acceleration = self.x_acceleration
            IMU.y_acceleration = self.y_acceleration
            IMU.MUT.release()

            if msg.arbitration_id == IMU_MAGNETOXY:
                # x magnetic field, converted from milliGauss to Tesla
                self.x_magneto = (int(codecs.encode(msg.data[0:4],'hex'), 16))*0.0000001
                # y magnetic field, converted from milliGauss to Tesla
                self.y_magneto = (int(codecs.encode(msg.data[4:8],'hex'), 16))*0.0000001

            global IMU
            IMU.MUT.acquire()
            IMU.x_magneto = self.x_magneto
            IMU.y_magneto = self.y_magneto
            IMU.MUT.release()

            if msg.arbitration_id == IMU_ROTATIONXY:
                # x rotation, converted from mdps to rad/sec
                self.x_rotation = twos_complement(codecs.encode(msg.data[0:4],'hex'), 32)*0.001*(VALPI/180)
                # y rotation, converted from mdps to rad/sec
                self.y_rotation = twos_complement(codecs.encode(msg.data[4:8],'hex'), 32)*0.001*(VALPI/180)

            global IMU
            IMU.MUT.acquire()
            IMU.x_rotation = self.x_rotation
            IMU.y_rotation = self.y_rotation
            IMU.MUT.release()

            if msg.arbitration_id == IMU_ACCELMAGNETOZ:
                # z acceleration, converted from mg to m/s**2
                self.z_acceleration = twos_complement(codecs.encode(msg.data[0:4],'hex'), 32)*0.001*VALG
                # z magnetic field, converted from milliGauss to Tesla
                self.z_magneto = (int(codecs.encode(msg.data[4:8],'hex'), 16))*0.0000001
                
            global IMU
            IMU.MUT.acquire()
            IMU.z_acceleration = self.z_acceleration
            IMU.z_magneto = self.z_magneto
            IMU.MUT.release()

            if msg.arbitration_id == IMU_ROTATIONZ:
                # z rotation, converted from mdps to rad/sec
                self.z_rotation = twos_complement(codecs.encode(msg.data[0:4],'hex'), 16)*0.001*(VALPI/180)

            global IMU
            IMU.MUT.acquire()
            IMU.z_rotation = self.z_rotation
            IMU.MUT.release()
    
#Converts RPM to the PWM corresponding value
#For the forward mode:
def RPM_to_PWM_forward(RPM): 
    a = 0.431
    b = 0
    PWM=a*RPM+b
    # Treshold : 0<PWM<50 to go forward
    # Careful, +50 is applied in MyReceive.run() because 50 is the neutral position 
    if PWM>50:
        PWM=50
    elif PWM<=0:
        PWM=0
    return PWM

#For the backwards mode:
def RPM_to_PWM_backward(RPM):
    a = 0.431
    b = 0
    PWM=a*RPM+b
    # Treshold : -50<PWM<0 to go backward
    if PWM>=0:
        PWM=0
    elif PWM<-50:
        PWM=-50
    return PWM


sum_rightError = 0    # Sum of the errors for the integral correction
sum_leftError = 0  
sum_angleError = 0
time_old = 0
rightError_old = 0
leftError_old = 0

# PID to control car's speed
# param rightRef, leftRef = value that we want
# return cmdRight, cmdLeft = value to send to the motors (PWM)
def speed_PID(rightRef, leftRef):
    global sum_rightError, sum_leftError
    global rightError_old, leftError_old
    global time_old
    global MOTOR_SENSORS
    kp=0.4     #Proportional coefficient
    ki=40      #Integral coefficient
    kd=0       #Derivative coefficient
    
    time_new = time.clock()    
    delta_t = time_new - time_old
    time_old = time_new
    #print("delta time " ,delta_t) 
    #print("Right ref ",rightRef) 
    MOTOR_SENSORS.MUT.acquire()
    L_speed = float(MOTOR_SENSORS.motor_speed_L)    #RPM
    R_speed = float(MOTOR_SENSORS.motor_speed_R)    #RPM
    MOTOR_SENSORS.MUT.release()
    
    rightError = rightRef - R_speed
    leftError = leftRef - L_speed
    #print("Right error ", rightError)    
    I_right = sum_rightError + (ki * rightError * delta_t)    
    I_left = sum_leftError + (ki * leftError * delta_t)
    sum_rightError = I_right
    sum_leftError = I_left
    #print("R_error ",rightError," L_error",leftError)
    #print("Sum right error ",sum_rightError, " left err. ", sum_leftError)     
    delta_rightError = rightError - rightError_old
    delta_leftError = leftError - leftError_old
    
    # P and D calculation
    P_right = kp * rightError
    P_left = kp * leftError
    D_right = (kd * delta_rightError) / (delta_t)
    D_left = (kd * delta_leftError) / (delta_t)

    rightError_old = rightError
    leftError_old = leftError
    
    cmdRight_RPM = P_right + I_right + D_right
    cmdLeft_RPM = P_left + I_left + D_left
    
    # Conversion RPM to PWM
    if (rightRef > 0):
        cmdRight = int(RPM_to_PWM_forward(cmdRight_RPM))
    elif (rightRef < 0):
        cmdRight = int(RPM_to_PWM_backward(cmdRight_RPM))
    elif(rightRef == 0):
        cmdRight = 0

    if (leftRef > 0):
        cmdLeft = int(RPM_to_PWM_forward(cmdLeft_RPM))
    elif (leftRef < 0):
        cmdLeft = int(RPM_to_PWM_backward(cmdLeft_RPM))
    elif(leftRef == 0):
        cmdLeft = 0
       
    return cmdRight, cmdLeft 

# For the front wheels direction:
# max to the right => 30deg => PWM of 100 => 50 for PWM centered in 0
# max to the left => -30deg => PWM of 0 => -50 for PWM centered in 0
# straight => 0deg => PWM of 50 => 0 for PWM centered in 0
def Angle_to_PWM(Angle):
    a = 1
    b = 0
    PWM=a*Angle+b
    if PWM>50:
        PWM=50
    elif PWM<-50:
        PWM=-50
    return -PWM #car tourner a droite pwm positif alors que erreur negative sinon 


# PID to control car's steering wheel (so the direction)
# param refAngle = desired angle, between -30 et 30deg (TO CHECK !!!)
# return cmdAngle = PWM that we send to the motor
def steering_PID(refAngle):
    global MOTOR_SENSORS
    MOTOR_SENSORS.MUT.acquire()
    kp = 10
    ki = 0
    angle = MOTOR_SENSORS.steering_angle  # angle given by the sensor (in degrees)
    MOTOR_SENSORS.MUT.release()
    angleError =refAngle-angle
    #print(angleError)
    global sum_angleError
    sum_angleError = sum_angleError + angleError # Integral error
    
    # PI : command calculation
    
    cmdAngle_degre = kp*angleError + ki*sum_angleError
    #if (refAngle > 0):
    cmdAngle = int(Angle_to_PWM(cmdAngle_degre))
    #else:
    #    cmdAngle = int(Angle_to_PWM(cmdAngle_degre))
    
    #print('cmdAngle',cmdAngle)
    
    return cmdAngle 


def callback_motor_cmd(data):
    #rospy.loginfo(rospy.get_caller_id() + 'I heard %d', data.linear.x)
    #print('I heard %d', data.linear.x)
    global MOTOR_COMMANDS
    MOTOR_COMMANDS.MUT.acquire()
    #print("ENABLE VALUE IS = ", MOTOR_COMMANDS.drive_enabled)
    if(MOTOR_COMMANDS.drive_enabled == 1):
        MOTOR_COMMANDS.speed_cmd = int(data.linear.x)
        if (data.angular.z<(-25)):
            MOTOR_COMMANDS.steering_cmd = -25
        elif (data.angular.z>(25)):
            MOTOR_COMMANDS.steering_cmd = 25
        MOTOR_COMMANDS.steering_cmd = int(data.angular.z)
    else:
        print('DRIVING IS NOT ALLOWED BECAUSE OF OBJECT DETECTION')
        
    MOTOR_COMMANDS.MUT.release()

#function that return the object name regarding the ROS number that was read on the ROS topic
def ROS_number_to_Detection(class_number):
    if class_number == 0: #Nothing
        return "nothing"
    elif class_number == 1: #Person 
        return "person"
    elif class_number == 2: #Baggage 
        return "baggage"
    elif class_number == 3: #Bus
        return "bus"
    elif class_number == 4: #Car
        return "car"
    elif class_number == 5: #Bicycle
        return "bicycle"
    elif class_number == 6: #Cat
        return "cat"
    elif class_number == 7: #Dog
        return "dog"
    elif class_number == 8: #Motorcycle
        return "motorcycle"

    
def callback_detection(data):
    #rospy.loginfo(rospy.get_caller_id() + 'I heard %d', data.linear.x)
    print('I heard that we detected (NUMBER): ', data.data) 
    print('I heard that we detected (STRING): ', ROS_number_to_Detection(data.data))
    global MOTOR_COMMANDS
    
    MOTOR_COMMANDS.MUT.acquire()
    if (data.data == 0): #if nothing is detected
        MOTOR_COMMANDS.drive_enabled = 1
        print("WE ENABLE COMMANDS")
        
    else: #if anything is detected
        MOTOR_COMMANDS.speed_cmd = 0
        MOTOR_COMMANDS.drive_enabled = 0
        print("WE DISABLE COMMANDS")
    MOTOR_COMMANDS.MUT.release()

def callback_ultrasonicDetection():
    global ULTRASONIC_SENSORS1
    global ULTRASONIC_SENSORS2
    global MOTOR_COMMANDS

    ULTRASONIC_SENSORS1.MUT.acquire()
    fLU = float(ULTRASONIC_SENSORS1.frontLeftUltr) # Front left ultrasonic sensor
    fRU = float(ULTRASONIC_SENSORS1.frontRightUltr) # Front right ultrasonic sensor
    rCU = float(ULTRASONIC_SENSORS1.rearCentralUltr) # Central rear ultrasonic sensor
    ULTRASONIC_SENSORS1.MUT.release()

    ULTRASONIC_SENSORS2.MUT.acquire()
    rLU = float(ULTRASONIC_SENSORS2.rearLeftUltr) # Left rear ultrasonic sensor 
    rRU = float(ULTRASONIC_SENSORS2.rearRightUltr) # Right rear ultrasonic sensor 
    fCU = float(ULTRASONIC_SENSORS2.frontCentralUltr) # Central front ultrasonic sensor
    ULTRASONIC_SENSORS2.MUT.release()
    
    MOTOR_COMMANDS.MUT.acquire()
    if((fLU or fRU or rCU or rLU or rRU or fCU) < 10.0): #if anything is detected in less than 10 centimeters the car stops
        print('I heard there is a hurdle')    
        MOTOR_COMMANDS.speed_cmd = 0
        MOTOR_COMMAND.drive_enabled = 0
    else: # nothing is detected         
        MOTOR_COMMAND.drive_enabled = 1
    
    MOTOR_COMMANDS.MUT.release()
    
def listener():
    # In ROS, nodes are uniquely named. If two nodes with the same
    # name are launched, the previous one is kicked off. The
    # anonymous=True flag means that rospy will choose a unique
    # name for our 'listener' node so that multiple listeners can
    # run simultaneously.
    rospy.init_node('listener', anonymous=True)
    rospy.Subscriber('/speed_cmd', Twist, callback_motor_cmd)
    rospy.Subscriber('/detection', UInt8, callback_detection)


class MyTalker(Thread):

    def __init__(self):
        Thread.__init__(self)

    def run(self):
        global MOTOR_SENSORS
        pub = rospy.Publisher('/motor_sensors', Float32MultiArray, queue_size=10)
        pubUltr1 = rospy.Publisher('/ultrasonic_sensors1', Float32MultiArray, queue_size=10)
        pubUltr2 = rospy.Publisher('/ultrasonic_sensors2', Float32MultiArray, queue_size=10)
        pubGPS = rospy.Publisher('/GPS_coordinates', NavSatFix, queue_size=10)
        pubIMUraw = rospy.Publisher('imu/data_raw', Imu, queue_size=10)
        pubIMUmagn = rospy.Publisher('imu/mag', MagneticField, queue_size=10)

        #rospy.init_node('talker', anonymous=True)
        rate = rospy.Rate(10) # 10hz
        vect = Float32MultiArray()
        vect.layout.dim.append(MultiArrayDimension())
        vect.layout.dim[0].label = "height"
        vect.layout.dim[0].size = 4
        vect.layout.dim[0].stride = 4
        
        #Ultrasonic1 publisher
        rateU = rospy.Rate(10) # 10hz
        vectU = Float32MultiArray()
        vectU.layout.dim.append(MultiArrayDimension())
        vectU.layout.dim[0].label = "height"
        vectU.layout.dim[0].size = 3
        vectU.layout.dim[0].stride = 3

        #Ultrasonic1 publisher
        rateU2 = rospy.Rate(10) # 10hz
        vectU2 = Float32MultiArray()
        vectU2.layout.dim.append(MultiArrayDimension())
        vectU2.layout.dim[0].label = "height"
        vectU2.layout.dim[0].size = 3
        vectU2.layout.dim[0].stride = 3

        #GPS publisher
        rateGPS = rospy.Rate(10)
        vectGPS = NavSatFix()
        vectGPS.header.frame_id = "base_link"
        vectGPS.status.status = 1
        vectGPS.status.service = 1
        vectGPS.altitude = 0.0
        vectGPS.position_covariance[0] = 1.0
        vectGPS.position_covariance[4] = 1.0
        vectGPS.position_covariance[8] = 1.0
        vectGPS.position_covariance_type = 0 #0=Unknown

        #IMU raw data publisher
        rateIMUraw = rospy.Rate(10)
        vectIMUraw = Imu()
        vectIMUraw.header.frame_id = "imu_link"
        vectIMUraw.orientation.x = 0.0
        vectIMUraw.orientation.y = 0.0
        vectIMUraw.orientation.z = 0.0
        vectIMUraw.orientation.w = 0.0
        vectIMUraw.orientation_covariance[0] = 0.0
        vectIMUraw.orientation_covariance[4] = 0.0
        vectIMUraw.orientation_covariance[8] = 0.0
        vectIMUraw.angular_velocity_covariance[0] = (3.40)*(10**(-6))
        vectIMUraw.angular_velocity_covariance[4] = (7.96)*(10**(-7))
        vectIMUraw.angular_velocity_covariance[8] = (1.69)*(10**(-5))
        vectIMUraw.linear_acceleration_covariance[0] = (4.63)*(10**(-4))
        vectIMUraw.linear_acceleration_covariance[4] = (8.79)*(10**(-4))
        vectIMUraw.linear_acceleration_covariance[8] = (2.56)*(10**(-4))

        #IMU magnetic field publisher
        rateIMUmagn = rospy.Rate(10)
        vectIMUmagn = MagneticField()
        vectIMUmagn.header.frame_id = "imu_link"
        vectIMUmagn.magnetic_field_covariance[0] = 0.0
        vectIMUmagn.magnetic_field_covariance[4] = 0.0
        vectIMUmagn.magnetic_field_covariance[8] = 0.0


        while not rospy.is_shutdown():       
            current_time = rospy.Time.now()     
            MOTOR_SENSORS.MUT.acquire()
            vect.data = [MOTOR_SENSORS.steering_angle, MOTOR_SENSORS.batt_level, MOTOR_SENSORS.motor_speed_L, MOTOR_SENSORS.motor_speed_R]    
            MOTOR_SENSORS.MUT.release()
            pub.publish(vect)
            rate.sleep()
            
            ULTRASONIC_SENSORS1.MUT.acquire()
            vectU.data = [ULTRASONIC_SENSORS1.frontLeftUltr, ULTRASONIC_SENSORS1.frontRightUltr, ULTRASONIC_SENSORS1.rearCentralUltr]    
            ULTRASONIC_SENSORS1.MUT.release()
            pubUltr1.publish(vectU)
            rateU.sleep()

            ULTRASONIC_SENSORS2.MUT.acquire()
            vectU2.data = [ULTRASONIC_SENSORS2.rearLeftUltr, ULTRASONIC_SENSORS2.rearRightUltr, ULTRASONIC_SENSORS2.frontCentralUltr]    
            ULTRASONIC_SENSORS2.MUT.release()
            pubUltr2.publish(vectU2)
            rateU2.sleep()

            GPS.MUT.acquire()
            vectGPS.latitude = GPS.latitude
            vectGPS.longitude = GPS.longitude
            GPS.MUT.release()
            pubGPS.publish(vectGPS)
            rateGPS.sleep()

            IMU.MUT.acquire()
            vectIMUraw.header.stamp = current_time
            vectIMUraw.angular_velocity.x = IMU.x_rotation
            vectIMUraw.angular_velocity.y = IMU.y_rotation
            vectIMUraw.angular_velocity.z = IMU.z_rotation
            vectIMUraw.linear_acceleration.x = IMU.x_acceleration
            vectIMUraw.linear_acceleration.y = IMU.y_acceleration
            vectIMUraw.linear_acceleration.z = IMU.z_acceleration
            IMU.MUT.release()
            pubIMUraw.publish(vectIMUraw)
            rateIMUraw.sleep()

            IMU.MUT.acquire()
            vectIMUmagn.header.stamp = current_time
            vectIMUmagn.magnetic_field.x = IMU.x_magneto
            vectIMUmagn.magnetic_field.y = IMU.y_magneto
            vectIMUmagn.magnetic_field.z = IMU.z_magneto
            IMU.MUT.release()
            pubIMUmagn.publish(vectIMUmagn)
            rateIMUmagn.sleep()


            

if __name__ == '__main__':
       
    listener()
    print('Bring up CAN0....')
    #os.system("sudo /sbin/ip link set can0 up type can bitrate 400000")
    time.sleep(0.1)

    try:
        bus = can.interface.Bus(channel='can0', bustype='socketcan_native')
    except OSError:
        print('Cannot find PiCAN board.')
        exit()

    newthread = MyReceive(bus)
    newthread.start()
    newsend = MySend(bus)
    newsend.start()
    
    newrostalker = MyTalker()
    newrostalker.start()
    #newthread.join()
    
    # spin() simply keeps python from exiting until this node is stopped
    rospy.spin()

