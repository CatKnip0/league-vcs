from setuptools import setup, find_packages

setup(
    name='league-vcs',
    version='0.2.0',
    packages=find_packages(),
    author='CatKnip0',
    entry_points={
        'console_scripts': ['league-vcs=league_vcs.cli:main']
    },
    install_requires=['click>=8.0', 'pywin32>=306', 'wxPython>=4.2', 'tqdm>=4.60'],
)
