# -*- coding: utf-8 -*-
"""Setup script for RevPiPyLoad."""
__author__ = "Sven Sager"
__copyright__ = "Copyright (C) 2023 Sven Sager"
__license__ = "GPLv2"

from setuptools import find_namespace_packages, setup

from src.revpipyload import __version__

setup(
    name="revpipyload",
    version=__version__,

    packages=find_namespace_packages("src"),
    package_dir={'': 'src'},
    include_package_data=True,

    install_requires=[
        "paho-mqtt >= 1.4.0",
        "revpimodio2 >= 2.6.0",
    ],
    entry_points={
        'console_scripts': [
            'revpipyloadd = revpipyload.revpipyload:main',
            'revpipyload_secure_installation = revpipyload.secure_installation:main',
        ],
    },

    platforms=["revolution pi"],

    url="https://revpimodio.org/revpipyplc/",
    license="GPLv2",
    author="Sven Sager",
    author_email="akira@narux.de",
    maintainer="Sven Sager",
    maintainer_email="akira@revpimodio.org",

    description="PLC Loader für Python-Projekte auf den RevolutionPi",
    long_description="Dieses Programm startet beim Systemstart ein angegebenes Python PLC \n"
                     "Programm. Es überwacht das Programm und startet es im Fehlerfall neu. \n"
                     "Bei Absturz kann das gesamte /dev/piControl0 auf 0x00 gesetzt werden. \n"
                     "Außerdem stellt es einen XML-RPC Server bereit, über den die Software \n"
                     "auf den RevPi geladen werden kann. Das Prozessabbild kann über ein \n"
                     "Tool zur Laufzeit überwacht werden.",
    keywords=["revpi", "revolution pi", "revpimodio", "plc"],
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: No Input/Output (Daemon)",
        "Intended Audience :: Manufacturing",
        "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
        "Operating System :: POSIX :: Linux",
        "Topic :: System :: Operating System",
    ],
)
