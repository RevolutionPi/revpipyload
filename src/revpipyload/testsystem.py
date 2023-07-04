# -*- coding: utf-8 -*-
"""Test all config files and print results."""
__author__ = "Sven Sager"
__copyright__ = "Copyright (C) 2023 Sven Sager"
__license__ = "GPLv2"

from configparser import ConfigParser

from . import proginit

newline = "\n------------------------------------------------------------\n"


class TestSystem:
    """Main class for test system of revpipyload."""

    def __init__(self):
        """Init TestSystem class."""
        self.gc = ConfigParser()
        lst_file = self.gc.read(proginit.globalconffile)
        if len(lst_file) <= 0:
            proginit.logger.error("can not read config file")

    def test_replace_io(self):
        """Test replace_io file.
        @return 0 if successful testet"""
        print("Test replace_io data:")
        file = self.gc["DEFAULT"].get("replace_ios")
        if file is None:
            print("\tFile MISSING")
            return 1

        print("\tFile: {0}\n".format(file))

        try:
            import revpimodio2
        except Exception as e:
            print("\tERROR: {0}".format(e))
            return 1

        try:
            rpi = revpimodio2.RevPiModIO(
                configrsc=proginit.pargs.configrsc,
                procimg=proginit.pargs.procimg,
                monitoring=True,
                debug=True,
                replace_io_file=file,
            )
        except Exception as e:
            print(e)
            return 1
        else:
            print("\tPrinting replaced IOs:")
            for io in rpi.io:
                if isinstance(io, revpimodio2.io.StructIO):
                    print("\t\tNew io: {0}".format(io.name))

            rpi.cleanup()
            return 0

    def test_sections(self):
        """Test config file.
        @return 0 if successful testet"""
        print("Parse config file:")
        print("\tSection DEFAULT : {0}".format("DEFAULT" in self.gc))
        print("\tSection PLCSERVER: {0}".format("PLCSERVER" in self.gc))
        print("\tSection XMLRPC  : {0}".format("XMLRPC" in self.gc))
        print("\tSection MQTT    : {0}".format("MQTT" in self.gc))
        return 0

    def start(self):
        """Start test program and run tests."""
        program_ec = 0

        print("--- RevPiPyLoad Testsystem ---\n")
        ec = self.test_sections()
        program_ec += int(ec) << 0
        print(newline)

        # TODO: Test Values of each section
        # print()

        ec = self.test_replace_io()
        program_ec += (int(ec) << 7)
        print(newline)

        if program_ec != 0:
            print("result: {0}".format(program_ec))
        exit(program_ec)
