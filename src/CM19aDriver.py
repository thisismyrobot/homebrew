#!/usr/bin/env python

"""

    A Python driver for the CM19a X10 RF Transceiver (USB)
    This is a user space driver so a kernel driver for the CM19a does not need
    to be installed.

    Initially coded by: Andrew Cuddon (www.cuddon.net)

    Heavily revised for web-server-only mode by Robert Wallhead
    (thisismyrobot.com)

"""

import BaseHTTPServer
import httplib
import os
import SimpleHTTPServer
import sys
import threading
import time
import types
import usb


SERVER_IP_ADDRESS = 'localhost'
SERVER_PORT = 80
REFRESH = 1.0

global cm19a, server


class USBdevice:
    def __init__(self, vendor_id, product_id):
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.bus = None
        self.device = None
        self._find_device()

    def _find_device(self):
        buses = usb.busses()
        for bus in buses:
            for device in bus.devices:
                if (device.idVendor == self.vendor_id and
                    device.idProduct == self.product_id):
                    self.bus = bus
                    self.device = device
                    break
            if self.device:
                break

    def get_device(self):
        return self.device


class CM19aDevice(threading.Thread):

    # Class constants (run once only when the module is loaded)
    VENDOR_ID = 0x0bc7              # Vendor Id: X10 Wireless Technology, Inc.
    PRODUCT_ID = 0x0002             # Product Id: Firecracker Interface (ACPI-compliant)
    CONFIGURATION_ID = 1            # Use configuration #1 (This device has only 1 configuration)
    INTERFACE_ID = 0                # The interface we use to talk to the device (This is the only configuration available for this device)
    ALTERNATE_SETTING_ID = 0        # The alternate setting to use on the selected interface
    READ_EP_ADDRESS = 0x081         # Endpoint for reading from the device: 129 (decimal)
    WRITE_EP_ADDRESS = 0x002        # Endpoint for writing to the device: 2  (decimal)
    PACKET_LENGTH = 8               # Maximum packet length is 8 bytes (possibly 5 for std X10 remotes)
    ACK = 0x0FF                     # Bit string received on CM19a send success = 11111111 (binary) = 255 (decimal)

    SEND_TIMEOUT = 1000             # 1000 ms = 1s
    RECEIVE_TIMEOUT = 100           # 100 ms
    PROTOCOL_FILE = "CM19aProtocol.ini"

    def __init__(self, refresh=1, polling=False):
        # Initialise the object and create the device driver
        threading.Thread.__init__(self)     # initialise the thread for automatic monitoring
        self.refresh = refresh
        self.polling = polling
        self.alive = False                  # Set to false to permanently stop the thread that automatic monitors for received commands
        self.paused = False                 # Set to True to temporarily stop automatic monitoring of receive commands
        self.initialised = False            # True when the device has been opened and the driver initialised successfully
        self.device = False                 # USB device class instance
        self.receivequeue = []              # Queue of commands received automatically
        self.receivequeuecount = 0          # Number of items in the receive queue
        self.protocol = {}                  # Dict containing the communications protocol for the CM19a

        # Find the correct USB device
        self.USB_device = USBdevice(self.VENDOR_ID, self.PRODUCT_ID)
        # save the USB instance that points to the CM19a
        self.device = self.USB_device.device
        if not self.device:
            print >> sys.stderr, "The CM19a is probably not plugged in or is being controlled by another USB driver."
            return

        # Open the device for send/receive
        if not self._open_device():
            # Device was not opened successfully
            return

        # Load the communications protocol
        self._load_protocol()

        # Initialise the device to read the remote controls
        self._initialise_remotes()

        # Start the thread for automatically polling for inbound commands
        # If you just send commands via the CM19a and do not need to check for incoming commands from a remote control
        # then set 'start' to False when the class instance is created
        if self.polling:
            self.start()

    def _open_device(self):
        """ Open the device, claim the interface, and create a device handle """

        if not self.device:
            # no device object
            return False

        self.handle = None      # file-like handle
        try:
            # Open the device and create a handle
            self.handle = self.device.open()                    # --> DeviceHandle object

            # Select the active configuration
            self.handle.setConfiguration(self.CONFIGURATION_ID)

            # detach any other kernel drivers that are currently attached to the required interface
            #self.handle.detachKernelDriver(self.INTERFACE_ID)

            # Claim control of the interface
            self.handle.claimInterface(self.INTERFACE_ID)

            # Set the alternative setting for this interface
            self.handle.setAltInterface(self.ALTERNATE_SETTING_ID)

            print "Cm19a opened and interface claimed."
            self.initialised = True
        except usb.USBError, err:
            print >> sys.stderr, err
            print >> sys.stderr, "Unable to open and claim the CM19a interface."
            self.initialised = False
            return False

        return True


    def _initialise_remotes(self):
        # Initilises the CM19a for wireless remote controls
        sequence=[]
        sequence.append([0x020,0x034,0x0cb,0x058,0x0a7])       # 5 byte sequence (interestingly this is the same sequence as P16 ON)
        sequence.append([0x080,0x001,0x000,0x020,0x014])       # 5 byte sequence
        sequence.append([0x080,0x001,0x000,0x000,0x014,0x024,0x020,0x020])     # 8 byte sequence

        for s in sequence:
            result = self._write_bytes(s)
            if not result:
                print  >> sys.stderr, "Error initialising the CM19a for wireless remote controls"


    def run(self):
        """
            This is the main function that will run in the thread when start() is issued
            Check for an incoming command received via the CM19a
            If a command is found, it is decoded and added to the receive queue
            Rechecks the device every refresh seconds
            set 'self.alive' to False to halt checking
        """
        self.alive = True
        while self.alive:
            # continues to run the following code in a separate thread until alive is set to false
            if self.paused:
                # Device is paused (eg during a send command) so do not read
                pass
            else:
                # Device is not paused so check for incoming commands
                self.receive()

            # wait for 'refresh' seconds before checking the device again
            time.sleep(self.refresh)


    def receive(self):
        """ Receive any available data from the Cm19a
            Append it to the queue
        """
        if not self.initialised:
            return

        # Raw read any data from the device
        data = None
        try:
            data = self.handle.interruptRead(self.READ_EP_ADDRESS, self.PACKET_LENGTH, self.RECEIVE_TIMEOUT)
        except:
            # error or simply nothing in the buffer to read
            pass

        # Decode the data and add any commands to the receive queue
        if data:
            # something read so add it the the receive queue
            result = self._decode(data)     # decode the byte stream
            if result == str(self.ACK):
                # Ignore any send command acknowledgements
                pass
            else:
                self.receivequeue.append(result)
                self.receivequeuecount = self.receivequeuecount + 1

    def getReceiveQueue(self):
        """ 
            Returns the queue (list) of incoming commands
            Clears it ready for receiving more
        """

        if self.receivequeuecount > 0:
            # Pause receiving so the receive thread does not add items just before we clear to queue
            self.paused = True
            # Temporarily store the queue because we clear it before we return it the the calling routine
            tmp = self.receivequeue

            # clear the queue
            self.receivequeue = []
            self.receivequeuecount = 0
            self.paused = False
            return tmp
        else:
            # no commands so return an empty queue (list)
            return []


    def send(self, house_code, unit_number, function):
        """
            Sends a command request to the device
            Tries to send just once
            Returns False if an error occurs
        """
        if not self.initialised:
            return False

        # Encode the command to the X10 protocol
        command_sequence = self._encode(house_code, unit_number, function)        # -> list
        if not command_sequence:
            # encoding error
            return False

        # Pause automatic receiving while we send
        if self.polling:
            self.paused = True
            time.sleep(int(self.refresh/2))

        # Flush the device before we send anything so we do not lose any incoming requests
        self.receive()

        # Write the command sequence to the device
        result = self._write_bytes(command_sequence)

        # Restart automatic receiving and return
        self.paused = False
        return result


    def _write_bytes(self, bytesequence):
        # Write the bytes to the device
        # bytesequence is a list of bytes to be written
        if len(bytesequence) == 0:
            return False

        try:
            chars_written = self.handle.interruptWrite(self.WRITE_EP_ADDRESS, bytesequence, self.SEND_TIMEOUT)
            returnval = True
        except Exception, err:
            chars_written = 0
            returnval = False

        if chars_written != len(bytesequence):
            # Incorrect number of bytes written
            returnval = False

        return returnval


    def _encode(self, house_code,  unit_number,  on_off):
        """
            Looks up the X10 protocol for the appropriate byte command sequence
        """
        key = house_code.upper() + unit_number + on_off.upper()
        if key in self.protocol:
            return self.protocol[key]
        else:
            return False


    def _decode(self, receive_sequence):
        """
            Uses the X10 protocol to decode a command received by the CM19a
            'receive_sequence' is a list of decimal values
            returns the command (housecode, unit number, on/off) that the sequence represents
            If it cannot decode the sequence then the sequence is simply returned
        """

        if not (self.protocol and self.protocol_remote):
            # the protocol has not been loaded
            return ""

        return_value = None

        # Convert the inbound command sequence (bytes) to a string to it can be compared to the protocol values
        # the received command sequence is a list of decimal values (not text)
        # Note: cannot use Python sets for comparing the lists because there may be duplicate values in each list (esp zeros)
        receive_string = ""
        for i in range(len(receive_sequence)):
            receive_string += str(receive_sequence[i])

        # Now search for the string in the protocol
        for cmd, seq in self.protocol.iteritems():
            # Protocol is a dict of command:command_sequence pairs
            # cmd is the command (eg A1OFF)
            # seq is a list of the command bytes
            protocol_string = ""
            for i in range(len(seq)):
                protocol_string += str(seq[i])
            if receive_string == protocol_string:
                # protocol match found
                return_value = cmd
                break

        # Now search for the string in the RF remote protocol (this overrides anything found in the above std x10 protocol)
        for cmd, seq in self.protocol_remote.iteritems():
            # Protocol is a dict of command:command_sequence pairs
            # cmd is the command (eg A1OFF)
            # seq is a list of the command bytes
            protocol_string = ""
            for i in range(len(seq)):
                protocol_string += str(seq[i])
            if receive_string == protocol_string:
                # protocol match found
                return_value = cmd
                break

        if not return_value:
            # The byte string was not found in the protocol so return the bytes
            receive_string = ""
            for i in range(len(receive_sequence)):
                receive_string += str(receive_sequence[i])+" "
            return_value = receive_string

        return return_value.strip()


    def _load_protocol(self):
        # Loads the X10 protocol into a dict

        if not self.device:
            return

        self.protocol = {}  # empty dictionary
        self.protocol_remote = {}  # empty dictionary

        # Open the configuration file
        fname = self.PROTOCOL_FILE
        if not os.path.isfile(fname):
            self.initialised = False
            return None
        f = open(fname, "r")

        section=None
        for aline in f.readlines():
            aline = aline.strip()
            if not aline or aline[0] == "#":
                # comment or blank line so ignore
                pass
            elif aline[0] == "[":
                # new section
                section = aline
            else:
                # extract the data using regular expressions
                if section == "[CM19A X10 CODES]":
                    aline = aline.replace(" ", "")  # remove any whitespace
                    data = aline.split(',', 3)     # Comma separate data but keep the command sequence as a single string
                    house_code = data[0].upper()
                    unit_number = data[1]
                    on_off_dim = data[2].upper()
                    command_sequence = data[3].split(',')
                    for i in range(len(command_sequence)):
                        command_sequence[i] = int(command_sequence[i], 16)      # Convert the list items from text to values (the text representation is hex)
                    if data:
                        # add the command to the list (which is actually a dictionary in the form:
                        #{key : command_sequence}   command_sequence is a list of the bytes
                        key = house_code + unit_number + on_off_dim
                        self.protocol[key] =  command_sequence
                elif section == "[X10 RF REMOTE DIM/BRIGHT CODES]":
                    aline = aline.replace(" ", "")  # remove any whitespace
                    data = aline.split(',', 3)     # Comma separate data but keep the command sequence as a single string
                    house_code = data[0].upper()
                    unit_number = data[1]
                    on_off_dim = data[2].upper()
                    command_sequence = data[3].split(',')
                    for i in range(len(command_sequence)):
                        command_sequence[i] = int(command_sequence[i], 16)      # Convert the list items from text to values (the text representation is hex)
                    if data:
                        # add the command to the list (which is actually a dictionary in the form:
                        #{key : command_sequence}   command_sequence is a list of the bytes
                        key = house_code + unit_number + on_off_dim
                        self.protocol_remote[key] =  command_sequence
                elif section == "[OTHER]":
                    # Not required
                    pass
                #endif
            # endif
        #end for

        f.close()
    #endsub


    def finish(self):
        """ Close everything and release device interface """
        self.alive = False
        self.paused = True
        try:
            #self.handle.reset()
            self.handle.releaseInterface()
        except Exception, err:
            print >> sys.stderr, err
        self.handle, self.device = None, None


