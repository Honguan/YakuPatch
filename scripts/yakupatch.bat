@echo off
rem YakuPatch command line with the bundled Python, e.g. yakupatch.bat extract "D:\Games\MyGame"
"%~dp0runtime\python.exe" -m yakupatch %*
