"""The inspected media-runtime directory supplies the executable and sources.

Do not collect the wheel's unrelated nonfree binary as an implicit fallback.
"""
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules('imageio_ffmpeg')
datas = []
binaries = []
