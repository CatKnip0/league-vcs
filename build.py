from cx_Freeze import setup, Executable

setup(name="League VCS",
      version="0.2.0",
      description="Version control system and replay watcher for League of Legends.",
      executables=[
          Executable("entrypoints/main.py", target_name='League VCS',
                     icon='raster/icon.ico', base='Win32GUI'),
      ])
