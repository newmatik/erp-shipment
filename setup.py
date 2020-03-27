# -*- coding: utf-8 -*-
from setuptools import setup, find_packages

with open('requirements.txt') as f:
	install_requires = f.read().strip().split('\n')

# get version from __version__ variable in shipment/__init__.py
from shipment import __version__ as version

setup(
	name='shipment',
	version=version,
	description='Shipment',
	author='Newmatik',
	author_email='service@newmatik.com',
	packages=find_packages(),
	zip_safe=False,
	include_package_data=True,
	install_requires=install_requires
)
