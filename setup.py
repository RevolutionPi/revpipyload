#! /usr/bin/env python3
#
# (c) Sven Sager, License: LGPLv3
#
# -*- coding: utf-8 -*-
"""Setupscript fuer RevPiPyLoad."""
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
    version="0.2.6",

    scripts=["data/revpipyload"],

    data_files=[
        ("/etc/default", ["data/etc/default/revpipyload"]),
        ("/etc/revpipyload", ["data/etc/revpipyload/revpipyload.conf"]),
        ("share/revpipyload", glob("revpipyload/*.*")),
        ("/var/lib/revpipyload", ["data/var/lib/revpipyload/.placeholder"])
    ],

    description="PLC Loader für Python-Projekte auf den RevolutionPi",
    long_description=""
    "Dieses Programm startet beim Systemstart ein angegebenes Python PLC\n"
    "Programm. Es überwacht das Programm und startet es im Fehlerfall neu.\n"
    "Bei Absturz kann das gesamte /dev/piControl0 auf 0x00 gesetzt werden.\n"
    "Außerdem stellt es einen XML-RPC Server bereit, über den die Software\n"
    "auf den RevPi geladen werden kann. Das Prozessabbild kann über ein Tool\n"
    "zur Laufzeit überwacht werden.",

    classifiers=[
        "License :: OSI Approved :: "
        "GNU Lesser General Public License v3 (LGPLv3)",
        "Operating System :: POSIX :: Linux",
    ],
    cmdclass={"install_egg_info": MyEggInfo},
)
