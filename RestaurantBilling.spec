# PyInstaller single-file Windows build. User data is never bundled.
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

datas = collect_data_files('customtkinter') + collect_data_files('escpos')
binaries = collect_dynamic_libs('libusb_package')
a = Analysis(
    ['main.py'], pathex=[], binaries=binaries, datas=datas,
    hiddenimports=['escpos.printer', 'escpos.printer.usb', 'escpos.printer.win32raw',
                   'usb.backend.libusb1', 'libusb_package', 'win32print', 'win32ui',
                   'win32gui', 'win32con', 'PIL.ImageWin'],
    hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=['pytest'],
    noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name='RestaurantBilling', debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False, disable_windowed_traceback=False)