class HTTPServer(BaseHTTPServer.HTTPServer):
    """
        Subclasses the BaseHTTPServer and overrides the serve_forever method so that we can interrupt it and quit gracefully
    """
    def serve_forever(self):
        # override the std serve_forever method which can be stopped only by a Ctrl-C
        self.alive = True
        while self.alive:
            # Continue to respond to HTTP requests until self.alive is set to False
            self.handle_request()

class HTTPhandler(SimpleHTTPServer.SimpleHTTPRequestHandler):
    """ Performs X10 controls in the /api/ namespace, loads file for other
        paths.
    """
    server_version= "MyHandler/1.1"

    def do_GET(self):
        # Example client calls
        # http://192.168.1.3:8008/?house=A&unit=1&command=ON
        # http://192.168.1.3:8008/?house=A&unit=1&command=DIM
        # http://192.168.1.3:8008?command=getqueue
        # http://192.168.1.3:8008?command=getlog
        # http://192.168.1.3:8008?command=quit

        # remove leading gumph
        qmarkpos = self.path.find('?')
        self.path = self.path[qmarkpos+1:]

        # replace any escaped spaces with a real space
        self.path = self.path.replace('%20',  " ")
        respcode = 200

        # extract the arguments
        argsdict = {}
        for arg in self.path.split('&'):
            if arg.find('=') >= 0:
                key = arg.split('=')[0]
                value = arg.split('=')[1]
                argsdict[key] = value

        house = ""
        unit = ""
        command = ""

        if 'house' in argsdict:
            house = argsdict['house'].lower()
        if 'unit' in argsdict:
            unit = argsdict['unit']
        if 'command' in argsdict:
            command = argsdict['command'].lower()

        if command in ['on', 'off', 'dim', 'bright', 'allon', 'alloff']:
            # Valid command request
            try:
                response = cm19a.send(house, unit, command)     # True if the command was sent OK
            except:
                response = False
        else:
            # error no command request
            respcode = 400
            response = "NAK: Invalid 'command' value"

        if type(response) == types.BooleanType:
            if response:
                respcode = 200
                response = "ACK"
            else:
                respcode = 500
                response = "NAK"

        self.sendPage(respcode, "text/html", str(response))

    def sendPage(self, code, cttype, body):
        body+= "\n\r"
        self.send_response(code)
        self.send_header("Content-type", cttype)
        self.send_header("Content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__ == '__main__':
    cm19a = CM19aDevice(REFRESH, polling = True)       # Initialise device. Note: auto polling/receviing in a thread is turned ON
    if cm19a.initialised:
        server = HTTPServer((SERVER_IP_ADDRESS, SERVER_PORT,), HTTPhandler)
        server.serve_forever()
        # Finish and tidy up
        server = None
        cm19a.finish()
        sys.exit(0)
    else:
        cm19a.finish()
        sys.exit(1)
