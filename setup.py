#! /usr/bin/env python3
# -*- coding: utf-8 -*-
"""Setupscript fuer RevPiPyLoad."""
__author__ = "Sven Sager"
__copyright__ = "Copyright (C) 2018 Sven Sager"
__license__ = "GPLv3"
import distutils.command.install_egg_info
from glob import glob
from distutils.core import setup


class MyEggInfo(distutils.command.install_egg_info.install_egg_info):

    """Disable egg_info installation, seems pointless for a non-library."""

    def run(self):
        """just pass egg_info."""
        pass


setup(
    author="Sven Sager",
    author_email="akira@narux.de",
    url="https://revpimodio.org/revpipyplc/",
    maintainer="Sven Sager",
    maintainer_email="akira@revpimodio.org",

    license="LGPLv3",
    name="revpipyload",
    version="0.9.7",

    scripts=[
        "data/revpipyload",
        "data/revpipyload_secure_installation",
    ],

    install_requires=["revpimodio2 >= 2.5.0"],
    python_requires=">=3.2",

    data_files=[
        ("/etc/avahi/services", [
            "data/etc/avahi/services/revpipyload.service",
        ]),
        ("/etc/revpipyload", [
            "data/etc/revpipyload/aclplcslave.conf",
            "data/etc/revpipyload/aclxmlrpc.conf",
            "data/etc/revpipyload/replace_ios.conf",
            "data/etc/revpipyload/revpipyload.conf",
        ]),
        ("share/revpipyload", glob("revpipyload/*.*")),
        ("share/revpipyload/shared", glob("revpipyload/shared/*.*")),
        ("share/revpipyload/paho", ["lib/paho/__init__.py"]),
        ("share/revpipyload/paho/mqtt", glob("lib/paho/mqtt/*.*")),
        ("/var/lib/revpipyload", [
            "data/var/lib/revpipyload/.placeholder",
        ])
    ],

    description="PLC Loader für Python-Projekte auf den RevolutionPi",
    long_description=""
    "Dieses Programm startet beim Systemstart ein angegebenes Python PLC \n"
    "Programm. Es überwacht das Programm und startet es im Fehlerfall neu. \n"
    "Bei Absturz kann das gesamte /dev/piControl0 auf 0x00 gesetzt werden. \n"
    "Außerdem stellt es einen XML-RPC Server bereit, über den die Software \n"
    "auf den RevPi geladen werden kann. Das Prozessabbild kann über ein \n"
    "Tool zur Laufzeit überwacht werden.",

    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: No Input/Output (Daemon)",
        "Intended Audience :: Manufacturing",
        "License :: OSI Approved :: "
        "GNU Lesser General Public License v3 (LGPLv3)",
        "Operating System :: POSIX :: Linux",
        "Topic :: System :: Operating System",
    ],
    cmdclass={"install_egg_info": MyEggInfo},
)
