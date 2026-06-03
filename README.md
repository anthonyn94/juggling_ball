# juggling_ball
Juggling balls with IMU and LED, sending data to host via bluetooth low energy (BLE). Running circuitpython. Three currently made. Two part threaded shell printed via Clear Overture Fast TPU with a thickness of 4.5mm. PLA printed holder for board and button in order to have button and USBC be externally accessible. Hot glue for joining it all together.

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

### How to Use
Turn ball(s) on via pressing down button for at least 1 second. Lights should turn on shortly after (takes a very small delay for the board to wake). Run host.py code on computer, terminal should start updating with messages from balls, including a "dashboard" for delay time. Turn ball(s) off by pressing button for at least 1 second.

### How to Edit
Circuitpython is already loaded on the boards, which should result in their own directory appearing when you connect to the board's usbc port via cable. From there you can navigate to the directory and directly edit the code.py file - any saved changes will automatically update the board. If adding new functionality you may need to add [libraries](https://circuitpython.org/libraries).


### Future goals:
- Capacitive sensing
- Silicone or Polyurethane shell instead of 3D printed exterior (maybe rotomolded)

### Notes:
- Code for each ball should be changed so that ball name is "Ball_" A,B,C,etc.
- Tap detection is based on acceleration change, sensitivity can be changed from code.

commands sent from host:  
COLOR ball_A 255 0 0        # set ball_A to red  
COLOR ball_B 0 255 0        # set ball_B to green  
COLOR ALL 0 0 255           # set all balls to blue  
COLOR ball_A RESET          # return ball_A to its current mode  
MODE ball_A rainbow         # switch back to rainbow  
MODE ALL blue               # set all balls to solid blue  
