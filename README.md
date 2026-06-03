# juggling_ball
Juggling balls with IMU and LED, sending data to host via bluetooth low energy (BLE). Running circuitpython. Three currently made.

### v1:  
- (1) QTPY
- (1) MPU6050
- (1) neopixel
- (1) Lipo 3.7V 400mAH
  
  
### v2:
- (1) Xiao SEEED studio nRF52840 Sense Plus (includes IMU on board)
- (2) neopixel
- (1) Lipo 3.7V 400mAH
- (1) button

Two part threaded shell printed via Clear Overture Fast TPU with a thickness of 4.5mm. PLA printed holder for board and button in order to have button and USBC be externally accessible. Hot glue for joining it all together.


### Future goals:
- Capacitive sensing
- Silicone or Polyurethane shell instead of 3D printed exterior (maybe rotomolded)

### Notes:
- Code for each ball should be changed so that ball name is "Ball_" A,B,C,etc.

commands sent from host:  
COLOR ball_A 255 0 0        # set ball_A to red  
COLOR ball_B 0 255 0        # set ball_B to green  
COLOR ALL 0 0 255           # set all balls to blue  
COLOR ball_A RESET          # return ball_A to its current mode  
MODE ball_A rainbow         # switch back to rainbow  
MODE ALL blue               # set all balls to solid blue  
