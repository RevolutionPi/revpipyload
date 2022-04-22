# -*- coding: utf-8 -*-
"""Watchdog systems to monitor plc program and reset_driver of piCtory."""
__author__ = "Sven Sager"
__copyright__ = "Copyright (C) 2020 Sven Sager"
__license__ = "GPLv3"

import os
from fcntl import ioctl
from random import random
from struct import pack, unpack
from subprocess import Popen
from threading import Event, Thread
from time import time

import proginit as pi


class SoftwareWatchdog:

    def __init__(self, address, timeout, kill_process=None):
        """
        Software watchdog thread, which must be recreate if triggered.

        :param address: Byte address of RevPiLED byte
        :param timeout: Timeout to trigger watchdog on no change of bit
        :param kill_process: Process to kill on trigger
        """
        self.__th = Thread()
        self._exit = Event()
        self._ioctl_bytes = b''
        self._process = None
        self._stopped = False
        self._timeout = 0.0
        self.triggered = False

        # Process and check the values
        self.address = address
        self.kill_process = kill_process

        # The timeout property will start/stop the thread
        self.timeout = timeout

    def __th_run(self):
        """Thread function for watchdog."""
        pi.logger.debug("enter SoftwareWatchdog.__th_run()")

        # Startup delay to let the python program start and trigger
        if self._exit.wait(2.0):
            pi.logger.debug("leave SoftwareWatchdog.__th_run()")
            return

        fd = os.open(pi.pargs.procimg, os.O_RDONLY)
        mrk = self._ioctl_bytes
        tmr = time()

        # Random wait value 0.0-0.1 to become async to process image
        while not self._exit.wait(random() / 10):
            try:
                # Get SoftWatchdog bit
                bit_7 = ioctl(fd, 19215, self._ioctl_bytes)
            except Exception:
                pass
            else:
                if bit_7 != mrk:
                    # Toggling detected, wait the rest of time to free cpu
                    self._exit.wait(self._timeout - (time() - tmr))
                    mrk = bit_7
                    tmr = time()
                    continue

            if time() - tmr >= self._timeout:
                pi.logger.debug("software watchdog timeout reached")
                self.triggered = True
                if self._process is not None:
                    self._process.kill()
                    pi.logger.warning("process killed by software watchdog")
                break

        os.close(fd)

        pi.logger.debug("leave SoftwareWatchdog.__th_run()")

    def reset(self):
        """Reset watchdog functions after triggered or stopped."""
        pi.logger.debug("enter SoftwareWatchdog.reset()")
        self._stopped = False
        self._exit.clear()
        self.triggered = False

        # The timeout property will start / stop the thread
        self.timeout = int(self.timeout)

        pi.logger.debug("leave SoftwareWatchdog.reset()")

    def stop(self):
        """Shut down watchdog task and wait for exit."""
        pi.logger.debug("enter SoftwareWatchdog.stop()")
        self._stopped = True
        self._exit.set()
        if self.__th.is_alive():
            self.__th.join()
        pi.logger.debug("leave SoftwareWatchdog.stop()")

    @property
    def address(self):
        """Byte address of RevPiLED byte."""
        return unpack("<Hxx", self._ioctl_bytes)[0]

    @address.setter
    def address(self, value):
        """Byte address of RevPiLED byte."""
        if not isinstance(value, int):
            raise TypeError("address must be <class 'int'>")
        if not 0 <= value < 4096:
            raise ValueError("address must be 0 - 4095")

        # Use Bit 7 of RevPiLED byte (wd of Connect device)
        self._ioctl_bytes = pack("<HBx", value, 7)

        pi.logger.debug("set software watchdog address to {0}".format(value))

    @property
    def kill_process(self):
        return self._process

    @kill_process.setter
    def kill_process(self, value):
        if not (value is None or isinstance(value, Popen)):
            raise TypeError("kill_process must be <class 'subprocess.Popen'>")
        self._process = value

    @property
    def timeout(self):
        """Timeout to trigger watchdog on no change of bit."""
        return int(self._timeout)

    @timeout.setter
    def timeout(self, value):
        """
        Timeout to trigger watchdog on no change of bit.

        Value in seconds, 0 will stop watchdog monitoring.
        """
        if not isinstance(value, int):
            raise TypeError("timeout must be <class 'int'>")
        if value < 0:
            raise ValueError("timeout value must be 0 to disable or a positive number")

        if value == 0:
            # A value of 0 will stop the watchdog thread
            self._exit.set()
            if self.__th.is_alive():
                self.__th.join()

            # Set after exit thread to not trigger watchdog
            self._timeout = 0.0
        else:
            self._timeout = float(value)
            if not (self.triggered or self._stopped or self.__th.is_alive()):
                self._exit.clear()
                self.__th = Thread(target=self.__th_run)
                self.__th.start()
            pi.logger.debug("set software watchdog timeout to {0} seconds".format(value))


class ResetDriverWatchdog(Thread):
    """Watchdog to catch a piCtory reset_driver action."""

    def __init__(self):
        super(ResetDriverWatchdog, self).__init__()
        self.daemon = True
        self._calls = []
        self._exit = False
        self._fh = None
        self.not_implemented = False
        """True, if KB_WAIT_FOR_EVENT is not implemented in piControl."""
        self._triggered = False
        self.start()

    def run(self):
        """
        Mainloop of watchdog for reset_driver.

        If the thread can not open the process image or the IOCTL is not
        implemented (wheezy), the thread function will stop. The trigger
        property will always return True.
        """
        pi.logger.debug("enter ResetDriverWatchdog.run()")

        try:
            self._fh = os.open(pi.pargs.procimg, os.O_RDONLY)
        except Exception:
            self.not_implemented = True
            pi.logger.error(
                "can not open process image at '{0}' for piCtory reset_driver watchdog"
                "".format(pi.pargs.procimg)
            )
            return

        # The ioctl will return 2 byte (c-type int)
        byte_buff = bytearray(2)
        while not self._exit:
            try:
                rc = ioctl(self._fh, 19250, byte_buff)
                if rc == 0 and byte_buff[0] == 1:
                    self._triggered = True
                    pi.logger.debug("piCtory reset_driver detected")
                    for func in self._calls:
                        func()
            except Exception:
                self.not_implemented = True
                os.close(self._fh)
                self._fh = None
                pi.logger.warning("IOCTL KB_WAIT_FOR_EVENT is not implemented")
                return

        pi.logger.debug("leave ResetDriverWatchdog.run()")

    def register_call(self, function):
        """Register a function, if watchdog triggers."""
        if not callable(function):
            return ValueError("Function is not callable.")
        if function not in self._calls:
            self._calls.append(function)

    def stop(self):
        """Stop watchdog for piCtory reset_driver."""
        pi.logger.debug("enter ResetDriverWatchdog.stop()")

        self._exit = True
        if self._fh is not None:
            os.close(self._fh)
            self._fh = None

        pi.logger.debug("leave ResetDriverWatchdog.stop()")

    def unregister_call(self, function=None):
        """Remove a function call on watchdog trigger."""
        if function is None:
            self._calls.clear()
        elif function in self._calls:
            self._calls.remove(function)

    @property
    def triggered(self):
        """Will return True one time after watchdog was triggered."""
        rc = self._triggered
        self._triggered = False
        return rc
