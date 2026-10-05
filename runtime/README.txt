本目录是随项目一起分发的 Python 运行时（Windows x64）。
来源：CPython 3.12 + tkinter/tcl-tk 8.6 + Pillow 12.3
已裁剪掉与 AI Downloader 无关的第三方库（numpy/pandas/lxml/openpyxl 等）、
测试与示例目录、头文件与静态库、pip、__pycache__。

启动器（启动 AI Downloader.bat）会优先使用这里的 pythonw.exe；
若本目录缺失或损坏，会自动回退到系统安装的 Python。
