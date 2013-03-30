Notes
-----

Uses the cm19a driver, by Andrew Cuddon, from:
http://www.cm19a.com/2013/02/python-cm19a-driver-change-log.html

Setup
-----

You'll need PyUSB:

 > sudo apt-get install python-pip
 > sudo pip install pyusb

And to stop drivers from stealing the device:

 > sudo nano /etc/modprobe.d/raspi-blacklist.conf
 
Then add the following line and save:
    blacklist ati_remote